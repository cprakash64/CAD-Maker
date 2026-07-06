"""Silhouette-only rejection gate for dimensioned mechanical drawings.

The pipeline uses one profile-extrusion path for two very different inputs:

* a LOGO / icon / silhouette (a USB glyph) — a single closed outline is the
  whole part, and extruding it is exactly right;
* a DIMENSIONED ENGINEERING drawing (a bracket, a flange cover, a key) — the
  outline is only the start; the Ø/R callouts, repeated-hole notation, inner
  openings and internal channels are ENGINEERING FEATURES that must survive.

When an engineering drawing is reduced to a silhouette disk/blob (the field
failure: "Ø100, 4xØ14, Ø20, Ø44 … generated one solid circle"), the model is
wrong even though it is a valid watertight solid. This module:

* parses the sheet's dimension callouts into structured EVIDENCE (repeated
  diameters/radii, global fillet, inner-opening hints) — spec Part 6;
* classifies the drawing ROUTE (logo vs dimensioned mechanical) so the strict
  gate NEVER fires on a genuine logo/simple profile — spec Part 1;
* scores the generated model against that evidence (dimension / internal-cutout
  / hole-count / repeated-pattern / recess sub-scores) and returns an
  accept / review verdict with the explicit MISSING features — spec Parts 2, 7.

Everything is deterministic and pure; the router uses the verdict to downgrade a
silhouette-only result to REVIEW (never a silent PASS) with the missing-feature
list attached. It never raises.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.observability import log_event

# A drawing is "dimensioned mechanical" (strict gate applies) when it carries at
# least this many distinct engineering callouts, OR any repeated-hole/radius
# notation, OR an explicit inner-opening hint.
_MIN_CALLOUTS_FOR_MECHANICAL = 3
# A single Ø that is a large fraction of the envelope AND paired with a smaller
# Ø/R is an inner opening (annular body): outer Ø100 + inner Ø44 / R50.
_INNER_OPENING_RATIO = 0.35


@dataclass
class DimensionEvidence:
    """Structured dimensional evidence parsed off the sheet."""

    diameters: list[float] = field(default_factory=list)
    radii: list[float] = field(default_factory=list)
    repeated_diameters: list[tuple[int, float]] = field(default_factory=list)  # (n, Ø)
    repeated_radii: list[tuple[int, float]] = field(default_factory=list)      # (n, R)
    global_fillet: float | None = None
    linear: list[float] = field(default_factory=list)

    # ---- derived expectations -------------------------------------------------
    @property
    def distinct_circular_callouts(self) -> int:
        """How many DISTINCT circular features the callouts imply (each Ø/R plus
        each repeated group counts once)."""
        vals = {round(v, 2) for v in self.diameters + self.radii}
        vals |= {round(v, 2) for _, v in self.repeated_diameters + self.repeated_radii}
        return len(vals)

    @property
    def expected_hole_count(self) -> int:
        """Lower bound on holes the drawing calls for: every repeated-diameter
        group contributes its count; a lone small Ø is one hole."""
        n = sum(c for c, _ in self.repeated_diameters)
        # Lone diameters smaller than the largest (the outer body) read as holes.
        if self.diameters:
            big = max(self.diameters)
            n += sum(1 for d in self.diameters if d < big * 0.9)
        return n

    @property
    def expected_repeated_features(self) -> int:
        return sum(c for c, _ in self.repeated_diameters + self.repeated_radii)

    @property
    def has_repeated_notation(self) -> bool:
        return bool(self.repeated_diameters or self.repeated_radii)

    @property
    def has_inner_opening(self) -> bool:
        """An outer circle plus a substantial smaller circle ⇒ an annular body
        with a large inner opening (a flange cover / annular bracket)."""
        circ = sorted(self.diameters + [2 * r for r in self.radii], reverse=True)
        if len(circ) < 2:
            return False
        return circ[1] >= _INNER_OPENING_RATIO * circ[0]

    def is_dimensioned_mechanical(self) -> bool:
        return (self.has_repeated_notation
                or self.distinct_circular_callouts >= _MIN_CALLOUTS_FOR_MECHANICAL
                or (self.has_inner_opening and self.distinct_circular_callouts >= 2))

    def to_dict(self) -> dict:
        return {
            "diameters": self.diameters, "radii": self.radii,
            "repeated_diameters": self.repeated_diameters,
            "repeated_radii": self.repeated_radii,
            "global_fillet": self.global_fillet,
            "distinct_circular_callouts": self.distinct_circular_callouts,
            "expected_hole_count": self.expected_hole_count,
            "expected_repeated_features": self.expected_repeated_features,
            "has_inner_opening": self.has_inner_opening,
        }


def parse_dimension_evidence(texts: list[str] | None) -> DimensionEvidence:
    """Sheet/notes text → structured dimensional evidence. Never raises."""
    from app.drawing.dim_labels import parse_dimension_labels

    labels = parse_dimension_labels(texts)
    return DimensionEvidence(
        diameters=list(labels.diameters),
        radii=list(labels.radii),
        repeated_diameters=list(labels.hole_callouts),
        repeated_radii=list(labels.radius_callouts),
        global_fillet=labels.global_fillet,
        linear=list(labels.linear),
    )


# ------------------------------------------------------------------ route

_ROUTE_TYPES = (
    "logo_profile_extrusion", "simple_closed_profile_part",
    "dimensioned_mechanical_plate", "annular_bracket_or_flange_plate",
    "key_or_channel_plate", "pipe_branch", "unknown_engineering_drawing_review",
)


def classify_drawing_route(evidence: DimensionEvidence, *,
                           is_pipe_flange: bool = False,
                           has_internal_dark_channel: bool = False,
                           internal_region_count: int = 0) -> str:
    """Pick the drawing route (spec Part 1). Plain profile extrusion is reserved
    for logos / simple profiles; a dimensioned drawing routes to a mechanical
    reconstruction so its engineering features are preserved."""
    if is_pipe_flange:
        return "pipe_branch"
    if has_internal_dark_channel:
        return "key_or_channel_plate"
    if not evidence.is_dimensioned_mechanical():
        # No engineering callouts → a logo/icon/silhouette or a simple profile.
        return ("simple_closed_profile_part" if internal_region_count
                else "logo_profile_extrusion")
    if evidence.has_inner_opening or evidence.has_repeated_notation:
        return "annular_bracket_or_flange_plate"
    return "dimensioned_mechanical_plate"


# ------------------------------------------------------------------ coverage

@dataclass
class GeneratedShape:
    """What the BUILT model carries, read off its measured report / analysis."""

    hole_count: int = 0
    distinct_circular_features: int = 0   # holes + bosses + inner openings built
    has_internal_cutout: bool = False
    has_recess_or_channel: bool = False
    is_single_disk: bool = False          # one dominant circle, no arm/legs/holes


@dataclass
class FeatureCoverageScores:
    dimension_coverage_score: float = 1.0
    internal_cutout_score: float = 1.0
    hole_count_score: float = 1.0
    repeated_pattern_score: float = 1.0
    recess_or_channel_score: float = 1.0

    def overall(self) -> float:
        return min(self.dimension_coverage_score, self.internal_cutout_score,
                   self.hole_count_score, self.repeated_pattern_score,
                   self.recess_or_channel_score)

    def to_dict(self) -> dict:
        return {"dimension_coverage_score": round(self.dimension_coverage_score, 3),
                "internal_cutout_score": round(self.internal_cutout_score, 3),
                "hole_count_score": round(self.hole_count_score, 3),
                "repeated_pattern_score": round(self.repeated_pattern_score, 3),
                "recess_or_channel_score": round(self.recess_or_channel_score, 3),
                "overall": round(self.overall(), 3)}


@dataclass
class SilhouetteVerdict:
    silhouette_only: bool
    status: str                      # "ok" | "review"
    scores: FeatureCoverageScores
    missing: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"silhouette_only": self.silhouette_only, "status": self.status,
                "missing_features": self.missing, "scores": self.scores.to_dict()}


# Coverage below this on a dimensioned drawing triggers repair/REVIEW (spec 7).
_REPAIR_THRESHOLD = 0.85
_ACCEPT_FLOOR = 0.70


def evaluate_feature_coverage(evidence: DimensionEvidence,
                              generated: GeneratedShape,
                              *, has_internal_dark_channel: bool = False,
                              design_id: str | None = None) -> SilhouetteVerdict:
    """Score the built model against the drawing's dimensional evidence and
    decide whether it is a silhouette-only failure. Only meaningful for a
    dimensioned mechanical drawing — a logo returns a clean pass.

    Emits ``drawing_silhouette_only_rejected`` when a dimensioned drawing built a
    feature-less silhouette."""
    scores = FeatureCoverageScores()
    missing: list[str] = []
    if not evidence.is_dimensioned_mechanical() and not has_internal_dark_channel:
        return SilhouetteVerdict(False, "ok", scores)   # logo / simple profile

    # 1. Hole-count coverage: expected holes vs built holes.
    exp_holes = evidence.expected_hole_count
    if exp_holes > 0:
        scores.hole_count_score = min(1.0, generated.hole_count / exp_holes)
        if generated.hole_count < exp_holes:
            missing.append(f"{exp_holes - generated.hole_count} of {exp_holes} "
                           "called-out holes")

    # 2. Repeated-pattern coverage (4xØ14 / 8xR8 must not collapse to 0-1).
    exp_rep = evidence.expected_repeated_features
    if exp_rep > 0:
        # A repeated group is honored when at least most of its members exist.
        scores.repeated_pattern_score = min(1.0, generated.distinct_circular_features
                                            / max(1, exp_rep))
        if generated.distinct_circular_features < exp_rep * 0.5:
            missing.append(f"repeated pattern ({exp_rep} bosses/holes called out)")

    # 3. Internal-cutout coverage: an inner opening / internal contour must exist.
    if evidence.has_inner_opening and not generated.has_internal_cutout:
        scores.internal_cutout_score = 0.0
        missing.append("large inner circular opening")

    # 4. Recess / channel coverage (the key's dark internal channel).
    if has_internal_dark_channel and not generated.has_recess_or_channel:
        scores.recess_or_channel_score = 0.0
        missing.append("internal recess / channel")

    # 5. Dimension coverage: distinct circular callouts vs distinct built circles.
    exp_circ = evidence.distinct_circular_callouts
    if exp_circ > 0:
        scores.dimension_coverage_score = min(
            1.0, generated.distinct_circular_features / exp_circ)

    # Single-disk failure: the drawing has multiple circular features but the
    # model is one dominant disk with (almost) nothing inside.
    single_disk_fail = (generated.is_single_disk
                        and (exp_holes >= 2 or evidence.has_inner_opening
                             or evidence.has_repeated_notation))
    if single_disk_fail:
        scores.dimension_coverage_score = min(scores.dimension_coverage_score, 0.2)
        if "single dominant disk" not in " ".join(missing):
            missing.append("model is a single dominant disk (drawing has "
                           "multiple mechanical features)")

    overall = scores.overall()
    silhouette_only = single_disk_fail or overall < _ACCEPT_FLOOR or (
        bool(missing) and overall < _REPAIR_THRESHOLD)
    status = "review" if silhouette_only else "ok"
    verdict = SilhouetteVerdict(silhouette_only, status, scores, missing)
    if silhouette_only:
        log_event("drawing_silhouette_only_rejected", design_id=design_id,
                  missing=missing[:12], **scores.to_dict(),
                  expected_holes=exp_holes, built_holes=generated.hole_count,
                  expected_repeated=exp_rep)
    else:
        log_event("drawing_feature_coverage_ok", design_id=design_id,
                  **scores.to_dict())
    return verdict


__all__ = ["DimensionEvidence", "parse_dimension_evidence", "classify_drawing_route",
           "GeneratedShape", "FeatureCoverageScores", "SilhouetteVerdict",
           "evaluate_feature_coverage"]
