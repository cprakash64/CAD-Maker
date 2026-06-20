"""Observability: structured events are emitted and secrets are never logged."""
import json
import logging

from app.observability import log_event, logger, timed


def test_log_event_emits_json(caplog):
    with caplog.at_level(logging.INFO, logger="sourcecad"):
        log_event("geometry_generated", design_id="abc", provider="mock", latency_ms=12.3)
    record = json.loads(caplog.records[-1].message)
    assert record["event"] == "geometry_generated"
    assert record["design_id"] == "abc"
    assert record["provider"] == "mock"


def test_secrets_are_scrubbed(caplog):
    with caplog.at_level(logging.INFO, logger="sourcecad"):
        log_event(
            "suspicious",
            openai_api_key="sk-secret",
            password="hunter2",
            access_token="jwt.token.here",
            safe_field="ok",
        )
    record = json.loads(caplog.records[-1].message)
    assert record["openai_api_key"] == "***"
    assert record["password"] == "***"
    assert record["access_token"] == "***"
    assert record["safe_field"] == "ok"


def test_timed_logs_latency_and_status(caplog):
    with caplog.at_level(logging.INFO, logger="sourcecad"):
        with timed("unit_op", design_id="d1"):
            pass
    record = json.loads(caplog.records[-1].message)
    assert record["event"] == "unit_op"
    assert record["status"] == "ok"
    assert "latency_ms" in record


def test_timed_logs_error_then_reraises(caplog):
    import pytest

    with caplog.at_level(logging.INFO, logger="sourcecad"):
        with pytest.raises(ValueError):
            with timed("failing_op"):
                raise ValueError("boom")
    record = json.loads(caplog.records[-1].message)
    assert record["status"] == "error"
    assert record["error_type"] == "ValueError"


def test_logger_is_named_and_configured():
    assert logger.name == "sourcecad"
    assert logger.handlers
