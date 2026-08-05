"""Internal operations endpoints: liveness/readiness probes and /metrics.

/health (liveness, in app.main) stays a trivial "is the process up" check.
/ready here actually probes the dependencies a request needs (DB, storage) —
a load balancer or systemd should route traffic / consider the service
healthy based on THIS, not /health. /metrics exposes Prometheus text format,
gated by OPS_API_TOKEN (required in production — see
Settings.production_problems) so cost/queue/business data isn't publicly
readable.
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Header, HTTPException, Response
from sqlalchemy import text

from app.config import settings
from app.database import SessionLocal
from app.metrics import render_latest
from app.rate_limit import rate_limit
from app.observability import log_event

router = APIRouter(tags=["ops"])


def _check_db() -> tuple[bool, str | None]:
    try:
        db = SessionLocal()
        try:
            db.execute(text("SELECT 1"))
            return True, None
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001 - report, never raise from a probe
        return False, type(exc).__name__


def _check_storage() -> tuple[bool, str | None]:
    try:
        from app.storage.storage import get_storage

        storage = get_storage()
        if settings.storage_backend == "local":
            import os

            if not os.access(settings.storage_dir, os.W_OK):
                return False, "storage_dir not writable"
            return True, None
        # S3: a live network round-trip on every readiness probe is too
        # expensive/flaky to gate traffic on; configuration presence (bucket
        # set, get_storage() constructed without raising) is the check.
        _ = storage
        return True, None
    except Exception as exc:  # noqa: BLE001
        return False, type(exc).__name__


def _require_ops_token(authorization: str | None) -> None:
    """Bearer-token gate for endpoints that reveal cost/queue/business data.

    In dev/test (OPS_API_TOKEN unset), access is open for local convenience —
    production_problems() refuses to boot production without a token set, so
    this can never be silently open in a real deployment.
    """
    if not settings.ops_api_token:
        return
    expected = f"Bearer {settings.ops_api_token}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="Missing or invalid ops token")


@router.get("/ready", dependencies=[rate_limit("read")])
def ready() -> Response:
    """Readiness: are dependencies actually reachable right now. Public (no
    secrets in the body — only per-check booleans), so a load balancer can
    poll it without credentials."""
    start = time.perf_counter()
    db_ok, db_err = _check_db()
    storage_ok, storage_err = _check_storage()
    checks = {
        "database": {"ok": db_ok, **({"error": db_err} if db_err else {})},
        "storage": {"ok": storage_ok, **({"error": storage_err} if storage_err else {})},
    }
    all_ok = db_ok and storage_ok
    body = {"ready": all_ok, "checks": checks}
    if not all_ok:
        log_event("readiness_check_failed", checks=checks,
                  latency_ms=round((time.perf_counter() - start) * 1000, 2))
    import json

    return Response(
        content=json.dumps(body),
        media_type="application/json",
        status_code=200 if all_ok else 503,
    )


@router.get("/metrics", dependencies=[rate_limit("read")])
def metrics(authorization: str | None = Header(default=None)) -> Response:
    _require_ops_token(authorization)
    return Response(content=render_latest(), media_type="text/plain; version=0.0.4")
