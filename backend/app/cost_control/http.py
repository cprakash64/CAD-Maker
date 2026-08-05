"""Structured, safe HTTP error responses for every cost-control rejection
(docs/operations/cost-control-architecture.md Step 5).

Every response body has the SAME shape:

    {"code": "...", "message": "...", "retry_after_seconds": N|null,
     "remaining_quota": {...}|null}

``code`` is machine-readable (stable across releases -- a frontend can
switch on it). ``message`` is always safe to show a user. Neither ever
includes a stack trace, an internal exception repr, or enough detail about
the budget internals to help an attacker tune requests to just barely evade
a limit (e.g. never "you have used 97 of 100" -- see get_remaining_quota's
deliberately coarse shape).
"""
from __future__ import annotations

from fastapi import HTTPException

from app.cost_control.service import (
    AccountCostCapExceeded,
    CostControlError,
    DailyLimitExceeded,
    DrawingLimitExceeded,
    EmergencyStopActive,
    GenerationDisabledForAccount,
    GlobalBudgetExceeded,
    MonthlyLimitExceeded,
    RequestTooExpensive,
)

_STATUS_BY_ERROR: dict[type[CostControlError], int] = {
    EmergencyStopActive: 503,
    GenerationDisabledForAccount: 403,
    RequestTooExpensive: 422,
    DailyLimitExceeded: 429,
    MonthlyLimitExceeded: 429,
    DrawingLimitExceeded: 429,
    AccountCostCapExceeded: 429,
    GlobalBudgetExceeded: 503,
}


def to_http_exception(exc: CostControlError) -> HTTPException:
    status_code = _STATUS_BY_ERROR.get(type(exc), 429)
    body = {
        "code": exc.code,
        "message": exc.message,
        "retry_after_seconds": exc.retry_after_seconds,
        "remaining_quota": None,
    }
    headers = {}
    if exc.retry_after_seconds:
        headers["Retry-After"] = str(exc.retry_after_seconds)
    return HTTPException(status_code=status_code, detail=body, headers=headers or None)


# --- other safe structured contracts referenced by docs/operations/
# cost-control-architecture.md Step 5, not raised by CostControlError itself
# (these wrap OTHER existing exceptions from job_service / the LLM layer so
# every rejection in the system uses the same response shape) ---------------

def provider_outage_response(message: str, *, retry_after_seconds: int = 30) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={"code": "PROVIDER_UNAVAILABLE", "message": message,
               "retry_after_seconds": retry_after_seconds, "remaining_quota": None},
        headers={"Retry-After": str(retry_after_seconds)},
    )


def storage_quota_response(message: str) -> HTTPException:
    return HTTPException(
        status_code=429,
        detail={"code": "STORAGE_QUOTA_REACHED", "message": message,
               "retry_after_seconds": None, "remaining_quota": None},
    )


def concurrent_limit_response(message: str) -> HTTPException:
    return HTTPException(
        status_code=429,
        detail={"code": "CONCURRENT_LIMIT_REACHED", "message": message,
               "retry_after_seconds": None, "remaining_quota": None},
    )


def retry_exhausted_response(message: str) -> HTTPException:
    return HTTPException(
        status_code=422,
        detail={"code": "RETRY_LIMIT_EXHAUSTED", "message": message,
               "retry_after_seconds": None, "remaining_quota": None},
    )
