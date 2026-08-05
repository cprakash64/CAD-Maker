"""Atomic budget reservation (docs/operations/cost-control-architecture.md).

The only place that writes app.models.BudgetCounter / BudgetReservation /
AdminAuditLog / EmergencyStop rows, mirroring app.services.job_service's
role as the sole writer of the Job table.

Reservation lifecycle (a state machine on BudgetReservation.status):

    reserved --commit_reservation--> committed   (success; actual usage recorded)
    reserved --release_reservation--> released   (failure of any kind; full refund)
    reserved --reap_stale_reservations--> released, release_reason="stale_ttl"

Concurrency: every counter increment is a single, WHERE-guarded conditional
UPDATE (see ``_try_increment``) executed inside the SAME open transaction as
every other counter this reservation touches, committed only once ALL of
them succeed. Two concurrent requests racing for the last unit of budget on
the same (scope, scope_id, period_key) row are serialized by the database's
own row-level write lock on that row -- whichever transaction's UPDATE
executes first holds the lock until it commits or rolls back; the second
transaction's UPDATE blocks until then, and re-evaluates the WHERE clause
against the now-current value, so it correctly fails if the first
transaction's increment used up the remaining budget. This is the same
"atomic conditional UPDATE, no SELECT ... FOR UPDATE" pattern already used
by app.services.job_service.claim_next_job, chosen for the same reason: it
is portable across SQLite (dev/test) and PostgreSQL (production) without
relying on FOR UPDATE SKIP LOCKED, which SQLite doesn't support.

This does NOT replace app.llm.circuit_breaker (the existing per-process,
in-memory daily-spend/failure-rate breaker) -- that stays exactly as is and
still trips fast, in-process, as a first line of defense. This module is the
durable, cross-process, restart-safe ledger BEHIND it: the circuit breaker
can protect a single worker from hammering a dead provider; only a
DB-backed reservation can stop two different worker processes (or the same
process across a restart) from each believing the global budget still has
room.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.cost_control.estimator import CostEstimate
from app.cost_control.policy import EffectivePolicy, effective_policy
from app.models import AccountLimitOverride, AdminAuditLog, BudgetCounter, BudgetReservation, EmergencyStop
from app.observability import log_event

SCOPE_ACCOUNT = "account"
SCOPE_GLOBAL = "global"
GLOBAL_SCOPE_ID = "global"

STATUS_RESERVED = "reserved"
STATUS_COMMITTED = "committed"
STATUS_RELEASED = "released"
STATUS_EXPIRED = "expired"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _day_key(now: datetime) -> str:
    return now.strftime("%Y-%m-%d")


def _month_key(now: datetime) -> str:
    return now.strftime("%Y-%m")


def _hour_key(now: datetime) -> str:
    return now.strftime("%Y-%m-%dT%H")


# --------------------------------------------------------------- exceptions

class CostControlError(Exception):
    """Base for every reservation-rejection reason. ``code`` is the
    machine-readable API contract (app.schemas.cost_control); ``message`` is
    always safe to show a user (never leaks internal budget figures beyond
    what the policy explicitly allows -- see each subclass)."""
    code = "cost_control_error"
    retry_after_seconds: int | None = None

    def __init__(self, message: str, **extra):
        super().__init__(message)
        self.message = message
        self.extra = extra


class EmergencyStopActive(CostControlError):
    code = "SERVICE_PAUSED"


class GenerationDisabledForAccount(CostControlError):
    code = "GENERATION_DISABLED"


class RequestTooExpensive(CostControlError):
    code = "REQUEST_TOO_EXPENSIVE"


class DailyLimitExceeded(CostControlError):
    code = "DAILY_LIMIT_REACHED"
    retry_after_seconds = 24 * 3600


class MonthlyLimitExceeded(CostControlError):
    code = "MONTHLY_LIMIT_REACHED"


class DrawingLimitExceeded(CostControlError):
    code = "DAILY_LIMIT_REACHED"
    retry_after_seconds = 24 * 3600


class AccountCostCapExceeded(CostControlError):
    code = "DAILY_LIMIT_REACHED"
    retry_after_seconds = 24 * 3600


class GlobalBudgetExceeded(CostControlError):
    code = "GLOBAL_BUDGET_REACHED"
    retry_after_seconds = 3600


# ------------------------------------------------------- the atomic primitive

def _try_increment(db: Session, *, scope: str, scope_id: str, period_key: str,
                    amount: int, limit: int) -> bool:
    """Atomically add ``amount`` to the (scope, scope_id, period_key) counter
    IF AND ONLY IF the result would not exceed ``limit``. Returns whether it
    succeeded. ``limit <= 0`` means "unlimited" (always succeeds, no write).
    Does NOT commit -- the caller controls the transaction boundary so
    multiple counters can be reserved atomically together (see reserve_budget).
    """
    if limit <= 0:
        return True
    if amount > limit:
        return False  # can never fit; no write needed

    result = db.execute(
        update(BudgetCounter)
        .where(BudgetCounter.scope == scope, BudgetCounter.scope_id == scope_id,
               BudgetCounter.period_key == period_key,
               (BudgetCounter.used + amount) <= limit)
        .values(used=BudgetCounter.used + amount, updated_at=_now())
    )
    if result.rowcount == 1:
        return True

    existing = db.scalar(
        select(BudgetCounter.id).where(
            BudgetCounter.scope == scope, BudgetCounter.scope_id == scope_id,
            BudgetCounter.period_key == period_key))
    if existing is not None:
        return False  # row exists and is already at/over the limit

    # First use of this period: create the row under a SAVEPOINT so a
    # concurrent creator's unique-constraint win only unwinds this nested
    # savepoint, not the whole outer reservation transaction.
    try:
        with db.begin_nested():
            db.execute(insert(BudgetCounter).values(
                scope=scope, scope_id=scope_id, period_key=period_key,
                used=amount, updated_at=_now()))
        return True
    except IntegrityError:
        result = db.execute(
            update(BudgetCounter)
            .where(BudgetCounter.scope == scope, BudgetCounter.scope_id == scope_id,
                   BudgetCounter.period_key == period_key,
                   (BudgetCounter.used + amount) <= limit)
            .values(used=BudgetCounter.used + amount, updated_at=_now()))
        return result.rowcount == 1


def _decrement(db: Session, *, scope: str, scope_id: str, period_key: str, amount: int) -> None:
    """Plain decrement, no limit check -- used for refunds. Never commits."""
    if amount == 0:
        return
    db.execute(
        update(BudgetCounter)
        .where(BudgetCounter.scope == scope, BudgetCounter.scope_id == scope_id,
               BudgetCounter.period_key == period_key)
        .values(used=BudgetCounter.used - amount, updated_at=_now()))


# ------------------------------------------------------------------ checks

class UploadFrequencyExceeded(CostControlError):
    code = "REQUEST_TOO_EXPENSIVE"
    retry_after_seconds = 3600


def check_upload_frequency(db: Session, user_id: str) -> None:
    """Per-account upload-frequency cap (docs/operations/cost-control-
    architecture.md), distinct from app.rate_limit's burst window and
    app.services.upload_guard's per-file size caps -- this bounds total
    uploads/hour regardless of how they're spaced. Uses the same atomic
    counter primitive as reserve_budget, but commits immediately (no
    reservation/refund lifecycle -- an upload that later fails validation
    still consumed one upload attempt)."""
    from app.config import settings

    limit = settings.cost_max_upload_frequency_per_hour
    if limit <= 0:
        return
    now = _now()
    ok = _try_increment(db, scope=SCOPE_ACCOUNT, scope_id=user_id,
                        period_key=f"upload:hourly:{_hour_key(now)}", amount=1, limit=limit)
    if not ok:
        db.rollback()
        raise UploadFrequencyExceeded(
            f"Too many uploads this hour (limit {limit}). Please try again later.")
    db.commit()


def check_emergency_stop(db: Session) -> None:
    row = db.get(EmergencyStop, 1)
    if row is not None and row.active:
        raise EmergencyStopActive(
            "Generation is temporarily paused. Please try again later.",
            reason=row.reason)


@dataclass(frozen=True)
class _CounterSpec:
    scope: str
    scope_id: str
    period_key: str
    amount: int
    limit: int
    error: type[CostControlError]
    error_message: str


def _counter_specs(*, user_id: str, operation_type: str, estimate: CostEstimate,
                    policy: EffectivePolicy, now: datetime) -> list[_CounterSpec]:
    day, month, hour = _day_key(now), _month_key(now), _hour_key(now)
    specs = [
        _CounterSpec(
            SCOPE_ACCOUNT, user_id, f"gen:daily:{day}", 1,
            policy.daily_generation_limit, DailyLimitExceeded,
            f"Daily generation limit reached ({policy.daily_generation_limit} "
            "per day). Try again tomorrow."),
        _CounterSpec(
            SCOPE_ACCOUNT, user_id, f"gen:monthly:{month}", 1,
            policy.monthly_generation_limit, MonthlyLimitExceeded,
            f"Monthly generation limit reached ({policy.monthly_generation_limit} "
            "per month). Contact support if you need a higher limit."),
        _CounterSpec(
            SCOPE_ACCOUNT, user_id, f"cost:daily:{day}", estimate.estimated_cost_cents,
            policy.max_daily_account_cost_cents, AccountCostCapExceeded,
            "Daily spending limit reached for this account. Try again tomorrow."),
        _CounterSpec(
            SCOPE_GLOBAL, GLOBAL_SCOPE_ID, f"cost:daily:{day}", estimate.estimated_cost_cents,
            policy.global_daily_budget_cents, GlobalBudgetExceeded,
            "The service is at its daily generation budget. Please try again "
            "later."),
        _CounterSpec(
            SCOPE_GLOBAL, GLOBAL_SCOPE_ID, f"cost:hourly:{hour}",
            estimate.estimated_cost_cents, policy.global_hourly_emergency_budget_cents,
            GlobalBudgetExceeded,
            "The service is at its hourly generation budget. Please try "
            "again in a while."),
    ]
    if operation_type in ("drawing_interpret", "drawing_to_cad", "drawing_generate"):
        specs.append(_CounterSpec(
            SCOPE_ACCOUNT, user_id, f"drawing:daily:{day}", 1,
            policy.daily_drawing_limit, DrawingLimitExceeded,
            f"Daily drawing-conversion limit reached ({policy.daily_drawing_limit} "
            "per day). Try again tomorrow."))
    return specs


def reserve_budget(
    db: Session, *, user_id: str, operation_type: str, estimate: CostEstimate,
    idempotency_key: str | None = None,
) -> BudgetReservation:
    """Atomically check every applicable limit and, if all pass, create a
    ``reserved`` BudgetReservation. Raises a CostControlError subclass on the
    FIRST limit that would be exceeded (no partial reservation is ever left
    behind -- the whole transaction is rolled back before raising).

    Idempotent: resubmitting the same (user_id, idempotency_key) returns the
    EXISTING reservation untouched, without re-checking or re-reserving --
    the same guarantee app.services.job_service.submit_job already gives for
    the Job it wraps."""
    if idempotency_key:
        existing = db.scalar(
            select(BudgetReservation).where(
                BudgetReservation.user_id == user_id,
                BudgetReservation.idempotency_key == idempotency_key))
        if existing is not None:
            log_event("budget_reservation_idempotent_hit", reservation_id=existing.id,
                      operation_type=operation_type)
            return existing

    check_emergency_stop(db)

    policy = effective_policy(db, user_id)
    if policy.generation_disabled:
        raise GenerationDisabledForAccount(
            policy.disabled_reason or "Generation has been temporarily disabled "
            "for this account. Contact support.")

    if estimate.estimated_total_tokens > policy.max_estimated_tokens_per_request:
        raise RequestTooExpensive(
            "This request is too complex to process. Try simplifying it.")
    if estimate.estimated_cost_cents > policy.max_estimated_cost_cents_per_request:
        raise RequestTooExpensive(
            "This request is too complex to process. Try simplifying it.")

    now = _now()
    specs = _counter_specs(user_id=user_id, operation_type=operation_type,
                            estimate=estimate, policy=policy, now=now)

    touched: list[dict] = []
    for spec in specs:
        ok = _try_increment(db, scope=spec.scope, scope_id=spec.scope_id,
                            period_key=spec.period_key, amount=spec.amount,
                            limit=spec.limit)
        if not ok:
            db.rollback()
            from app.metrics import cost_control_rejections_total
            cost_control_rejections_total.labels(
                reason=spec.error.code, scope=spec.scope).inc()
            log_event("budget_reservation_rejected", user_id=user_id,
                      operation_type=operation_type, reason=spec.error.code,
                      period_key=spec.period_key)
            raise spec.error(spec.error_message)
        touched.append({"scope": spec.scope, "scope_id": spec.scope_id,
                        "period_key": spec.period_key, "amount": spec.amount})

    reservation = BudgetReservation(
        user_id=user_id, operation_type=operation_type, status=STATUS_RESERVED,
        idempotency_key=idempotency_key,
        estimated_tokens=estimate.estimated_total_tokens,
        estimated_cost_cents=estimate.estimated_cost_cents,
        counter_keys=touched, policy_version=policy.version,
        expires_at=now + timedelta(seconds=_ttl_seconds()),
    )
    db.add(reservation)
    try:
        db.commit()
    except IntegrityError:
        # Race: two concurrent requests with the same idempotency key both
        # passed the SELECT above -- same shape as job_service.submit_job's
        # own idempotency race handling.
        db.rollback()
        if idempotency_key:
            existing = db.scalar(
                select(BudgetReservation).where(
                    BudgetReservation.user_id == user_id,
                    BudgetReservation.idempotency_key == idempotency_key))
            if existing is not None:
                return existing
        raise
    db.refresh(reservation)
    from app.metrics import budget_reservations_total
    budget_reservations_total.labels(operation_type=operation_type, outcome="reserved").inc()
    log_event("budget_reservation_created", reservation_id=reservation.id,
              user_id=user_id, operation_type=operation_type,
              estimated_cost_cents=estimate.estimated_cost_cents)
    return reservation


@contextmanager
def guarded_operation(db: Session, *, user_id: str, operation_type: str,
                      idempotency_key: str | None = None):
    """For INLINE (non-job-queued) cost-bearing operations
    (app.routers.designs.modify_design, app.routers.drawings.interpret):
    reserve budget, run the wrapped block inside app.llm.usage.track_usage(),
    and commit (success) or release (any exception) automatically -- the
    same guarantee job_service's mark_succeeded/mark_terminal_failure give
    the queued paths, just synchronous instead of job-lifecycle-driven.

    A CostControlError raised BY reserve_budget itself propagates before the
    block ever runs (nothing to release -- no reservation was created)."""
    from app.config import settings as _settings
    from app.cost_control.estimator import estimate_request_cost
    from app.llm.usage import snapshot as usage_snapshot
    from app.llm.usage import track_usage

    if not _settings.cost_control_enabled:
        yield
        return

    estimate = estimate_request_cost(operation_type)
    reservation = reserve_budget(
        db, user_id=user_id, operation_type=operation_type, estimate=estimate,
        idempotency_key=idempotency_key)
    try:
        with track_usage():
            yield
            actual_tokens, actual_cost_cents = usage_snapshot()
        commit_reservation(db, reservation.id, actual_tokens=actual_tokens,
                           actual_cost_cents=actual_cost_cents)
    except Exception:
        release_reservation(db, reservation.id, reason="inline_operation_failed")
        raise


def _ttl_seconds() -> int:
    from app.config import settings
    return settings.cost_reservation_ttl_seconds


def attach_job(db: Session, reservation: BudgetReservation, job_id: str) -> None:
    db.execute(update(BudgetReservation).where(BudgetReservation.id == reservation.id)
              .values(job_id=job_id))
    db.commit()


def commit_reservation(
    db: Session, reservation_id: str, *, actual_tokens: int, actual_cost_cents: int,
) -> None:
    """Success path: record actual usage and reconcile the difference against
    the SAME counters the reservation touched (refund if actual < estimated;
    top up, uncapped, if actual > estimated -- the work already happened, we
    never retroactively fail a completed job, but the overage is metriced)."""
    reservation = db.scalar(
        select(BudgetReservation).where(
            BudgetReservation.id == reservation_id, BudgetReservation.status == STATUS_RESERVED))
    if reservation is None:
        return  # already finalized (commit/release is idempotent) or unknown

    delta_cost = actual_cost_cents - reservation.estimated_cost_cents
    for key in reservation.counter_keys:
        if "cost" not in key["period_key"]:
            continue  # count-based counters (gen/drawing) are never adjusted by actual cost
        if delta_cost < 0:
            _decrement(db, scope=key["scope"], scope_id=key["scope_id"],
                      period_key=key["period_key"], amount=-delta_cost)
        elif delta_cost > 0:
            # Top up without a limit check -- the work already happened.
            db.execute(
                update(BudgetCounter)
                .where(BudgetCounter.scope == key["scope"], BudgetCounter.scope_id == key["scope_id"],
                       BudgetCounter.period_key == key["period_key"])
                .values(used=BudgetCounter.used + delta_cost, updated_at=_now()))

    db.execute(
        update(BudgetReservation).where(BudgetReservation.id == reservation_id,
                                        BudgetReservation.status == STATUS_RESERVED)
        .values(status=STATUS_COMMITTED, actual_tokens=actual_tokens,
               actual_cost_cents=actual_cost_cents, reconciled_at=_now()))
    db.commit()

    from app.metrics import (
        budget_reservations_total,
        cost_reconciliation_delta_cents,
        estimated_vs_actual_cost_cents,
    )
    budget_reservations_total.labels(
        operation_type=reservation.operation_type, outcome="committed").inc()
    cost_reconciliation_delta_cents.labels(
        operation_type=reservation.operation_type).observe(delta_cost)
    if reservation.estimated_cost_cents > 0:
        estimated_vs_actual_cost_cents.labels(operation_type=reservation.operation_type).observe(
            actual_cost_cents / reservation.estimated_cost_cents)
    log_event("budget_reservation_committed", reservation_id=reservation_id,
              estimated_cost_cents=reservation.estimated_cost_cents,
              actual_cost_cents=actual_cost_cents, delta_cents=delta_cost)


def release_reservation(db: Session, reservation_id: str, *, reason: str) -> None:
    """Full refund -- used for EVERY non-success outcome: user/validation
    failure, unsupported request, provider failure, model-schema failure,
    CAD compiler failure, CAD timeout, semantic-validation failure, export
    failure, user cancellation, internal service failure. Idempotent: a
    reservation can only be released once (the guarding UPDATE only matches
    status='reserved'); calling this again on an already-released/committed
    reservation is a safe no-op."""
    reservation = db.scalar(
        select(BudgetReservation).where(
            BudgetReservation.id == reservation_id, BudgetReservation.status == STATUS_RESERVED))
    if reservation is None:
        return

    for key in reservation.counter_keys:
        _decrement(db, scope=key["scope"], scope_id=key["scope_id"],
                  period_key=key["period_key"], amount=key["amount"])

    db.execute(
        update(BudgetReservation).where(BudgetReservation.id == reservation_id,
                                        BudgetReservation.status == STATUS_RESERVED)
        .values(status=STATUS_RELEASED, release_reason=reason[:64], reconciled_at=_now()))
    db.commit()

    from app.metrics import budget_reservations_total
    budget_reservations_total.labels(
        operation_type=reservation.operation_type, outcome="released").inc()
    log_event("budget_reservation_released", reservation_id=reservation_id, reason=reason)


def reap_stale_reservations(db: Session) -> int:
    """Release any 'reserved' row past its TTL with no terminal outcome yet
    -- the restart-safety net for a process that died between reserve and
    commit/release (mirrors app.services.job_service.reap_stale_jobs)."""
    now = _now()
    stale = list(db.scalars(
        select(BudgetReservation).where(
            BudgetReservation.status == STATUS_RESERVED,
            BudgetReservation.expires_at.is_not(None),
            BudgetReservation.expires_at < now)))
    for reservation in stale:
        release_reservation(db, reservation.id, reason="stale_ttl")
    if stale:
        from app.metrics import stale_reservations_reaped_total
        stale_reservations_reaped_total.inc(len(stale))
    return len(stale)


# --------------------------------------------------------------- admin API

def get_account_usage(db: Session, user_id: str) -> dict:
    """A snapshot of an account's current-period usage against its
    EFFECTIVE (override-applied) policy -- for the admin "view an account's
    quota state" capability."""
    now = _now()
    policy = effective_policy(db, user_id)
    day, month = _day_key(now), _month_key(now)

    def used(period_key: str) -> int:
        return db.scalar(
            select(BudgetCounter.used).where(
                BudgetCounter.scope == SCOPE_ACCOUNT, BudgetCounter.scope_id == user_id,
                BudgetCounter.period_key == period_key)) or 0

    return {
        "user_id": user_id,
        "policy_version": policy.version,
        "generation_used_today": used(f"gen:daily:{day}"),
        "generation_limit_daily": policy.daily_generation_limit,
        "generation_used_this_month": used(f"gen:monthly:{month}"),
        "generation_limit_monthly": policy.monthly_generation_limit,
        "drawing_used_today": used(f"drawing:daily:{day}"),
        "drawing_limit_daily": policy.daily_drawing_limit,
        "cost_used_today_cents": used(f"cost:daily:{day}"),
        "cost_limit_daily_cents": policy.max_daily_account_cost_cents,
        "generation_disabled": policy.generation_disabled,
        "disabled_reason": policy.disabled_reason,
    }


def get_global_usage(db: Session) -> dict:
    now = _now()
    day, hour = _day_key(now), _hour_key(now)

    def used(period_key: str) -> int:
        return db.scalar(
            select(BudgetCounter.used).where(
                BudgetCounter.scope == SCOPE_GLOBAL, BudgetCounter.scope_id == GLOBAL_SCOPE_ID,
                BudgetCounter.period_key == period_key)) or 0

    from app.config import settings
    stop = db.get(EmergencyStop, 1)
    return {
        "global_cost_used_today_cents": used(f"cost:daily:{day}"),
        "global_daily_budget_cents": settings.cost_global_daily_budget_cents,
        "global_cost_used_this_hour_cents": used(f"cost:hourly:{hour}"),
        "global_hourly_emergency_budget_cents": settings.cost_global_hourly_emergency_budget_cents,
        "emergency_stop_active": bool(stop.active) if stop else False,
        "emergency_stop_reason": stop.reason if stop else None,
    }


def _audit(db: Session, *, admin_user_id: str, action: str,
          target_user_id: str | None = None, details: dict | None = None) -> None:
    db.add(AdminAuditLog(admin_user_id=admin_user_id, action=action,
                         target_user_id=target_user_id, details=details or {}))
    db.commit()


def set_generation_disabled(
    db: Session, *, admin_user_id: str, target_user_id: str, disabled: bool,
    reason: str | None = None,
) -> None:
    override = db.get(AccountLimitOverride, target_user_id)
    if override is None:
        override = AccountLimitOverride(user_id=target_user_id)
        db.add(override)
    override.generation_disabled = disabled
    override.disabled_reason = reason if disabled else None
    override.updated_by_user_id = admin_user_id
    db.commit()
    _audit(db, admin_user_id=admin_user_id,
          action="enable_generation" if not disabled else "disable_generation",
          target_user_id=target_user_id, details={"reason": reason})


def adjust_account_limits(
    db: Session, *, admin_user_id: str, target_user_id: str, **limit_fields,
) -> AccountLimitOverride:
    """``limit_fields`` are any of AccountLimitOverride's nullable override
    columns (daily_generation_limit=..., storage_quota_mb=..., etc). Only the
    provided keys are changed; pass None explicitly to clear an override back
    to the global default."""
    allowed = {
        "daily_generation_limit", "monthly_generation_limit", "daily_drawing_limit",
        "concurrent_job_limit", "daily_cost_cap_cents", "storage_quota_mb",
        "max_design_versions",
    }
    unknown = set(limit_fields) - allowed
    if unknown:
        raise ValueError(f"Unknown limit field(s): {sorted(unknown)}")

    override = db.get(AccountLimitOverride, target_user_id)
    if override is None:
        override = AccountLimitOverride(user_id=target_user_id)
        db.add(override)
    for field, value in limit_fields.items():
        setattr(override, field, value)
    override.updated_by_user_id = admin_user_id
    db.commit()
    db.refresh(override)
    _audit(db, admin_user_id=admin_user_id, action="adjust_limits",
          target_user_id=target_user_id, details=dict(limit_fields))
    return override


def set_emergency_stop(db: Session, *, admin_user_id: str, active: bool, reason: str | None = None) -> None:
    row = db.get(EmergencyStop, 1)
    if row is None:
        row = EmergencyStop(id=1)
        db.add(row)
    row.active = active
    row.reason = reason if active else None
    if active:
        row.activated_by_user_id = admin_user_id
        row.activated_at = _now()
    else:
        row.deactivated_at = _now()
    db.commit()
    _audit(db, admin_user_id=admin_user_id,
          action="emergency_stop_activate" if active else "emergency_stop_deactivate",
          details={"reason": reason})
    from app.metrics import emergency_stop_active
    emergency_stop_active.set(1 if active else 0)


def admin_release_stale_reservations(db: Session, *, admin_user_id: str) -> int:
    count = reap_stale_reservations(db)
    _audit(db, admin_user_id=admin_user_id, action="release_stale_reservations",
          details={"count": count})
    return count
