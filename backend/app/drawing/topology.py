"""Topology coverage contract for Drawing → CAD.

Before a drawing-derived model is accepted, its BUILT geometry is compared
against the reconstructed sketch's intent: a sketch that traced 5 cut features
can never compile to 0 holes, a rectangular slot must survive as a cut, a pipe
branch's per-flange bolt circles must actually be drilled. A gross mismatch
rejects the design so another route (or a clean failure) takes over — never a
silently wrong model.

Coverage is intentionally lenient on EXACT counts (a merged/overlapping cut may
drop one) but strict on the categorical invariants (holes present when expected,
slots present when expected).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TopologyCoverage:
    score: float
    acceptable: bool
    reason: str
    expected_cuts: int
    actual_cuts: int
    details: dict = field(default_factory=dict)


# Fraction of expected through-cuts that must actually appear in the solid.
_MIN_HOLE_COVERAGE = 0.6


def topology_coverage(ir, design) -> TopologyCoverage:
    """Compare a MechanicalSketchIR against the built design's measured holes.

    ``design`` is the persisted Design (its dimension_report carries the
    compiler-measured hole_count — real subtractive operations, not metadata)."""
    expected = ir.through_hole_count()
    actual = _measured_holes(design)
    details = {"ir_summary": ir.feature_summary()}

    if expected == 0:
        # Nothing to cut — coverage is trivially satisfied (a plain plate).
        return TopologyCoverage(1.0, True, "no cuts expected", 0, actual, details)

    if actual == 0:
        return TopologyCoverage(
            0.0, False, f"IR has {expected} cut features but the model has 0 holes",
            expected, 0, details)

    score = min(1.0, actual / expected)
    # Categorical invariant: if the IR has any slot, the built plan must carry a
    # polygon/slot cut (the compiler counts polygon_cut in its hole count, so a
    # present slot already lifts ``actual``; this guards the "slot silently
    # dropped to a plain plate" case).
    has_slot_intent = any(c.kind in ("rectangular_slot", "rounded_slot", "arc_slot",
                                     "polygon_hole", "arbitrary_cutout")
                          for c in ir.cut_features)
    slot_ok = (not has_slot_intent) or _plan_has_polygon_cut(design)
    acceptable = score >= _MIN_HOLE_COVERAGE and slot_ok
    reason = "ok" if acceptable else (
        "slot cut missing" if not slot_ok
        else f"only {actual}/{expected} cuts survived")
    return TopologyCoverage(round(score, 3), acceptable, reason,
                            expected, actual, details)


def branch_bolt_coverage(design, expected_bolts_per_flange: int) -> bool:
    """Every flange of a pipe branch must be drilled: the measured through-hole
    count must cover all three bolt circles (+ bores), not just the branch."""
    actual = _measured_holes(design)
    need = 3 * max(1, expected_bolts_per_flange)
    return actual >= need


def _measured_holes(design) -> int:
    report = (getattr(design, "semantic_json", None) or {}).get("dimension_report") or {}
    measured = report.get("measured") or {}
    val = measured.get("hole_count")
    return int(val) if isinstance(val, (int, float)) else 0


def _plan_has_polygon_cut(design) -> bool:
    for f in (getattr(design, "features_json", None) or []):
        if f.get("type") in ("polygon_cut", "rectangular_cut", "slot", "arc_slot"):
            return True
    return False
