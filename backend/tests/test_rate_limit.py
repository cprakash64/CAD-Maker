"""Rate limiting on abuse-prone routes.

Limiting is OFF in dev/test by default (so the rest of the suite is unaffected);
these tests opt in by flipping ``settings.rate_limit_enabled`` and use tiny
limits. Each test resets the in-memory counters first.
"""
from __future__ import annotations

import pytest

from app.config import settings
from app.rate_limit import reset_rate_limit


@pytest.fixture
def rl(monkeypatch):
    """Enable rate limiting with a clean counter store for one test."""
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    reset_rate_limit()
    assert settings.rate_limit_active() is True
    yield
    reset_rate_limit()


def _login(client, email="nobody@example.com"):
    return client.post("/api/auth/login", json={"email": email, "password": "whatever1"})


# --- auth (IP-based) ------------------------------------------------------
def test_login_is_rate_limited_per_ip(client, rl, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_auth", "3/60")
    # 3 attempts allowed (each a normal 401), the 4th is blocked.
    for _ in range(3):
        assert _login(client).status_code == 401
    blocked = _login(client)
    assert blocked.status_code == 429
    assert "Retry-After" in blocked.headers
    assert blocked.headers["Retry-After"].isdigit()
    assert "rate limit" in blocked.json()["detail"].lower()


def test_signup_shares_the_auth_bucket(client, rl, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_auth", "2/60")
    # signup + login both count against the same per-IP "auth" budget.
    r1 = client.post("/api/auth/signup",
                     json={"email": "a@example.com", "password": "password123"})
    assert r1.status_code == 201
    assert _login(client).status_code == 401          # 2nd auth hit: allowed
    assert _login(client).status_code == 429          # 3rd: blocked


# --- design creation (user-based) -----------------------------------------
def test_create_is_rate_limited_per_user(client, auth, rl, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_create", "2/60")
    # A fast-clarifying prompt avoids heavy CAD work but still hits the route.
    body = {"prompt": "make me a blind flange"}
    for _ in range(2):
        assert client.post("/api/designs/create", json=body,
                           headers=auth["headers"]).status_code == 200
    blocked = client.post("/api/designs/create", json=body, headers=auth["headers"])
    assert blocked.status_code == 429
    assert blocked.headers.get("Retry-After", "").isdigit()


def test_create_limit_is_isolated_per_user(client, auth, auth2, rl, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_create", "1/60")
    body = {"prompt": "make me a blind flange"}
    # User A spends their single allowance...
    assert client.post("/api/designs/create", json=body,
                       headers=auth["headers"]).status_code == 200
    assert client.post("/api/designs/create", json=body,
                       headers=auth["headers"]).status_code == 429
    # ...while user B still has theirs (per-user, not global).
    assert client.post("/api/designs/create", json=body,
                       headers=auth2["headers"]).status_code == 200


# --- disabled by default --------------------------------------------------
def test_disabled_by_default_in_tests(client, monkeypatch):
    # No `rl` fixture: limiting is inactive, so repeated calls never 429.
    monkeypatch.setattr(settings, "rate_limit_auth", "1/60")
    assert settings.rate_limit_active() is False
    statuses = {_login(client).status_code for _ in range(5)}
    assert 429 not in statuses
