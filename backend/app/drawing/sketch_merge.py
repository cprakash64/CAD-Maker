"""Merge a provider (vision) interpretation into the CV-built sketch IR.

The CV geometry is authoritative for the SHAPES it actually traced — the
provider enhances it, it does not replace it:

* CV loops (outer profile, holes, slots, counterbores) always win — the
  provider cannot delete a high-confidence CV loop or invent holes the CV never
  saw (that is what produced "random holes" from hallucinated callouts);
* the provider may LABEL the part (family / title), supply an overall scale when
  the CV had none, and up-rate feature dimensions (a Ø from a callout refines a
  traced circle);
* if the provider timed out or returned nothing usable, the CV-only IR still
  generates.
"""
from __future__ import annotations

from app.drawing.sketch_ir import MechanicalSketchIR, Scale
from app.observability import log_event


def merge_provider_interpretation_with_cv_ir(
    cv_ir: MechanicalSketchIR, provider_interp) -> MechanicalSketchIR:
    """Return the CV IR enhanced with trustworthy provider signals. ``cv_ir`` is
    never geometrically overridden. Never raises."""
    try:
        return _merge(cv_ir, provider_interp)
    except Exception as exc:  # noqa: BLE001 - merge is best-effort
        log_event("drawing_sketch_merge_failed", reason=type(exc).__name__)
        return cv_ir


def _merge(cv_ir: MechanicalSketchIR, interp) -> MechanicalSketchIR:
    if interp is None or not getattr(interp, "generatable_with_assumptions", None):
        return cv_ir
    if not interp.generatable_with_assumptions():
        return cv_ir  # provider unusable (timeout/weak) — CV-only

    cv_ir.source = "hybrid"
    dims = {k.lower(): float(v) for k, v in (interp.overall_dimensions or {}).items()
            if v and float(v) > 0}

    # 1) Scale: only ADOPT a provider overall dimension when the CV had to guess.
    if cv_ir.scale and cv_ir.scale.estimated and cv_ir.outer:
        overall = _pick(dims, "width", "length", "outer_diameter", "od", "overall")
        outer = cv_ir.outer
        cur_max = max(outer.bbox_mm.get("w", 0), outer.bbox_mm.get("h", 0))
        if overall and cur_max > 0:
            factor = overall / cur_max
            _rescale_ir(cv_ir, factor)
            cv_ir.scale = Scale(px_per_mm=cv_ir.scale.px_per_mm / factor,
                                source_dimension_label=f"{overall:g} (vision)",
                                estimated=False)
            cv_ir.assumptions.append(
                f"Overall size {overall:g}mm taken from the vision reading "
                "(CV geometry preserved, only rescaled)")

    # 2) Thickness from the provider when the CV had only a default.
    thick = _pick(dims, "thickness", "thick", "depth", "height")
    if thick and cv_ir.default_thickness_mm == 6.0:
        cv_ir.default_thickness_mm = thick

    # 3) The provider may LABEL the part (never changes geometry).
    label = interp.suggested_object_type or interp.detected_object_type
    if label:
        cv_ir.assumptions.append(f"Vision labelled this a {label.replace('_', ' ')}")

    log_event("drawing_sketch_merged",
              cut_features=len(cv_ir.cut_features),
              nested_features=len(cv_ir.nested_features),
              scale_estimated=cv_ir.scale.estimated if cv_ir.scale else True)
    return cv_ir


def _pick(dims: dict, *needles: str) -> float | None:
    for k, v in dims.items():
        if any(n in k for n in needles):
            return v
    return None


def _rescale_ir(ir: MechanicalSketchIR, factor: float) -> None:
    def sv(v):
        return [round(c * factor, 3) for c in v]

    for op in ir.outer_profiles:
        op.vertices = [sv(v) for v in op.vertices]
        op.bbox_mm = {k: round(val * factor, 3) for k, val in op.bbox_mm.items()}
        if op.corner_radius_mm:
            op.corner_radius_mm = round(op.corner_radius_mm * factor, 3)
    for cf in ir.cut_features:
        cf.center = sv(cf.center)
        cf.vertices = [sv(v) for v in cf.vertices]
        for a in ("diameter_mm", "radius_mm", "width_mm", "height_mm"):
            if getattr(cf, a) is not None:
                setattr(cf, a, round(getattr(cf, a) * factor, 3))
    for nf in ir.nested_features:
        nf.center = sv(nf.center)
        nf.outer_diameter_mm = round(nf.outer_diameter_mm * factor, 3)
        nf.inner_diameter_mm = round(nf.inner_diameter_mm * factor, 3)
    for g in ir.concentric_groups:
        g.center = sv(g.center)
        g.circles_mm = [round(d * factor, 3) for d in g.circles_mm]
        g.outer_diameter_mm = round(g.outer_diameter_mm * factor, 3)
        g.counterbore_or_boss_diameter_mm = round(
            g.counterbore_or_boss_diameter_mm * factor, 3)
        g.through_hole_diameter_mm = round(g.through_hole_diameter_mm * factor, 3)
        if g.depth_mm:
            g.depth_mm = round(g.depth_mm * factor, 3)
