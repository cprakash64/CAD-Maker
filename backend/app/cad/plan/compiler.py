"""Deterministic CadPlan → CadQuery compiler.

Trusted: dispatches on a FIXED whitelist of feature ``kind`` values, reading only
numeric params. No eval/exec, no LLM-generated code. Unknown kinds are rejected.

Each feature either adds a solid (unioned into the running body), cuts material
(holes/slots/grooves/cuts), or modifies the body (fillet/chamfer/shell/mirror).
Holes and through-holes are counted as real subtractive operations so validation
can confirm — geometrically — that requested holes were actually cut.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import cadquery as cq

from app.cad.base import CadGenerationError
from app.cad.plan.schema import MODIFIER_KINDS, CadPlan, Feature, FeatureKind
from app.export.exporter import PreviewMesh, _export_bytes, _tessellate

_BIG = 10000.0  # tool length for guaranteed-through cuts


@dataclass
class CadPlanResult:
    solid: cq.Workplane
    bbox_mm: dict[str, float]
    hole_count: int
    through_hole_count: int
    feature_count: int
    warnings: list[str] = field(default_factory=list)
    feature_meta: list[dict] = field(default_factory=list)


# --- small geometry helpers ------------------------------------------------
def _pos(f: Feature) -> tuple[float, float, float]:
    x, y, z = f.at
    return float(x), float(y), float(z)


def _dia(f: Feature, *keys: str, default: float = 0.0) -> float:
    """Resolve a diameter from any of `keys` (diameter/od/bore...) or radius*2."""
    for k in keys:
        if k in f.params:
            return abs(float(f.params[k]))
    r = f.p("radius", 0.0)
    return r * 2 if r > 0 else default


def _require(value: float, what: str) -> float:
    if value <= 0:
        raise CadGenerationError(f"{what} must be positive (got {value})")
    return value


def _box(w: float, d: float, h: float, at, base_z0: bool = True) -> cq.Workplane:
    _require(min(w, d, h), "box width/depth/height")
    x, y, z = at
    wp = cq.Workplane("XY").box(w, d, h, centered=(True, True, not base_z0))
    return wp.translate((x, y, z))


def _cyl(dia: float, h: float, at, base_z0: bool = True) -> cq.Workplane:
    r = _require(dia / 2, "cylinder diameter") if dia else 0
    _require(h, "cylinder height")
    x, y, z = at
    solid = cq.Workplane("XY").circle(r).extrude(h)
    if not base_z0:
        solid = solid.translate((0, 0, -h / 2))
    return solid.translate((x, y, z))


def _orient(solid: cq.Workplane, axis: str) -> cq.Workplane:
    """Rotate a Z-built solid so its primary axis points along x/y/z."""
    if axis == "x":
        return solid.rotate((0, 0, 0), (0, 1, 0), 90)
    if axis == "y":
        return solid.rotate((0, 0, 0), (1, 0, 0), -90)
    return solid


def _through_tool(dia: float, at, axis: str = "z") -> cq.Workplane:
    """A cylinder long enough to cut entirely through any body, centered on `at`."""
    r = _require(dia / 2, "hole diameter")
    x, y, z = at
    tool = cq.Workplane("XY").circle(r).extrude(_BIG).translate((0, 0, -_BIG / 2))
    return _orient(tool, axis).translate((x, y, z))


def _blind_tool(dia: float, depth: float, top_z: float, at, axis: str = "z") -> cq.Workplane:
    r = _require(dia / 2, "hole diameter")
    _require(depth, "blind hole depth")
    x, y, _ = at
    tool = cq.Workplane("XY").circle(r).extrude(depth).translate((0, 0, top_z - depth))
    return _orient(tool, axis).translate((x, y, 0))


# --- additive primitive builders -------------------------------------------
def _build_box(f: Feature) -> cq.Workplane:
    return _box(f.p("width", 10, "x", "length"), f.p("depth", 10, "y", "width_y"),
               f.p("height", 10, "thickness", "z", "h"), _pos(f))


def _build_cylinder(f: Feature) -> cq.Workplane:
    dia = _dia(f, "diameter", "od", "outer_diameter", default=10)
    return _orient(_cyl(dia, f.p("height", 10, "length", "thickness"), _pos(f)), f.axis)


def _build_boss(f: Feature) -> cq.Workplane:
    dia = _dia(f, "diameter", "od", "outer_diameter", default=20)
    return _cyl(dia, f.p("height", 10, "length", "thickness"), _pos(f))


def _build_pipe(f: Feature) -> cq.Workplane:
    od = _dia(f, "od", "outer_diameter", "diameter", default=40)
    bore = _dia(f, "id", "bore", "inner_diameter")
    wall = f.p("wall", 0, "wall_thickness", "thickness")
    if bore <= 0 and wall > 0:
        bore = od - 2 * wall
    if bore <= 0:
        bore = max(0.1, od - 2 * max(2.0, od * 0.1))
    length = _require(f.p("length", 50, "height", "h"), "pipe length")
    if bore >= od:
        raise CadGenerationError("pipe bore must be smaller than outer diameter")
    x, y, z = _pos(f)
    tube = cq.Workplane("XY").circle(od / 2).circle(bore / 2).extrude(length)
    return _orient(tube, f.axis).translate((x, y, z))


def _flange_disc(od: float, thk: float, pcd: float, bolt_count: int, bolt_dia: float,
                 bore: float) -> tuple[cq.Workplane, int]:
    """A circular flange built at the origin (disc on z 0..thk, centered in XY),
    with its bolt circle + optional center bore. Caller orients/translates.
    Returns (solid, bolt_hole_count)."""
    _require(od, "flange OD")
    _require(thk, "flange thickness")
    disc = cq.Workplane("XY").circle(od / 2).extrude(thk)
    holes = 0
    if bolt_count > 0 and bolt_dia > 0 and pcd > 0:
        for k in range(bolt_count):
            ang = 2 * math.pi * k / bolt_count
            hx, hy = (pcd / 2) * math.cos(ang), (pcd / 2) * math.sin(ang)
            disc = disc.cut(_through_tool(bolt_dia, (hx, hy, 0)))
            holes += 1
    if bore > 0:
        disc = disc.cut(_through_tool(bore, (0, 0, 0)))
    return disc, holes


def _build_circular_flange(f: Feature) -> tuple[cq.Workplane, int, int]:
    od = _dia(f, "od", "outer_diameter", "flange_od", default=100)
    thk = f.p("thickness", 12, "flange_thickness", "height")
    pcd = _dia(f, "pcd", "bolt_circle_diameter", "bolt_circle")
    bolt_count = int(f.p("bolt_count", 0, "holes", "count", "bolt_holes"))
    bolt_dia = _dia(f, "bolt_diameter", "hole_diameter", "bolt_dia")
    bore = _dia(f, "bore", "center_bore", "id", "inner_diameter")
    solid, bolt_holes = _flange_disc(od, thk, pcd, bolt_count, bolt_dia, bore)
    solid = _orient(solid, f.axis).translate(_pos(f))
    # The center bore is itself a hole. Total holes = bolt holes + center bore,
    # and all of them are through, so hole_count == through_hole_count (never the
    # impossible "fewer holes than through-holes").
    total = bolt_holes + (1 if bore > 0 else 0)
    return solid, total, total


def _build_pipe_spool(f: Feature) -> tuple[cq.Workplane, int, int]:
    """Straight pipe with a circular flange on each end (NOT a tee)."""
    length = _require(f.p("length", 200, "h"), "spool length")
    pipe_od = _dia(f, "od", "pipe_od", "outer_diameter", default=80)
    bore = _dia(f, "id", "bore", "inner_diameter", default=max(1.0, pipe_od - 16))
    flange_od = _dia(f, "flange_od", "flange_outer_diameter", default=pipe_od + 40)
    flange_thk = f.p("flange_thickness", 12, "flange_thk")
    bolt_count = int(f.p("bolt_count", 8, "holes", "bolt_holes"))
    bolt_dia = _dia(f, "bolt_diameter", "hole_diameter", default=9.0)
    pcd = _dia(f, "pcd", "bolt_circle_diameter", default=max(bore + 20, flange_od - 20))
    x, y, z = _pos(f)
    if bore >= pipe_od:
        raise CadGenerationError("spool bore must be smaller than pipe OD")
    pipe = cq.Workplane("XY").circle(pipe_od / 2).circle(bore / 2).extrude(length)
    body = pipe.translate((x, y, z))
    holes = 0
    for fz in (z, z + length - flange_thk):
        flange, n = _flange_disc(flange_od, flange_thk, pcd, bolt_count, bolt_dia, bore)
        body = body.union(flange.translate((x, y, fz)))
        holes += n
    body = _orient(body, f.axis)
    return body, holes, holes


def _build_pipe_elbow(f: Feature, warnings: list[str]) -> tuple[cq.Workplane, int, int]:
    """Two straight pipe legs joined at an angle (approximate centerline bend)."""
    od = _dia(f, "od", "outer_diameter", "diameter", default=60)
    bore = _dia(f, "id", "bore", "inner_diameter", default=max(1.0, od - 10))
    leg = _require(f.p("leg_length", f.p("length", 60), "leg"), "elbow leg length")
    if bore >= od:
        raise CadGenerationError("elbow bore must be smaller than OD")
    x, y, z = _pos(f)
    vert = cq.Workplane("XY").circle(od / 2).circle(bore / 2).extrude(leg)
    horiz = (cq.Workplane("XY").circle(od / 2).circle(bore / 2).extrude(leg)
             .rotate((0, 0, 0), (0, 1, 0), 90).translate((0, 0, leg)))
    body = vert.union(horiz).translate((x, y, z))
    warnings.append("pipe_elbow approximated as two straight legs joined at the centerline")
    return body, 0, 0


def _build_rect_wall(f: Feature) -> cq.Workplane:
    return _box(f.p("width", 10, "length", "x"), f.p("depth", 6, "thickness", "y"),
                f.p("height", 30, "z", "h"), _pos(f))


def _build_rib(f: Feature) -> cq.Workplane:
    return _box(f.p("length", 20, "width", "x"), f.p("thickness", 3, "depth", "y"),
                f.p("height", 15, "z"), _pos(f))


def _build_gusset(f: Feature) -> cq.Workplane:
    length = _require(f.p("length", 20, "x"), "gusset length")
    height = _require(f.p("height", 20, "z"), "gusset height")
    thk = _require(f.p("thickness", 4, "y"), "gusset thickness")
    x, y, z = _pos(f)
    tri = (cq.Workplane("XZ").polyline([(0, 0), (length, 0), (0, height)]).close()
           .extrude(thk))
    return tri.translate((x, y - thk / 2, z))


def _build_plate(f: Feature) -> cq.Workplane:
    return _box(f.p("width", 100, "length", "x"), f.p("depth", 60, "width_y", "y"),
                f.p("thickness", 6, "height", "z"), _pos(f))


# --- subtractive feature tools (return a tool solid + counts) ---------------
def _top_z(base: cq.Workplane) -> float:
    return base.val().BoundingBox().zmax


def _hole_tool(f: Feature, base: cq.Workplane) -> tuple[cq.Workplane, int]:
    dia = _dia(f, "diameter", "dia", "d", default=0)
    _require(dia, "hole diameter")
    if f.through:
        return _through_tool(dia, _pos(f), f.axis), 1
    depth = f.p("depth", 0)
    top = _top_z(base) if f.axis == "z" else 0
    if depth <= 0:
        return _through_tool(dia, _pos(f), f.axis), 1  # treat as through if no depth
    return _blind_tool(dia, depth, top, _pos(f), f.axis), 0


def _rect_pattern(f: Feature, base: cq.Workplane) -> tuple[cq.Workplane, int, int]:
    dia = _require(_dia(f, "diameter", "hole_diameter", default=0), "pattern hole diameter")
    nx = int(f.p("nx", 2, "cols"))
    ny = int(f.p("ny", 2, "rows"))
    pat = f.p("pattern", 0, "square", "bolt_pattern")  # square side
    sx = f.p("spacing_x", pat or f.p("spacing", 20), "dx")
    sy = f.p("spacing_y", pat or f.p("spacing", 20), "dy")
    cx, cy, cz = _pos(f)
    tool = None
    count = 0
    for ix in range(nx):
        for iy in range(ny):
            px = cx + (ix - (nx - 1) / 2) * sx
            py = cy + (iy - (ny - 1) / 2) * sy
            t = _through_tool(dia, (px, py, cz))
            tool = t if tool is None else tool.union(t)
            count += 1
    return tool, count, count


def _circle_pattern(f: Feature, base: cq.Workplane) -> tuple[cq.Workplane, int, int]:
    dia = _require(_dia(f, "diameter", "hole_diameter", default=0), "pattern hole diameter")
    pcd = _require(_dia(f, "pcd", "bolt_circle_diameter", "bolt_circle"), "pattern PCD")
    count = int(f.p("count", 0, "bolt_count", "holes"))
    _require(count, "pattern count")
    cx, cy, cz = _pos(f)
    start = math.radians(f.p("start_angle", 0))
    tool = None
    for k in range(count):
        ang = start + 2 * math.pi * k / count
        px, py = cx + (pcd / 2) * math.cos(ang), cy + (pcd / 2) * math.sin(ang)
        t = _through_tool(dia, (px, py, cz))
        tool = t if tool is None else tool.union(t)
    return tool, count, count


def _slot_tool(f: Feature, base: cq.Workplane) -> cq.Workplane:
    length = _require(f.p("length", 20, "x"), "slot length")
    width = _require(f.p("width", 6, "y"), "slot width")
    x, y, z = _pos(f)
    wp = cq.Workplane("XY")
    if hasattr(wp, "slot2D"):
        tool = wp.slot2D(max(length, width), width, 0).extrude(_BIG)
    else:
        tool = wp.box(length, width, _BIG, centered=(True, True, True))
    return tool.translate((x, y, z))


def _v_groove_tool(f: Feature, base: cq.Workplane) -> cq.Workplane:
    angle = f.p("angle", 90)
    depth = _require(f.p("depth", 5, "z"), "v-groove depth")
    length = f.p("length", 0, "x")
    bb = base.val().BoundingBox()
    if length <= 0:
        length = bb.xlen + 2
    top = bb.zmax
    half = math.tan(math.radians(angle / 2)) * depth
    x, y_at, _ = _pos(f)
    # Default to a groove running down the centerline of the top face; an
    # explicit non-zero `at` y overrides it.
    y = y_at if abs(y_at) > 1e-9 else (bb.ymin + bb.ymax) / 2
    tri = (cq.Workplane("YZ")
           .polyline([(y - half, top + 0.1), (y + half, top + 0.1), (y, top - depth)])
           .close().extrude(length).translate((-length / 2 + x, 0, 0)))
    return tri


def _rect_cut_tool(f: Feature, base: cq.Workplane) -> cq.Workplane:
    w = _require(f.p("width", 10, "x"), "cut width")
    d = _require(f.p("depth_y", f.p("depth", 10), "y"), "cut depth(y)")
    x, y, z = _pos(f)
    return cq.Workplane("XY").box(w, d, _BIG, centered=(True, True, True)).translate((x, y, z))


def _csk_cbore_tools(f: Feature, base: cq.Workplane) -> tuple[cq.Workplane, int]:
    dia = _require(_dia(f, "diameter", "d", default=0), "hole diameter")
    x, y, z = _pos(f)
    top = _top_z(base)
    shaft = _through_tool(dia, (x, y, z))
    if f.kind == FeatureKind.counterbore:
        cap_d = _dia(f, "counterbore_diameter", "cap_diameter", default=dia * 1.8)
        cap_depth = f.p("counterbore_depth", max(1.0, dia * 0.5), "cap_depth")
        cap = cq.Workplane("XY").circle(cap_d / 2).extrude(cap_depth).translate((x, y, top - cap_depth))
        return shaft.union(cap), 1
    cap_d = _dia(f, "countersink_diameter", "cap_diameter", default=dia * 2)
    cap_depth = max(1.0, (cap_d - dia) / 2)
    cone = (cq.Workplane("XY").circle(dia / 2).workplane(offset=cap_depth).circle(cap_d / 2)
            .loft(combine=True).translate((x, y, top - cap_depth)))
    return shaft.union(cone), 1


# --- modifiers --------------------------------------------------------------
def _apply_fillet(f: Feature, base: cq.Workplane, warnings: list[str]) -> cq.Workplane:
    r = _require(f.p("radius", 0, "size", "r"), "fillet radius")
    sel = (f.description or "vertical").lower()
    selector = ">Z and |Z" if "top" in sel else ("|Z" if "vert" in sel else "")
    try:
        edges = base.edges(selector) if selector else base.edges()
        return edges.fillet(r)
    except Exception as exc:  # noqa: BLE001 - keep the body, warn
        warnings.append(f"fillet r={r} skipped: {exc}")
        return base


def _apply_chamfer(f: Feature, base: cq.Workplane, warnings: list[str]) -> cq.Workplane:
    c = _require(f.p("size", 0, "radius", "c"), "chamfer size")
    sel = (f.description or "vertical").lower()
    selector = "|Z" if "vert" in sel else ""
    try:
        edges = base.edges(selector) if selector else base.edges()
        return edges.chamfer(c)
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"chamfer {c} skipped: {exc}")
        return base


def _apply_shell(f: Feature, base: cq.Workplane, warnings: list[str]) -> cq.Workplane:
    thk = _require(f.p("thickness", 2, "wall", "wall_thickness"), "shell thickness")
    face = (f.description or "top").lower()
    sel = "<Z" if "bottom" in face else ">Z"
    try:
        return base.faces(sel).shell(-thk)
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"shell {thk} skipped: {exc}")
        return base


def _apply_mirror(f: Feature, base: cq.Workplane, solids: dict) -> cq.Workplane:
    src = solids.get(f.target, base)
    plane = (f.params.get("plane") or "YZ")
    plane = str(plane).upper() if isinstance(plane, str) else "YZ"
    if plane not in ("XY", "XZ", "YZ"):
        plane = "YZ"
    return base.union(src.mirror(mirrorPlane=plane))


# --- top-level compile ------------------------------------------------------
def compile_cad_plan(plan: CadPlan) -> CadPlanResult:
    if not plan.features:
        raise CadGenerationError("CadPlan has no features to build")

    base: cq.Workplane | None = None
    solids: dict[str, cq.Workplane] = {}
    warnings: list[str] = []
    feature_meta: list[dict] = []
    hole_count = 0
    through_count = 0

    for f in plan.features:
        added_solid: cq.Workplane | None = None
        cut_tool: cq.Workplane | None = None
        f_holes = f_through = 0

        if f.kind in (FeatureKind.box, FeatureKind.plate, FeatureKind.rectangular_wall,
                      FeatureKind.cylinder, FeatureKind.boss, FeatureKind.pipe,
                      FeatureKind.rib, FeatureKind.gusset, FeatureKind.pipe_elbow,
                      FeatureKind.circular_flange, FeatureKind.pipe_spool):
            if f.kind in (FeatureKind.box, FeatureKind.plate):
                added_solid = _build_box(f) if f.kind == FeatureKind.box else _build_plate(f)
            elif f.kind == FeatureKind.rectangular_wall:
                added_solid = _build_rect_wall(f)
            elif f.kind == FeatureKind.cylinder:
                added_solid = _build_cylinder(f)
            elif f.kind == FeatureKind.boss:
                added_solid = _build_boss(f)
            elif f.kind == FeatureKind.pipe:
                added_solid = _build_pipe(f)
            elif f.kind == FeatureKind.rib:
                added_solid = _build_rib(f)
            elif f.kind == FeatureKind.gusset:
                added_solid = _build_gusset(f)
            elif f.kind == FeatureKind.circular_flange:
                added_solid, f_holes, f_through = _build_circular_flange(f)
            elif f.kind == FeatureKind.pipe_spool:
                added_solid, f_holes, f_through = _build_pipe_spool(f)
            elif f.kind == FeatureKind.pipe_elbow:
                added_solid, f_holes, f_through = _build_pipe_elbow(f, warnings)
        elif f.kind == FeatureKind.hole:
            cut_tool, f_through = _hole_tool(f, _need(base, f))
            f_holes = 1
        elif f.kind == FeatureKind.hole_pattern_rect:
            cut_tool, f_holes, f_through = _rect_pattern(f, _need(base, f))
        elif f.kind == FeatureKind.hole_pattern_circle:
            cut_tool, f_holes, f_through = _circle_pattern(f, _need(base, f))
        elif f.kind == FeatureKind.slot:
            cut_tool = _slot_tool(f, _need(base, f))
        elif f.kind == FeatureKind.v_groove:
            cut_tool = _v_groove_tool(f, _need(base, f))
        elif f.kind == FeatureKind.rectangular_cut:
            cut_tool = _rect_cut_tool(f, _need(base, f))
        elif f.kind in (FeatureKind.countersink, FeatureKind.counterbore):
            cut_tool, f_holes = _csk_cbore_tools(f, _need(base, f))
            f_through = 1
        elif f.kind == FeatureKind.fillet:
            base = _apply_fillet(f, _need(base, f), warnings)
        elif f.kind == FeatureKind.chamfer:
            base = _apply_chamfer(f, _need(base, f), warnings)
        elif f.kind == FeatureKind.shell:
            base = _apply_shell(f, _need(base, f), warnings)
        elif f.kind == FeatureKind.mirror:
            base = _apply_mirror(f, _need(base, f), solids)
        elif f.kind in (FeatureKind.union, FeatureKind.subtract):
            ref = solids.get(f.target)
            if ref is None:
                raise CadGenerationError(
                    f"{f.kind.value} references unknown solid '{f.target}'"
                )
            base = _need(base, f).cut(ref) if f.kind == FeatureKind.subtract \
                else _need(base, f).union(ref)
        else:  # pragma: no cover - schema enum prevents this
            raise CadGenerationError(f"unsupported feature kind '{f.kind}'")

        # Apply add/cut to the running body.
        if added_solid is not None:
            if f.op == "cut":
                base = _need(base, f).cut(added_solid)
            else:
                base = added_solid if base is None else base.union(added_solid)
            solids[f.id] = added_solid
        if cut_tool is not None:
            base = _need(base, f).cut(cut_tool)
            solids[f.id] = cut_tool

        # Holes from hole features AND the internal bolt holes of composite
        # additive features (circular_flange / pipe_spool) both count.
        hole_count += f_holes
        through_count += f_through

        if f.kind not in MODIFIER_KINDS:
            feature_meta.append({
                "id": f.id,
                "type": f.kind.value,
                "label": f.description or f.kind.value.replace("_", " "),
                "anchor": [round(v, 3) for v in _pos(f)],
                "meta": {"op": f.op},
            })

    # Explicit operations (rarely used; per-feature ordering covers most plans).
    for op in plan.operations:
        tgt = solids.get(op.target)
        if tgt is None:
            continue
        if op.op == "mirror":
            plane = (op.plane or "YZ").upper()
            plane = plane if plane in ("XY", "XZ", "YZ") else "YZ"
            base = _need(base, None).union(tgt.mirror(mirrorPlane=plane))
        elif op.op in ("union", "subtract") and op.tool in solids:
            tool = solids[op.tool]
            base = _need(base, None)
            base = base.union(tool) if op.op == "union" else base.cut(tool)

    if base is None:
        raise CadGenerationError("CadPlan produced no solid body")

    # SINGLE-PART FUSE SAFEGUARD: a single-part plan should be ONE connected body.
    # If primitives ended up as several near-collinear sub-bodies with small gaps
    # (handle+shaft+tip, pin+head, shaft+collar), bridge them so the part fuses
    # instead of exporting a disconnected, validation-failing model. Bounded +
    # safe: only small collinear gaps are bridged; clearly-separate bodies are
    # left alone (and still fail single-body validation -> export blocked).
    try:
        n_solids = len(base.val().Solids())
    except Exception:  # noqa: BLE001 - never let the safeguard break a build
        n_solids = 1
    if n_solids > 1:
        base, fused = _fuse_disconnected(base)
        if fused:
            warnings.append(
                f"Auto-fused {n_solids} disconnected collinear sub-bodies into one "
                "solid (small gaps bridged).")
        else:
            warnings.append(
                f"Auto-fuse attempted but failed: {n_solids} disconnected sub-bodies "
                "remain (gaps too large to bridge safely).")

    bb = base.val().BoundingBox()
    bbox = {"x": round(bb.xlen, 3), "y": round(bb.ylen, 3), "z": round(bb.zlen, 3)}
    return CadPlanResult(
        solid=base, bbox_mm=bbox, hole_count=hole_count, through_hole_count=through_count,
        feature_count=len(plan.features), warnings=warnings, feature_meta=feature_meta,
    )


def _fuse_disconnected(base: cq.Workplane) -> tuple[cq.Workplane, bool]:
    """Bridge small collinear gaps between disconnected sub-bodies so a
    single-part model fuses into one solid. Returns (solid, repaired). Bounded
    and conservative: only bridges when the gap is small AND the bodies are
    nearly collinear along the dominant axis; otherwise returns the input
    unchanged so genuinely-separate bodies still fail validation."""
    try:
        solids = base.val().Solids()
        if len(solids) <= 1:
            return base, False
        bbs = [s.BoundingBox() for s in solids]
        overall = base.val().BoundingBox()
        ext = {"x": overall.xlen, "y": overall.ylen, "z": overall.zlen}
        axis = max(ext, key=ext.get)
        ai = {"x": 0, "y": 1, "z": 2}[axis]
        perp = [i for i in (0, 1, 2) if i != ai]

        def lo(bb):
            return (bb.xmin, bb.ymin, bb.zmin)[ai]

        def hi(bb):
            return (bb.xmax, bb.ymax, bb.zmax)[ai]

        def center(bb):
            return ((bb.xmin + bb.xmax) / 2, (bb.ymin + bb.ymax) / 2,
                    (bb.zmin + bb.zmax) / 2)

        def perp_size(bb):
            sizes = (bb.xlen, bb.ylen, bb.zlen)
            return min(sizes[i] for i in perp)

        order = sorted(range(len(solids)), key=lambda i: lo(bbs[i]))
        bridges: list[cq.Workplane] = []
        for a, b in zip(order, order[1:]):
            ba, bb2 = bbs[a], bbs[b]
            gap = lo(bb2) - hi(ba)
            if gap <= 0.05:
                continue  # already touching / overlapping
            r = max(1.0, 0.45 * min(perp_size(ba), perp_size(bb2)))
            # Only bridge SMALL collinear gaps; bail on clearly-separate bodies.
            if gap > max(20.0, 3.0 * r):
                return base, False
            ca, cb = center(ba), center(bb2)
            off = math.dist([ca[i] for i in perp], [cb[i] for i in perp])
            if off > r:
                return base, False  # not collinear enough
            mc = [(ca[i] + cb[i]) / 2 for i in range(3)]
            start = [mc[0], mc[1], mc[2]]
            start[ai] = hi(ba) - 0.5
            normal = [0.0, 0.0, 0.0]
            normal[ai] = 1.0
            bridge = (cq.Workplane(cq.Plane(origin=tuple(start), normal=tuple(normal)))
                      .circle(r).extrude(gap + 1.0))
            bridges.append(bridge)
        if not bridges:
            return base, False
        fused = base
        for br in bridges:
            fused = fused.union(br)
        if len(fused.val().Solids()) == 1:
            return fused, True
        return base, False
    except Exception:  # noqa: BLE001 - safeguard must never break a build
        return base, False


def _need(base, f) -> cq.Workplane:
    if base is None:
        what = f.kind.value if f is not None else "operation"
        raise CadGenerationError(f"'{what}' needs an existing body to act on")
    return base


def export_solid(solid: cq.Workplane) -> tuple[bytes, bytes, PreviewMesh]:
    """STL + STEP bytes + a preview mesh, using the shared exporter helpers."""
    return _export_bytes(solid, ".stl"), _export_bytes(solid, ".step"), _tessellate(solid)
