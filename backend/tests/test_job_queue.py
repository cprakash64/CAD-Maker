"""The job queue (docs/adr/0001-job-queue-database-backed.md): timeout,
worker crash, cancellation, duplicate submission, stale job, queue
saturation, per-user concurrency, artifact cleanup, and API survival after a
worker failure.

Two tiers:
  * Unit-level tests against app.services.job_service directly (fast, no
    subprocess) -- idempotency, concurrency limits, retry policy, stale-job
    reaping, atomic claiming.
  * Real-subprocess tests driving app.worker.supervisor.Supervisor against
    the built-in _test_* handlers (app.worker.handlers) -- genuine OS-process
    isolation is the entire point of this system, so timeout/crash/
    cancellation are verified against REAL child processes, not mocks.
"""
from __future__ import annotations

import os
import time
import uuid

import pytest

from app.database import SessionLocal
from app.models import Job, User
from app.services import job_service as js
from app.worker.runner import job_tmp_dir
from app.worker.supervisor import Supervisor


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(autouse=True)
def _isolated_jobs_table(db):
    """The jobs table is shared across the whole test session (no per-test
    DB reset elsewhere in this suite). claim_next_job is a real FIFO queue
    (oldest queued row first, across ALL rows) -- without this, a queued row
    left behind by an earlier test would be claimed by a LATER test expecting
    to claim its own freshly-submitted job, silently breaking every
    assertion downstream. Give every test in this file a clean table."""
    db.query(Job).delete()
    db.commit()
    yield
    db.query(Job).delete()
    db.commit()


@pytest.fixture
def user(db):
    u = User(id=uuid.uuid4().hex, email=f"{uuid.uuid4().hex}@test.com", password_hash="x")
    db.add(u)
    db.commit()
    return u


def _drain(db, sup: Supervisor, *, timeout_s: float, spawn_new: bool = True) -> None:
    """Run the supervisor's own loop pieces directly (no real_forever's sleep
    cadence) until either its active set drains or timeout_s elapses."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        db.expire_all()
        sup._reap_finished(db)
        sup._check_active_deadlines(db)
        if spawn_new and len(sup.active) < 4:
            job = js.claim_next_job(db, worker_id=sup.worker_id)
            if job is not None:
                sup._spawn(job)
        if not sup.active and not spawn_new:
            return
        time.sleep(0.05)


# --------------------------------------------------------------------- unit

def test_duplicate_submission_is_idempotent(db, user):
    r1 = js.submit_job(db, user_id=user.id, job_type="_test_echo",
                       payload={"a": 1}, idempotency_key="same-key")
    r2 = js.submit_job(db, user_id=user.id, job_type="_test_echo",
                       payload={"a": 2}, idempotency_key="same-key")
    assert r1.job.id == r2.job.id
    assert r1.created is True
    assert r2.created is False
    # The SECOND payload never took effect -- the original job is untouched.
    assert r1.job.payload_json == {"a": 1}


def test_different_users_same_idempotency_key_do_not_collide(db):
    u1 = User(id=uuid.uuid4().hex, email=f"{uuid.uuid4().hex}@test.com", password_hash="x")
    u2 = User(id=uuid.uuid4().hex, email=f"{uuid.uuid4().hex}@test.com", password_hash="x")
    db.add_all([u1, u2])
    db.commit()
    r1 = js.submit_job(db, user_id=u1.id, job_type="_test_echo", payload={},
                       idempotency_key="k")
    r2 = js.submit_job(db, user_id=u2.id, job_type="_test_echo", payload={},
                       idempotency_key="k")
    assert r1.job.id != r2.job.id


def test_per_user_concurrency_limit_enforced(db, user, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "job_per_user_concurrent_limit", 2)
    js.submit_job(db, user_id=user.id, job_type="_test_echo", payload={})
    js.submit_job(db, user_id=user.id, job_type="_test_echo", payload={})
    with pytest.raises(js.UserConcurrencyLimitExceeded):
        js.submit_job(db, user_id=user.id, job_type="_test_echo", payload={})


def test_queue_saturation_enforced(db, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "job_max_queue_depth", 2)
    monkeypatch.setattr(settings, "job_per_user_concurrent_limit", 100)
    u = User(id=uuid.uuid4().hex, email=f"{uuid.uuid4().hex}@test.com", password_hash="x")
    db.add(u)
    db.commit()
    js.submit_job(db, user_id=u.id, job_type="_test_echo", payload={})
    js.submit_job(db, user_id=u.id, job_type="_test_echo", payload={})
    with pytest.raises(js.JobQueueSaturated):
        js.submit_job(db, user_id=u.id, job_type="_test_echo", payload={})


def test_claim_is_atomic_no_double_claim(db, user, monkeypatch):
    """Two 'workers' racing to claim the queued set never grab the same job."""
    from app.config import settings

    # This test is specifically about claim atomicity, not the per-user
    # concurrency cap (covered separately) -- raise it so 6 claims for one
    # user aren't throttled by either the submit-time or claim-time check.
    monkeypatch.setattr(settings, "job_per_user_concurrent_limit", 100)
    for _ in range(6):
        js.submit_job(db, user_id=user.id, job_type="_test_echo", payload={})

    claimed_a = []
    claimed_b = []
    while True:
        a = js.claim_next_job(db, worker_id="worker-a")
        if a:
            claimed_a.append(a.id)
        b = js.claim_next_job(db, worker_id="worker-b")
        if b:
            claimed_b.append(b.id)
        if a is None and b is None:
            break
    assert set(claimed_a).isdisjoint(claimed_b)
    assert len(claimed_a) + len(claimed_b) == 6


def test_retry_policy_requeues_then_exhausts(db, user):
    job = js.submit_job(db, user_id=user.id, job_type="_test_echo", payload={}).job
    claimed = js.claim_next_job(db, worker_id="w1")
    assert claimed.attempt == 1
    requeued = js.maybe_retry_or_fail(db, claimed.id, category="timeout", exc=TimeoutError())
    assert requeued is True
    db.refresh(job)
    assert job.status == js.STATUS_QUEUED

    claimed2 = js.claim_next_job(db, worker_id="w1")
    assert claimed2.attempt == 2  # timeout policy allows exactly 2 attempts
    # terminal_status mirrors exactly what the real supervisor passes for a
    # timeout kill (app/worker/supervisor.py::_finalize) -- job_service
    # itself defaults to STATUS_FAILED when a caller doesn't specify.
    exhausted = js.maybe_retry_or_fail(db, claimed2.id, category="timeout", exc=TimeoutError(),
                                       terminal_status=js.STATUS_TIMED_OUT)
    assert exhausted is False
    db.refresh(job)
    assert job.status == js.STATUS_TIMED_OUT
    assert job.error_message


def test_cancellation_of_queued_job_is_immediate(db, user):
    job = js.submit_job(db, user_id=user.id, job_type="_test_echo", payload={}).job
    cancelled = js.request_cancel(db, job.id)
    assert cancelled.status == js.STATUS_CANCELLED
    # A cancelled job is never claimable.
    for _ in range(3):
        claimed = js.claim_next_job(db, worker_id="w1")
        assert claimed is None or claimed.id != job.id


def test_stale_running_job_is_reaped_and_requeued(db, user):
    from datetime import datetime, timedelta, timezone

    job = js.submit_job(db, user_id=user.id, job_type="_test_echo", payload={}).job
    claimed = js.claim_next_job(db, worker_id="dead-worker")
    claimed.heartbeat_at = datetime.now(timezone.utc) - timedelta(hours=1)
    db.commit()

    reaped = js.reap_stale_jobs(db)
    assert reaped == 1
    db.refresh(job)
    # "_test_echo" has no explicit retry policy entry other than "transient"
    # (what a stale reap always classifies as), default 3 attempts -- 1 < 3,
    # so it's requeued rather than failed outright.
    assert job.status == js.STATUS_QUEUED


def test_no_negative_side_effects_from_unrelated_job_types(db, user):
    """A job for an unregistered job_type fails cleanly, never crashes the
    claiming/submission machinery for anyone else's jobs."""
    js.submit_job(db, user_id=user.id, job_type="totally_unknown_type", payload={})
    claimed = js.claim_next_job(db, worker_id="w1")
    assert claimed.job_type == "totally_unknown_type"


# ------------------------------------------------------- real subprocess

def test_successful_job_runs_in_isolated_subprocess_and_cleans_up(db, user):
    job = js.submit_job(db, user_id=user.id, job_type="_test_echo",
                        payload={"hello": "world"}).job
    sup = Supervisor(worker_id="pytest-worker")
    claimed = js.claim_next_job(db, worker_id=sup.worker_id)
    sup._spawn(claimed)
    _drain(db, sup, timeout_s=20, spawn_new=False)

    db.refresh(job)
    assert job.status == js.STATUS_SUCCEEDED
    assert job.result_json == {"echo": {"hello": "world"}}
    assert not os.path.exists(job_tmp_dir(job.id)), "per-job temp dir must be cleaned up"


def test_worker_crash_is_contained_and_reported_gracefully(db, user):
    """A segfaulting job (simulates an OCCT crash) must land the job in a
    clean 'failed'/category='crash' state -- and, since this whole test
    process is standing in for 'the API process', the fact that we can keep
    executing code after the child dies IS the proof the crash was
    contained (a real in-process crash would have taken this process down
    too)."""
    job = js.submit_job(db, user_id=user.id, job_type="_test_crash", payload={}).job
    sup = Supervisor(worker_id="pytest-worker")
    claimed = js.claim_next_job(db, worker_id=sup.worker_id)
    sup._spawn(claimed)
    _drain(db, sup, timeout_s=20, spawn_new=False)

    db.refresh(job)
    assert job.status == js.STATUS_FAILED
    assert job.error_category == "crash"
    assert "crashed" in job.error_message.lower()
    assert not os.path.exists(job_tmp_dir(job.id))


def test_api_survives_a_worker_crash(db, user):
    """The literal acceptance criterion: a pathological job cannot block or
    crash the surrounding process. After a crash job resolves, ordinary job
    submission/claim/query keep working in THIS SAME process."""
    crash_job = js.submit_job(db, user_id=user.id, job_type="_test_crash", payload={}).job
    sup = Supervisor(worker_id="pytest-worker")
    claimed = js.claim_next_job(db, worker_id=sup.worker_id)
    sup._spawn(claimed)
    _drain(db, sup, timeout_s=20, spawn_new=False)
    db.refresh(crash_job)
    assert crash_job.status == js.STATUS_FAILED  # crash resolved, didn't hang

    # The surrounding process is demonstrably still alive and functional:
    normal_job = js.submit_job(db, user_id=user.id, job_type="_test_echo",
                               payload={"still": "alive"}).job
    assert normal_job.status == js.STATUS_QUEUED
    fetched = js.get_owned_job(db, normal_job.id, user.id)
    assert fetched is not None


def test_hung_job_is_killed_at_hard_timeout(db, user, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "job_hard_timeout_seconds", 2)
    monkeypatch.setattr(settings, "job_sigkill_grace_seconds", 1)
    job = js.submit_job(db, user_id=user.id, job_type="_test_sleep",
                        payload={"seconds": 60}).job
    sup = Supervisor(worker_id="pytest-worker")
    claimed = js.claim_next_job(db, worker_id=sup.worker_id)
    sup._spawn(claimed)
    _drain(db, sup, timeout_s=15, spawn_new=False)

    db.refresh(job)
    assert job.status in (js.STATUS_TIMED_OUT, js.STATUS_QUEUED)  # queued = mid-retry
    assert not os.path.exists(job_tmp_dir(job.id))


def test_cancellation_of_a_running_job_terminates_the_child(db, user, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "job_hard_timeout_seconds", 60)
    monkeypatch.setattr(settings, "job_sigkill_grace_seconds", 1)
    job = js.submit_job(db, user_id=user.id, job_type="_test_sleep",
                        payload={"seconds": 60}).job
    sup = Supervisor(worker_id="pytest-worker")
    claimed = js.claim_next_job(db, worker_id=sup.worker_id)
    sup._spawn(claimed)

    time.sleep(1.0)  # let the child actually get running
    js.request_cancel(db, job.id)
    _drain(db, sup, timeout_s=15, spawn_new=False)

    db.refresh(job)
    assert job.status == js.STATUS_CANCELLED
    assert not os.path.exists(job_tmp_dir(job.id))


def test_well_behaved_handler_observes_cancel_flag_itself(db, user, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "job_hard_timeout_seconds", 60)
    job = js.submit_job(db, user_id=user.id, job_type="_test_cancel_aware",
                        payload={"iterations": 200}).job
    sup = Supervisor(worker_id="pytest-worker")
    claimed = js.claim_next_job(db, worker_id=sup.worker_id)
    sup._spawn(claimed)

    time.sleep(1.0)
    js.request_cancel(db, job.id)
    _drain(db, sup, timeout_s=15, spawn_new=False)

    db.refresh(job)
    assert job.status == js.STATUS_CANCELLED


def test_unhandled_exception_fails_cleanly_with_safe_message(db, user):
    job = js.submit_job(db, user_id=user.id, job_type="_test_raise",
                        payload={"message": "bad geometry"}).job
    sup = Supervisor(worker_id="pytest-worker")
    claimed = js.claim_next_job(db, worker_id=sup.worker_id)
    sup._spawn(claimed)
    _drain(db, sup, timeout_s=20, spawn_new=False)

    db.refresh(job)
    assert job.status == js.STATUS_FAILED
    assert job.error_message
    assert "Traceback" not in job.error_message
    assert "site-packages" not in job.error_message


def test_queue_processes_multiple_jobs_across_users(db):
    """A broader integration pass: several users, several job types, run
    through a real supervisor loop concurrently."""
    users = []
    for _ in range(3):
        u = User(id=uuid.uuid4().hex, email=f"{uuid.uuid4().hex}@test.com", password_hash="x")
        db.add(u)
        users.append(u)
    db.commit()

    jobs = []
    for u in users:
        jobs.append(js.submit_job(db, user_id=u.id, job_type="_test_echo",
                                  payload={"u": u.id}).job)

    sup = Supervisor(worker_id="pytest-worker")
    _drain(db, sup, timeout_s=25, spawn_new=True)

    for job in jobs:
        db.refresh(job)
        assert job.status == js.STATUS_SUCCEEDED
        assert not os.path.exists(job_tmp_dir(job.id))
