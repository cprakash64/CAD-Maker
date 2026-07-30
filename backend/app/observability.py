"""Lightweight structured logging for production observability.

Emits one JSON line per event so latency, provider usage, and failure modes are
queryable from logs. Never logs API keys, raw secrets, authorization headers,
signed URLs, complete uploaded drawings, or proprietary prompts — callers pass
only the fields defined here, and `_scrub` is a second, defense-in-depth layer
that redacts by key name AND by value pattern (see docs/ops/observability.md).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from app.config import settings

logger = logging.getLogger("sourcecad")
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(_handler)
    logger.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))

# Keys that must never appear in a log payload (exact key name, case-insensitive).
_SECRET_KEYS = {
    "api_key",
    "openai_api_key",
    "password",
    "password_hash",
    "access_token",
    "token",
    "secret",
    "jwt_secret",
    "s3_secret_access_key",
    "authorization",
    "auth_header",
    "bearer",
    "cookie",
    "set_cookie",
    "signed_url",
    "presigned_url",
}

# Value-pattern redaction: a field can slip a secret through even under a safe
# key name (e.g. a URL logged as `url=...` that happens to carry a signature).
# Each pattern's matched span is replaced with "***", not the whole value, so
# the rest of a long string (e.g. a path) stays useful for debugging.
_VALUE_PATTERNS = [
    re.compile(r"[Bb]earer\s+[A-Za-z0-9\-_.~+/]+=*"),          # Authorization: Bearer <token>
    re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"),  # a raw JWT
    re.compile(r"://[^/\s:@]+:[^/\s:@]+@"),                     # scheme://user:pass@host
    # Signed-URL query params (S3/GCS/Azure conventions) — strip the whole
    # query string rather than trying to pick out just the signature, since a
    # signed URL is only as private as every one of its params together.
    re.compile(r"\?(?=.*(?:Signature|X-Amz-Signature|X-Amz-Credential)=)[^\s\"']+", re.IGNORECASE),
]
_MAX_VALUE_LEN = 2000  # defense against accidentally dumping a whole file/body


def _scrub_value(v: Any) -> Any:
    if isinstance(v, str):
        for pat in _VALUE_PATTERNS:
            v = pat.sub("***", v)
        if len(v) > _MAX_VALUE_LEN:
            v = v[:_MAX_VALUE_LEN] + f"...<{len(v) - _MAX_VALUE_LEN} more chars truncated>"
        return v
    if isinstance(v, dict):
        return _scrub(v)
    if isinstance(v, (list, tuple)):
        return [_scrub_value(x) for x in v]
    return v


def _scrub(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        k: ("***" if k.lower() in _SECRET_KEYS else _scrub_value(v))
        for k, v in payload.items()
    }


# --- request-id propagation --------------------------------------------------
# Set once per HTTP request (app.main's middleware) or once per worker job
# (app.worker.runner); every log_event call in between picks it up
# automatically, so events don't need to thread request_id through every
# function signature to stay correlatable.
_request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


def set_request_id(value: str | None) -> None:
    _request_id_ctx.set(value)


def get_request_id() -> str | None:
    return _request_id_ctx.get()


def pseudonymize(value: str | None) -> str | None:
    """A stable, non-reversible-without-the-secret form of an id (user/tenant
    id), safe for ordinary logs. Deterministic — the same input always
    produces the same output — so events/metrics can still be correlated by
    this value without a log reader being able to recover the raw id."""
    if not value:
        return value
    secret = settings.telemetry_hash_secret or settings.jwt_secret
    return hmac.new(secret.encode(), value.encode(), hashlib.sha256).hexdigest()[:16]


def content_fingerprint(text: str | None) -> str | None:
    """A content-addressed, non-reversible fingerprint (e.g. of a prompt) —
    lets logs/metrics correlate repeated or versioned content WITHOUT ever
    storing the content itself in ordinary telemetry. See
    docs/ops/data-retention.md for where the real content is retained."""
    if not text:
        return None
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def log_event(event: str, **fields: Any) -> None:
    rid = get_request_id()
    if rid and "request_id" not in fields:
        fields["request_id"] = rid
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


def log_exception_redacted(message: str, exc: BaseException) -> None:
    """Server-side-only traceback logging with the same value-pattern
    redaction as log_event, for the rare case (main.py's last-resort
    exception handlers) where the full traceback is genuinely useful for
    debugging but MUST NOT carry a credential embedded in an exception's own
    message (e.g. a DB driver error echoing back a connection string). Never
    sent to the client — those handlers return a generic detail message."""
    import traceback

    text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    logger.error(_scrub_value(f"{message}\n{text}"))
