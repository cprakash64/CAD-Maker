"""Deterministic dimension-scale resolution for Drawing → CAD.

The field failure: the SAME pipe-branch drawing produced 14.9 × 14.8 × 15 mm on
one run and 120 × 120 × 120 mm on another — because the final size depended on
whether the (non-deterministic) vision call succeeded or timed out, and a
unit-ambiguous tiny reading (12 bolts on a Ø14.8 flange) was built literally as
degenerate geometry.

This module is the single, PURE resolver: given a spec + the same image hash it
always returns the same dimensions and the same ``estimated`` verdict. It never
consults the provider. Its job:

* accept confidently-parsed dimensions as-is;
* apply a FAMILY PLAUSIBILITY FLOOR — a flanged pipe branch carrying N bolts can
  not have a Ø14.8 flange; such a reading is unit-ambiguous, so the whole spec is
  scaled up to a canonical envelope and marked estimated (→ REVIEW);
* emit a stable ``repeatability_key`` tying the image hash to the resolved route
  + dimensions, so a replay is observable and assertable.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace

from app.observability import log_event

# Canonical envelope a unit-ambiguous flanged pipe branch is scaled up to.
_CANON_FLANGE_OD = 120.0
# A flange must be wide enough for its bolt circle: PCD + clearance, with a
# small absolute floor. Below this the reading is treated as unit-ambiguous.
_MIN_FLANGE_ABS_MM = 40.0
_BOLT_PITCH_FACTOR = 2.6   # OD >= bolt_count * bolt_dia * (this / pi) heuristic


@dataclass
class ResolvedScale:
    scale_factor: float          # multiplier applied to the input spec
    estimated: bool              # True → REVIEW (uncertain or floored)
    source: str                  # parsed | floored_family_default
    repeatability_key: str
    reason: str = ""


def _min_plausible_flange_od(bolt_count: int, bolt_dia: float) -> float:
    """Smallest flange OD that can carry ``bolt_count`` bolts of ``bolt_dia``
    without the bolt circle overrunning the rim."""
    import math

    ring = bolt_count * bolt_dia * (_BOLT_PITCH_FACTOR / math.pi) if bolt_count else 0
    return max(_MIN_FLANGE_ABS_MM, ring + 2 * bolt_dia)


def resolve_pipe_branch_scale(spec, image_hash: str | None = None,
                              parsed_confident: bool = False):
    """Deterministically resolve a flanged pipe-branch spec's absolute size.

    Returns ``(resolved_spec, ResolvedScale)``. Pure: identical ``spec`` +
    ``image_hash`` always yield identical output. ``spec`` is a
    ``DrawingPipeBranchSpec`` (has flange_od/main_od/main_len/… + bolt fields)."""
    floor = _min_plausible_flange_od(int(spec.bolt_count or 0), float(spec.bolt_dia or 0))
    implausible = (spec.flange_od or 0) < floor

    if implausible:
        # Unit-ambiguous tiny reading → scale the WHOLE spec up to the canonical
        # envelope (preserves proportions) and flag estimated.
        factor = round(_CANON_FLANGE_OD / max(1e-6, spec.flange_od or _CANON_FLANGE_OD), 4)
        resolved = _scale_spec(spec, factor)
        source, estimated = "floored_family_default", True
        reason = (f"flange Ø{spec.flange_od:g} implausible for {spec.bolt_count}×"
                  f"Ø{spec.bolt_dia:g} bolts (min Ø{floor:.0f}); scaled ×{factor:g} "
                  f"to a Ø{_CANON_FLANGE_OD:g} envelope")
    else:
        resolved, factor = spec, 1.0
        source = "parsed" if parsed_confident else "parsed_unverified"
        estimated = not parsed_confident
        reason = "dimensions within a plausible range for the family"

    key = _repeatability_key(image_hash, "flanged_pipe_branch", resolved)
    scale = ResolvedScale(scale_factor=factor, estimated=estimated, source=source,
                          repeatability_key=key, reason=reason)
    log_event("drawing_dimension_scale_resolved", family="flanged_pipe_branch",
              source=source, scale_factor=factor, estimated=estimated,
              flange_od=round(resolved.flange_od, 2), main_od=round(resolved.main_od, 2),
              main_len=round(resolved.main_len, 2), reason=reason)
    log_event("drawing_repeatability_key", key=key, image_hash=image_hash,
              route="flanged_pipe_branch")
    return resolved, scale


def _scale_spec(spec, factor: float):
    """Scale every LENGTH field of a DrawingPipeBranchSpec by ``factor`` (counts
    like bolt_count are left alone)."""
    s = replace(spec)
    for attr in ("main_od", "main_len", "branch_od", "branch_len", "wall",
                 "flange_od", "flange_thk", "bolt_dia", "pcd"):
        v = getattr(s, attr, None)
        if isinstance(v, (int, float)) and v:
            setattr(s, attr, round(v * factor, 3))
    s.assumptions = list(getattr(s, "assumptions", []) or []) + [
        f"Dimensions scaled ×{factor:g} to a plausible {_CANON_FLANGE_OD:g}mm "
        "flange envelope (drawing units were ambiguous) — REVIEW before manufacturing"]
    return s


def _repeatability_key(image_hash: str | None, route: str, spec) -> str:
    """Stable key: same image + same route + same resolved dims → same key."""
    dims = "|".join(f"{a}={round(float(getattr(spec, a, 0) or 0), 2)}"
                    for a in ("main_od", "main_id", "main_len", "branch_od",
                              "flange_od", "flange_thk", "bolt_count", "bolt_dia",
                              "pcd"))
    payload = f"{image_hash or 'nohash'}::{route}::{dims}"
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


__all__ = ["ResolvedScale", "resolve_pipe_branch_scale"]
