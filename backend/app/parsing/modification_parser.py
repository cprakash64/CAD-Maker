"""Parse an edit prompt into a validated DesignModification and apply it.

Same safety contract as part creation: the LLM only ever emits a strict
DesignModification (data), never code. We validate it, apply it deterministically
to the current spec, and re-validate the resulting DesignSpec.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from pydantic import ValidationError

from app.llm.base import LLMProvider
from app.llm.factory import get_provider
from app.schemas.design_spec import (
    DesignModification,
    DesignSpec,
    apply_modification,
)


@dataclass
class ModificationResult:
    spec: Optional[DesignSpec] = None
    clarification_question: Optional[str] = None
    summary: Optional[str] = None


def parse_and_apply(
    prompt: str, current: DesignSpec, provider: LLMProvider | None = None
) -> ModificationResult:
    if not prompt or not prompt.strip():
        return ModificationResult(
            clarification_question="Describe the change you'd like to make."
        )

    provider = provider or get_provider()
    raw = provider.parse_modification(prompt, current.model_dump(mode="json"))

    if raw.get("clarification_question"):
        return ModificationResult(clarification_question=raw["clarification_question"])

    try:
        mod = DesignModification(**raw)
    except ValidationError as exc:
        return ModificationResult(
            clarification_question=(
                "I couldn't apply that change safely. Could you rephrase? ("
                + "; ".join(e.get("msg", "") for e in exc.errors()[:2])
                + ")"
            )
        )

    if mod.is_empty():
        return ModificationResult(
            clarification_question=(
                "I couldn't tell what to change. Try e.g. 'make it 100mm wide' "
                "or 'add rounded edges'."
            )
        )

    try:
        new_spec = apply_modification(current, mod)
    except ValidationError as exc:
        return ModificationResult(
            clarification_question=(
                "That change would make the part invalid ("
                + "; ".join(e.get("msg", "") for e in exc.errors()[:2])
                + "). Try smaller values."
            )
        )

    return ModificationResult(spec=new_spec, summary=mod.summary)
