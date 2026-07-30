"""Best-effort OpenAI usage/retry capture for `--live` eval runs.

Nothing in `app/llm/` tracks token usage today (confirmed by inspection: no
`usage`/`tokens`/`cost` field anywhere in the provider code) — the previous
`scripts/run_eval.py` harness reported a hardcoded 0 for exactly this reason.
This module closes that gap WITHOUT touching production provider code: it
wraps the OpenAI SDK client's `responses.create` on a live `OpenAIProvider`
instance after construction, so `response.usage` (input/output/total tokens)
is captured for every call the eval run makes. The mock-provider (default,
offline) path is completely unaffected — this module is only ever invoked
when `--live` is passed.
"""
from __future__ import annotations

from dataclasses import dataclass

# Rough $/1K-token estimates (input, output). Update as pricing changes; a
# missing model estimates 0 rather than guessing, and the report says so.
_COST_PER_1K_USD: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.00015, 0.0006),
    "gpt-4o": (0.0025, 0.01),
    "gpt-4.1": (0.002, 0.008),
}


@dataclass
class UsageRecord:
    calls: int = 0
    retries: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    model: str | None = None

    def estimated_cost_usd(self) -> float:
        rates = _COST_PER_1K_USD.get(self.model or "")
        if not rates:
            return 0.0
        in_rate, out_rate = rates
        return round(
            (self.prompt_tokens / 1000) * in_rate + (self.completion_tokens / 1000) * out_rate,
            6,
        )


_current: UsageRecord | None = None


def begin(model: str | None) -> UsageRecord:
    global _current
    _current = UsageRecord(model=model)
    return _current


def current() -> UsageRecord | None:
    return _current


def install(provider) -> bool:
    """Monkeypatch a live OpenAIProvider instance's SDK client to record
    usage/retries. Returns False (no-op) for anything that isn't a real
    OpenAI client — safe to call unconditionally."""
    client = getattr(provider, "_client", None)
    create = getattr(getattr(client, "responses", None), "create", None)
    if create is None or getattr(create, "_eval_wrapped", False):
        return False

    def _wrapped(**kwargs):
        rec = _current
        try:
            resp = create(**kwargs)
        except Exception:
            if rec is not None:
                rec.retries += 1
            raise
        if rec is not None:
            rec.calls += 1
            usage = getattr(resp, "usage", None)
            if usage is not None:
                rec.prompt_tokens += getattr(usage, "input_tokens", 0) or 0
                rec.completion_tokens += getattr(usage, "output_tokens", 0) or 0
                rec.total_tokens += getattr(usage, "total_tokens", 0) or 0
        return resp

    _wrapped._eval_wrapped = True
    client.responses.create = _wrapped
    return True
