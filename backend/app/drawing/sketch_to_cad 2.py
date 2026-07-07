"""Compile a MechanicalSketchIR into a deterministic CadPlan.

The IR carries the reconstructed topology (outer profile + classified cut /
nested / additive features in mm); this turns it into the trusted feature-graph
plan the CadQuery compiler builds:

* outer profile → an ``extruded_profile`` (the traced outline, rounded corners
  and all) extruded to thickness;
* circular hole → a clean round ``hole`` cut;
* rectangular / rounded / arc / polygon cut → a ``polygon_cut`` of the EXACT
  traced outline (an arc slot keeps its curve, a hex stays a hexagon) — only if
  the trace is degenerate does it fall back to a rectangle or circle;
* nested group → a ``counterbore`` (outer recess + inner through bore) — one
  feature per bolt, never two unrelated holes.

The plan's object_type is neutral (``reconstructed_sketch_part``) so it is built
and validated generically — no pipe/rim/plate family audit hijacks a plain
reconstructed sketch.
"""
from __future__ import annotations

from app.cad.plan.schema import CadPlan, Expected, Feature
from app.drawing.sketch_ir import MechanicalSketchIR
from app.observability import log_event

_MIN_FEATURE_MM = 0.8


def resolve_recess_depth(thickness_mm: float, explicit_depth_mm: float | None = None,
                         feature_context: str | None = None) -> float:
    """Depth of a BLIND recess/channel (mm). A dark internal region is a shallow
    depressed FACE, never a full-thickness cut:

    * an explicit drawing depth callout wins (clamped below the thickness so a
      floor always remains);
    * otherwise the default is ``min(2.0, max(1.0, thickness*0.30))`` — ≈1.8mm on
      a 6mm plate — and never deeper than half the thickness (>=50% floor).
    """
    if explicit_depth_mm and explicit_depth_mm > 0:
        return round(min(explicit_depth_mm, max(0.4, thickness_mm - 0.8)), 3)
    default = min(2.0, max(1.0, thickness_mm * 0.30))
    # Never exceed half the thickness — the recess must leave >=50% material.
    return round(min(default, thickness_mm * 0.5), 3)


def generate_cad_from_sketch_ir(ir: MechanicalSketchIR,
                                default_thickness_mm: float = 6.0) -> CadPlan | None:
    """IR → CadPlan, or None when the IR has no usable outer profile."""
    outer = ir.outer
    if outer is None or len(outer.vertices) < 3:
        return None
    thickness = ir.default_thickness_mm or default_thickness_mm

    features: list[Feature] = [Feature(
        id="outer_profile", kind="extruded_profile",
        description=f"{outer.kind.replace('_', ' ')} outer profile from the drawing",
        params={"thickness": thickness}, profile=list(outer.vertices))]

    through = 0
    for cf in ir.cut_features:
        feat = _cut_feature(cf, thickness)
        if feat is None:
            continue
        features.append(feat)
        if cf.through and cf.kind not in (
                "recessed_channel", "blind_recess"):
            through += 1

    # CONCENTRIC GROUPS: one group → ONE counterbore feature (through-hole +
    # shallow ring/outer-ring recesses), never independent holes per circle.
    groups = ir.concentric_groups or [_group_from_nested(nf) for nf in ir.nested_features]
    counterbore_or_ring = 0
    for g in groups:
        feat = _concentric_group_feature(g, thickness)
        if feat is None:
            continue
        features.append(feat)
        through += 1
        if g.counterbore_or_boss_diameter_mm > g.through_hole_diameter_mm:
            counterbore_or_ring += 1

    bbox = outer.bbox_mm or {}
    plan = CadPlan(
        object_type="reconstructed_sketch_part",
        name="Reconstructed 2D sketch (from drawing)",
        assumptions=list(ir.assumptions) + list(ir.warnings),
        features=features,
        expected=Expected(
            bbox_mm={"x": round(bbox.get("w", 0.0), 2),
                     "y": round(bbox.get("h", 0.0), 2), "z": thickness},
            hole_count=through, through_hole_count=through),
    )
    log_event("drawing_sketch_cad_built", features=len(features),
              through_holes=through, object_type=plan.object_type,
              concentric_groups_generated=len(groups),
              counterbore_or_ring_features=counterbore_or_ring)
    return plan


def _concentric_group_feature(g, thickness: float) -> Feature | None:
    """One concentric group → one counterbore Feature (through-hole + concentric
    ring/outer-ring recesses). The compiler's ``generate_concentric_hole_group``
    turns this into a clean stepped recess — the outer/ring circles are NOT cut
    through."""
    through = g.through_hole_diameter_mm
    if through < _MIN_FEATURE_MM:
        through = max(_MIN_FEATURE_MM, (g.counterbore_or_boss_diameter_mm or 0) * 0.45)
    ring = max(through + 0.5, g.counterbore_or_boss_diameter_mm or 0)
    outer = g.outer_diameter_mm or 0
    # Deterministic recess depth (Part C): min(plate*0.35, 1.5mm), scaled down on
    # tiny plates so the recess never approaches a through-cut.
    depth = g.depth_mm or round(max(0.2, min(thickness * 0.35, 1.5)), 3)
    params = {"diameter": round(through, 3), "counterbore_diameter": round(ring, 3),
              "counterbore_depth": depth}
    # A 3rd (outer) ring only when it is meaningfully wider than the counterbore.
    if outer > ring + 0.3:
        params["outer_ring_diameter"] = round(outer, 3)
        params["outer_ring_depth"] = round(depth * 0.5, 3)
    g.generated_metadata = {**(g.generated_metadata or {}), "generated": True,
                            "generated_as": "stepped_counterbore"
                            if "outer_ring_diameter" in params else "counterbore",
                            "rings": len(g.circles_mm)}
    return Feature(
        id=g.id, kind="counterbore", op="cut",
        description=(f"Ø{outer or ring:g}mm concentric group: Ø{ring:g}mm "
                     f"counterbore over a Ø{through:g}mm through hole"),
        params=params, at=[g.center[0], g.center[1], 0.0])


def _group_from_nested(nf):
    """Adapt a legacy NestedFeature into a concentric-group view (safety path for
    IRs that carry nested_features but no concentric_groups)."""
    from app.drawing.sketch_ir import ConcentricHoleGroup

    return ConcentricHoleGroup(
        id=nf.id, center=list(nf.center),
        circles_mm=[nf.outer_diameter_mm, nf.inner_diameter_mm],
        outer_diameter_mm=nf.outer_diameter_mm,
        counterbore_or_boss_diameter_mm=nf.outer_diameter_mm,
        through_hole_diameter_mm=nf.inner_diameter_mm,
        group_kind="counterbore_with_through_hole", confidence=nf.confidence)


def _cut_feature(cf, thickness: float = 6.0) -> Feature | None:
    if cf.kind == "circular_hole":
        d = cf.diameter_mm or (cf.radius_mm * 2 if cf.radius_mm else 0.0)
        if d < _MIN_FEATURE_MM:
            return None
        return Feature(id=cf.id, kind="hole", op="cut",
                       description=f"Ø{d:g}mm through hole",
                       params={"diameter": round(d, 3)},
                       at=[cf.center[0], cf.center[1], 0.0], through=True)
    # A recessed channel / blind recess is a SHALLOW depressed surface with a
    # FLOOR — cut the traced profile only partway from the top face, keeping the
    # body solid below. It is a through cut ONLY when the drawing explicitly says
    # so (``through_channel_cut``); an internal dark region is blind by default.
    if cf.kind in ("recessed_channel", "blind_recess", "through_channel_cut") \
            and len(cf.vertices) >= 3:
        through = cf.kind == "through_channel_cut" and bool(cf.through)
        if through:
            log_event("drawing_recess_cut_applied", kind=cf.kind, through=True)
            return Feature(id=cf.id, kind="polygon_cut", op="cut",
                           description=f"{cf.kind.replace('_', ' ')} (through) from the drawing",
                           profile=list(cf.vertices), through=True)
        depth = resolve_recess_depth(thickness)
        log_event("drawing_blind_recess_applied", kind=cf.kind, depth_mm=depth,
                  thickness_mm=thickness, leaves_floor=True)
        return Feature(id=cf.id, kind="polygon_cut", op="cut",
                       description=(f"{cf.kind.replace('_', ' ')} — {depth:g}mm blind "
                                    f"recess (floor below) from the drawing"),
                       profile=list(cf.vertices), through=False,
                       params={"top_z": thickness, "recess_depth": depth})
    # All non-circular cuts keep their exact traced outline.
    if len(cf.vertices) >= 3:
        log_event("drawing_slot_cut_applied", kind=cf.kind, verts=len(cf.vertices))
        return Feature(id=cf.id, kind="polygon_cut", op="cut",
                       description=f"{cf.kind.replace('_', ' ')} from the drawing",
                       profile=list(cf.vertices), through=True)
    # Degenerate trace → rectangle from width/height, else drop.
    if cf.width_mm and cf.height_mm and min(cf.width_mm, cf.height_mm) >= _MIN_FEATURE_MM:
        return Feature(id=cf.id, kind="rectangular_cut", op="cut",
                       description=f"{cf.kind.replace('_', ' ')} from the drawing",
                       params={"width": cf.width_mm, "depth_y": cf.height_mm},
                       at=[cf.center[0], cf.center[1], 0.0])
    return None
