"""LLM circuit breaker: failure-rate and daily-spend tripping."""
from app.config import settings
from app.llm import circuit_breaker


def test_closed_by_default():
    open_, reason = circuit_breaker.is_open()
    assert open_ is False
    assert reason is None


def test_trips_open_after_consecutive_failure_threshold(monkeypatch):
    monkeypatch.setattr(settings, "llm_circuit_breaker_failure_threshold", 3)
    for _ in range(2):
        circuit_breaker.record_failure()
    assert circuit_breaker.is_open() == (False, None)
    circuit_breaker.record_failure()
    open_, reason = circuit_breaker.is_open()
    assert open_ is True
    assert "failures" in reason


def test_success_resets_consecutive_failure_count(monkeypatch):
    monkeypatch.setattr(settings, "llm_circuit_breaker_failure_threshold", 3)
    circuit_breaker.record_failure()
    circuit_breaker.record_failure()
    circuit_breaker.record_success()
    circuit_breaker.record_failure()
    circuit_breaker.record_failure()
    # Only 2 consecutive since the reset -- still closed.
    assert circuit_breaker.is_open() == (False, None)


def test_cooldown_expires(monkeypatch):
    monkeypatch.setattr(settings, "llm_circuit_breaker_failure_threshold", 1)
    monkeypatch.setattr(settings, "llm_circuit_breaker_cooldown_seconds", 0)
    circuit_breaker.record_failure()
    open_, _ = circuit_breaker.is_open()
    # cooldown=0 means it's already expired by the time we check.
    assert open_ is False


def test_disabled_never_trips_on_failures(monkeypatch):
    monkeypatch.setattr(settings, "llm_circuit_breaker_enabled", False)
    monkeypatch.setattr(settings, "llm_circuit_breaker_failure_threshold", 1)
    circuit_breaker.record_failure()
    circuit_breaker.record_failure()
    circuit_breaker.record_failure()
    assert circuit_breaker.is_open() == (False, None)


def test_trips_open_on_daily_cost_cap(monkeypatch):
    monkeypatch.setattr(settings, "llm_cost_daily_cap_usd", 1.0)
    circuit_breaker.record_cost(0.5)
    assert circuit_breaker.is_open() == (False, None)
    circuit_breaker.record_cost(0.6)
    open_, reason = circuit_breaker.is_open()
    assert open_ is True
    assert "spend" in reason


def test_cost_cap_disabled_when_zero(monkeypatch):
    monkeypatch.setattr(settings, "llm_cost_daily_cap_usd", 0)
    circuit_breaker.record_cost(10_000.0)
    assert circuit_breaker.is_open() == (False, None)


def test_state_snapshot_reports_cost_and_failures(monkeypatch):
    monkeypatch.setattr(settings, "llm_circuit_breaker_failure_threshold", 5)
    circuit_breaker.record_failure()
    circuit_breaker.record_cost(1.5)
    snap = circuit_breaker.state()
    assert snap["consecutive_failures"] == 1
    assert snap["daily_cost_usd"] == 1.5
    assert snap["open"] is False


def test_reset_clears_everything():
    circuit_breaker.record_failure()
    circuit_breaker.record_cost(5.0)
    circuit_breaker.reset()
    snap = circuit_breaker.state()
    assert snap["consecutive_failures"] == 0
    assert snap["daily_cost_usd"] == 0.0
    assert snap["open"] is False
