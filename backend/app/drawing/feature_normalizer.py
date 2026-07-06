"""Deterministic post-processing normalizer for reconstructed drawings.

The vectorizer (``build_sketch_ir``) traces geometry from PIXELS: its circle
diameters are only as accurate as the pixel scale. A mechanical drawing, though,
prints its dimensions as text (``Ø8.2``, ``Ø4.8``, ``Ø3.6`` for a stepped
concentric feature). Those printed CALLOUTS are the source of truth — this
module snaps the pixel-measured geometry onto the exact callout numbers so a
Ø3.6 through-hole is modelled at 3.6mm, never 3.9mm because the scale drifted.

It runs AFTER geometry reconstruction and BEFORE CAD generation, and it is a
pure transform over the ``MechanicalSketchIR`` / ``CadPlan`` (no image, no CAD
kernel). Responsibilities (spec Part 6):

* snap every concentric-group circle to the exact Ø callout closest to it,
  preserving DESC order and re-deriving the through/counterbore/outer roles;
* snap plain circular-hole diameters to the nearest Ø callout;
* apply a printed fillet radius (``R1.6``) to the outer profile when detected;
* drop tiny noise contours;
* merge duplicate circular holes at the same centre;
* reject un-backed rectangular/polygon cutouts on a cylindrical pipe wall (the
  "accidental notch on the side cylinder" failure) — a cut on a round pipe is
  only kept when a dimension callout backs it.

Everything is best-effort: any failure returns the IR/plan untouched.
"""
from __future__ import annotations

from app.observability import log_event

# A pixel-measured circle snaps to a callout only when the callout is within
# this relative distance (30%); a bigger gap means they are unrelated circles.
_SNAP_REL_TOL = 0.30
# Two circular holes at (nearly) the same centre and diameter are duplicates.
_DUP_CENTER_MM = 0.6
_DUP_DIA_REL = 0.12


def snap_ir_to_callouts(ir, diameter_callouts=None, radii_callouts=None):
    """Return ``ir`` with its circle geometry snapped to the exact printed
    callouts. ``diameter_callouts`` / ``radii_callouts`` are the Ø / R values
    parsed off the sheet (mm). Never raises."""
    try:
        return _snap(ir, diameter_callouts or [], radii_callouts or [])
    except Exception as exc:  # noqa: BLE001 - normalization is advisory
        log_event("drawing_feature_normalize_failed", reason=type(exc).__name__,
                  detail=str(exc)[:200])
        return ir


def _snap(ir, diameters, radii):
    callouts = sorted({round(float(d), 4) for d in diameters if d and float(d) > 0},
                      reverse=True)
    snapped_groups = 0
    snapped_holes = 0
    if callouts:
        # ONE drawing scale governs every circle: anchor the largest pixel circle
        # to the largest Ø callout so nearest-matching survives an estimated
        # pixel scale (a defaulted envelope would otherwise assign wrong values).
        k = _global_scale(ir, callouts)
        for g in getattr(ir, "concentric_groups", None) or []:
            if _snap_group(g, callouts, k):
                snapped_groups += 1
        # Keep the derived nested view consistent with the snapped groups.
        if snapped_groups and getattr(ir, "nested_features", None):
            ir.nested_features = [g.to_nested() for g in ir.concentric_groups]
        for cf in getattr(ir, "cut_features", None) or []:
            if cf.kind == "circular_hole" and _snap_circle(cf, callouts, k):
                snapped_holes += 1

    fillet = _apply_fillet(ir, radii)
    removed = _merge_duplicate_holes(ir)

    if snapped_groups or snapped_holes or fillet or removed:
        log_event("drawing_features_normalized",
                  snapped_groups=snapped_groups, snapped_holes=snapped_holes,
                  fillet_applied=fillet, duplicate_holes_removed=removed,
                  callouts=len(callouts))
        if snapped_groups or snapped_holes:
            ir.assumptions.append(
                "Circle diameters snapped to the drawing's Ø callouts "
                "(exact printed dimensions, not pixel estimates)")
    return ir


def _global_scale(ir, callouts) -> float:
    """One correction factor mapping pixel-mm onto callout-mm, anchored on the
    largest circle ↔ largest Ø (the biggest printed diameter is almost always
    the biggest circle). 1.0 when there is nothing to anchor on."""
    pix = [g.circles_mm[0] for g in (getattr(ir, "concentric_groups", None) or [])
           if g.circles_mm]
    pix += [c.diameter_mm for c in (getattr(ir, "cut_features", None) or [])
            if c.kind == "circular_hole" and c.diameter_mm]
    biggest = max(pix) if pix else 0.0
    if biggest <= 0 or not callouts:
        return 1.0
    return callouts[0] / biggest


def _nearest_callout(value, callouts, used):
    """Closest unused callout to ``value`` within the relative tolerance."""
    best, best_err = None, _SNAP_REL_TOL
    for c in callouts:
        if c in used:
            continue
        err = abs(c - value) / value if value else 1.0
        if err <= best_err:
            best, best_err = c, err
    return best


def _snap_group(g, callouts, k: float = 1.0) -> bool:
    """Snap a concentric group's circle list to exact callouts, DESC, distinct.
    Only commits when EVERY circle finds a distinct callout and the snapped list
    stays strictly descending (a real stepped feature never inverts)."""
    circles = list(g.circles_mm)
    if not circles:
        return False
    used: set[float] = set()
    snapped: list[float] = []
    for d in circles:                      # already DESC (outer → inner)
        c = _nearest_callout(d * k, callouts, used)
        if c is None:
            return False
        used.add(c)
        snapped.append(c)
    if snapped == circles:
        return False                        # already exact
    if any(snapped[i] <= snapped[i + 1] for i in range(len(snapped) - 1)):
        return False                        # would invert the step order
    g.circles_mm = [round(c, 4) for c in snapped]
    g.outer_diameter_mm = g.circles_mm[0]
    g.through_hole_diameter_mm = g.circles_mm[-1]
    g.counterbore_or_boss_diameter_mm = g.circles_mm[-2] if len(g.circles_mm) >= 2 \
        else g.circles_mm[0]
    g.associated_dimension_labels = [f"Ø{c:g}" for c in g.circles_mm]
    g.confidence = min(0.95, max(g.confidence, 0.85))
    g.generated_metadata = {**(g.generated_metadata or {}),
                            "diameters_from_callouts": True}
    return True


def _snap_circle(cf, callouts, k: float = 1.0) -> bool:
    d = cf.diameter_mm or (cf.radius_mm * 2 if cf.radius_mm else 0.0)
    if d <= 0:
        return False
    c = _nearest_callout(d * k, callouts, set())
    if c is None or abs(c - d) < 1e-6:
        return False
    cf.diameter_mm = round(c, 4)
    cf.confidence = min(0.95, max(cf.confidence, 0.85))
    return True


def _apply_fillet(ir, radii) -> bool:
    """Adopt the smallest printed R as the outer-profile corner radius when the
    trace found no rounding of its own (spec: side/neck fillets ≈ R1.6)."""
    outer = getattr(ir, "outer", None)
    plausible = [round(float(r), 4) for r in radii
                 if r and 0.3 <= float(r) <= 50.0]
    if not outer or not plausible or outer.corner_radius_mm:
        return False
    outer.corner_radius_mm = min(plausible)
    return True


def _merge_duplicate_holes(ir) -> int:
    """Drop circular holes that duplicate another at the same centre/diameter
    (double-traced inner/outer edge of one thin ring)."""
    cuts = getattr(ir, "cut_features", None) or []
    circles = [c for c in cuts if c.kind == "circular_hole"]
    keep, removed = [], 0
    for c in circles:
        dup = False
        for k in keep:
            cd = c.diameter_mm or 0
            kd = k.diameter_mm or 0
            if (abs(c.center[0] - k.center[0]) <= _DUP_CENTER_MM
                    and abs(c.center[1] - k.center[1]) <= _DUP_CENTER_MM
                    and kd and abs(cd - kd) / kd <= _DUP_DIA_REL):
                dup = True
                break
        if dup:
            removed += 1
        else:
            keep.append(c)
    if removed:
        ir.cut_features = [c for c in cuts if c.kind != "circular_hole"] + keep
    return removed


# ------------------------------------------------ pipe-wall false-cutout guard

# Plan feature kinds that carve a planar patch (a rectangular notch) rather than
# a round bore/hole — never valid on a cylindrical pipe wall without a callout.
_PLANAR_CUT_KINDS = {"rectangular_cut", "polygon_cut", "slot", "pocket"}


def strip_unbacked_pipe_wall_cutouts(plan, has_cutout_callout: bool = False) -> int:
    """Remove planar (rectangular/polygon/slot/pocket) cut features from a
    cylindrical pipe/branch plan when NO dimension callout backs them.

    A flanged pipe branch is round pipes + flanges + round bores; a rectangular
    notch on the side cylinder is almost always a mis-read of a section line,
    centreline, hidden line, or dimension extension — the "accidental square cut
    on the branch wall" field failure. Returns the number of features stripped.
    Never raises."""
    try:
        if has_cutout_callout:
            return 0
        cuts = [f for f in plan.features
                if str(getattr(f, "kind", "")).split(".")[-1] in _PLANAR_CUT_KINDS
                and str(getattr(f, "op", "cut")).endswith("cut")]
        if not cuts:
            return 0
        keep_ids = {id(f) for f in cuts}
        plan.features = [f for f in plan.features if id(f) not in keep_ids]
        for f in cuts:
            log_event("drawing_pipe_wall_cutout_rejected", feature_id=f.id,
                      kind=str(f.kind).split(".")[-1],
                      reason="planar cut on cylindrical wall not backed by a callout")
        plan.assumptions.append(
            f"Rejected {len(cuts)} un-dimensioned planar cut(s) on the pipe wall "
            "(likely a section/centre/hidden line, not a real cutout)")
        return len(cuts)
    except Exception as exc:  # noqa: BLE001 - sanitizer is best-effort
        log_event("drawing_pipe_wall_sanitize_failed", reason=type(exc).__name__)
        return 0


__all__ = ["snap_ir_to_callouts", "strip_unbacked_pipe_wall_cutouts"]
