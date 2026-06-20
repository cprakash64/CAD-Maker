"""Lightweight structured logging for beta observability.

Emits one JSON line per event so latency, provider usage, and failure modes are
queryable from logs. Never logs API keys or raw secrets — callers pass only the
fields defined here.
"""
from __future__ import annotations

import json
import logging
import time
from contextlib import contextmanager
from typing import Any

from app.config import settings

logger = logging.getLogger("sourcecad")
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(_handler)
    logger.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))

# Keys that must never appear in a log payload.
_SECRET_KEYS = {
    "api_key",
    "openai_api_key",
    "anthropic_api_key",
    "password",
    "password_hash",
    "access_token",
    "token",
    "secret",
    "jwt_secret",
    "s3_secret_access_key",
}


def _scrub(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        k: ("***" if k.lower() in _SECRET_KEYS else v) for k, v in payload.items()
    }


def log_event(event: str, **fields: Any) -> None:
    record = {"event": event, **_scrub(fields)}
    try:
        logger.info(json.dumps(record, default=str))
    except (TypeError, ValueError):
        logger.info(json.dumps({"event": event, "error": "unserializable_fields"}))


@contextmanager
def timed(event: str, **fields: Any):
    """Context manager that logs an event with latency_ms and ok/error status."""
    start = time.perf_counter()
    try:
        yield
    except Exception as exc:  # noqa: BLE001 - re-raised after logging
        log_event(
            event,
            status="error",
            error_type=type(exc).__name__,
            latency_ms=round((time.perf_counter() - start) * 1000, 2),
            **fields,
        )
        raise
    else:
        log_event(
            event,
            status="ok",
            latency_ms=round((time.perf_counter() - start) * 1000, 2),
            **fields,
        )


def elapsed_ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 2)
