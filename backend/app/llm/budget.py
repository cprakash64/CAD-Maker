"""Per-request wall-clock budget for a single generation.

A generation may issue several LLM calls (model fallback chain + one repair
pass). Without a shared budget those can stack up to many minutes. We set one
monotonic deadline at the start of a request (via ``generation_budget``) and the
providers consult it before each call, capping per-call timeouts and aborting
cleanly once the budget is spent.

Implemented with a ``ContextVar`` so it is isolated per request/thread (Starlette
runs sync endpoints in a threadpool that copies the context).
"""
from __future__ import annotations

import time
from contextlib import contextmanager
from contextvars import ContextVar

_deadline: ContextVar[float | None] = ContextVar("generation_deadline", default=None)


@contextmanager
def generation_budget(seconds: float | None):
    """Set a generation deadline for the duration of the block.

    ``seconds=None`` or ``<= 0`` disables the budget (no deadline)."""
    token = _deadline.set(
        time.monotonic() + seconds if seconds and seconds > 0 else None
    )
    try:
        yield
    finally:
        _deadline.reset(token)


def remaining_seconds() -> float | None:
    """Seconds left in the budget, or None when no budget is active."""
    dl = _deadline.get()
    if dl is None:
        return None
    return dl - time.monotonic()


def budget_expired() -> bool:
    """True only when a budget is active and has run out."""
    rem = remaining_seconds()
    return rem is not None and rem <= 0
