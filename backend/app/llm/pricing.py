"""Estimated OpenAI cost from token usage.

These are ESTIMATES for cost-spike detection and the cost/request,
cost/validated-result, cost/downloaded-result metrics — not a billing
reconciliation. Verify against your actual OpenAI invoice periodically and
update `_PRICE_PER_1M_TOKENS` when it drifts (see
https://openai.com/api/pricing/); this table is not fetched live, so nothing
here can silently start under/over-reporting spend without the corresponding
price actually having changed. If a model isn't listed, a conservative
default rate is used and the miss is logged once.
"""
from __future__ import annotations

from app.observability import log_event

# {model: (input_$_per_1M_tokens, output_$_per_1M_tokens)}. Prices last
# checked/updated: 2026-07 (see module docstring).
_PRICE_PER_1M_TOKENS: dict[str, tuple[float, float]] = {
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4.1": (2.00, 8.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1-nano": (0.10, 0.40),
}
# Used for any model not in the table above (e.g. a newly-released model id
# configured before this table is updated) — deliberately conservative
# (priced at the higher end of known models) so a cost spike is more likely
# to be OVER-estimated than silently missed.
_DEFAULT_PRICE_PER_1M_TOKENS = (5.00, 15.00)

_warned_models: set[str] = set()


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    prices = _PRICE_PER_1M_TOKENS.get(model)
    if prices is None:
        prices = _DEFAULT_PRICE_PER_1M_TOKENS
        if model not in _warned_models:
            _warned_models.add(model)
            log_event("llm_pricing_unknown_model", model=model,
                      using_default_per_1m=_DEFAULT_PRICE_PER_1M_TOKENS)
    in_price, out_price = prices
    return round((input_tokens / 1_000_000) * in_price
                + (output_tokens / 1_000_000) * out_price, 6)
