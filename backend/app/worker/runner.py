"""Runs INSIDE an isolated worker subprocess (spawned by
app.worker.supervisor via multiprocessing's "spawn" context -- a fresh
interpreter per job, not a fork, so no CadQuery/OCCT C-extension state is
ever inherited across jobs).

Resource limits are applied FIRST, before any of the heavy CAD imports, so
they bound the entire job (not just the parts after some later checkpoint).
If OCCT segfaults, this whole process dies with it -- that's fine, it's
disposable and isolated; app.worker.supervisor notices via the exit code and
writes the terminal job state itself (this process can't write anything once
it's gone).
"""
from __future__ import annotations

import os


def _apply_resource_limits() -> None:
    """Best-effort and Linux-first: RLIMIT_AS (address space) is what
    actually bounds process memory on Linux; macOS's setrlimit often silently
    refuses it, which is fine -- this degrades to "no memory ceiling" there
    rather than crashing the worker, since dev on macOS is still a supported
    workflow. RLIMIT_CPU (actual CPU seconds consumed, not wall clock) is
    supported cross-platform on POSIX and is applied unconditionally."""
    try:
        import resource
    except ImportError:  # pragma: no cover - no `resource` module on Windows
        return
    from app.config import settings

    if settings.job_cpu_time_limit_seconds > 0:
        try:
            hard = settings.job_cpu_time_limit_seconds + 5
            resource.setrlimit(resource.RLIMIT_CPU,
                               (settings.job_cpu_time_limit_seconds, hard))
        except (ValueError, OSError):
            pass
    if settings.job_memory_limit_mb > 0:
        try:
            limit_bytes = settings.job_memory_limit_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (limit_bytes, limit_bytes))
        except (ValueError, OSError):
            pass  # not supported on this platform -- "where supported"


def job_tmp_dir(job_id: str) -> str:
    from app.config import settings

    return os.path.join(settings.job_tmp_root, job_id)


def _isolate_tmp_dir(job_id: str) -> str:
    job_dir = job_tmp_dir(job_id)
    os.makedirs(job_dir, exist_ok=True)
    # A fresh (spawned) interpreter hasn't cached tempfile.gettempdir() yet, so
    # setting TMPDIR here makes every tempfile.* call in this process (e.g.
    # app.export.exporter._export_bytes's NamedTemporaryFile) land inside this
    # job's own directory, never a shared system /tmp.
    os.environ["TMPDIR"] = job_dir
    return job_dir


class _RunnerContext:
    """The real, DB-backed JobContext a handler sees (app.worker.handlers
    .JobContext protocol) -- writes go straight to the Job row so the API
    process can serve GET /api/jobs/{id} from a cheap read."""

    def __init__(self, job_id: str, db):
        self.job_id = job_id
        self._db = db
        self._tmp_dir = job_tmp_dir(job_id)

    def set_stage(self, stage: str, *, status: str | None = None) -> None:
        from app.services import job_service

        job_service.set_stage(self._db, self.job_id, stage, status=status)

    def is_cancel_requested(self) -> bool:
        from app.services import job_service

        return job_service.is_cancel_requested(self._db, self.job_id)

    def tmp_dir(self) -> str:
        return self._tmp_dir


def _execute(job_id: str, *, cleanup_tmp_dir: bool) -> None:
    """The actual "run this job's handler and write its outcome" core, shared
    by the real subprocess entry point (run_one_job) and the in-process
    inline path (run_inline, tests/dev-without-a-worker only). Never applies
    resource limits itself -- callers that want process isolation/limits
    wrap this; run_inline deliberately does NOT, since RLIMIT_CPU/RLIMIT_AS
    are irrevocable for the lifetime of whatever process calls setrlimit, and
    the test runner / dev API process must never have those applied to it."""
    from app.database import SessionLocal
    from app.services import job_service
    from app.worker.handlers import HANDLERS

    db = SessionLocal()
    try:
        job = job_service.get_job(db, job_id)
        if job is None:
            return
        if job_service.is_cancel_requested(db, job_id):
            job_service.mark_terminal_failure(
                db, job_id, status=job_service.STATUS_CANCELLED,
                category="cancelled", exc=None)
            return

        ctx = _RunnerContext(job_id, db)
        handler = HANDLERS.get(job.job_type)
        if handler is None:
            job_service.mark_terminal_failure(
                db, job_id, status=job_service.STATUS_FAILED, category="internal",
                exc=RuntimeError(f"no handler registered for job_type {job.job_type!r}"))
            return
        try:
            result = handler(dict(job.payload_json or {}), ctx)
        except Exception as exc:  # noqa: BLE001 - classify; never let a job hang ambiguous
            category = job_service.classify_error(exc)
            job_service.maybe_retry_or_fail(db, job_id, category=category, exc=exc)
            return
        job_service.mark_succeeded(db, job_id, result)
    finally:
        db.close()
        if cleanup_tmp_dir:
            import shutil

            shutil.rmtree(job_tmp_dir(job_id), ignore_errors=True)


def run_one_job(job_id: str) -> None:
    """The subprocess entry point. Must be a picklable, top-level, importable
    function (multiprocessing "spawn" requirement) -- no closures, no
    lambdas. Runs exactly one job and returns; the process then exits."""
    _apply_resource_limits()
    _isolate_tmp_dir(job_id)
    _execute(job_id, cleanup_tmp_dir=True)


_INLINE_MAX_ATTEMPTS = 5  # >= the highest per-category value in RETRY_POLICY


def run_inline(job_id: str) -> None:
    """Run a job in the CURRENT process, synchronously, with NO resource
    limits and NO process isolation. Only for settings.testing (and,
    deliberately, nowhere else): tests don't run a separate worker process,
    so app.routers.designs.create_design / drawings.py call this right after
    submission so the bounded sync-wait has something to observe finishing.
    NEVER used for a real request in production -- see docs/adr/
    0001-job-queue-database-backed.md.

    A real supervisor keeps polling and re-claims a job app.services
    .job_service.maybe_retry_or_fail put back to 'queued'; there is no such
    loop here, so this helper re-executes the SAME job inline until it
    reaches a terminal state (or the safety bound trips) -- otherwise a
    retryable failure (e.g. category='internal') would strand the job at
    'queued' forever under the test harness."""
    from app.config import settings
    from app.database import SessionLocal
    from app.services import job_service

    if not settings.testing:
        raise RuntimeError("run_inline is a test-only execution path")
    for _ in range(_INLINE_MAX_ATTEMPTS):
        db = SessionLocal()
        try:
            job = job_service.get_job(db, job_id)
            if job is None or job.status != job_service.STATUS_QUEUED:
                return
            job_service.claim_for_inline_execution(db, job_id)
        finally:
            db.close()

        _execute(job_id, cleanup_tmp_dir=False)

        db = SessionLocal()
        try:
            job = job_service.get_job(db, job_id)
            if job is None or job.status != job_service.STATUS_QUEUED:
                return
        finally:
            db.close()


if __name__ == "__main__":  # pragma: no cover - manual/debug entry point
    import sys

    run_one_job(sys.argv[1])
