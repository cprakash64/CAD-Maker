"""Phase 5: semantic selectable-face metadata from a CadQuery solid.

Mesh triangles don't know CAD faces, so the backend inspects the real BRep
faces of the generated solid and emits stable, selectable face metadata (id,
optional feature id, normal, center, area, bounds, local frame, allowed
operations, confidence). The frontend prefers this over its mesh-only grouping.

Everything is in the CadQuery *model* frame (mm, Z-up) — the same frame as the
``features`` anchors — so the viewer transforms both with the same rotation.

Robustness first: any inspection failure yields ``[]`` (or drops that one face)
and never breaks generation. We never invent high confidence for a face we can't
confidently identify.
"""
from __future__ import annotations

import hashlib
import math
from typing import Optional

# Families whose faces we can only describe generically (no template feature ids).
_RECONSTRUCTED_TYPES = {"feature_graph"}

# Chip key -> allowed-operation name (kept in sync with the frontend chips).
CHIP_OPERATION = {
    "hole": "add_hole",
    "slot": "add_slot",
    "cutout": "add_cutout",
    "boss": "add_boss",
    "vent": "add_vent",
    "fillet": "fillet_edges",
    "chamfer": "chamfer_edges",
    "pattern": "pattern",
}


def _normalize(x: float, y: float, z: float) -> tuple[float, float, float]:
    n = math.sqrt(x * x + y * y + z * z)
    if n < 1e-9:
        return (0.0, 0.0, 1.0)
    return (x / n, y / n, z / n)


def _perp(n: tuple[float, float, float]) -> tuple[float, float, float]:
    ref = (1.0, 0.0, 0.0) if abs(n[0]) < 0.9 else (0.0, 1.0, 0.0)
    x = (
        n[1] * ref[2] - n[2] * ref[1],
        n[2] * ref[0] - n[0] * ref[2],
        n[0] * ref[1] - n[1] * ref[0],
    )
    return _normalize(*x)


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _geom_kind(gtype: str) -> str:
    g = (gtype or "").upper()
    if g == "PLANE":
        return "planar"
    if g == "CYLINDER":
        return "cylindrical"
    return "curved"


def _axis_identity(n: tuple[float, float, float]) -> Optional[tuple[str, str]]:
    """(feature_id, axis_label) when the normal is cleanly axis-aligned, else None."""
    ax, ay, az = abs(n[0]), abs(n[1]), abs(n[2])
    dominant = max(ax, ay, az)
    if dominant < 0.8:  # oblique — don't claim a principal face
        return None
    if az >= ax and az >= ay:
        return ("face_top", "Top face") if n[2] >= 0 else ("face_bottom", "Bottom face")
    if ax >= ay and ax >= az:
        return ("face_+X", "+X side face") if n[0] >= 0 else ("face_-X", "-X side face")
    return ("face_+Y", "+Y side face") if n[1] >= 0 else ("face_-Y", "-Y side face")


def _allowed_operations(kind: str, area: float, max_area: float, min_extent: float) -> list[str]:
    # CAPABILITY MATRIX (see the Phase 6 note): advertise only implemented ops.
    # Planar plate faces support add_hole + edge fillet/chamfer. Slot/cutout/boss/
    # vent/pattern are NOT implemented by the safe pipeline, so they're never
    # advertised as chips. A cylindrical face has no safe parametric edit of its
    # own (real holes are selected as hole entities), so it advertises nothing.
    if kind == "planar":
        large = area >= 0.12 * max_area or min_extent >= 8.0
        if large:
            return ["add_hole", "fillet_edges", "chamfer_edges"]
        ops = ["fillet_edges", "chamfer_edges"]
        if min_extent >= 5.0:  # enough room for a small hole on a side face
            ops.insert(0, "add_hole")
        return ops
    if kind == "cylindrical":
        return []
    return []


def _stable_face_id(slug: str, kind: str, n, c) -> str:
    """Deterministic id: readable slug + a hash of the rounded geometry, so the
    same spec regenerates the same ids."""
    sig = (
        f"{kind}:{round(n[0], 2)},{round(n[1], 2)},{round(n[2], 2)}:"
        f"{round(c[0], 1)},{round(c[1], 1)},{round(c[2], 1)}"
    )
    return f"{slug}_{hashlib.sha1(sig.encode()).hexdigest()[:8]}"


def extract_selectable_faces(
    solid, spec, max_faces: int = 48
) -> list[dict]:
    """Inspect the solid's BRep faces and return selectable-face metadata.

    Returns ``[]`` on any failure — this is advisory metadata and must never
    break geometry generation or export."""
    try:
        faces = solid.val().Faces()
    except Exception:  # noqa: BLE001 - never let metadata break generation
        return []
    if not faces:
        return []

    reconstructed = getattr(spec, "object_type", None) in _RECONSTRUCTED_TYPES
    family = (getattr(spec, "object_type", "") or "part").replace("_", " ")

    raw: list[dict] = []
    for f in faces:
        try:
            area = float(f.Area())
            if area <= 1e-6:
                continue
            c = f.Center()
            center = (round(c.x, 3), round(c.y, 3), round(c.z, 3))
            nrm = f.normalAt()
            normal = _normalize(nrm.x, nrm.y, nrm.z)
            kind = _geom_kind(f.geomType())
            bb = f.BoundingBox()
            extents = sorted([bb.xlen, bb.ylen, bb.zlen], reverse=True)
        except Exception:  # noqa: BLE001 - skip any face we can't read
            continue
        raw.append(
            {"area": area, "center": center, "normal": normal, "kind": kind, "extents": extents}
        )

    if not raw:
        return []

    # Keep the most significant faces (largest area) to bound the payload.
    raw.sort(key=lambda r: r["area"], reverse=True)
    raw = raw[:max_faces]
    max_area = raw[0]["area"]

    out: list[dict] = []
    for r in raw:
        kind, normal, center = r["kind"], r["normal"], r["center"]
        w, h, d = r["extents"]  # width >= height >= depth
        min_extent = h  # smaller in-plane extent (depth ~ 0 for a planar face)

        identity = _axis_identity(normal) if kind == "planar" else None
        if identity is not None:
            feature_id, axis_label = identity
            label = f"{axis_label} of {family}"
            confidence = 0.95
            slug = feature_id
        elif kind == "planar":
            feature_id = "reconstructed_body" if reconstructed else None
            label = f"Planar face of {family}"
            confidence = 0.7
            slug = "planar"
        elif kind == "cylindrical":
            feature_id = "reconstructed_body" if reconstructed else None
            label = f"Cylindrical face of {family}"
            confidence = 0.6
            slug = "cylindrical"
        else:
            feature_id = "reconstructed_body" if reconstructed else None
            label = f"Curved face of {family}"
            confidence = 0.4
            slug = "curved"

        if reconstructed:
            confidence = round(confidence * 0.8, 2)  # never over-trust a reconstruction

        z_axis = normal
        x_axis = _perp(z_axis)
        y_axis = _normalize(*_cross(z_axis, x_axis))

        out.append(
            {
                "face_id": _stable_face_id(slug, kind, normal, center),
                "feature_id": feature_id,
                "body_id": "main_body",
                "face_kind": kind,
                "label": label,
                "normal": list(normal),
                "center": list(center),
                "area_mm2": round(r["area"], 2),
                "bounds_mm": {"width": round(w, 2), "height": round(h, 2), "depth": round(d, 2)},
                "local_frame": {
                    "origin": list(center),
                    "x_axis": list(x_axis),
                    "y_axis": list(y_axis),
                    "z_axis": list(z_axis),
                },
                "allowed_operations": _allowed_operations(kind, r["area"], max_area, min_extent),
                "confidence": confidence,
            }
        )
    return out


# --- Phase 6: edges / holes / bodies / features ----------------------------
#
# CAPABILITY MATRIX: advertise ONLY operations that are actually implemented
# end-to-end by the safe parametric pipeline, so every enabled chip the UI shows
# from `allowed_operations` really works. Unimplemented ops (move_hole,
# pattern_hole, slot/cutout/boss, body/feature actions) are deliberately omitted
# — they are not shown as chips until they're built.
_HOLE_OPS = ["resize_hole", "delete_hole"]
_EDGE_OPS = ["fillet_edge", "chamfer_edge"]
_BODY_OPS: list[str] = []
_FEATURE_OPS: list[str] = []


def _plate_top_z(spec) -> float:
    for key in ("thickness", "wall_thickness"):
        if key in getattr(spec, "dimensions", {}):
            try:
                return spec.to_mm(spec.dimensions[key]) / 2.0
            except Exception:  # noqa: BLE001
                return 0.0
    return 0.0


def _stable_hole_id(dia: float, cx: float, cy: float, cz: float, hole_type) -> str:
    """Deterministic id: hash of rounded diameter + center + type, so edits that
    add/remove/reorder OTHER holes don't shift this hole's identity (mirrors
    ``_stable_face_id``). Genuinely identical holes (same diameter and center)
    collide on this base id; callers disambiguate with a trailing ``_<n>``
    suffix in declaration order, same as before for that rare case only."""
    sig = f"hole:{round(dia, 3)}:{round(cx, 2)},{round(cy, 2)},{round(cz, 2)}:{hole_type}"
    return f"hole_{hashlib.sha1(sig.encode()).hexdigest()[:8]}"


def _dedupe_hole_id(base_id: str, seen: dict[str, int]) -> str:
    n = seen.get(base_id, 0)
    seen[base_id] = n + 1
    return base_id if n == 0 else f"{base_id}_{n}"


def extract_selectable_holes(spec) -> list[dict]:
    """Selectable holes straight from the validated spec (deterministic, in mm).

    Holes are parametric features the edit pipeline already understands. Ids are
    content-hashed from diameter/position/type (see ``_stable_hole_id``) so they
    stay stable across edits that add, remove, or reorder other holes."""
    out: list[dict] = []
    try:
        holes = list(getattr(spec, "holes", []) or [])
    except Exception:  # noqa: BLE001
        return []
    top_z = _plate_top_z(spec)
    seen: dict[str, int] = {}
    for i, h in enumerate(holes):
        try:
            dia = spec.to_mm(h.diameter)
            cx, cy = spec.to_mm(h.x), spec.to_mm(h.y)
            hole_type = getattr(h.hole_type, "value", h.hole_type)
        except Exception:  # noqa: BLE001
            continue
        base_id = _stable_hole_id(dia, cx, cy, top_z, hole_type)
        out.append(
            {
                "hole_id": _dedupe_hole_id(base_id, seen),
                # True index into spec.holes -- edit handlers resolve a hole_id
                # back to this to mutate the right list entry, since the id
                # itself no longer encodes position (see app.editing.face_edit).
                "hole_index": i,
                "feature_id": "holes",
                "diameter_mm": round(dia, 3),
                "center": [round(cx, 3), round(cy, 3), round(top_z, 3)],
                "axis": [0.0, 0.0, 1.0],
                "through": True,
                "hole_type": str(hole_type),
                "allowed_operations": list(_HOLE_OPS),
                "confidence": 0.9,
            }
        )
    return out


def _plan_top_z(plan) -> float:
    """Top-face Z (mm) of a plan's flat plate base — where a Z-axis through hole
    opens. A plate/box is built base-at-Z0 spanning 0..thickness, then translated,
    so the top face sits at ``base.at.z + thickness``. Falls back to 0."""
    for f in getattr(plan, "features", []) or []:
        kind = getattr(f.kind, "value", f.kind)
        if kind in ("plate", "box", "extruded_profile") and not f.is_subtractive:
            try:
                thk = f.p("thickness", 0.0, "height", "z", "depth", "h")
                return float(f.at[2]) + float(thk)
            except Exception:  # noqa: BLE001
                return 0.0
    return 0.0


def extract_selectable_holes_from_plan(plan) -> list[dict]:
    """Selectable holes from a CadPlan feature graph (CadPlan-built parts have no
    DesignSpec, so :func:`extract_selectable_holes` can't read them).

    Ids are content-hashed from diameter/position/type (see ``_stable_hole_id``),
    same as :func:`extract_selectable_holes`, so they stay stable across edits
    that add, remove, or reorder other holes. Each entry also carries
    ``feature_index``, the declaration-order index into ALL of the plan's
    hole-kind features (matching ``app.editing.face_edit._plan_hole_features``
    exactly, including entries this function itself skips as degenerate), which
    is what edit handlers use to map a resize/delete back to the right feature.
    Advisory; returns ``[]`` on any failure and never breaks generation."""
    out: list[dict] = []
    try:
        features = list(getattr(plan, "features", []) or [])
    except Exception:  # noqa: BLE001
        return []
    top_z = _plan_top_z(plan)
    seen: dict[str, int] = {}
    i = 0
    for f in features:
        kind = getattr(f.kind, "value", f.kind)
        if kind != "hole":
            continue
        try:
            dia = float(f.p("diameter", 0.0, "dia", "d"))
            at = [float(x) for x in (f.at or [0.0, 0.0, 0.0])]
            axis = getattr(f, "axis", "z")
            through = bool(getattr(f, "through", True))
        except Exception:  # noqa: BLE001
            i += 1  # still consume an index so ordering stays aligned
            continue
        if dia <= 0:
            i += 1
            continue
        axis_vec = {"x": [1.0, 0.0, 0.0], "y": [0.0, 1.0, 0.0]}.get(axis, [0.0, 0.0, 1.0])
        # A Z-axis hole opens on the plate's top face; a side hole keeps its own z.
        cz = top_z if axis == "z" else at[2]
        hole_type = "through" if through else "blind"
        base_id = _stable_hole_id(dia, at[0], at[1], cz, hole_type)
        out.append(
            {
                "hole_id": _dedupe_hole_id(base_id, seen),
                "feature_index": i,
                "feature_id": "holes",
                "diameter_mm": round(dia, 3),
                "center": [round(at[0], 3), round(at[1], 3), round(cz, 3)],
                "axis": axis_vec,
                "through": through,
                "hole_type": hole_type,
                "allowed_operations": list(_HOLE_OPS),
                "confidence": 0.9,
            }
        )
        i += 1
    return out


def extract_selectable_edges(solid, max_edges: int = 24) -> list[dict]:
    """Selectable edges from the solid (longest first). Advisory; ``[]`` on error."""
    try:
        edges = solid.val().Edges()
    except Exception:  # noqa: BLE001
        return []
    raw: list[dict] = []
    for e in edges:
        try:
            length = float(e.Length())
            if length < 0.5:  # ignore tiny stitching edges
                continue
            gt = (e.geomType() or "").upper()
            sp, ep = e.startPoint(), e.endPoint()
            start = (round(sp.x, 3), round(sp.y, 3), round(sp.z, 3))
            end = (round(ep.x, 3), round(ep.y, 3), round(ep.z, 3))
        except Exception:  # noqa: BLE001
            continue
        kind = "linear" if gt == "LINE" else ("circular" if gt == "CIRCLE" else "curved")
        raw.append({"length": length, "kind": kind, "start": start, "end": end})

    raw.sort(key=lambda r: r["length"], reverse=True)
    out: list[dict] = []
    for r in raw[:max_edges]:
        sig = f"{r['kind']}:{r['start']}:{r['end']}"
        out.append(
            {
                "edge_id": f"edge_{hashlib.sha1(sig.encode()).hexdigest()[:8]}",
                "feature_id": None,
                "edge_kind": r["kind"],
                "start": list(r["start"]),
                "end": list(r["end"]),
                "length_mm": round(r["length"], 2),
                "allowed_operations": list(_EDGE_OPS),
                "confidence": 0.75 if r["kind"] == "linear" else 0.6,
            }
        )
    return out


def extract_selectable_bodies(solid, spec, bbox: dict | None) -> list[dict]:
    """Selectable solid bodies (one entry per solid; usually a single body)."""
    family = (getattr(spec, "object_type", "") or "part").replace("_", " ")
    material = getattr(spec, "material", None)
    try:
        solids = solid.val().Solids()
    except Exception:  # noqa: BLE001
        solids = []
    out: list[dict] = []
    multi = len(solids) > 1
    for i, s in enumerate(solids or [None]):
        vol = 0.0
        try:
            vol = round(float(s.Volume()), 2) if s is not None else 0.0
        except Exception:  # noqa: BLE001
            vol = 0.0
        body_id = f"body_{i}" if multi else "main_body"
        out.append(
            {
                "body_id": body_id,
                "feature_id": None,
                "label": f"{family} body" if not multi else f"{family} body {i + 1}",
                "volume_mm3": vol,
                "bounds_mm": bbox,
                "material": material,
                "allowed_operations": list(_BODY_OPS),
                "confidence": 0.9,
            }
        )
    return out


def extract_selectable_features(spec, bbox: dict | None) -> list[dict]:
    """Recognized template features (holes, flanges, bosses, bolt patterns, …)
    with parameter-edit operations. Reuses the existing deterministic feature
    metadata; faces/edges/bodies are excluded (they have their own lists)."""
    try:
        from app.cad.features import extract_features

        feats = extract_features(spec, bbox)
    except Exception:  # noqa: BLE001
        return []
    out: list[dict] = []
    for f in feats:
        if f.type in ("face", "body", "edge"):
            continue
        out.append(
            {
                "feature_id": f.id,
                "feature_type": f.type,
                "label": f.label,
                "center": list(f.anchor),
                "allowed_operations": list(_FEATURE_OPS),
                "confidence": 0.8,
            }
        )
    return out
