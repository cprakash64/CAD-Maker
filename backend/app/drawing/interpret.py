"""Drawing-to-CAD Assist: image -> validated interpretation -> (confirmed) DesignSpec.

The provider only ever returns data (a DrawingInterpretationSpec). We validate
it, and only after the user confirms do we map it to a DesignSpec for the normal
trusted-template generation path. Uncertainty is surfaced, never hidden.
"""
from __future__ import annotations

import base64

from pydantic import ValidationError

from app.llm.base import LLMProvider
from app.llm.factory import get_provider
from app.schemas.design_spec import DesignSpec, Hole
from app.schemas.drawing_spec import (
    DrawingAssumption,
    DrawingClarificationQuestion,
    DrawingInterpretationSpec,
)


def interpret_image(
    image_bytes: bytes,
    media_type: str = "image/png",
    provider: LLMProvider | None = None,
    hint: str | None = None,
) -> DrawingInterpretationSpec:
    from app.config import settings
    from app.drawing.hint_classifier import hint_is_usable, interpret_from_hint
    from app.observability import log_event

    provider = provider or get_provider()
    image_b64 = base64.b64encode(image_bytes).decode("ascii")
    log_event(
        "drawing_interpret_request",
        provider=getattr(provider, "name", "?"),
        media_type=media_type,
        image_bytes=len(image_bytes),
        has_hint=hint_is_usable(hint),
    )

    raw: dict | None = None
    provider_error: str | None = None
    try:
        raw = provider.interpret_drawing(image_b64, media_type, hint)
    except NotImplementedError:
        provider_error = (
            "This provider can't read drawings. Set LLM_PROVIDER=openai with an "
            "API key for image understanding."
        )
    except Exception as exc:  # noqa: BLE001 - surface, don't swallow
        provider_error = f"Provider error while reading the drawing: {exc}"
        log_event("drawing_provider_error", error_type=type(exc).__name__, detail=str(exc)[:300])

    interp: DrawingInterpretationSpec | None = None
    if raw is not None:
        if settings.dev_mode:
            log_event("drawing_raw_detected",
                      detected=raw.get("detected_object_type") or raw.get("suggested_object_type"),
                      confidence=raw.get("overall_confidence"))
        try:
            interp = DrawingInterpretationSpec(**raw)
        except ValidationError:
            # Repair pass: long labels / over-long strings must NOT kill parsing.
            try:
                interp = DrawingInterpretationSpec(**_sanitize_drawing_raw(raw))
                log_event("drawing_repaired", ok=True)
            except ValidationError as exc:
                # Preserve a partial interpretation from whatever validated.
                interp = _partial_interpretation(raw, _first_error(exc))
                provider_error = provider_error or f"Partial interpretation ({_first_error(exc)})."
                log_event("drawing_validation_error", detail=_first_error(exc))

    # Hint fallback / merge: a usable correction hint should let generation
    # proceed even if the image path was weak or failed.
    if hint_is_usable(hint):
        hint_interp = interpret_from_hint(hint)
        if interp is None or not interp.is_actionable():
            if provider_error and not hint_interp.provider_error:
                hint_interp.provider_error = provider_error
            return hint_interp
        # Both usable -> keep the more confident interpretation.
        return interp if interp.overall_confidence >= hint_interp.overall_confidence else hint_interp

    if interp is not None:
        if provider_error:
            interp.provider_error = provider_error
        return interp

    # No usable result and no hint -> surface the real error (never silent 0%).
    return DrawingInterpretationSpec(
        suggested_object_type=None,
        detected_object_type="unknown",
        overall_confidence=0.0,
        provider_error=provider_error,
        clarification_questions=[
            DrawingClarificationQuestion(
                field="image",
                question="Couldn't interpret the drawing. Upload a clearer image, or "
                "add a correction hint describing the part and its key dimensions.",
            )
        ],
    )


# Overall-dimension keys each template understands (for mapping).
_TEMPLATE_DIM_KEYS = {
    "rectangular_bracket": ("width", "depth", "thickness"),
    "adapter_plate": ("width", "depth", "thickness", "center_bore"),
    "drill_jig": ("length", "width", "thickness"),
    "l_bracket": ("length", "width", "height", "thickness"),
    "enclosure": ("width", "depth", "height", "wall_thickness"),
    "spacer": ("outer_diameter", "length", "bore_diameter"),
    "flanged_pipe_branch": (
        "main_pipe_outer_diameter_mm", "main_pipe_length_mm",
        "branch_pipe_outer_diameter_mm", "flange_outer_diameter_mm",
        "flange_thickness_mm", "bolt_count", "bolt_hole_diameter_mm",
        "bolt_circle_diameter_mm", "wall_thickness_mm",
    ),
    "simple_gear_or_pulley": (
        "outer_diameter_mm", "thickness_mm", "bore_diameter_mm", "tooth_count",
    ),
    "inline_4_crankshaft": (),  # uses engineered defaults
}


def to_design_spec(interp: DrawingInterpretationSpec) -> DesignSpec | None:
    """Map a confirmed interpretation to a DesignSpec, or None if not actionable."""
    if not interp.is_actionable():
        return None

    ot = interp.suggested_object_type
    allowed = _TEMPLATE_DIM_KEYS.get(ot)
    dims = {
        k: float(v)
        for k, v in interp.overall_dimensions.items()
        if (allowed is None or k in allowed) and v > 0
    }

    holes = _holes_from_callouts(interp, dims)
    return DesignSpec(
        object_type=ot,
        units=interp.units,
        dimensions=dims,
        holes=[h.model_dump() for h in holes],
    )


def _holes_from_callouts(interp: DrawingInterpretationSpec, dims: dict) -> list[Hole]:
    """Lay the drawing's hole callouts along the part's X centerline."""
    diameters: list[float] = []
    for callout in interp.holes:
        if callout.diameter and callout.diameter > 0:
            diameters.extend([callout.diameter] * max(1, callout.count))
    if not diameters:
        return []
    width = dims.get("width") or dims.get("length") or 80.0
    n = len(diameters)
    if n == 1:
        xs = [0.0]
    else:
        span = width / 2.0 - min(width * 0.25, 15.0)
        xs = [(-span + 2 * span * i / (n - 1)) for i in range(n)]
    return [Hole(diameter=d, x=round(x, 2), y=0.0) for d, x in zip(diameters, xs)]


def _first_error(exc: ValidationError) -> str:
    e = exc.errors()[0]
    return ".".join(str(p) for p in e.get("loc", [])) + ": " + e.get("msg", "")


# Field length caps used to sanitize over-long model output (mirror the schema).
_STR_CAPS = {
    "label": 256, "tolerance": 128, "callout": 256, "pattern": 256, "name": 128,
    "description": 1024, "field": 128, "assumption": 1024, "question": 1024,
    "title": 256, "detected_object_type": 128, "template_candidate": 128,
    "unsupported_reason": 1024, "interpretation_rationale": 2000,
}


def _sanitize(obj):
    """Recursively truncate over-long strings so a long label can't reject the
    whole interpretation."""
    if isinstance(obj, dict):
        return {k: (_truncate(k, v) if isinstance(v, str) else _sanitize(v))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(v) for v in obj]
    return obj


def _truncate(key: str, value: str) -> str:
    cap = _STR_CAPS.get(key)
    return value[:cap] if cap and len(value) > cap else value


def _sanitize_drawing_raw(raw: dict) -> dict:
    return _sanitize(raw)


def _partial_interpretation(raw: dict, error: str) -> DrawingInterpretationSpec:
    """Best-effort interpretation when full validation fails — keep whatever is
    safe (type/confidence/overall dims) instead of returning unknown / 0%."""
    safe_dims = {}
    for k, v in (raw.get("overall_dimensions") or {}).items():
        try:
            safe_dims[str(k)[:64]] = float(v)
        except (TypeError, ValueError):
            continue
    return DrawingInterpretationSpec(
        title=str(raw.get("title") or "Drawing")[:256],
        # The field validator normalizes to a supported type or generic.
        suggested_object_type=raw.get("suggested_object_type")
        or raw.get("detected_object_type"),
        detected_object_type=str(raw.get("detected_object_type") or "unknown")[:128],
        overall_dimensions=safe_dims,
        overall_confidence=float(raw.get("overall_confidence") or 0.3),
        partial=True,
        unsupported_reason=None,
        interpretation_rationale=f"Partial interpretation kept after a validation issue ({error}).",
        clarification_questions=[
            DrawingClarificationQuestion(
                field="interpretation",
                question="Some fields couldn't be read cleanly. Confirm the part type "
                "and key dimensions, or add a correction hint.",
            )
        ],
    )


__all__ = [
    "interpret_image",
    "to_design_spec",
    "DrawingAssumption",
]
