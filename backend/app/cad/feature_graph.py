"""Trusted feature-graph interpreter.

Builds a CadQuery solid from a validated ``CADFeatureGraph`` by dispatching on a
FIXED whitelist of operation names. There is NO eval/exec and no arbitrary
equations — every op reads only numeric parameters. Unknown ops are rejected.
"""
from __future__ import annotations

import math

import cadquery as cq

from app.cad.base import CadGenerationError
from app.schemas.complex_cad import CADFeatureGraph

_ALLOWED = {
    "box", "cylinder", "tube", "hex_prism", "polygon_prism", "cone", "sphere",
    "extrude_profile", "revolve_profile", "cut_hole", "counterbore", "countersink",
    "slot", "stepped_slot", "rectangular_cutout",
    "circular_pattern", "linear_pattern", "boolean_union", "boolean_cut",
    "union", "subtract", "fillet", "chamfer", "translate", "rotate", "mirror",
}
_PRIMITIVE_OPS = {
    "box", "cylinder", "tube", "hex_prism", "polygon_prism", "cone", "sphere",
    "extrude_profile", "revolve_profile",
}
# Friendly aliases.
_OP_ALIASES = {"union": "boolean_union", "subtract": "boolean_cut"}

_MAX_DIM = 5000.0


def _p(params: dict, key: str, default: float = 0.0) -> float:
    v = float(params.get(key, default))
    if not math.isfinite(v) or abs(v) > _MAX_DIM:
        raise CadGenerationError(f"parameter '{key}'={v} is out of range")
    return v


def _primitive(op: str, params: dict, at) -> cq.Workplane:
    x, y, z = at
    if op == "box":
        w, d, h = _p(params, "width", 10), _p(params, "depth", 10), _p(params, "height", 10)
        if min(w, d, h) <= 0:
            raise CadGenerationError("box dimensions must be positive")
        return cq.Workplane("XY").box(w, d, h).translate((x, y, z))
    if op == "cylinder":
        r, h = _p(params, "radius", 5), _p(params, "height", 10)
        if r <= 0 or h <= 0:
            raise CadGenerationError("cylinder radius/height must be positive")
        return cq.Workplane("XY").circle(r).extrude(h).translate((x, y, z))
    if op == "tube":
        ro = _p(params, "radius", 10)
        ri = _p(params, "inner_radius", max(0.1, ro - 2))
        h = _p(params, "height", 10)
        if ro <= 0 or h <= 0 or ri >= ro:
            raise CadGenerationError("tube needs 0 < inner_radius < radius and height > 0")
        return cq.Workplane("XY").circle(ro).circle(ri).extrude(h).translate((x, y, z))
    if op in ("hex_prism", "polygon_prism"):
        sides = 6 if op == "hex_prism" else int(_p(params, "sides", 6))
        if not (3 <= sides <= 64):
            raise CadGenerationError("polygon sides must be 3..64")
        # circumscribed-circle diameter (across corners)
        dia = _p(params, "diameter", 0) or (2 * _p(params, "radius", 0)) or (
            _p(params, "across_flats", 10) / math.cos(math.pi / sides)
        )
        h = _p(params, "height", 10)
        if dia <= 0 or h <= 0:
            raise CadGenerationError("prism diameter/height must be positive")
        return cq.Workplane("XY").polygon(sides, dia).extrude(h).translate((x, y, z))
    if op == "cone":
        r1, r2, h = _p(params, "radius1", 5), _p(params, "radius2", 2), _p(params, "height", 10)
        if r1 <= 0 or h <= 0:
            raise CadGenerationError("cone radius1/height must be positive")
        solid = cq.Solid.makeCone(r1, max(0.0, r2), h)
        return cq.Workplane("XY").add(solid).translate((x, y, z))
    if op == "sphere":
        r = _p(params, "radius", 5)
        if r <= 0:
            raise CadGenerationError("sphere radius must be positive")
        return cq.Workplane("XY").sphere(r).translate((x, y, z))
    if op == "extrude_profile":
        pts = params.get("points")
        h = _p(params, "height", 5)
        if not isinstance(pts, list) or len(pts) < 3:
            raise CadGenerationError("extrude_profile needs >=3 (x,y) points")
        poly = [(float(p[0]), float(p[1])) for p in pts]
        return cq.Workplane("XY").polyline(poly).close().extrude(h).translate((x, y, z))
    if op == "revolve_profile":
        pts = params.get("points")
        angle = _p(params, "angle", 360)
        if not isinstance(pts, list) or len(pts) < 3:
            raise CadGenerationError("revolve_profile needs >=3 (x,y) points")
        poly = [(float(p[0]), float(p[1])) for p in pts]
        return cq.Workplane("XY").polyline(poly).close().revolve(angle).translate((x, y, z))
    raise CadGenerationError(f"unknown primitive op '{op}'")


def build_feature_graph(graph: CADFeatureGraph) -> cq.Workplane:
    if not graph.is_nonempty():
        raise CadGenerationError("feature graph has no operations")
    solids: dict[str, cq.Workplane] = {}
    last_id: str | None = None

    for raw in graph.operations:
        op = _OP_ALIASES.get(raw.get("op"), raw.get("op"))
        oid = raw.get("id")
        if op not in _ALLOWED:
            raise CadGenerationError(f"operation '{op}' is not allowed")
        if not oid:
            raise CadGenerationError("every operation needs an id")
        params = raw.get("params", {}) or {}

        if op in _PRIMITIVE_OPS:
            solids[oid] = _primitive(op, params, tuple(raw.get("at", (0, 0, 0))))
        elif op in ("boolean_union", "boolean_cut"):
            target, tool = solids.get(raw.get("target")), solids.get(raw.get("tool"))
            if target is None or tool is None:
                raise CadGenerationError(f"{op} references unknown solid id")
            solids[oid] = target.union(tool) if op == "boolean_union" else target.cut(tool)
        elif op == "cut_hole":
            target = solids.get(raw.get("target"))
            if target is None:
                raise CadGenerationError("cut_hole references unknown solid id")
            r = _p(params, "radius", 2)
            depth = _p(params, "depth", 50)
            at = tuple(raw.get("at", (0, 0, 0)))
            tool = cq.Workplane("XY").circle(r).extrude(depth).translate(at)
            solids[oid] = target.cut(tool)
        elif op == "rectangular_cutout":
            target = solids.get(raw.get("target"))
            if target is None:
                raise CadGenerationError("rectangular_cutout references unknown solid id")
            w, d, h = _p(params, "width", 5), _p(params, "depth", 5), _p(params, "height", 50)
            at = tuple(raw.get("at", (0, 0, 0)))
            tool = cq.Workplane("XY").box(w, d, h, centered=(True, True, True)).translate(at)
            solids[oid] = target.cut(tool)
        elif op in ("counterbore", "countersink"):
            target = solids.get(raw.get("target"))
            if target is None:
                raise CadGenerationError(f"{op} references unknown target")
            r = _p(params, "radius", 2)
            depth = _p(params, "depth", 50)
            x, y, z = tuple(raw.get("at", (0, 0, 0)))
            tool = cq.Workplane("XY").circle(r).extrude(depth).translate((x, y, z))
            cap_r = _p(params, "cap_radius", r * 2)
            cap_d = _p(params, "cap_depth", max(1.0, depth * 0.4))
            if op == "counterbore":
                cap = cq.Workplane("XY").circle(cap_r).extrude(cap_d).translate((x, y, z + depth - cap_d))
            else:  # countersink cone
                cap = (cq.Workplane("XY").circle(r).workplane(offset=cap_d).circle(cap_r)
                       .loft(combine=True).translate((x, y, z + depth - cap_d)))
            solids[oid] = target.cut(tool).cut(cap)
        elif op in ("slot", "stepped_slot"):
            target = solids.get(raw.get("target"))
            if target is None:
                raise CadGenerationError(f"{op} references unknown target")
            length = _p(params, "length", 20)
            width = _p(params, "width", 6)
            depth = _p(params, "depth", 50)
            x, y, z = tuple(raw.get("at", (0, 0, 0)))
            slot = (cq.Workplane("XY").slot2D(max(width, length), width, 0)
                    .extrude(depth).translate((x, y, z))) if hasattr(
                        cq.Workplane("XY"), "slot2D") else (
                    cq.Workplane("XY").box(length, width, depth, centered=(True, True, False)).translate((x, y, z)))
            result = target.cut(slot)
            if op == "stepped_slot":
                step_w = _p(params, "step_width", width * 1.8)
                step_d = _p(params, "step_depth", max(1.0, depth * 0.4))
                step = cq.Workplane("XY").box(length, step_w, step_d, centered=(True, True, False)).translate(
                    (x, y, z + depth - step_d))
                result = result.cut(step)
            solids[oid] = result
        elif op in ("translate", "rotate", "mirror"):
            src = solids.get(raw.get("source") or raw.get("target"))
            if src is None:
                raise CadGenerationError(f"{op} references unknown source")
            if op == "translate":
                solids[oid] = src.translate(
                    (_p(params, "dx", 0), _p(params, "dy", 0), _p(params, "dz", 0))
                )
            elif op == "rotate":
                axis = str(raw.get("axis", "z")).lower()
                vec = {"x": (1, 0, 0), "y": (0, 1, 0), "z": (0, 0, 1)}.get(axis, (0, 0, 1))
                solids[oid] = src.rotate((0, 0, 0), vec, _p(params, "angle", 90))
            else:  # mirror
                plane = str(raw.get("plane", "XY")).upper()
                if plane not in ("XY", "XZ", "YZ"):
                    raise CadGenerationError("mirror plane must be XY/XZ/YZ")
                solids[oid] = src.mirror(mirrorPlane=plane)
        elif op in ("circular_pattern", "linear_pattern"):
            src = solids.get(raw.get("source"))
            if src is None:
                raise CadGenerationError(f"{op} references unknown source")
            count = int(raw.get("count", 1))
            if not (1 <= count <= 200):
                raise CadGenerationError("pattern count out of range")
            result = src
            if op == "circular_pattern":
                radius = _p(params, "radius", 0)
                for k in range(1, count):
                    ang = 360.0 * k / count
                    inst = src.translate((radius, 0, 0)).rotate((0, 0, 0), (0, 0, 1), ang)
                    result = result.union(inst)
            else:
                sx, sy, sz = _p(params, "dx", 0), _p(params, "dy", 0), _p(params, "dz", 0)
                for k in range(1, count):
                    result = result.union(src.translate((sx * k, sy * k, sz * k)))
            solids[oid] = result
        elif op in ("fillet", "chamfer"):
            target = solids.get(raw.get("target"))
            if target is None:
                raise CadGenerationError(f"{op} references unknown target")
            size = _p({"s": raw.get("size", 1)}, "s", 1)
            try:
                edges = target.edges()
                solids[oid] = edges.fillet(size) if op == "fillet" else edges.chamfer(size)
            except Exception:  # noqa: BLE001 - best-effort; keep unfilleted
                solids[oid] = target
        last_id = oid

    result_id = graph.result_id or last_id
    result = solids.get(result_id)
    if result is None:
        raise CadGenerationError("feature graph produced no result solid")
    return result
