"""Per-request LLM token/cost usage accumulator.

A generation may issue several LLM calls (model-fallback chain, a repair
pass) across app.llm.openai_provider.OpenAIProvider._run. This accumulates
the REAL usage from every call within one ``track_usage()`` block, so a
caller (a worker job handler, or a router for an inline call) can read back
the true total for app.cost_control.service.commit_reservation's
reconciliation -- never the pre-flight ESTIMATE, which is only a
conservative upper bound used for the reservation check itself.

ContextVar-based, same isolation as app.llm.budget (Starlette runs sync
endpoints in a threadpool that copies context; a worker job runs in its own
freshly-spawned subprocess, so there is never cross-request leakage either
way).
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from app.cost_control.estimator import actual_cost_cents as _actual_cost_cents


@dataclass
class _Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cost_cents: int = 0


_usage: ContextVar["_Usage | None"] = ContextVar("llm_usage", default=None)


@contextmanager
def track_usage():
    """Accumulate real LLM usage for the duration of the block."""
    token = _usage.set(_Usage())
    try:
        yield
    finally:
        _usage.reset(token)


def record(model: str, input_tokens: int, output_tokens: int) -> None:
    """Called from app.llm.openai_provider._run after every real API
    response. A no-op outside a track_usage() block (e.g. calls made without
    cost-control wiring, or the mock provider, which never calls this)."""
    acc = _usage.get()
    if acc is None:
        return
    acc.input_tokens += input_tokens
    acc.output_tokens += output_tokens
    acc.cost_cents += _actual_cost_cents(model, input_tokens, output_tokens)


def snapshot() -> tuple[int, int]:
    """(total_tokens, cost_cents) accumulated so far in the active
    track_usage() block. (0, 0) outside one."""
    acc = _usage.get()
    if acc is None:
        return (0, 0)
    return (acc.input_tokens + acc.output_tokens, acc.cost_cents)
