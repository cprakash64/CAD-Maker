"""Per-account generation and storage quotas (docs/ops/observability.md) --
distinct from rate limiting (burst rate) and concurrency limits (how many at
once): these bound total VOLUME per account per day/month, and total storage."""
import pytest

from app.config import settings


def _create(client, auth, prompt="a bracket 80x40x6mm"):
    return client.post("/api/designs/create", json={"prompt": prompt}, headers=auth["headers"])


def test_daily_quota_blocks_further_creates(client, auth, monkeypatch):
    monkeypatch.setattr(settings, "quota_designs_per_day", 2)
    monkeypatch.setattr(settings, "quota_designs_per_month", 0)
    monkeypatch.setattr(settings, "storage_quota_mb_per_user", 0)
    assert _create(client, auth).status_code == 200
    assert _create(client, auth).status_code == 200
    r = _create(client, auth)
    assert r.status_code == 429
    assert "quota" in r.json()["detail"].lower()


def test_daily_quota_is_per_user(client, auth, auth2, monkeypatch):
    monkeypatch.setattr(settings, "quota_designs_per_day", 1)
    monkeypatch.setattr(settings, "quota_designs_per_month", 0)
    monkeypatch.setattr(settings, "storage_quota_mb_per_user", 0)
    assert _create(client, auth).status_code == 200
    assert _create(client, auth).status_code == 429
    # A different account still has its own untouched quota.
    assert _create(client, auth2).status_code == 200


def test_monthly_quota_blocks_further_creates(client, auth, monkeypatch):
    monkeypatch.setattr(settings, "quota_designs_per_day", 0)
    monkeypatch.setattr(settings, "quota_designs_per_month", 1)
    monkeypatch.setattr(settings, "storage_quota_mb_per_user", 0)
    assert _create(client, auth).status_code == 200
    r = _create(client, auth)
    assert r.status_code == 429


def test_quota_disabled_by_default_zero(client, auth, monkeypatch):
    monkeypatch.setattr(settings, "quota_designs_per_day", 0)
    monkeypatch.setattr(settings, "quota_designs_per_month", 0)
    monkeypatch.setattr(settings, "storage_quota_mb_per_user", 0)
    for _ in range(3):
        assert _create(client, auth).status_code == 200


def test_storage_quota_blocks_further_creates(client, auth, monkeypatch):
    import app.services.job_service as job_service

    monkeypatch.setattr(settings, "quota_designs_per_day", 0)
    monkeypatch.setattr(settings, "quota_designs_per_month", 0)
    monkeypatch.setattr(settings, "storage_quota_mb_per_user", 1)
    # Simulate an account that's already well past a 1 MB cap, rather than
    # depending on the exact byte size of a generated STL/STEP pair.
    monkeypatch.setattr(job_service, "_user_storage_bytes", lambda db, user_id: 10 * 1024 * 1024)
    r = _create(client, auth)
    assert r.status_code == 429
    assert "storage" in r.json()["detail"].lower()


def test_storage_quota_allows_create_when_under_cap(client, auth, monkeypatch):
    import app.services.job_service as job_service

    monkeypatch.setattr(settings, "quota_designs_per_day", 0)
    monkeypatch.setattr(settings, "quota_designs_per_month", 0)
    monkeypatch.setattr(settings, "storage_quota_mb_per_user", 1000)
    monkeypatch.setattr(job_service, "_user_storage_bytes", lambda db, user_id: 1024)
    assert _create(client, auth).status_code == 200


def test_idempotent_resubmit_bypasses_quota_check(client, auth, monkeypatch):
    """Resubmitting the SAME idempotency key returns the original job even
    with the quota already exhausted -- it isn't a new generation."""
    monkeypatch.setattr(settings, "quota_designs_per_day", 1)
    monkeypatch.setattr(settings, "quota_designs_per_month", 0)
    monkeypatch.setattr(settings, "storage_quota_mb_per_user", 0)
    headers = {**auth["headers"], "Idempotency-Key": "same-key-1"}
    r1 = client.post("/api/designs/create", json={"prompt": "a bracket"}, headers=headers)
    assert r1.status_code == 200
    r2 = client.post("/api/designs/create", json={"prompt": "a bracket"}, headers=headers)
    assert r2.status_code == 200
    assert r1.json()["id"] == r2.json()["id"]
