"""Trust-level → validation-status policy for the Object Intelligence layer.

The single rule the whole product hangs on: **GPT-estimated critical dimensions can
never PASS.** Each source type maps to the BEST status a generated part may reach;
the geometry validator can only *lower* it from there (e.g. a missing port cutout
downgrades an otherwise-PASS-eligible enclosure).
"""
from __future__ import annotations

from app.cad.object_intelligence.mechanical_spec import (
    SOURCE_GPT,
    SOURCE_LOCAL_VERIFIED,
    SOURCE_OFFICIAL,
    SOURCE_UNKNOWN,
    SOURCE_USER,
    SOURCE_WEB,
)

# Status ceilings (best achievable verdict) per source type.
STATUS_PASS = "pass"
STATUS_REVIEW = "review"          # maps to validation "warning"
STATUS_CONCEPT = "concept"
STATUS_CLARIFY = "clarify"

# Official extraction may PASS only above this confidence, else REVIEW.
_OFFICIAL_PASS_CONFIDENCE = 0.85


def status_ceiling(source_type: str, confidence: float = 0.0) -> str:
    """The best validation status this source/confidence may reach (before geometry
    checks, which can only lower it)."""
    if source_type in (SOURCE_LOCAL_VERIFIED, SOURCE_USER):
        return STATUS_PASS
    if source_type == SOURCE_OFFICIAL:
        return STATUS_PASS if confidence >= _OFFICIAL_PASS_CONFIDENCE else STATUS_REVIEW
    if source_type == SOURCE_WEB:
        return STATUS_REVIEW
    if source_type == SOURCE_GPT:
        return STATUS_CONCEPT          # estimated dims: never PASS
    if source_type == SOURCE_UNKNOWN:
        return STATUS_CLARIFY
    return STATUS_REVIEW


def can_pass(source_type: str, confidence: float = 0.0) -> bool:
    return status_ceiling(source_type, confidence) == STATUS_PASS


def status_to_validation(status: str) -> str:
    """Map an OI status ceiling to a dimension-report validation status."""
    return {
        STATUS_PASS: "pass",
        STATUS_REVIEW: "warning",
        STATUS_CONCEPT: "warning",
        STATUS_CLARIFY: "warning",
    }.get(status, "warning")
