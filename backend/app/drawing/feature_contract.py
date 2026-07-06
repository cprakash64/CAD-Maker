"""Drawing feature-preservation contract (Part B).

Detected drawing features are a CONTRACT: once the pipeline detects a hole,
slot, polygon hole, counterbore, concentric group, branch pipe, or flange bolt
circle, the generated CAD must either build that feature or the candidate is
rejected/repaired. It is never silently dropped, and a valid mesh alone is NOT
sufficient acceptance.

This module turns a reconstructed ``MechanicalSketchIR`` (or a pipe-branch
``CadPlan``) into an expectation, measures what the built plan actually carries
(matched per feature id so a polygon hole can never be counted as a plain
hole), and scores coverage. The router uses the score to accept (PASS/REVIEW),
repair, or reject the candidate.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from app.observability import log_event

# Cut kinds that a plain profile extrusion (or a circular hole) mangles; their
# absence from the built plan is a MAJOR miss (never acceptable below full
# coverage). An arched/curved-top window MUST survive as its profile, never a
# circular hole.
_MAJOR_CUT_KINDS = {"polygon_hole", "rectangular_slot", "rounded_slot",
                    "arc_slot", "arched_rectangular_cutout", "arbitrary_cutout"}
# Blind recess/channel kinds: partial-depth pockets that keep a floor — NEVER
# counted as through openings.
_RECESS_KINDS = {"blind_recess", "recessed_channel", "through_channel_cut"}
# Plan feature kinds (FeatureKind values, stringified) that realize each IR cut.
_HOLE_PLAN_KINDS = {"hole", "counterbore", "countersink"}
_POLYGON_PLAN_KINDS = {"polygon_cut", "rectangular_cut", "slot"}

# Acceptance bands (Part B).
_COVERAGE_PASS = 0.90
_COVERAGE_REVIEW_FLOOR = 0.65


@dataclass
class DrawingFeatureContract:
    expected_outer_profiles: int = 0
    expected_circular_holes: int = 0
    expected_polygon_holes: int = 0
    expected_rectangular_cutouts: int = 0
    expected_rounded_slots: int = 0
    expected_arc_slots: int = 0
    expected_arched_cutouts: int = 0
    expected_freeform_cutouts: int = 0
    expected_counterbore_groups: int = 0
    expected_concentric_groups: int = 0
    expected_through_holes_from_groups: int = 0
    expected_counterbore_or_ring_features: int = 0
    expected_boss_groups: int = 0
    expected_flanges: int = 0
    expected_bolt_hole_patterns: int = 0
    expected_side_branch: bool = False
    expected_pipe_bores: int = 0
    # Per-feature ledger: {feature_id: category} — the exact features that must
    # survive into CAD. This is what makes "5 detected, 3 built" observable.
    feature_ids: dict = field(default_factory=dict)

    def total_features(self) -> int:
        return len(self.feature_ids)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class GeneratedFeatureSummary:
    actual_circular_holes: int = 0
    actual_polygon_holes: int = 0
    actual_rectangular_cutouts: int = 0
    actual_slots: int = 0
    actual_counterbores: int = 0
    actual_concentric_groups: int = 0
    actual_through_holes_from_groups: int = 0
    actual_counterbore_or_ring_features: int = 0
    actual_boss_groups: int = 0
    actual_flanges: int = 0
    actual_bolt_holes_by_flange: dict = field(default_factory=dict)
    actual_side_branch: bool = False
    actual_bores: int = 0
    measured_hole_count: int = 0
    built_feature_ids: dict = field(default_factory=dict)  # {id: plan_kind}

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class FeatureCoverage:
    score: float
    acceptable: bool
    reason: str
    missing: list[str] = field(default_factory=list)
    expected_total: int = 0
    matched_total: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


# ------------------------------------------------------------------ contract

def contract_from_sketch_ir(ir) -> DrawingFeatureContract:
    """Expected features from a reconstructed 2D sketch."""
    c = DrawingFeatureContract(expected_outer_profiles=len(ir.outer_profiles))
    for cf in ir.cut_features:
        c.feature_ids[cf.id] = cf.kind
        if cf.kind == "circular_hole":
            c.expected_circular_holes += 1
        elif cf.kind == "polygon_hole":
            c.expected_polygon_holes += 1
        elif cf.kind == "rectangular_slot":
            c.expected_rectangular_cutouts += 1
        elif cf.kind == "rounded_slot":
            c.expected_rounded_slots += 1
        elif cf.kind == "arc_slot":
            c.expected_arc_slots += 1
        elif cf.kind == "arched_rectangular_cutout":
            c.expected_arched_cutouts += 1
        elif cf.kind == "arbitrary_cutout":
            c.expected_freeform_cutouts += 1
    # Concentric groups (rich) drive the group expectations; fall back to the
    # derived nested_features view for IRs that lack the groups.
    groups = getattr(ir, "concentric_groups", None) or ir.nested_features
    for g in groups:
        c.feature_ids[g.id] = "counterbore"  # a group builds a counterbore feature
        c.expected_concentric_groups += 1
        c.expected_counterbore_groups += 1
        c.expected_through_holes_from_groups += 1   # ONE through-hole per group
        ring = getattr(g, "counterbore_or_boss_diameter_mm", None)
        through = getattr(g, "through_hole_diameter_mm", None)
        has_ring = (ring is not None and through is not None and ring > through) \
            or getattr(g, "outer_diameter_mm", 0) > 0
        if has_ring:
            c.expected_counterbore_or_ring_features += 1
    for af in ir.additive_features:
        if af.kind in ("boss", "raised_ring"):
            c.expected_boss_groups += 1
    return c


def contract_from_pipe_branch_plan(plan, side_branch: bool = True,
                                   bolt_per_flange: int = 0) -> DrawingFeatureContract:
    """Expected features for a route-locked flanged pipe branch, read off the
    deterministic plan's anatomy (main + branch pipes, three flanges, bores)."""
    kinds = [str(f.kind).split(".")[-1] for f in plan.features]
    flanges = [f for f in plan.features
               if str(f.kind).endswith("circular_flange")]
    pipes = [f for f in plan.features if str(f.kind).endswith("pipe")]
    bolt = bolt_per_flange or max(
        (int(f.p("bolt_count", 0, "holes")) for f in flanges), default=0)
    return DrawingFeatureContract(
        expected_flanges=len(flanges),
        expected_bolt_hole_patterns=len(flanges),
        expected_side_branch=side_branch or any("branch" in f.id for f in pipes),
        expected_pipe_bores=len(pipes),
        feature_ids={f.id: str(f.kind).split(".")[-1] for f in plan.features
                     if str(f.kind).endswith(("pipe", "circular_flange"))},
        expected_boss_groups=0,
    ) if flanges else DrawingFeatureContract()


# ------------------------------------------------------------------ summary

def generated_summary_from_ir(ir) -> dict:
    """Named, positional generated-feature summary for a reconstructed sketch.

    Reported ONLY after the feature contract has proven every IR feature reached
    the built plan (so IR features == generated features). Splits concentric
    groups into TOP vs LOWER by their y position and counts each group's through
    bore toward ``circular_holes`` — the shape a bracket acceptance test asserts.
    """
    groups = getattr(ir, "concentric_groups", None) or ir.nested_features
    top = [g for g in groups if g.center[1] > 0]
    lower = [g for g in groups if g.center[1] <= 0]
    rings = sum(1 for g in groups
                if getattr(g, "counterbore_or_boss_diameter_mm",
                           getattr(g, "outer_diameter_mm", 0))
                > getattr(g, "through_hole_diameter_mm",
                          getattr(g, "inner_diameter_mm", 0)))
    circular = sum(1 for c in ir.cut_features if c.kind == "circular_hole")
    rect = sum(1 for c in ir.cut_features if c.kind == "rectangular_slot")
    rounded = sum(1 for c in ir.cut_features if c.kind == "rounded_slot")
    arc = sum(1 for c in ir.cut_features
              if c.kind in ("arc_slot", "semicircular_cutout"))
    arched = sum(1 for c in ir.cut_features
                 if c.kind == "arched_rectangular_cutout")
    freeform = sum(1 for c in ir.cut_features if c.kind == "arbitrary_cutout")
    polygon = sum(1 for c in ir.cut_features if c.kind == "polygon_hole")
    blind_recesses = sum(1 for c in ir.cut_features if c.kind == "blind_recess")
    recessed_channels = sum(1 for c in ir.cut_features if c.kind == "recessed_channel")
    through_channels = sum(1 for c in ir.cut_features
                           if c.kind == "through_channel_cut")
    # through openings NEVER include blind recesses/channels (they keep a floor).
    plain_through = sum(1 for c in ir.cut_features
                        if c.kind == "circular_hole" or
                        (c.kind not in _RECESS_KINDS and c.through))
    return {
        "outer_profiles": len(ir.outer_profiles),
        "blind_recesses": blind_recesses,
        "recessed_channels": recessed_channels,
        "through_channel_cuts": through_channels,
        "internal_dark_regions": blind_recesses + recessed_channels + through_channels,
        "through_holes": plain_through + len(groups),
        "top_concentric_groups": len(top),
        "lower_counterbore_groups": len(lower),
        "concentric_groups": len(groups),
        "concentric_groups_generated": len(groups),
        "through_holes_from_groups": len(groups),
        "counterbore_or_ring_features": rings,
        # Each concentric group carries one through bore → counts as a hole.
        "circular_holes": circular + len(groups),
        "plain_circular_holes": circular,
        "polygon_holes": polygon,
        "rectangular_cutouts": rect,
        "rounded_slots": rounded,
        "arc_or_semicircular_cutouts": arc,
        "arched_rectangular_cutouts": arched,
        "freeform_or_polygon_cutouts": freeform + polygon,
        "middle_circular_hole": False,   # a window is NEVER a circular hole
        "lower_groups_symmetric": _lower_groups_symmetric(lower),
        "total_features": len(ir.cut_features) + len(groups),
    }


def _lower_groups_symmetric(lower_groups, tol: float = 0.18) -> bool:
    """A symmetric drawing's lower-left / lower-right groups mirror across x=0:
    same y (within tol of the span) and opposite x. True for 0/1 groups
    (nothing to compare) and for a mirrored pair."""
    if len(lower_groups) < 2:
        return True
    xs = sorted(lower_groups, key=lambda g: g.center[0])
    left, right = xs[0], xs[-1]
    span = max(1.0, abs(right.center[0] - left.center[0]))
    x_mirror = abs(left.center[0] + right.center[0]) <= tol * span + 0.5
    y_match = abs(left.center[1] - right.center[1]) <= tol * span + 0.5
    return bool(x_mirror and y_match)


def summary_from_plan(plan, measured_hole_count: int) -> GeneratedFeatureSummary:
    """What the built plan actually carries, matched by feature id so a polygon
    hole is never miscounted as a plain circular hole."""
    s = GeneratedFeatureSummary(measured_hole_count=int(measured_hole_count or 0))
    for f in plan.features:
        kind = str(f.kind).split(".")[-1]
        s.built_feature_ids[f.id] = kind
        if kind == "hole":
            s.actual_circular_holes += 1
        elif kind == "polygon_cut":
            # A polygon_cut realizes a polygon hole OR a traced slot; the id
            # ledger disambiguates, but count it as a polygon here.
            s.actual_polygon_holes += 1
        elif kind == "rectangular_cut":
            s.actual_rectangular_cutouts += 1
        elif kind == "slot":
            s.actual_slots += 1
        elif kind in ("counterbore", "countersink"):
            s.actual_counterbores += 1
            s.actual_concentric_groups += 1
            s.actual_through_holes_from_groups += 1   # a group cuts ONE through-hole
            ring = f.p("counterbore_diameter", 0, "cap_diameter", "outer_ring_diameter")
            if ring > 0:
                s.actual_counterbore_or_ring_features += 1
        elif kind == "boss":
            s.actual_boss_groups += 1
        elif kind == "circular_flange":
            s.actual_flanges += 1
            s.actual_bolt_holes_by_flange[f.id] = int(f.p("bolt_count", 0, "holes"))
        elif kind == "pipe":
            s.actual_bores += 1
            if "branch" in f.id:
                s.actual_side_branch = True
    return s


# ------------------------------------------------------------------ coverage

def evaluate_coverage(contract: DrawingFeatureContract,
                      summary: GeneratedFeatureSummary,
                      ir=None, plan=None) -> FeatureCoverage:
    """Score how much of the contract the build honored, matched per feature id.

    A feature is HONORED when its id appears in the built plan with a
    kind-appropriate realization (a polygon hole must be a polygon_cut, not a
    plain hole; a counterbore must be a counterbore). Missing MAJOR cuts
    (polygon holes, slots, rectangular cutouts) cap acceptance below full
    coverage."""
    expected = contract.feature_ids
    if not expected:
        return FeatureCoverage(1.0, True, "no features expected", [], 0, 0)

    missing: list[str] = []
    matched = 0
    for fid, cat in expected.items():
        built = summary.built_feature_ids.get(fid)
        if built is not None and _kind_matches(cat, built):
            matched += 1
        else:
            missing.append(f"{fid}:{cat}")

    score = matched / len(expected)
    # Hard categorical invariants: an expected MAJOR cut category with zero
    # actuals is never acceptable, whatever the ratio says.
    hard_fail = None
    if contract.expected_polygon_holes and summary.actual_polygon_holes == 0:
        hard_fail = "polygon_hole"
    elif contract.expected_rectangular_cutouts and \
            summary.actual_rectangular_cutouts == 0 and summary.actual_polygon_holes == 0:
        hard_fail = "rectangular_cutout"
    elif contract.expected_arc_slots and summary.actual_polygon_holes == 0 \
            and summary.actual_slots == 0:
        hard_fail = "arc_slot"
    elif (contract.expected_arched_cutouts
          and summary.actual_polygon_holes == 0
          and summary.actual_rectangular_cutouts == 0 and summary.actual_slots == 0):
        # An arched window that produced no profile cut (it collapsed to a plain
        # circular hole / nothing) is the exact field bug — reject it.
        hard_fail = "arched_rectangular_cutout"
    elif (contract.expected_counterbore_groups
          and summary.actual_counterbores == 0):
        hard_fail = "counterbore_group"

    major_missing = any(m.split(":")[1] in _MAJOR_CUT_KINDS for m in missing)
    if hard_fail is not None:
        acceptable, reason = False, f"required {hard_fail} not generated"
    elif score >= _COVERAGE_PASS:
        acceptable, reason = True, "ok"
    elif score >= _COVERAGE_REVIEW_FLOOR:
        acceptable = not major_missing
        reason = "ok (minor misses)" if acceptable else "major feature missing"
    else:
        acceptable, reason = False, f"only {matched}/{len(expected)} features built"
    return FeatureCoverage(round(score, 3), acceptable, reason, missing,
                           len(expected), matched)


def _kind_matches(ir_category: str, plan_kind: str) -> bool:
    if ir_category == "circular_hole":
        return plan_kind in _HOLE_PLAN_KINDS
    if ir_category in ("counterbore", "countersink", "concentric_ring",
                       "boss_with_hole"):
        return plan_kind in ("counterbore", "countersink", "hole", "boss")
    if ir_category in _MAJOR_CUT_KINDS:
        return plan_kind in _POLYGON_PLAN_KINDS
    # pipe/flange anatomy
    if ir_category in ("pipe", "circular_flange"):
        return plan_kind == ir_category
    return plan_kind in (_POLYGON_PLAN_KINDS | _HOLE_PLAN_KINDS)


# ------------------------------------------------- concentric-group integrity

def check_recess_integrity(ir, plan) -> tuple[bool, str]:
    """A detected internal dark region must build as a BLIND recess that keeps a
    floor — never a through cut and never counted as a through hole. Returns
    (ok, reason); ok=True when there is no recess evidence."""
    recesses = [c for c in ir.cut_features if c.kind in _RECESS_KINDS]
    if not recesses:
        return True, "no recess evidence"
    by_id = {f.id: f for f in plan.features}
    for c in recesses:
        if c.kind == "through_channel_cut":
            continue  # an explicitly-through channel is allowed
        f = by_id.get(c.id)
        if f is None:
            return False, f"recess {c.id} was dropped from the build"
        if getattr(f, "through", False):
            return False, f"recess {c.id} was cut THROUGH (should be blind)"
        if f.p("recess_depth", 0) <= 0:
            return False, f"recess {c.id} has no blind depth (would cut through)"
    return True, "ok"


def check_concentric_group_integrity(ir, plan, summary) -> tuple[bool, str]:
    """Reject a candidate whose concentric groups were NOT built as structured
    groups. Guards the four failure modes:

    * IR has groups but CAD produced only plain holes (no counterbore);
    * CAD created EXTRA through-holes from the outer/middle circles;
    * a group centre drifted from its detected centre;
    * symmetric lower groups came out asymmetric."""
    groups = getattr(ir, "concentric_groups", None) or ir.nested_features
    if not groups:
        return True, "no concentric groups"

    if summary.actual_counterbores == 0:
        return False, "concentric groups collapsed to plain holes (no counterbore built)"

    # Extra through-holes: each group must contribute exactly ONE through-hole.
    expected_through = sum(1 for c in ir.cut_features if c.through) + len(groups)
    if summary.measured_hole_count > expected_through:
        return False, (f"extra through-holes from group circles "
                       f"({summary.measured_hole_count} > {expected_through})")

    # Centre drift: each group's counterbore must sit at its detected centre.
    by_id = {f.id: f for f in plan.features}
    tol = 0.6 + 0.02 * max(abs(g.center[0]) for g in groups)
    for g in groups:
        f = by_id.get(g.id)
        if f is None:
            continue
        at = getattr(f, "at", None) or [0, 0, 0]
        if abs(at[0] - g.center[0]) > tol or abs(at[1] - g.center[1]) > tol:
            return False, f"group {g.id} centre drifted beyond {tol:.1f}mm"

    lower = [g for g in groups if g.center[1] <= 0]
    if not _lower_groups_symmetric(lower):
        return False, "lower concentric groups are asymmetric"
    return True, "ok"


# ------------------------------------------------------------------ orchestration

def enforce_sketch_feature_contract(ir, plan, measured_hole_count: int,
                                    design_id: str | None = None) -> FeatureCoverage:
    """Build the contract, summarize the plan, log the full ledger, and return
    the coverage verdict. Emits the Part B/I observability events."""
    contract = contract_from_sketch_ir(ir)
    summary = summary_from_plan(plan, measured_hole_count)
    cov = evaluate_coverage(contract, summary, ir, plan)
    # Concentric-group integrity is a HARD gate layered on top of coverage.
    group_ok, group_reason = check_concentric_group_integrity(ir, plan, summary)
    if not group_ok and cov.acceptable:
        cov.acceptable = False
        cov.reason = group_reason
    log_event("drawing_feature_contract", design_id=design_id,
              **{k: v for k, v in contract.to_dict().items() if k != "feature_ids"})
    log_event("drawing_generated_feature_summary", design_id=design_id,
              **{k: v for k, v in summary.to_dict().items()
                 if k != "built_feature_ids"})
    log_event("drawing_feature_coverage_score", design_id=design_id,
              score=cov.score, acceptable=cov.acceptable,
              expected=cov.expected_total, matched=cov.matched_total,
              concentric_group_integrity=group_ok, group_reason=group_reason)
    if cov.missing:
        log_event("drawing_feature_missing", design_id=design_id,
                  missing=cov.missing[:16])
    return cov


__all__ = ["DrawingFeatureContract", "GeneratedFeatureSummary", "FeatureCoverage",
           "contract_from_sketch_ir", "contract_from_pipe_branch_plan",
           "summary_from_plan", "evaluate_coverage", "generated_summary_from_ir",
           "check_concentric_group_integrity", "check_recess_integrity",
           "enforce_sketch_feature_contract"]
