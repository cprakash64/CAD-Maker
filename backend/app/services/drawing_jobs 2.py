"""Async job registry for Drawing → CAD generation.

Drawing generation (vision call + CadQuery build + validation + export) takes
seconds to a couple of minutes — far too long for a synchronous HTTP request.
The endpoints create a job, return 202 + job_id immediately, and a worker
thread runs the pipeline with its own DB session while the frontend polls
``GET /api/drawings/jobs/{id}`` for stage/progress until done/failed.

In-process by design: jobs are short-lived and per-node (the app runs a single
uvicorn process); finished jobs expire after ``_TTL_SECONDS``. A restart loses
only in-flight jobs — the poller surfaces that as a clean "job not found"
failure with retry, never a stuck spinner.
"""
from __future__ import annotations

import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field

from app.observability import log_event

# Ordered user-visible stages (progress % is derived from the stage).
STAGE_PROGRESS: dict[str, int] = {
    "queued": 5,
    "reading_drawing": 20,
    "interpreting": 30,           # provider (vision) call in flight
    "extracting_dimensions": 40,
    "fallback_generating": 55,    # deterministic best-effort after provider failure
    "building_cad": 60,
    "validating": 80,
    "exporting": 92,
    "done": 100,
    "failed": 100,
}

_TTL_SECONDS = 3600.0
_MAX_JOBS = 500


@dataclass
class DrawingJob:
    id: str
    user_id: str
    status: str = "queued"          # queued | running | done | failed
    stage: str = "queued"
    message: str | None = None
    design_id: str | None = None
    result: dict | None = None      # final response payload (when done)
    error: str | None = None
    created: float = field(default_factory=time.monotonic)
    updated: float = field(default_factory=time.monotonic)

    @property
    def progress(self) -> int:
        return STAGE_PROGRESS.get(self.stage, 50)

    def to_json(self) -> dict:
        out = {
            "job_id": self.id,
            "status": self.status,
            "stage": self.stage,
            "progress": self.progress,
            "message": self.message,
            "error": self.error,
            "design_id": self.design_id,
        }
        if self.status in ("done", "failed"):
            out["result"] = self.result
        return out


_jobs: dict[str, DrawingJob] = {}
_lock = threading.Lock()


def _evict_locked() -> None:
    now = time.monotonic()
    stale = [j.id for j in _jobs.values()
             if j.status in ("done", "failed") and now - j.updated > _TTL_SECONDS]
    for jid in stale:
        _jobs.pop(jid, None)
    if len(_jobs) > _MAX_JOBS:  # oldest finished first, then oldest of any state
        for j in sorted(_jobs.values(), key=lambda j: (j.status not in ("done", "failed"),
                                                       j.created)):
            if len(_jobs) <= _MAX_JOBS:
                break
            _jobs.pop(j.id, None)


def create_job(user_id: str) -> DrawingJob:
    job = DrawingJob(id=uuid.uuid4().hex, user_id=user_id)
    with _lock:
        _evict_locked()
        _jobs[job.id] = job
    return job


def get_job(job_id: str, user_id: str) -> DrawingJob | None:
    with _lock:
        job = _jobs.get(job_id)
    if job is None or job.user_id != user_id:
        return None
    _watchdog(job)
    return job


def _watchdog(job: DrawingJob) -> None:
    """Fail a job that has run past DRAWING_JOB_TIMEOUT_SECONDS so the poller
    never sits on a stuck spinner. Checked lazily on poll — no timer thread."""
    from app.config import settings

    limit = float(settings.drawing_job_timeout_seconds)
    if limit <= 0 or job.status not in ("queued", "running"):
        return
    if time.monotonic() - job.created <= limit:
        return
    with _lock:
        if job.status not in ("queued", "running"):
            return
        job.status = "failed"
        job.stage = "failed"
        job.error = (f"The drawing job exceeded its {int(limit)}s time limit and "
                     "was stopped. Please retry — a retry usually succeeds.")
        job.updated = time.monotonic()
    log_event("drawing_job_watchdog_timeout", job_id=job.id)


def set_stage(job: DrawingJob, stage: str, message: str | None = None) -> None:
    with _lock:
        job.stage = stage
        if message is not None:
            job.message = message
        job.updated = time.monotonic()


def run_job(job: DrawingJob, work) -> None:
    """Run ``work(job)`` on a daemon thread; it must return the final result
    payload dict (and may call ``set_stage`` as it progresses). Any exception
    lands the job in a clean 'failed' state with a user-safe message."""

    def _target() -> None:
        job.status = "running"
        start = time.perf_counter()
        try:
            result = work(job)
            with _lock:
                if job.status == "failed":
                    return  # the watchdog already failed this job; don't revive it
                job.result = result
                job.design_id = ((result or {}).get("design") or {}).get("id")
                job.status = "done"
                job.stage = "done"
                job.updated = time.monotonic()
            log_event("drawing_job_done", job_id=job.id,
                      design_id=job.design_id,
                      generated=bool((result or {}).get("generated")),
                      latency_ms=int((time.perf_counter() - start) * 1000))
        except Exception as exc:  # noqa: BLE001 - job must land in a clean state
            detail = getattr(exc, "detail", None) or str(exc) or type(exc).__name__
            with _lock:
                job.status = "failed"
                job.stage = "failed"
                job.error = str(detail)[:500]
                job.updated = time.monotonic()
            log_event("drawing_job_failed", job_id=job.id,
                      error_type=type(exc).__name__, detail=str(exc)[:300],
                      latency_ms=int((time.perf_counter() - start) * 1000))
            if not hasattr(exc, "detail"):  # unexpected: keep the trace in logs
                traceback.print_exc()

    threading.Thread(target=_target, name=f"drawing-job-{job.id[:8]}",
                     daemon=True).start()
