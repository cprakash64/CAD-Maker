"""Shared Drawing → CAD job-progress vocabulary and the sync-path job shim.

The real async job registry is now app.services.job_service (a DB-backed
queue, docs/adr/0001-job-queue-database-backed.md) — this module no longer
owns any job STATE. What remains here and is still genuinely used:

  * ``DrawingJob`` — a plain, in-memory stand-in used ONLY for the
    ``sync=true`` (opt-in, scripts/tests) request path in
    app.routers.drawings, which runs the pipeline inline in the current
    request and never touches the job queue at all.
  * ``STAGE_PROGRESS`` — the ordered stage -> progress% map, shared by the
    sync shim above and by app.routers.drawings._drawing_job_json (which
    translates a real, DB-backed Job row into this same wire vocabulary for
    GET /api/drawings/jobs/{id}, unchanged for existing clients).
  * ``set_stage`` — called by the pipeline functions themselves
    (app.routers.drawings._run_to_cad_pipeline /
    _run_generate_pipeline) to report progress; it updates whatever
    job-like object it's given (the sync shim above, OR a job-service-backed
    shim from app.worker.handlers, via the optional ``on_stage`` hook).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

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


def set_stage(job: DrawingJob, stage: str, message: str | None = None) -> None:
    job.stage = stage
    if message is not None:
        job.message = message
    job.updated = time.monotonic()
    # Optional hook: app.worker.handlers passes a job-service-backed shim here
    # (not a real DrawingJob) so the SAME _run_to_cad_pipeline stage calls also
    # update the real, DB-backed Job row a worker subprocess owns. A genuine
    # DrawingJob never sets this attribute, so the sync-path caller above is
    # unaffected.
    on_stage = getattr(job, "on_stage", None)
    if on_stage is not None:
        on_stage(stage, message)
