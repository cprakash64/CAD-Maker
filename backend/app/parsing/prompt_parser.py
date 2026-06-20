"""Turn a natural-language prompt into a validated ParseResult.

Flow: provider returns raw JSON -> we validate it into a DesignSpec (Pydantic).
If validation fails we retry ONCE, feeding the validation errors back to the
provider (real providers re-prompt the model; the offline mock declines). If it
still fails we return a useful clarification question rather than a 500.
"""
from __future__ import annotations

from pydantic import ValidationError

from app.cad.base import CadGenerationError
from app.cad.registry import all_templates, get_template
from app.llm.base import LLMProvider
from app.llm.factory import get_provider
from app.parsing.policy import default_assumption_notes, split_missing
from app.schemas.design_spec import DesignSpec, ParseResult


def _spec_from_raw(raw: dict) -> DesignSpec:
    # Preserve a long/descriptive material string as visual_notes (the material
    # field itself is normalized to a short keyword by DesignSpec).
    raw_material = raw.get("material")
    visual = raw.get("visual_notes") or raw.get("finish") or raw.get("appearance")
    if isinstance(raw_material, str) and len(raw_material) > 64 and not visual:
        visual = raw_material
    return DesignSpec(
        object_type=raw.get("object_type"),
        units=raw.get("units", "mm"),
        manufacturing_method=raw.get("manufacturing_method", "fdm_3d_print"),
        material=raw_material if raw_material else "PLA",
        dimensions=raw.get("dimensions", {}),
        holes=raw.get("holes", []),
        fillet_radius=raw.get("fillet_radius"),
        chamfer_size=raw.get("chamfer_size"),
        notes=(raw.get("notes") or None),
        visual_notes=(visual[:4000] if isinstance(visual, str) else None),
        feature_graph=raw.get("feature_graph"),
    )


def parse_prompt(prompt: str, provider: LLMProvider | None = None) -> ParseResult:
    if not prompt or not prompt.strip():
        return ParseResult(
            missing_required=["prompt"],
            clarification_question="Please describe the part you want to make.",
        )

    provider = provider or get_provider()
    raw = provider.parse_prompt(prompt)

    missing = list(raw.get("missing_required") or [])
    clarification = raw.get("clarification_question")
    assumptions = list(raw.get("assumptions") or [])
    critical, non_critical = split_missing(missing)

    object_type = raw.get("object_type")

    # CRITICAL: object type unknown or unsupported -> must clarify.
    if not object_type or object_type not in all_templates():
        if "object_type" not in critical:
            critical.append("object_type")
        return ParseResult(
            missing_required=critical or ["object_type"],
            clarification_question=(
                clarification
                or "I couldn't tell what kind of part to make. Which is it — a "
                "bracket, enclosure, drill jig, pipe clamp, gear, crankshaft, ...?"
            ),
            assumptions=assumptions,
            raw_llm_output=raw,
        )

    # Build the spec. Non-critical "missing_required" is IGNORED here — the
    # template defaults cover it, and we record it as an assumption instead.
    try:
        spec = _spec_from_raw(raw)
        get_template(spec.object_type).resolve(spec)  # required-dim / range check
    except (ValidationError, CadGenerationError) as exc:
        # Retry once with the errors fed back (real providers re-prompt).
        repaired = provider.repair(prompt, raw, _format(exc)) if isinstance(
            exc, ValidationError
        ) else None
        if repaired is not None:
            try:
                spec = _spec_from_raw(repaired)
                get_template(spec.object_type).resolve(spec)
                return _generated(spec, repaired, non_critical)
            except (ValidationError, CadGenerationError):
                pass
        # Genuinely impossible/contradictory -> critical clarification.
        return ParseResult(
            missing_required=["valid_design_spec"],
            clarification_question=(
                "Those values don't form a buildable part ("
                + _short(exc)
                + "). Could you adjust the key dimensions?"
            ),
            assumptions=assumptions,
            raw_llm_output=raw,
        )

    # Buildable. Fold any non-critical missing fields into assumptions and go.
    return _generated(spec, raw, non_critical, base_assumptions=assumptions)


def _generated(
    spec: DesignSpec, raw: dict, non_critical: list[str], base_assumptions: list[str] | None = None
) -> ParseResult:
    assumptions = list(base_assumptions or raw.get("assumptions") or [])
    assumptions += default_assumption_notes(non_critical)
    return ParseResult(spec=spec, assumptions=assumptions, raw_llm_output=raw)


def _short(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return _short_error(exc)
    return str(exc)


def _format(exc: ValidationError) -> str:
    return _format_errors(exc)


def _short_error(exc: ValidationError) -> str:
    first = exc.errors()[0]
    loc = ".".join(str(p) for p in first.get("loc", []))
    return f"{loc}: {first.get('msg', 'invalid')}"


def _format_errors(exc: ValidationError) -> str:
    return "; ".join(
        ".".join(str(p) for p in e.get("loc", [])) + ": " + e.get("msg", "")
        for e in exc.errors()
    )
