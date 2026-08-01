"""Conservative pre-flight cost estimates, BEFORE any LLM call is made.

These are deliberately flat, conservative upper bounds per operation type --
not a precise tokenizer count (the request text/image isn't even fully
assembled yet at reservation time for some paths, and a real tokenizer
would add a dependency + latency for marginal precision gain). Like
app.llm.pricing's own unknown-model fallback, the philosophy is "estimate
high, so a cost spike is caught rather than missed" -- app.llm.pricing then
converts the estimate to a $ figure using the SAME price table real usage
is billed against, so a request that can never fit the per-request budget is
rejected before it costs anything.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.config import settings
from app.llm.pricing import estimate_cost_usd

# {operation_type: (estimated_input_tokens, estimated_output_tokens)}.
# Vision calls (drawing_interpret / the vision leg of drawing_to_cad and
# drawing_generate) are the priciest -- a "detail: high" image alone can run
# several hundred to ~1500 tokens depending on resolution, on top of the
# schema + prompt text -- so they get the largest input-token estimate.
_OPERATION_ESTIMATES: dict[str, tuple[int, int]] = {
    "design_create": (4000, 2000),
    "modify": (2500, 1000),
    "drawing_interpret": (3000, 1500),
    "drawing_to_cad": (3500, 1800),
    "drawing_generate": (3500, 1800),
}
_DEFAULT_ESTIMATE = (4000, 2000)


@dataclass(frozen=True)
class CostEstimate:
    operation_type: str
    model: str
    estimated_input_tokens: int
    estimated_output_tokens: int
    estimated_total_tokens: int
    estimated_cost_cents: int  # ceiling-rounded, never under-estimated


def _model_for(operation_type: str) -> str:
    if operation_type in ("design_create",) or operation_type.startswith("drawing"):
        return settings.cad_llm_model or settings.openai_model
    return settings.openai_model


def estimate_request_cost(operation_type: str) -> CostEstimate:
    """A conservative, pre-call estimate for one request of this type."""
    in_tokens, out_tokens = _OPERATION_ESTIMATES.get(operation_type, _DEFAULT_ESTIMATE)
    model = _model_for(operation_type)
    usd = estimate_cost_usd(model, in_tokens, out_tokens)
    # Ceiling to the cent -- a fractional cent must never round DOWN to a
    # smaller reservation than the estimate actually implies.
    cents = -(-round(usd * 100, 6) // 1)  # ceiling division, integer result
    return CostEstimate(
        operation_type=operation_type,
        model=model,
        estimated_input_tokens=in_tokens,
        estimated_output_tokens=out_tokens,
        estimated_total_tokens=in_tokens + out_tokens,
        estimated_cost_cents=int(cents),
    )


def actual_cost_cents(model: str, input_tokens: int, output_tokens: int) -> int:
    """Same ceiling-rounding rule applied to REAL usage, for reconciliation."""
    usd = estimate_cost_usd(model, input_tokens, output_tokens)
    cents = -(-round(usd * 100, 6) // 1)
    return int(cents)
