"""Per-account cost controls and abuse protection
(docs/operations/cost-control-architecture.md).

Three tiers, matching tests/test_job_queue.py's own layering:
  * Unit-level tests against app.cost_control.service directly (fast, no
    HTTP) -- reservation, commit, release, reconciliation, idempotency,
    stale-reservation reaping.
  * HTTP-level tests via the TestClient (conftest's client/auth/auth2) --
    end-to-end limit enforcement through the real endpoints, admin
    authorization, and the structured error-contract shape.
  * Real-Postgres concurrency tests (opt-in via CADMAKER_TEST_PG_URL,
    same convention as tests/test_migrations.py) -- true concurrent
    transactions from separate connections proving the atomic
    conditional-UPDATE counter can't be raced past its limit, and that
    state survives a fresh session/"process restart".
"""
from __future__ import annotations

import os
import threading
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import SessionLocal
from app.models import (
    AccountLimitOverride,
    BudgetCounter,
    BudgetReservation,
    EmergencyStop,
    Job,
    User,
)
from app.cost_control import service as cost_service
from app.cost_control.estimator import CostEstimate, estimate_request_cost


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(autouse=True)
def _isolated_cost_control_tables(db):
    """Same rationale as test_job_queue.py's _isolated_jobs_table: these
    tables are shared across the whole test session with no other reset."""
    db.query(BudgetReservation).delete()
    db.query(BudgetCounter).delete()
    db.query(AccountLimitOverride).delete()
    db.query(EmergencyStop).delete()
    db.commit()
    yield
    db.query(BudgetReservation).delete()
    db.query(BudgetCounter).delete()
    db.query(AccountLimitOverride).delete()
    db.query(EmergencyStop).delete()
    db.commit()


@pytest.fixture
def user(db):
    u = User(id=uuid.uuid4().hex, email=f"{uuid.uuid4().hex}@test.com", password_hash="x")
    db.add(u)
    db.commit()
    return u


@pytest.fixture
def user2(db):
    u = User(id=uuid.uuid4().hex, email=f"{uuid.uuid4().hex}@test.com", password_hash="x")
    db.add(u)
    db.commit()
    return u


def _tiny_estimate(cost_cents: int = 1, tokens: int = 100) -> CostEstimate:
    return CostEstimate(
        operation_type="design_create", model="gpt-4o-mini",
        estimated_input_tokens=tokens, estimated_output_tokens=0,
        estimated_total_tokens=tokens, estimated_cost_cents=cost_cents,
    )


# ============================================================ 1. daily limit

def test_daily_account_generation_limit_enforced(db, user, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "cost_daily_generation_limit", 2)
    est = _tiny_estimate()
    cost_service.reserve_budget(db, user_id=user.id, operation_type="design_create", estimate=est)
    cost_service.reserve_budget(db, user_id=user.id, operation_type="design_create", estimate=est)
    with pytest.raises(cost_service.DailyLimitExceeded):
        cost_service.reserve_budget(db, user_id=user.id, operation_type="design_create", estimate=est)


# ========================================================== 2. monthly limit

def test_monthly_account_generation_limit_enforced(db, user, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "cost_monthly_generation_limit", 2)
    monkeypatch.setattr(settings, "cost_daily_generation_limit", 1000)
    est = _tiny_estimate()
    cost_service.reserve_budget(db, user_id=user.id, operation_type="design_create", estimate=est)
    cost_service.reserve_budget(db, user_id=user.id, operation_type="design_create", estimate=est)
    with pytest.raises(cost_service.MonthlyLimitExceeded):
        cost_service.reserve_budget(db, user_id=user.id, operation_type="design_create", estimate=est)


# ================================================ 3. concurrent race (SQLite)

def test_concurrent_reservations_never_exceed_the_limit_sqlite(monkeypatch):
    """Even on SQLite (single-file, coarser locking than Postgres row locks),
    the atomic conditional UPDATE must never let two threads both succeed
    past the limit -- see test_concurrent_reservations_never_exceed_the_limit_postgres
    below for the real-row-lock proof on the production database engine."""
    from app.config import settings

    monkeypatch.setattr(settings, "cost_daily_generation_limit", 5)
    monkeypatch.setattr(settings, "cost_monthly_generation_limit", 1000)
    monkeypatch.setattr(settings, "cost_max_daily_account_cost_cents", 1_000_000)
    monkeypatch.setattr(settings, "cost_global_daily_budget_cents", 1_000_000)
    monkeypatch.setattr(settings, "cost_global_hourly_emergency_budget_cents", 1_000_000)

    session = SessionLocal()
    uid = uuid.uuid4().hex
    session.add(User(id=uid, email=f"{uid}@test.com", password_hash="x"))
    session.commit()
    session.close()

    successes = []
    failures = []
    lock = threading.Lock()

    def attempt():
        s = SessionLocal()
        try:
            cost_service.reserve_budget(
                s, user_id=uid, operation_type="design_create", estimate=_tiny_estimate())
            with lock:
                successes.append(1)
        except cost_service.DailyLimitExceeded:
            with lock:
                failures.append(1)
        finally:
            s.close()

    threads = [threading.Thread(target=attempt) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(successes) == 5, f"expected exactly 5 successes, got {len(successes)}"
    assert len(failures) == 15


# =============================================================== 4. global budget

def test_global_daily_budget_enforced(db, user, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "cost_global_daily_budget_cents", 10)
    monkeypatch.setattr(settings, "cost_max_daily_account_cost_cents", 1_000_000)
    est = _tiny_estimate(cost_cents=6)
    cost_service.reserve_budget(db, user_id=user.id, operation_type="design_create", estimate=est)
    with pytest.raises(cost_service.GlobalBudgetExceeded):
        cost_service.reserve_budget(db, user_id=user.id, operation_type="design_create", estimate=est)


def test_global_hourly_emergency_budget_enforced(db, user, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "cost_global_hourly_emergency_budget_cents", 10)
    monkeypatch.setattr(settings, "cost_global_daily_budget_cents", 1_000_000)
    monkeypatch.setattr(settings, "cost_max_daily_account_cost_cents", 1_000_000)
    est = _tiny_estimate(cost_cents=6)
    cost_service.reserve_budget(db, user_id=user.id, operation_type="design_create", estimate=est)
    with pytest.raises(cost_service.GlobalBudgetExceeded):
        cost_service.reserve_budget(db, user_id=user.id, operation_type="design_create", estimate=est)


def test_one_account_cannot_exhaust_the_global_budget_alone(db, user, user2, monkeypatch):
    """The core "one user must not consume the total budget" requirement:
    account A hitting its OWN daily cost cap must still leave global budget
    for account B."""
    from app.config import settings

    monkeypatch.setattr(settings, "cost_max_daily_account_cost_cents", 10)
    monkeypatch.setattr(settings, "cost_global_daily_budget_cents", 1_000_000)
    est = _tiny_estimate(cost_cents=6)
    cost_service.reserve_budget(db, user_id=user.id, operation_type="design_create", estimate=est)
    with pytest.raises(cost_service.AccountCostCapExceeded):
        cost_service.reserve_budget(db, user_id=user.id, operation_type="design_create", estimate=est)
    # user2 is unaffected -- their OWN cap is independent.
    cost_service.reserve_budget(db, user_id=user2.id, operation_type="design_create", estimate=est)


# ======================================================= 5. per-IP unauthenticated

def test_per_ip_unauthenticated_abuse_is_rate_limited(client, monkeypatch):
    """Cost control is layered on TOP OF (not instead of) app.rate_limit's
    existing per-IP limiting for unauthenticated requests -- this proves
    that layer is still in force and reached before any auth/cost check."""
    from app import rate_limit as rl
    from app.config import settings

    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_auth", "3/60")
    rl.reset_rate_limit()
    try:
        statuses = [client.post("/api/auth/login", json={
            "email": "nobody@example.com", "password": "wrong"}).status_code
            for _ in range(6)]
        assert 429 in statuses
    finally:
        rl.reset_rate_limit()


# ======================================================== 6. failed request refund

def test_failed_inline_operation_is_fully_refunded(db, user, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "cost_daily_generation_limit", 1)
    with pytest.raises(RuntimeError):
        with cost_service.guarded_operation(db, user_id=user.id, operation_type="design_create"):
            raise RuntimeError("simulated user-facing validation failure")
    # The refund must have happened -- a second attempt succeeds under the
    # same limit=1 policy (proving the first reservation was released, not
    # left consuming the account's only daily slot).
    with cost_service.guarded_operation(db, user_id=user.id, operation_type="design_create"):
        pass
    reservations = db.query(BudgetReservation).filter(BudgetReservation.user_id == user.id).all()
    assert len(reservations) == 2
    assert reservations[0].status == "released"
    assert reservations[1].status == "committed"


# ===================================================== 7. provider failure refund

def test_provider_failure_job_fully_refunds_reservation(db, user):
    from app.services import job_service as js

    est = estimate_request_cost("design_create")
    submission = js.submit_job(db, user_id=user.id, job_type="design_create",
                               payload={"prompt": "x"})
    reservation = db.query(BudgetReservation).filter(
        BudgetReservation.job_id == submission.job.id).one()
    assert reservation.status == "reserved"

    js.mark_terminal_failure(db, submission.job.id, status=js.STATUS_FAILED,
                             category="provider_unavailable", exc=None)
    db.refresh(reservation)
    assert reservation.status == "released"
    assert reservation.release_reason == "job_failed"
    counter = db.query(BudgetCounter).filter(
        BudgetCounter.scope == "account", BudgetCounter.scope_id == user.id,
        BudgetCounter.period_key.like("gen:daily:%")).one()
    assert counter.used == 0


# ====================================================== 8. compiler failure refund

def test_cad_compiler_failure_job_fully_refunds_reservation(db, user):
    from app.services import job_service as js

    submission = js.submit_job(db, user_id=user.id, job_type="design_create",
                               payload={"prompt": "x"})
    reservation = db.query(BudgetReservation).filter(
        BudgetReservation.job_id == submission.job.id).one()

    js.mark_terminal_failure(db, submission.job.id, status=js.STATUS_FAILED,
                             category="invalid_input", exc=None)
    db.refresh(reservation)
    assert reservation.status == "released"


# ======================================================= 9. successful reconciliation

def test_successful_reconciliation_refunds_unused_estimate(db, user):
    est = CostEstimate(operation_type="design_create", model="gpt-4o-mini",
                       estimated_input_tokens=1000, estimated_output_tokens=0,
                       estimated_total_tokens=1000, estimated_cost_cents=50)
    reservation = cost_service.reserve_budget(
        db, user_id=user.id, operation_type="design_create", estimate=est)
    cost_service.commit_reservation(db, reservation.id, actual_tokens=100, actual_cost_cents=5)
    db.refresh(reservation)
    assert reservation.status == "committed"
    assert reservation.actual_cost_cents == 5

    counter = db.query(BudgetCounter).filter(
        BudgetCounter.scope == "account", BudgetCounter.scope_id == user.id,
        BudgetCounter.period_key.like("cost:daily:%")).one()
    assert counter.used == 5  # 50 reserved, refunded down to the real 5


def test_reconciliation_tops_up_without_blocking_when_actual_exceeds_estimate(db, user):
    """The estimate is a conservative upper bound, but if actual usage still
    exceeds it, the ALREADY-COMPLETED job is never retroactively failed --
    the overage is recorded, not blocked."""
    est = _tiny_estimate(cost_cents=5)
    reservation = cost_service.reserve_budget(
        db, user_id=user.id, operation_type="design_create", estimate=est)
    cost_service.commit_reservation(db, reservation.id, actual_tokens=999, actual_cost_cents=50)
    db.refresh(reservation)
    assert reservation.status == "committed"
    assert reservation.actual_cost_cents == 50
    counter = db.query(BudgetCounter).filter(
        BudgetCounter.scope == "account", BudgetCounter.scope_id == user.id,
        BudgetCounter.period_key.like("cost:daily:%")).one()
    assert counter.used == 50


# ==================================================== 10. unused reservation release

def test_unused_reservation_release_zeros_out_its_counters(db, user):
    est = _tiny_estimate(cost_cents=7)
    reservation = cost_service.reserve_budget(
        db, user_id=user.id, operation_type="design_create", estimate=est)
    cost_service.release_reservation(db, reservation.id, reason="test_manual_release")
    db.refresh(reservation)
    assert reservation.status == "released"
    for key in reservation.counter_keys:
        counter = db.query(BudgetCounter).filter(
            BudgetCounter.scope == key["scope"], BudgetCounter.scope_id == key["scope_id"],
            BudgetCounter.period_key == key["period_key"]).one()
        assert counter.used == 0


def test_release_is_idempotent(db, user):
    """Calling release twice (e.g. a duplicate cleanup pass) must not
    double-refund -- the second call is a safe no-op."""
    est = _tiny_estimate(cost_cents=7)
    reservation = cost_service.reserve_budget(
        db, user_id=user.id, operation_type="design_create", estimate=est)
    cost_service.release_reservation(db, reservation.id, reason="first")
    cost_service.release_reservation(db, reservation.id, reason="second")
    counter = db.query(BudgetCounter).filter(
        BudgetCounter.scope == "account", BudgetCounter.scope_id == user.id,
        BudgetCounter.period_key.like("cost:daily:%")).one()
    assert counter.used == 0  # not -7


# =================================================== 11. duplicate idempotent request

def test_duplicate_idempotency_key_never_double_charges(db, user):
    key = uuid.uuid4().hex
    est = _tiny_estimate(cost_cents=5)
    r1 = cost_service.reserve_budget(
        db, user_id=user.id, operation_type="design_create", estimate=est, idempotency_key=key)
    r2 = cost_service.reserve_budget(
        db, user_id=user.id, operation_type="design_create", estimate=est, idempotency_key=key)
    assert r1.id == r2.id
    counter = db.query(BudgetCounter).filter(
        BudgetCounter.scope == "account", BudgetCounter.scope_id == user.id,
        BudgetCounter.period_key.like("gen:daily:%")).one()
    assert counter.used == 1  # not 2


def test_job_submission_idempotency_reuses_the_same_reservation(db, user):
    from app.services import job_service as js

    key = uuid.uuid4().hex
    s1 = js.submit_job(db, user_id=user.id, job_type="design_create",
                       payload={"prompt": "x"}, idempotency_key=key)
    s2 = js.submit_job(db, user_id=user.id, job_type="design_create",
                       payload={"prompt": "x"}, idempotency_key=key)
    assert s1.job.id == s2.job.id
    assert s2.created is False
    reservations = db.query(BudgetReservation).filter(
        BudgetReservation.job_id == s1.job.id).all()
    assert len(reservations) == 1


# ============================================================ 12. retry accounting

def test_automatic_retry_does_not_touch_the_original_reservation(db, user):
    from app.services import job_service as js

    submission = js.submit_job(db, user_id=user.id, job_type="design_create",
                               payload={"prompt": "x"})
    reservation = db.query(BudgetReservation).filter(
        BudgetReservation.job_id == submission.job.id).one()

    # "timeout" has RETRY_POLICY max_attempts=2 -- attempt 0 < 2 requeues.
    js.claim_for_inline_execution(db, submission.job.id)
    requeued = js.maybe_retry_or_fail(db, submission.job.id, category="timeout", exc=None)
    assert requeued is True
    db.refresh(reservation)
    assert reservation.status == "reserved", "a retry must NOT release/refund the reservation"

    # Exhaust the retry budget -> now it terminally fails and DOES release.
    js.claim_for_inline_execution(db, submission.job.id)
    requeued_again = js.maybe_retry_or_fail(db, submission.job.id, category="timeout", exc=None)
    assert requeued_again is False
    db.refresh(reservation)
    assert reservation.status == "released"


# ==================================================== 12b. retry-storm prevention

def test_retry_storm_prevention_caps_below_a_generous_retry_policy(db, user, monkeypatch):
    """cost_max_retries_per_request is a hard ceiling on top of
    job_service.RETRY_POLICY -- even a category configured for many retries
    must never exceed it."""
    from app.config import settings
    from app.services import job_service as js

    monkeypatch.setattr(settings, "cost_max_retries_per_request", 1)
    submission = js.submit_job(db, user_id=user.id, job_type="design_create",
                               payload={"prompt": "x"})
    # "timeout" normally allows 2 attempts (js.RETRY_POLICY) -- capped to 1 here.
    js.claim_for_inline_execution(db, submission.job.id)
    requeued = js.maybe_retry_or_fail(db, submission.job.id, category="timeout", exc=None)
    assert requeued is False, "cost_max_retries_per_request=1 must override RETRY_POLICY's 2"


# ========================================================= 13. stale reservation

def test_stale_reservation_is_reaped_and_refunded(db, user):
    est = _tiny_estimate(cost_cents=9)
    reservation = cost_service.reserve_budget(
        db, user_id=user.id, operation_type="design_create", estimate=est)
    db.execute(BudgetReservation.__table__.update().where(
        BudgetReservation.id == reservation.id
    ).values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
    db.commit()

    count = cost_service.reap_stale_reservations(db)
    assert count == 1
    db.refresh(reservation)
    assert reservation.status == "released"
    assert reservation.release_reason == "stale_ttl"


def test_non_stale_reservation_is_left_alone(db, user):
    est = _tiny_estimate()
    reservation = cost_service.reserve_budget(
        db, user_id=user.id, operation_type="design_create", estimate=est)
    count = cost_service.reap_stale_reservations(db)
    assert count == 0
    db.refresh(reservation)
    assert reservation.status == "reserved"


# ============================================================ 14. admin authorization

def test_non_admin_cannot_access_admin_endpoints(client, auth):
    r = client.get("/api/admin/cost/overview", headers=auth["headers"])
    assert r.status_code == 403


def test_unauthenticated_cannot_access_admin_endpoints(client):
    r = client.get("/api/admin/cost/overview")
    assert r.status_code == 401


def test_admin_can_access_admin_endpoints(client, auth, db):
    db.execute(User.__table__.update().where(
        User.id == auth["user"]["id"]).values(is_admin=True))
    db.commit()
    r = client.get("/api/admin/cost/overview", headers=auth["headers"])
    assert r.status_code == 200
    body = r.json()
    assert "global_daily_budget_cents" in body


def test_admin_action_is_audit_logged(client, auth, auth2, db):
    from app.models import AdminAuditLog

    db.execute(User.__table__.update().where(
        User.id == auth["user"]["id"]).values(is_admin=True))
    db.commit()
    r = client.post(f"/api/admin/cost/accounts/{auth2['user']['id']}/disable",
                    json={"reason": "abuse investigation"}, headers=auth["headers"])
    assert r.status_code == 200
    log = db.query(AdminAuditLog).filter(
        AdminAuditLog.action == "disable_generation",
        AdminAuditLog.target_user_id == auth2["user"]["id"]).one()
    assert log.admin_user_id == auth["user"]["id"]
    assert log.details.get("reason") == "abuse investigation"


def test_admin_can_adjust_account_limits(client, auth, auth2, db):
    db.execute(User.__table__.update().where(
        User.id == auth["user"]["id"]).values(is_admin=True))
    db.commit()
    r = client.patch(f"/api/admin/cost/accounts/{auth2['user']['id']}/limits",
                     json={"daily_generation_limit": 3}, headers=auth["headers"])
    assert r.status_code == 200
    override = db.get(AccountLimitOverride, auth2["user"]["id"])
    assert override.daily_generation_limit == 3


# ==================================================== 15. global emergency stop

def test_emergency_stop_blocks_all_reservations(db, user):
    cost_service.set_emergency_stop(db, admin_user_id=user.id, active=True, reason="incident")
    with pytest.raises(cost_service.EmergencyStopActive):
        cost_service.reserve_budget(
            db, user_id=user.id, operation_type="design_create", estimate=_tiny_estimate())


def test_emergency_stop_can_be_deactivated(db, user):
    cost_service.set_emergency_stop(db, admin_user_id=user.id, active=True, reason="incident")
    cost_service.set_emergency_stop(db, admin_user_id=user.id, active=False)
    # No longer raises.
    cost_service.reserve_budget(
        db, user_id=user.id, operation_type="design_create", estimate=_tiny_estimate())


def test_emergency_stop_via_admin_api(client, auth, db):
    db.execute(User.__table__.update().where(
        User.id == auth["user"]["id"]).values(is_admin=True))
    db.commit()
    r = client.post("/api/admin/cost/emergency-stop", json={"reason": "test"},
                    headers=auth["headers"])
    assert r.status_code == 200
    row = db.get(EmergencyStop, 1)
    assert row.active is True

    r2 = client.delete("/api/admin/cost/emergency-stop", headers=auth["headers"])
    assert r2.status_code == 200
    db.refresh(row)
    assert row.active is False


# ======================================================= 16. restart persistence

def test_reservation_and_counter_state_survives_a_fresh_session(user):
    """Budget state lives in the database, never only in process memory -- a
    fresh SessionLocal() (standing in for a restarted/scaled-out process)
    must see EXACTLY what an earlier session committed."""
    s1 = SessionLocal()
    est = _tiny_estimate(cost_cents=11)
    reservation = cost_service.reserve_budget(
        s1, user_id=user.id, operation_type="design_create", estimate=est)
    reservation_id = reservation.id
    s1.close()

    s2 = SessionLocal()
    try:
        reloaded = s2.get(BudgetReservation, reservation_id)
        assert reloaded is not None
        assert reloaded.status == "reserved"
        assert reloaded.estimated_cost_cents == 11
        counter = s2.query(BudgetCounter).filter(
            BudgetCounter.scope == "account", BudgetCounter.scope_id == user.id,
            BudgetCounter.period_key.like("cost:daily:%")).one()
        assert counter.used == 11
        # And the reap/release path works from this "new process" too.
        cost_service.release_reservation(s2, reservation_id, reason="cleanup_in_new_session")
        s2.refresh(reloaded)
        assert reloaded.status == "released"
    finally:
        s2.close()


# =============================================== 17. multi-worker-safe atomicity

_PG_URL = os.environ.get("CADMAKER_TEST_PG_URL")


@pytest.mark.skipif(not _PG_URL, reason="set CADMAKER_TEST_PG_URL to test Postgres concurrency")
def test_concurrent_reservations_never_exceed_the_limit_postgres(monkeypatch):
    """The real proof: N genuinely separate connections/transactions against
    PostgreSQL, racing for a budget that fits exactly K of them. Row-level
    write locking on the BudgetCounter row must serialize the racers so
    EXACTLY K succeed -- never more (over-budget) and never fewer (a
    correctly-losing racer must still see an accurate, not stale, count)."""
    from app.config import settings

    engine = create_engine(_PG_URL, future=True)
    TestSessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    # Ensure schema exists on this disposable database.
    from app.database import Base
    from app import models  # noqa: F401
    Base.metadata.create_all(bind=engine)

    monkeypatch.setattr(settings, "cost_daily_generation_limit", 5)
    monkeypatch.setattr(settings, "cost_monthly_generation_limit", 1_000_000)
    monkeypatch.setattr(settings, "cost_max_daily_account_cost_cents", 1_000_000)
    monkeypatch.setattr(settings, "cost_global_daily_budget_cents", 1_000_000)
    monkeypatch.setattr(settings, "cost_global_hourly_emergency_budget_cents", 1_000_000)

    uid = uuid.uuid4().hex
    setup = TestSessionLocal()
    setup.add(User(id=uid, email=f"{uid}@test.com", password_hash="x"))
    setup.commit()
    setup.close()

    successes, failures = [], []
    lock = threading.Lock()
    barrier = threading.Barrier(25)

    def attempt():
        s = TestSessionLocal()
        try:
            barrier.wait()  # maximize actual concurrent overlap
            cost_service.reserve_budget(
                s, user_id=uid, operation_type="design_create", estimate=_tiny_estimate())
            with lock:
                successes.append(1)
        except cost_service.DailyLimitExceeded:
            with lock:
                failures.append(1)
        finally:
            s.close()

    threads = [threading.Thread(target=attempt) for _ in range(25)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(successes) == 5, f"expected exactly 5 successes, got {len(successes)}"
    assert len(failures) == 20

    verify = TestSessionLocal()
    try:
        counter = verify.query(BudgetCounter).filter(
            BudgetCounter.scope == "account", BudgetCounter.scope_id == uid,
            BudgetCounter.period_key.like("gen:daily:%")).one()
        assert counter.used == 5, "counter must reflect exactly the successful reservations"
    finally:
        verify.query(BudgetReservation).filter(BudgetReservation.user_id == uid).delete()
        verify.query(BudgetCounter).filter(BudgetCounter.scope_id == uid).delete()
        verify.query(User).filter(User.id == uid).delete()
        verify.commit()
        verify.close()
        engine.dispose()


# ==================================================== 18. user-facing error contracts

def test_daily_limit_error_contract_shape(client, auth, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "cost_daily_generation_limit", 1)
    r1 = client.post("/api/designs/create", json={"prompt": "a small bracket, 20x20x5mm"},
                     headers=auth["headers"])
    assert r1.status_code in (200, 202)
    r2 = client.post("/api/designs/create", json={"prompt": "a small bracket, 20x20x5mm"},
                     headers=auth["headers"])
    assert r2.status_code == 429
    body = r2.json()["detail"]
    assert body["code"] == "DAILY_LIMIT_REACHED"
    assert isinstance(body["message"], str) and body["message"]
    assert body["retry_after_seconds"] == 24 * 3600
    # Never a stack trace or exception repr.
    assert "Traceback" not in str(body)
    assert "File \"" not in str(body)


def test_emergency_stop_error_contract_shape(client, auth, db):
    cost_service.set_emergency_stop(db, admin_user_id=auth["user"]["id"], active=True,
                                    reason="incident")
    try:
        r = client.post("/api/designs/create", json={"prompt": "a small bracket, 20x20x5mm"},
                        headers=auth["headers"])
        assert r.status_code == 503
        body = r.json()["detail"]
        assert body["code"] == "SERVICE_PAUSED"
    finally:
        cost_service.set_emergency_stop(db, admin_user_id=auth["user"]["id"], active=False)


def test_request_too_expensive_error_contract_shape(client, auth, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "cost_max_estimated_cost_cents_per_request", 0)
    r = client.post("/api/designs/create", json={"prompt": "a small bracket, 20x20x5mm"},
                    headers=auth["headers"])
    assert r.status_code == 422
    body = r.json()["detail"]
    assert body["code"] == "REQUEST_TOO_EXPENSIVE"


def test_generation_disabled_error_contract_shape(client, auth, db):
    cost_service.set_generation_disabled(
        db, admin_user_id=auth["user"]["id"], target_user_id=auth["user"]["id"],
        disabled=True, reason="account under review")
    r = client.post("/api/designs/create", json={"prompt": "a small bracket, 20x20x5mm"},
                    headers=auth["headers"])
    assert r.status_code == 403
    body = r.json()["detail"]
    assert body["code"] == "GENERATION_DISABLED"
    assert "account under review" in body["message"]
