"""The worker supervisor process (docs/adr/0001-job-queue-database-backed.md).

Run as its own OS process (``python -m app.worker``), completely separate
from the FastAPI app -- started/restarted by systemd (``Restart=always``) on
the VPS, or directly for local dev. It:

  * claims queued jobs (bounded by settings.job_worker_concurrency running at
    once, and settings.job_per_user_concurrent_limit per user -- enforced by
    app.services.job_service.claim_next_job)
  * runs EACH job in its own ``multiprocessing`` "spawn" child process --
    genuine OS-process isolation, so a hung job or an OCCT segfault can only
    ever take down that one child, never the supervisor or (since they were
    never in the same process to begin with) the FastAPI app
  * enforces a hard wall-clock timeout (SIGTERM, then SIGKILL after a grace
    period) and propagates cancellation requests the same way
  * reaps jobs orphaned by a previous, now-dead supervisor instance on
    startup, and stale ones periodically while running
  * exits cleanly after settings.job_worker_max_jobs_before_recycle jobs so a
    process manager restarts it fresh (bounds slow native-library memory
    growth) -- "process replacement after crashes or configured job count"
  * ALWAYS removes the job's temp directory, whatever the outcome
"""
from __future__ import annotations

import multiprocessing
import os
import socket
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.config import settings
from app.observability import log_event
from app.services import job_service
from app.worker.runner import job_tmp_dir, run_one_job

_TERMINATE_TIMEOUT = "timeout"
_TERMINATE_CANCEL = "cancel"


@dataclass
class _ActiveJob:
    job_id: str
    process: multiprocessing.process.BaseProcess
    deadline: float  # time.monotonic() seconds
    terminate_reason: str | None = None
    terminated_at: float | None = None
    last_cancel_check: float = field(default_factory=time.monotonic)


class Supervisor:
    def __init__(self, worker_id: str | None = None):
        self.mp_ctx = multiprocessing.get_context("spawn")
        self.active: dict[str, _ActiveJob] = {}
        self.processed_count = 0
        self.worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}"
        self._stopping = False

    # -------------------------------------------------------------- lifecycle

    def run_forever(self) -> None:
        os.makedirs(settings.job_tmp_root, exist_ok=True)
        from app.database import SessionLocal

        db = SessionLocal()
        try:
            reaped = job_service.reap_stale_jobs(db)
            log_event("worker_started", worker_id=self.worker_id, reaped_on_start=reaped)
            last_reap = time.monotonic()
            while not self._stopping:
                # See the comment in _finalize: without this, the identity
                # map can serve stale rows for jobs a worker CHILD process
                # (a separate process with its own session) has since written.
                db.expire_all()
                self._reap_finished(db)
                now = time.monotonic()
                if now - last_reap > settings.job_stale_heartbeat_seconds / 2:
                    job_service.reap_stale_jobs(db)
                    last_reap = now
                self._check_active_deadlines(db)

                if len(self.active) < settings.job_worker_concurrency:
                    job = job_service.claim_next_job(db, worker_id=self.worker_id)
                    if job is not None:
                        self._spawn(job)

                if self._should_recycle():
                    log_event("worker_recycling", worker_id=self.worker_id,
                              processed=self.processed_count)
                    self._drain(db)
                    return

                time.sleep(settings.job_poll_interval_seconds
                          if not self.active else 0.2)
        finally:
            db.close()

    def stop(self) -> None:
        """Cooperative shutdown hook (e.g. SIGTERM handler in __main__)."""
        self._stopping = True

    def _should_recycle(self) -> bool:
        return (settings.job_worker_max_jobs_before_recycle > 0
                and self.processed_count >= settings.job_worker_max_jobs_before_recycle
                and not self.active)

    def _drain(self, db) -> None:
        """Wait out any still-active jobs before this instance exits (used
        both for recycling and for graceful shutdown)."""
        while self.active:
            self._reap_finished(db)
            self._check_active_deadlines(db)
            time.sleep(0.2)

    # ------------------------------------------------------------------ spawn

    def _spawn(self, job) -> None:
        process = self.mp_ctx.Process(target=run_one_job, args=(job.id,), daemon=False)
        process.start()
        deadline = time.monotonic() + settings.job_hard_timeout_seconds
        self.active[job.id] = _ActiveJob(job_id=job.id, process=process, deadline=deadline)
        log_event("worker_job_spawned", job_id=job.id, job_type=job.job_type,
                  pid=process.pid, worker_id=self.worker_id)

    # --------------------------------------------------------- monitor/reap

    def _check_active_deadlines(self, db) -> None:
        now = time.monotonic()
        for active in list(self.active.values()):
            if active.terminate_reason is not None:
                self._escalate_if_needed(active, now)
                continue
            if now >= active.deadline:
                self._begin_terminate(active, _TERMINATE_TIMEOUT)
                continue
            # Cheap DB check, not on every tick -- once a second is plenty
            # responsive for a human clicking "cancel".
            if now - active.last_cancel_check >= 1.0:
                active.last_cancel_check = now
                if job_service.is_cancel_requested(db, active.job_id):
                    self._begin_terminate(active, _TERMINATE_CANCEL)

    def _begin_terminate(self, active: _ActiveJob, reason: str) -> None:
        active.terminate_reason = reason
        active.terminated_at = time.monotonic()
        log_event("worker_job_terminate_begin", job_id=active.job_id, reason=reason,
                  pid=active.process.pid)
        try:
            active.process.terminate()  # SIGTERM
        except Exception:  # noqa: BLE001 - process may already be gone
            pass

    def _escalate_if_needed(self, active: _ActiveJob, now: float) -> None:
        if not active.process.is_alive():
            return
        if active.terminated_at is not None and \
                now - active.terminated_at >= settings.job_sigkill_grace_seconds:
            log_event("worker_job_sigkill", job_id=active.job_id, pid=active.process.pid)
            try:
                active.process.kill()  # SIGKILL
            except Exception:  # noqa: BLE001
                pass

    def _reap_finished(self, db) -> None:
        for job_id, active in list(self.active.items()):
            if active.process.is_alive():
                continue
            self._finalize(db, active)
            del self.active[job_id]
            self.processed_count += 1

    def _finalize(self, db, active: _ActiveJob) -> None:
        exitcode = active.process.exitcode
        # The supervisor's session is long-lived across many poll iterations;
        # without expiring, SQLAlchemy's identity map would happily return a
        # STALE, pre-child-write copy of this row (e.g. the child already
        # committed "succeeded" in its OWN process/session, which this
        # session has no way to know about otherwise) and we'd wrongly
        # overwrite a real success with a manufactured "crash".
        db.expire_all()
        job = job_service.get_job(db, active.job_id)
        terminal = True  # False only when maybe_retry_or_fail requeues it
        try:
            if job is not None and job.status in job_service.ACTIVE_STATUSES:
                # The child never reached a terminal write itself -- either we
                # killed it (timeout/cancel) or it crashed/was killed on its
                # own (e.g. an OCCT segfault, OOM-killer, RLIMIT_CPU SIGXCPU).
                if active.terminate_reason == _TERMINATE_TIMEOUT:
                    requeued = job_service.maybe_retry_or_fail(
                        db, active.job_id, category="timeout",
                        exc=TimeoutError(f"job exceeded {settings.job_hard_timeout_seconds}s"),
                        terminal_status=job_service.STATUS_TIMED_OUT)
                    terminal = not requeued
                elif active.terminate_reason == _TERMINATE_CANCEL:
                    job_service.mark_terminal_failure(
                        db, active.job_id, status=job_service.STATUS_CANCELLED,
                        category="cancelled", exc=None)
                else:
                    # Negative exitcode == killed by a signal (e.g. -11 ==
                    # SIGSEGV from OCCT, -9 == OOM-killer SIGKILL, -24 ==
                    # SIGXCPU from our own RLIMIT_CPU). Graceful handling: the
                    # supervisor was never in this process's memory space, so
                    # the crash is fully contained -- we just log it and move
                    # on to the next job.
                    log_event("worker_job_crashed", job_id=active.job_id,
                              exitcode=exitcode, worker_id=self.worker_id)
                    from app.metrics import worker_crashes_total

                    worker_crashes_total.labels(signal=str(-exitcode) if exitcode else "unknown").inc()
                    requeued = job_service.maybe_retry_or_fail(
                        db, active.job_id, category="crash",
                        exc=RuntimeError(f"worker process exited with code {exitcode}"))
                    terminal = not requeued
        finally:
            import shutil

            shutil.rmtree(job_tmp_dir(active.job_id), ignore_errors=True)
            # A retry needs the SAME uploaded file next attempt -- only clean
            # up storage once the job is truly done (this run's own handler
            # already cleans it up on a normal finish; this is the backstop
            # for a crash/timeout/cancel that never reached that finally).
            if terminal and job is not None:
                self._cleanup_job_storage(job)
            log_event("worker_job_finished", job_id=active.job_id, exitcode=exitcode,
                      terminate_reason=active.terminate_reason, worker_id=self.worker_id)

    @staticmethod
    def _cleanup_job_storage(job) -> None:
        key = (job.payload_json or {}).get("upload_storage_key")
        if not key:
            return
        try:
            from app.storage.storage import get_storage

            get_storage().delete(key)
        except Exception:  # noqa: BLE001 - best-effort; never block finalization
            log_event("worker_job_storage_cleanup_failed", job_id=job.id)
