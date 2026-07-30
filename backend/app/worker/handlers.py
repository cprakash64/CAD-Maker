"""job_type -> handler dispatch, run INSIDE the isolated worker subprocess.

Each handler reuses EXISTING, already-hardened generation code (design_service
/ app.routers.drawings._run_to_cad_pipeline) completely unchanged -- this
module is pure plumbing: reconstruct arguments from the job's payload, wire
progress callbacks to the job-service-backed row, and shape the result the
same way the old synchronous responses did so callers see an unchanged
contract.

A handler receives ``(payload: dict, ctx: JobContext)`` and either returns a
JSON-serializable result dict (success) or raises (failure; the supervisor
classifies the exception via app.services.job_service.classify_error and
applies the retry policy). Handlers must NEVER swallow an exception into a
"soft success" -- an ambiguous outcome is a failure, not a job.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol


class JobContext(Protocol):
    """What a handler can do to report progress. Implemented by
    app.worker.runner._RunnerContext (real DB writes) and by fakes in tests."""

    def set_stage(self, stage: str, *, status: str | None = None) -> None: ...
    def is_cancel_requested(self) -> bool: ...
    def tmp_dir(self) -> str: ...


class JobCancelled(Exception):
    """Raised by a handler (or the runner around it) when cancellation was
    observed mid-job. Maps to error_category='cancelled'."""


def handle_design_create(payload: dict, ctx: JobContext) -> dict:
    """Mirrors app.routers.designs.create_design's synchronous body exactly
    (docs/adr/0001-job-queue-database-backed.md) -- only WHERE it runs changed."""
    from app.database import SessionLocal
    from app.models import User
    from app.routers.designs import _to_dto
    from app.services import design_service

    db = SessionLocal()
    try:
        ctx.set_stage("compiling", status="running")
        design = design_service.create_design(
            db, payload["prompt"], payload.get("project_id"), payload.get("name"),
            payload["user_id"], route_lock=payload.get("route_lock"))
        ctx.set_stage("exporting", status="exporting")
        user = db.get(User, payload["user_id"])
        dto = _to_dto(design, user, db).model_dump(mode="json")
        return {"design": dto}
    finally:
        db.close()


class _StageShim:
    """Duck-typed for app.services.drawing_jobs.set_stage: needs mutable
    .stage/.message/.updated, plus the optional .on_stage hook it calls."""

    stage = "queued"
    message: str | None = None
    updated: float = 0.0

    def __init__(self, ctx: JobContext):
        self._ctx = ctx

    def on_stage(self, stage: str, message: str | None) -> None:
        self._ctx.set_stage(stage)


def _reraise_http_as_cad_error(exc) -> None:
    # app.routers.drawings' pipeline functions raise HTTPException for
    # user-input problems (unreadable file, bad params) -- these are
    # invalid_input, not a worker/kernel fault, and .detail is already a
    # user-safe message.
    from app.cad.base import CadGenerationError

    raise CadGenerationError(str(exc.detail)) from exc


def handle_drawing_to_cad(payload: dict, ctx: JobContext) -> dict:
    """Mirrors app.routers.drawings._run_to_cad_pipeline exactly -- that
    function is reused unchanged; only its stage callback is redirected from
    the old in-memory DrawingJob to this job's real, DB-backed row."""
    from fastapi import HTTPException

    from app.routers import drawings as drawings_router
    from app.storage.storage import get_storage

    storage = get_storage()
    key = payload["upload_storage_key"]
    data = storage.read(key)
    shim = _StageShim(ctx)
    try:
        try:
            return drawings_router._run_to_cad_pipeline(
                shim, data, payload.get("filename"), payload.get("content_type"),
                payload.get("notes"), payload.get("units"), payload.get("thickness_mm"),
                payload.get("family"), payload["user_id"])
        except HTTPException as exc:
            _reraise_http_as_cad_error(exc)
    finally:
        storage.delete(key)  # the upload is scratch input, never needed again


def handle_drawing_generate(payload: dict, ctx: JobContext) -> dict:
    """Mirrors app.routers.drawings._run_generate_pipeline exactly."""
    from fastapi import HTTPException

    from app.routers import drawings as drawings_router
    from app.storage.storage import get_storage

    storage = get_storage()
    key = payload["upload_storage_key"]
    data = storage.read(key)
    shim = _StageShim(ctx)
    try:
        try:
            return drawings_router._run_generate_pipeline(
                shim, data, payload["content_type"], payload.get("hint"),
                payload["user_id"])
        except HTTPException as exc:
            _reraise_http_as_cad_error(exc)
    finally:
        storage.delete(key)


def _test_echo(payload: dict, ctx: JobContext) -> dict:
    ctx.set_stage("compiling", status="running")
    ctx.set_stage("exporting", status="exporting")
    return {"echo": payload}


def _test_sleep(payload: dict, ctx: JobContext) -> dict:
    import time

    time.sleep(float(payload.get("seconds", 30)))
    return {"echo": payload}


def _test_crash(payload: dict, ctx: JobContext) -> dict:
    import os
    import signal

    os.kill(os.getpid(), signal.SIGSEGV)
    return {}  # unreachable


def _test_raise(payload: dict, ctx: JobContext) -> dict:
    raise ValueError(payload.get("message", "simulated failure"))


def _test_cancel_aware(payload: dict, ctx: JobContext) -> dict:
    """Polls is_cancel_requested like a well-behaved long job should."""
    import time

    for _ in range(int(payload.get("iterations", 100))):
        if ctx.is_cancel_requested():
            raise JobCancelled("cancelled by request")
        time.sleep(0.05)
    return {"echo": payload}


# HANDLERS is populated in a fresh interpreter for EVERY job (multiprocessing
# "spawn"), so registration must happen via module-level definitions here --
# a runtime monkeypatch in the submitting/test process would never cross the
# process boundary into the child. The _test_* entries are inert unless a
# test deliberately submits that job_type; they cost nothing in production
# (never referenced, never imported beyond this dict).
HANDLERS: dict[str, Callable[[dict, JobContext], dict]] = {
    "design_create": handle_design_create,
    "drawing_to_cad": handle_drawing_to_cad,
    "drawing_generate": handle_drawing_generate,
    "_test_echo": _test_echo,
    "_test_sleep": _test_sleep,
    "_test_crash": _test_crash,
    "_test_raise": _test_raise,
    "_test_cancel_aware": _test_cancel_aware,
}
