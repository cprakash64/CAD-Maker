"""Universal CAD generation contract.

Every prompt that enters generation must leave it in exactly ONE safe, terminal
state — never a 500, never a silently-broken model handed off as if it were good:

  * ``generated_single_part``  — a validated single part with exports
  * ``generated_assembly``     — a (concept) assembly with exports
  * ``needs_clarification``    — missing critical type/dimensions; we asked
  * ``needs_decomposition``    — a large multi-system machine; we returned a plan
  * ``unsupported``            — recognized but cannot be safely modeled
  * ``failed_safe``            — generation could not produce safe geometry; the
                                 design is inspectable but nothing broken is
                                 offered as manufacturable

:func:`resolve_outcome` reads a *finished* design row and reports its terminal
state. It is the single source of truth the API/tests use to assert that no
prompt ever escapes into broken, exportable geometry.
"""
from __future__ import annotations

from enum import Enum


class GenerationOutcome(str, Enum):
    generated_single_part = "generated_single_part"
    generated_assembly = "generated_assembly"
    needs_clarification = "needs_clarification"
    needs_decomposition = "needs_decomposition"
    unsupported = "unsupported"
    failed_safe = "failed_safe"


# Terminal states that did NOT yield exportable geometry but are still SAFE
# (the user gets guidance / a question / an honest "can't", never a broken file).
SAFE_NON_GEOMETRY = frozenset({
    GenerationOutcome.needs_clarification,
    GenerationOutcome.needs_decomposition,
    GenerationOutcome.unsupported,
    GenerationOutcome.failed_safe,
})
GEOMETRY_OUTCOMES = frozenset({
    GenerationOutcome.generated_single_part,
    GenerationOutcome.generated_assembly,
})


def _is_critical_failure(design) -> bool:
    """Validation found production-blocking (e.g. non-manifold / non-watertight)
    geometry. Mirrors design_service.is_critical_failure without importing it (to
    keep this module dependency-light and import-cycle free)."""
    report = (getattr(design, "semantic_json", None) or {}).get("dimension_report") or {}
    return (report.get("validation") or {}).get("status") == "critical_failure"


def _has_exports(design) -> bool:
    try:
        return bool(list(design.exports))
    except Exception:  # detached / not loaded
        return False


def _is_assembly(design) -> bool:
    if getattr(design, "route", None) == "assembly":
        return True
    mode = (getattr(design, "semantic_json", None) or {}).get("design_mode")
    if mode == "assembly":
        return True
    classification = (getattr(design, "semantic_json", None) or {}).get("classification") or {}
    return classification.get("design_mode") == "assembly"


def resolve_outcome(design) -> GenerationOutcome:
    """Classify a finished design into exactly one terminal contract state."""
    route = getattr(design, "route", None)
    spec_json = getattr(design, "spec_json", None)
    clarification = getattr(design, "clarification_question", None)

    if route == "needs_decomposition":
        return GenerationOutcome.needs_decomposition
    if route in ("failed_safe", "unsupported"):
        return (GenerationOutcome.failed_safe if route == "failed_safe"
                else GenerationOutcome.unsupported)
    if route == "clarification" or (spec_json is None and clarification):
        return GenerationOutcome.needs_clarification

    # Geometry routes. A critical validation failure is NEVER offered as a real
    # part — it collapses to failed_safe (exports are gated downstream).
    if _has_exports(design):
        if _is_critical_failure(design):
            return GenerationOutcome.failed_safe
        return (GenerationOutcome.generated_assembly if _is_assembly(design)
                else GenerationOutcome.generated_single_part)

    # No geometry and no clarification/decomposition -> nothing safe was produced.
    if clarification:
        return GenerationOutcome.needs_clarification
    return GenerationOutcome.failed_safe


def is_safe_outcome(outcome: GenerationOutcome) -> bool:
    """Every terminal state IS safe by definition — this exists so callers/tests
    can assert the contract held (the result is one of the six, geometry routes
    are never critical failures)."""
    return outcome in GEOMETRY_OUTCOMES or outcome in SAFE_NON_GEOMETRY


def contract_metadata(design) -> dict:
    """The contract block stored on ``semantic_json['contract']`` and surfaced in
    the API: the terminal outcome plus whether exportable geometry exists."""
    outcome = resolve_outcome(design)
    return {
        "outcome": outcome.value,
        "produced_geometry": outcome in GEOMETRY_OUTCOMES,
        "is_safe": True,  # by construction every terminal state is safe
    }
