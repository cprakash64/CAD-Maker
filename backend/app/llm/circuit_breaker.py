"""LLM circuit breaker: failure-rate AND daily-spend tripped.

Per-process, in-memory state (same model as app.rate_limit — good enough for
a single-VPS deployment; each uvicorn worker enforces independently). Two
independent trip conditions, either one opens the circuit:

  * consecutive failures >= LLM_CIRCUIT_BREAKER_FAILURE_THRESHOLD -- gives
    graceful degraded mode when OpenAI is down: once open, calls fail FAST
    with a clean LLMUnavailableError instead of waiting out a timeout on
    every single request while the provider is unreachable.
  * estimated daily spend >= LLM_COST_DAILY_CAP_USD -- a blunt backstop
    against a runaway loop or billing surprise (docs/ops/observability.md).
    Resets at UTC midnight.

Deterministic parts of the pipeline (templates, gears, precision routes) never
call the LLM at all, so they keep working even while this circuit is open —
see docs/ops/incident-response.md#openai-outage.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.config import settings


@dataclass
class _State:
    consecutive_failures: int = 0
    opened_until: float = 0.0  # time.monotonic() seconds; 0 == not open on failures
    daily_cost_usd: float = 0.0
    cost_day: str = ""  # UTC "YYYY-MM-DD" the accumulator belongs to
    cost_tripped: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)


_state = _State()


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _roll_day_if_needed() -> None:
    today = _today()
    if _state.cost_day != today:
        _state.cost_day = today
        _state.daily_cost_usd = 0.0
        _state.cost_tripped = False


def record_success() -> None:
    with _state.lock:
        _state.consecutive_failures = 0
        _state.opened_until = 0.0


def record_failure() -> None:
    if not settings.llm_circuit_breaker_enabled:
        return
    with _state.lock:
        _state.consecutive_failures += 1
        if _state.consecutive_failures >= settings.llm_circuit_breaker_failure_threshold:
            _state.opened_until = time.monotonic() + settings.llm_circuit_breaker_cooldown_seconds


def record_cost(usd: float) -> None:
    if usd <= 0:
        return
    with _state.lock:
        _roll_day_if_needed()
        _state.daily_cost_usd += usd
        cap = settings.llm_cost_daily_cap_usd
        if cap > 0 and _state.daily_cost_usd >= cap:
            _state.cost_tripped = True


def is_open() -> tuple[bool, str | None]:
    """(open, reason). reason is None when not open."""
    with _state.lock:
        _roll_day_if_needed()
        if _state.cost_tripped:
            return True, "daily model-spend cap reached"
        if _state.opened_until and time.monotonic() < _state.opened_until:
            return True, "too many recent provider failures"
        return False, None


def state() -> dict:
    """Snapshot for /metrics and /ready — never raises, never blocks long."""
    with _state.lock:
        _roll_day_if_needed()
        open_, reason = _state.cost_tripped, "daily model-spend cap reached"
        if not open_:
            open_ = bool(_state.opened_until and time.monotonic() < _state.opened_until)
            reason = "too many recent provider failures" if open_ else None
        return {
            "open": open_,
            "reason": reason,
            "consecutive_failures": _state.consecutive_failures,
            "daily_cost_usd": round(_state.daily_cost_usd, 4),
            "daily_cost_cap_usd": settings.llm_cost_daily_cap_usd,
        }


def reset() -> None:
    """Test-only: clear all breaker state."""
    with _state.lock:
        _state.consecutive_failures = 0
        _state.opened_until = 0.0
        _state.daily_cost_usd = 0.0
        _state.cost_day = ""
        _state.cost_tripped = False
