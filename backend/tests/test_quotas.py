"""Per-account generation and storage quotas
(docs/operations/cost-control-architecture.md) -- distinct from rate
limiting (burst rate) and concurrency limits (how many at once): these
bound total VOLUME per account per day/month, and total storage.

Daily/monthly generation-count enforcement moved from a live-COUNT check
(settings.quota_designs_per_day/month, superseded) to the atomic budget
reservation ledger (settings.cost_daily_generation_limit/
cost_monthly_generation_limit, app.cost_control.service) -- see
test_cost_control.py for the reservation-engine-level tests (including the
real-Postgres concurrency proof); this file covers the same behavior at the
HTTP/API level. Storage quota is unchanged in mechanism (still a live SUM
check, app.services.job_service._check_storage_quota) but its error
response now uses the same structured contract as every other cost-control
rejection (app.cost_control.http)."""
import pytest

from app.config import settings


def _create(client, auth, prompt="a bracket 80x40x6mm"):
    return client.post("/api/designs/create", json={"prompt": prompt}, headers=auth["headers"])


def test_daily_quota_blocks_further_creates(client, auth, monkeypatch):
    monkeypatch.setattr(settings, "cost_daily_generation_limit", 2)
    monkeypatch.setattr(settings, "cost_monthly_generation_limit", 0)
    monkeypatch.setattr(settings, "storage_quota_mb_per_user", 0)
    assert _create(client, auth).status_code == 200
    assert _create(client, auth).status_code == 200
    r = _create(client, auth)
    assert r.status_code == 429
    body = r.json()["detail"]
    assert body["code"] == "DAILY_LIMIT_REACHED"
    assert "limit" in body["message"].lower() or "quota" in body["message"].lower()


def test_daily_quota_is_per_user(client, auth, auth2, monkeypatch):
    monkeypatch.setattr(settings, "cost_daily_generation_limit", 1)
    monkeypatch.setattr(settings, "cost_monthly_generation_limit", 0)
    monkeypatch.setattr(settings, "storage_quota_mb_per_user", 0)
    assert _create(client, auth).status_code == 200
    assert _create(client, auth).status_code == 429
    # A different account still has its own untouched quota.
    assert _create(client, auth2).status_code == 200


def test_monthly_quota_blocks_further_creates(client, auth, monkeypatch):
    monkeypatch.setattr(settings, "cost_daily_generation_limit", 0)
    monkeypatch.setattr(settings, "cost_monthly_generation_limit", 1)
    monkeypatch.setattr(settings, "storage_quota_mb_per_user", 0)
    assert _create(client, auth).status_code == 200
    r = _create(client, auth)
    assert r.status_code == 429
    assert r.json()["detail"]["code"] == "MONTHLY_LIMIT_REACHED"


def test_quota_disabled_by_default_zero(client, auth, monkeypatch):
    monkeypatch.setattr(settings, "cost_daily_generation_limit", 0)
    monkeypatch.setattr(settings, "cost_monthly_generation_limit", 0)
    monkeypatch.setattr(settings, "storage_quota_mb_per_user", 0)
    for _ in range(3):
        assert _create(client, auth).status_code == 200


def test_storage_quota_blocks_further_creates(client, auth, monkeypatch):
    import app.services.job_service as job_service

    monkeypatch.setattr(settings, "cost_daily_generation_limit", 0)
    monkeypatch.setattr(settings, "cost_monthly_generation_limit", 0)
    monkeypatch.setattr(settings, "storage_quota_mb_per_user", 1)
    # Simulate an account that's already well past a 1 MB cap, rather than
    # depending on the exact byte size of a generated STL/STEP pair.
    monkeypatch.setattr(job_service, "_user_storage_bytes", lambda db, user_id: 10 * 1024 * 1024)
    r = _create(client, auth)
    assert r.status_code == 429
    body = r.json()["detail"]
    assert body["code"] == "STORAGE_QUOTA_REACHED"
    assert "storage" in body["message"].lower()


def test_storage_quota_allows_create_when_under_cap(client, auth, monkeypatch):
    import app.services.job_service as job_service

    monkeypatch.setattr(settings, "cost_daily_generation_limit", 0)
    monkeypatch.setattr(settings, "cost_monthly_generation_limit", 0)
    monkeypatch.setattr(settings, "storage_quota_mb_per_user", 1000)
    monkeypatch.setattr(job_service, "_user_storage_bytes", lambda db, user_id: 1024)
    assert _create(client, auth).status_code == 200


def test_idempotent_resubmit_bypasses_quota_check(client, auth, monkeypatch):
    """Resubmitting the SAME idempotency key returns the original job even
    with the quota already exhausted -- it isn't a new generation."""
    monkeypatch.setattr(settings, "cost_daily_generation_limit", 1)
    monkeypatch.setattr(settings, "cost_monthly_generation_limit", 0)
    monkeypatch.setattr(settings, "storage_quota_mb_per_user", 0)
    headers = {**auth["headers"], "Idempotency-Key": "same-key-1"}
    r1 = client.post("/api/designs/create", json={"prompt": "a bracket"}, headers=headers)
    assert r1.status_code == 200
    r2 = client.post("/api/designs/create", json={"prompt": "a bracket"}, headers=headers)
    assert r2.status_code == 200
    assert r1.json()["id"] == r2.json()["id"]
