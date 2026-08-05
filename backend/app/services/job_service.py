"""The database-backed job queue (docs/adr/0001-job-queue-database-backed.md).

This module is the ONLY place that writes app.models.Job rows. It owns:
  * submission (idempotency, queue-depth, per-user concurrency limits)
  * atomic claiming (portable across SQLite and Postgres -- no
    ``FOR UPDATE SKIP LOCKED``, which SQLite doesn't support)
  * the safe state machine (queued/running/validating/exporting/succeeded/
    failed/timed_out/cancelled) -- callers never see or set anything else
  * heartbeats + stale-job reaping
  * retry policy by failure category

The API process only ever reads/writes this table through here; it never
runs CAD geometry code itself once a route is wired to the queue (see
app.routers.jobs, app.routers.designs, app.routers.drawings). The worker
subprocess (app.worker.*) is the only thing that does real CAD work, and it
too only touches this table through here.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Job
from app.observability import log_event

# --- the safe, closed state machine (never expose anything else) -----------
STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_VALIDATING = "validating"
STATUS_EXPORTING = "exporting"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"
STATUS_TIMED_OUT = "timed_out"
STATUS_CANCELLED = "cancelled"

ALL_STATUSES = frozenset({
    STATUS_QUEUED, STATUS_RUNNING, STATUS_VALIDATING, STATUS_EXPORTING,
    STATUS_SUCCEEDED, STATUS_FAILED, STATUS_TIMED_OUT, STATUS_CANCELLED,
})
ACTIVE_STATUSES = frozenset({STATUS_QUEUED, STATUS_RUNNING, STATUS_VALIDATING, STATUS_EXPORTING})
TERMINAL_STATUSES = frozenset({STATUS_SUCCEEDED, STATUS_FAILED, STATUS_TIMED_OUT, STATUS_CANCELLED})

# --- failure categories -> retry policy (max attempts, INCLUDING the first) -
# A category not listed defaults to 1 (no auto-retry) -- see classify_error.
RETRY_POLICY: dict[str, int] = {
    "timeout": 2,             # could be transient system load
    "resource_exhausted": 2,  # ditto (queue/host was briefly under pressure)
    "transient": 3,           # e.g. a DB hiccup while writing progress
    "internal": 2,            # unexpected bug: worth one retry, not infinite
    "crash": 1,                # OCCT/CadQuery segfaults are usually input-
                                # deterministic; retrying wastes a worker slot
    "invalid_input": 1,       # will fail identically every time
    "provider_unavailable": 2,  # an LLM outage is often transient
    "cancelled": 1,
}


class JobQueueSaturated(Exception):
    """Raised when the global queue is at settings.job_max_queue_depth."""


class UserConcurrencyLimitExceeded(Exception):
    """Raised when a user already has settings.job_per_user_concurrent_limit
    jobs queued/running."""


class QuotaExceeded(Exception):
    """Raised when a user's daily/monthly generation quota is exhausted
    (settings.quota_designs_per_day / quota_designs_per_month)."""

    def __init__(self, message: str, quota: str):
        super().__init__(message)
        self.quota = quota  # "daily" | "monthly", for the quota_exceeded_total metric


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


def classify_error(exc: BaseException) -> str:
    """Map an exception to a retry-policy category. Anything unrecognized is
    'internal' -- worth exactly one retry, never assumed safe to retry
    forever, never assumed permanent without evidence."""
    from app.cad.base import CadGenerationError
    from app.llm.base import LLMUnavailableError
    from app.worker.handlers import JobCancelled

    name = type(exc).__name__
    if isinstance(exc, JobCancelled):
        return "cancelled"
    if isinstance(exc, LLMUnavailableError):
        # A provider outage/timeout is neither "this input is broken"
        # (invalid_input) nor "our code has a bug" (internal) -- callers
        # (e.g. app.routers.designs.create_design) map this to a clean 503,
        # matching the FastAPI-level LLMUnavailableError handler this
        # exception used to reach directly before jobs existed.
        return "provider_unavailable"
    if isinstance(exc, CadGenerationError):
        return "invalid_input"
    if isinstance(exc, TimeoutError) or name in ("FutureTimeoutError",):
        return "timeout"
    if isinstance(exc, MemoryError):
        return "resource_exhausted"
    if isinstance(exc, (ConnectionError, OSError)) and name != "MemoryError":
        return "transient"
    return "internal"


_SAFE_MESSAGES: dict[str, str] = {
    "timeout": "This job took too long and was stopped. Please retry — a "
               "simpler request usually succeeds.",
    "resource_exhausted": "This job needed more memory than allowed. Try "
                          "simplifying the request (fewer features, smaller "
                          "dimensions).",
    "transient": "A temporary system error interrupted this job. Please retry.",
    "internal": "Generation failed unexpectedly. This has been logged; "
               "please retry or contact support if it keeps happening.",
    "crash": "The CAD kernel crashed while processing this request. This has "
             "been logged; please retry or simplify the request.",
    "invalid_input": None,  # CadGenerationError's own message IS user-safe
    "provider_unavailable": None,  # LLMUnavailableError's own message IS user-safe
    "cancelled": "Job was cancelled.",
}
_MESSAGE_FROM_EXC = frozenset({"invalid_input", "provider_unavailable"})


def safe_message_for(category: str, exc: BaseException | None) -> str:
    """A message that is ALWAYS safe to show a client -- never a raw
    traceback or exception repr for anything but our own, already-user-safe
    CadGenerationError / LLMUnavailableError."""
    if category in _MESSAGE_FROM_EXC and exc is not None:
        return str(exc)[:500]
    return _SAFE_MESSAGES.get(category, _SAFE_MESSAGES["internal"])


@dataclass
class SubmitResult:
    job: Job
    created: bool  # False when an idempotency-key hit returned an existing job


def _check_storage_quota(db: Session, user_id: str) -> None:
    """Storage quota only (docs/operations/cost-control-architecture.md) --
    distinct from the concurrency/queue-depth checks above (how many AT
    ONCE) and from app.rate_limit (burst RATE). Storage is a persistent
    total, not a rolling-window count, so it stays a live SUM check here
    rather than an atomic reservation counter (see app.cost_control.service
    for daily/monthly GENERATION volume, which moved to the atomic
    reservation ledger to close a pre-existing concurrent-request race this
    live-COUNT approach had). Honors a per-account override
    (app.models.AccountLimitOverride.storage_quota_mb) when set. 0 disables
    the check.

    NOTE: settings.quota_designs_per_day/quota_designs_per_month are
    superseded by settings.cost_daily_generation_limit/
    cost_monthly_generation_limit (enforced atomically via
    app.cost_control.service.reserve_budget in submit_job below) and are no
    longer separately enforced here -- kept in app.config only so an
    existing .env override doesn't error, per the same "harmless kept
    field" pattern as drawing_job_timeout_seconds."""
    from app.cost_control.policy import effective_policy
    from app.metrics import quota_exceeded_total

    policy = effective_policy(db, user_id)
    if policy.storage_quota_mb > 0:
        used_bytes = _user_storage_bytes(db, user_id)
        cap_bytes = policy.storage_quota_mb * 1024 * 1024
        if used_bytes >= cap_bytes:
            quota_exceeded_total.labels(quota="storage").inc()
            raise QuotaExceeded(
                f"Storage quota reached ({policy.storage_quota_mb} MB). "
                "Delete some exports or contact support for more space.",
                quota="storage")


def _user_storage_bytes(db: Session, user_id: str) -> int:
    """Sum of ExportFile.size_bytes across every design this user owns
    (Design -> Project -> User), for the per-account storage quota."""
    from app.models import Design, ExportFile, Project

    return db.scalar(
        select(func.coalesce(func.sum(ExportFile.size_bytes), 0))
        .select_from(ExportFile)
        .join(Design, Design.id == ExportFile.design_id)
        .join(Project, Project.id == Design.project_id)
        .where(Project.user_id == user_id)
    ) or 0


def submit_job(
    db: Session, *, user_id: str, job_type: str, payload: dict,
    idempotency_key: Optional[str] = None,
) -> SubmitResult:
    """Enqueue a job, enforcing queue-depth, per-user concurrency, storage
    quota, and an atomic cost-control budget reservation
    (app.cost_control.service.reserve_budget) BEFORE any row is written.
    Idempotent: resubmitting the same (user_id, idempotency_key) pair
    returns the ORIGINAL job untouched (and skips every check entirely --
    it's not a NEW generation, so it must never be charged twice)."""
    if idempotency_key:
        existing = db.scalar(
            select(Job).where(Job.user_id == user_id,
                              Job.idempotency_key == idempotency_key))
        if existing is not None:
            log_event("job_idempotent_hit", job_id=existing.id, job_type=job_type)
            return SubmitResult(job=existing, created=False)

    total_active = db.scalar(
        select(func.count()).select_from(Job).where(Job.status.in_(ACTIVE_STATUSES)))
    if total_active is not None and total_active >= settings.job_max_queue_depth:
        raise JobQueueSaturated(
            f"The system is at capacity ({settings.job_max_queue_depth} jobs "
            "queued). Please try again shortly.")

    user_active = db.scalar(
        select(func.count()).select_from(Job)
        .where(Job.user_id == user_id, Job.status.in_(ACTIVE_STATUSES)))
    if user_active is not None and user_active >= settings.job_per_user_concurrent_limit:
        raise UserConcurrencyLimitExceeded(
            f"You already have {settings.job_per_user_concurrent_limit} "
            "generation(s) in progress. Wait for one to finish before "
            "starting another.")

    _check_storage_quota(db, user_id)

    reservation = None
    if settings.cost_control_enabled:
        from app.cost_control import service as cost_service
        from app.cost_control.estimator import estimate_request_cost

        estimate = estimate_request_cost(job_type)
        reservation = cost_service.reserve_budget(
            db, user_id=user_id, operation_type=job_type, estimate=estimate,
            idempotency_key=idempotency_key)

    job = Job(
        id=_uuid(), user_id=user_id, idempotency_key=idempotency_key,
        job_type=job_type, status=STATUS_QUEUED, payload_json=payload,
        max_attempts=1,  # set for real once the job fails, from its category
    )
    db.add(job)
    try:
        db.commit()
    except IntegrityError:
        # Race: two concurrent requests with the same idempotency key both
        # passed the SELECT above. Whoever loses the unique constraint just
        # re-reads and returns the winner's row -- never a duplicate job.
        # The loser's reservation is the SAME (already-idempotent) row the
        # winner holds (reserve_budget's own idempotency check above), so
        # there is nothing to release here -- releasing it would incorrectly
        # refund the winner's still-active reservation.
        db.rollback()
        if idempotency_key:
            existing = db.scalar(
                select(Job).where(Job.user_id == user_id,
                                  Job.idempotency_key == idempotency_key))
            if existing is not None:
                return SubmitResult(job=existing, created=False)
        raise
    db.refresh(job)
    if reservation is not None:
        from app.cost_control import service as cost_service
        cost_service.attach_job(db, reservation, job.id)
    log_event("job_submitted", job_id=job.id, job_type=job_type, user_id=user_id)
    return SubmitResult(job=job, created=True)


def get_job(db: Session, job_id: str) -> Job | None:
    return db.get(Job, job_id)


def get_owned_job(db: Session, job_id: str, user_id: str) -> Job | None:
    job = db.get(Job, job_id)
    if job is None or job.user_id != user_id:
        return None
    return job


def list_jobs(db: Session, user_id: str, *, limit: int = 50) -> list[Job]:
    return list(db.scalars(
        select(Job).where(Job.user_id == user_id)
        .order_by(Job.created_at.desc()).limit(limit)))


# --------------------------------------------------------------------- claim

def claim_next_job(db: Session, *, worker_id: str) -> Job | None:
    """Atomically claim ONE queued job, respecting the per-user concurrency
    limit (a user at their limit is skipped, not blocked -- their OTHER
    queued jobs simply wait their turn behind whichever is already running).

    Portable across SQLite and Postgres: no ``SELECT ... FOR UPDATE SKIP
    LOCKED`` (SQLite doesn't support it). Instead, candidates are read, then
    each is claimed with a single conditional UPDATE guarded by
    ``WHERE id = :id AND status = 'queued'`` -- at most one caller's UPDATE
    can match a given row (row-level atomicity is guaranteed by both
    backends), so a race between two workers naturally resolves to exactly
    one winner; the loser's UPDATE affects 0 rows and just tries the next
    candidate."""
    candidates = db.execute(
        select(Job.id, Job.user_id).where(Job.status == STATUS_QUEUED)
        .order_by(Job.created_at.asc()).limit(25)).all()

    if not candidates:
        return None

    user_running_counts: dict[str, int] = {}

    def _user_running(uid: str) -> int:
        if uid not in user_running_counts:
            user_running_counts[uid] = db.scalar(
                select(func.count()).select_from(Job)
                .where(Job.user_id == uid, Job.status.in_(
                    {STATUS_RUNNING, STATUS_VALIDATING, STATUS_EXPORTING}))) or 0
        return user_running_counts[uid]

    now = _now()
    for job_id, user_id in candidates:
        if _user_running(user_id) >= settings.job_per_user_concurrent_limit:
            continue
        result = db.execute(
            update(Job).where(Job.id == job_id, Job.status == STATUS_QUEUED)
            .values(status=STATUS_RUNNING, worker_id=worker_id, started_at=now,
                   heartbeat_at=now, attempt=Job.attempt + 1))
        db.commit()
        if result.rowcount == 1:
            job = db.get(Job, job_id)
            log_event("job_claimed", job_id=job_id, worker_id=worker_id,
                      job_type=job.job_type if job else None)
            return job
        db.rollback()
    return None


def claim_for_inline_execution(db: Session, job_id: str) -> None:
    """app.worker.runner.run_inline's equivalent of claim_next_job's own
    ``status='running', attempt=attempt+1`` transition -- test-only inline
    execution bypasses the claim loop entirely (there's no supervisor polling
    in the test harness), so without this, ``attempt`` would never advance
    and maybe_retry_or_fail's ``attempt < max_attempts`` check would stay
    true forever, requeuing a retryable failure in an infinite loop instead
    of ever reaching a terminal state."""
    db.execute(update(Job).where(Job.id == job_id).values(
        status=STATUS_RUNNING, started_at=_now(), heartbeat_at=_now(),
        attempt=Job.attempt + 1))
    db.commit()


def heartbeat(db: Session, job_id: str) -> None:
    db.execute(update(Job).where(Job.id == job_id).values(heartbeat_at=_now()))
    db.commit()


def set_stage(db: Session, job_id: str, stage: str, *, status: str | None = None,
             message: str | None = None) -> None:
    """Update fine-grained progress. ``status`` may additionally move the job
    into one of the "running" family's own safe states (validating/
    exporting) -- never into a terminal state (use the mark_* functions for
    that, which also set finished_at and enforce a valid transition)."""
    values: dict = {"stage": stage, "heartbeat_at": _now()}
    if status is not None:
        if status not in ACTIVE_STATUSES:
            raise ValueError(f"set_stage cannot move a job to terminal status {status!r}")
        values["status"] = status
    if message is not None:
        values["message"] = message[:2000]
    db.execute(update(Job).where(Job.id == job_id).values(**values))
    db.commit()


def _log_job_completed(job: Job) -> None:
    """The canonical, terminal-outcome telemetry event for EVERY job,
    regardless of type or outcome (docs/ops/observability.md) -- request id,
    workflow (job_type), retries, queue wait, and final outcome all land here
    exactly once per job, since this is only called from mark_succeeded /
    mark_terminal_failure (never from a requeue)."""
    from app.metrics import worker_job_duration_seconds, worker_job_queue_wait_seconds, worker_jobs_total

    def _delta_ms(a, b):
        """(a - b) in ms, tolerating SQLite round-tripping a timezone-aware
        datetime back as naive (Job.created_at/started_at/finished_at may mix
        naive-from-DB and aware-from-this-Python-session values)."""
        if a is None or b is None:
            return None
        if a.tzinfo is not None:
            a = a.replace(tzinfo=None)
        if b.tzinfo is not None:
            b = b.replace(tzinfo=None)
        return (a - b).total_seconds() * 1000

    queue_wait_ms = _delta_ms(job.started_at, job.created_at)
    run_ms = _delta_ms(job.finished_at, job.started_at)
    outcome = job.status
    worker_jobs_total.labels(job_type=job.job_type, outcome=outcome).inc()
    if queue_wait_ms is not None:
        worker_job_queue_wait_seconds.labels(job_type=job.job_type).observe(queue_wait_ms / 1000)
    if run_ms is not None:
        worker_job_duration_seconds.labels(job_type=job.job_type).observe(run_ms / 1000)
    log_event(
        "job_completed",
        job_id=job.id, job_type=job.job_type, workflow=job.job_type,
        final_outcome=outcome, error_category=job.error_category,
        retries=max(0, job.attempt - 1), attempt=job.attempt,
        queue_wait_ms=round(queue_wait_ms, 2) if queue_wait_ms is not None else None,
        run_ms=round(run_ms, 2) if run_ms is not None else None,
    )


def _reservation_for_job(db: Session, job_id: str):
    from app.models import BudgetReservation

    return db.scalar(select(BudgetReservation).where(BudgetReservation.job_id == job_id))


def mark_succeeded(
    db: Session, job_id: str, result: dict, *,
    actual_tokens: int = 0, actual_cost_cents: int = 0,
) -> None:
    db.execute(update(Job).where(Job.id == job_id).values(
        status=STATUS_SUCCEEDED, result_json=result, finished_at=_now(),
        stage=None, message=None))
    db.commit()
    log_event("job_succeeded", job_id=job_id)
    reservation = _reservation_for_job(db, job_id)
    if reservation is not None:
        from app.cost_control import service as cost_service

        cost_service.commit_reservation(
            db, reservation.id, actual_tokens=actual_tokens,
            actual_cost_cents=actual_cost_cents)
    job = get_job(db, job_id)
    if job is not None:
        _log_job_completed(job)


def mark_terminal_failure(
    db: Session, job_id: str, *, status: str, category: str, exc: BaseException | None,
) -> None:
    """status is one of failed/timed_out/cancelled. Never retries further --
    callers that want a retry should use maybe_retry_or_fail instead.

    EVERY terminal failure releases (fully refunds) any budget reservation
    for this job -- unsupported/invalid requests, provider outages, internal
    errors, CAD/compiler failures, timeouts, and cancellations all reach
    here with no user charge (docs/operations/cost-control-architecture.md
    Step 4). Automatic retries (maybe_retry_or_fail's requeue path) never
    call this -- the reservation stays 'reserved' across every attempt of
    the SAME job, consistent with "failed automatic retries are included in
    the original reservation.\""""
    db.execute(update(Job).where(Job.id == job_id).values(
        status=status, error_category=category,
        error_message=safe_message_for(category, exc), finished_at=_now()))
    db.commit()
    log_event("job_failed_terminal", job_id=job_id, status=status, category=category,
              error_type=type(exc).__name__ if exc else None)
    reservation = _reservation_for_job(db, job_id)
    if reservation is not None:
        from app.cost_control import service as cost_service

        cost_service.release_reservation(db, reservation.id, reason=f"job_{status}")
    job = get_job(db, job_id)
    if job is not None:
        _log_job_completed(job)


def maybe_retry_or_fail(
    db: Session, job_id: str, *, category: str, exc: BaseException | None,
    terminal_status: str = STATUS_FAILED,
) -> bool:
    """Apply the retry policy for `category`. Returns True if the job was
    requeued (status back to 'queued', ready for another claim), False if it
    was marked terminal (attempts exhausted).

    Retry-storm prevention: RETRY_POLICY's per-category value is further
    capped by settings.cost_max_retries_per_request (docs/operations/
    cost-control-architecture.md) -- whichever is LOWER wins, so a
    misconfigured/overly-generous RETRY_POLICY entry can never exceed the
    cost-control ceiling on how many attempts one reservation may cover."""
    job = db.get(Job, job_id)
    if job is None:
        return False
    if category == "cancelled":
        terminal_status = STATUS_CANCELLED  # never retry a cancellation
    max_attempts = min(RETRY_POLICY.get(category, 1), settings.cost_max_retries_per_request)
    if job.attempt < max_attempts:
        db.execute(update(Job).where(Job.id == job_id).values(
            status=STATUS_QUEUED, worker_id=None, started_at=None, heartbeat_at=None,
            stage=None, error_category=category,
            error_message=safe_message_for(category, exc)))
        db.commit()
        log_event("job_retrying", job_id=job_id, category=category,
                  attempt=job.attempt, max_attempts=max_attempts)
        return True
    mark_terminal_failure(db, job_id, status=terminal_status, category=category, exc=exc)
    return False


def request_cancel(db: Session, job_id: str) -> Job | None:
    """Mark cancellation requested. A still-queued job is cancelled
    immediately (nothing to interrupt); a running job's worker notices
    ``cancel_requested`` at its next heartbeat/stage checkpoint and the
    supervisor terminates the child -- see app.worker.supervisor."""
    job = db.get(Job, job_id)
    if job is None:
        return None
    if job.status == STATUS_QUEUED:
        db.execute(update(Job).where(Job.id == job_id, Job.status == STATUS_QUEUED).values(
            status=STATUS_CANCELLED, cancel_requested=True, finished_at=_now()))
        db.commit()
    elif job.status in {STATUS_RUNNING, STATUS_VALIDATING, STATUS_EXPORTING}:
        db.execute(update(Job).where(Job.id == job_id).values(cancel_requested=True))
        db.commit()
    db.refresh(job)
    log_event("job_cancel_requested", job_id=job_id, status=job.status)
    return job


def is_cancel_requested(db: Session, job_id: str) -> bool:
    return bool(db.scalar(select(Job.cancel_requested).where(Job.id == job_id)))


# ------------------------------------------------------------- stale recovery

def reap_stale_jobs(db: Session) -> int:
    """Find 'running-family' jobs whose heartbeat has gone stale (their
    worker process died without updating the DB -- e.g. the whole supervisor
    was killed, or the VPS rebooted) and recover them: requeue per retry
    policy, or fail if attempts are exhausted. Safe to call from a fresh
    supervisor on startup (that's exactly when orphans from a prior instance
    are found) or periodically from the running loop. Returns count reaped."""
    threshold = _now() - timedelta(seconds=settings.job_stale_heartbeat_seconds)
    stale = list(db.scalars(
        select(Job).where(
            Job.status.in_({STATUS_RUNNING, STATUS_VALIDATING, STATUS_EXPORTING}),
            Job.heartbeat_at.is_not(None), Job.heartbeat_at < threshold)))
    # A running job that somehow has no heartbeat at all (shouldn't happen --
    # claim_next_job always sets one -- but treat defensively) is stale too.
    stale += list(db.scalars(
        select(Job).where(
            Job.status.in_({STATUS_RUNNING, STATUS_VALIDATING, STATUS_EXPORTING}),
            Job.heartbeat_at.is_(None))))
    for job in stale:
        log_event("job_stale_reaped", job_id=job.id, status=job.status,
                  last_heartbeat=job.heartbeat_at.isoformat() if job.heartbeat_at else None)
        maybe_retry_or_fail(db, job.id, category="transient", exc=None,
                            terminal_status=STATUS_FAILED)
    return len(stale)


def queue_depth(db: Session) -> int:
    return db.scalar(
        select(func.count()).select_from(Job).where(Job.status.in_(ACTIVE_STATUSES))) or 0


def safe_dto(job: Job) -> dict:
    """The user-facing shape -- status/stage/progress/message/error/result.
    NEVER includes a traceback; error_message is always pre-sanitized (see
    safe_message_for), so it's safe to pass through verbatim here."""
    return {
        "job_id": job.id,
        "job_type": job.job_type,
        "status": job.status,
        "stage": job.stage,
        "message": job.message,
        "error": job.error_message,
        "error_category": job.error_category,
        "attempt": job.attempt,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "result": job.result_json if job.status == STATUS_SUCCEEDED else None,
    }
