"""Deterministic 'correct interpretation' hint -> DrawingInterpretationSpec.

Provider-agnostic: a user's text description of the drawing is classified the
same way regardless of which LLM (or none) read the image. This makes
"good hint -> generates" reliable even when the live image path is weak, and is
the single source of truth shared by the mock provider and the interpret fallback.
"""
from __future__ import annotations

from app.schemas.drawing_spec import DrawingInterpretationSpec


def interpret_from_hint(hint: str) -> DrawingInterpretationSpec:
    # Implemented in the mock provider (keyword + number extraction); reused here
    # so there is one canonical classifier. No cadquery / no network involved.
    from app.llm.mock_provider import _interpret_from_hint

    return DrawingInterpretationSpec(**_interpret_from_hint(hint))


def hint_is_usable(hint: str | None) -> bool:
    return bool(hint and hint.strip())
