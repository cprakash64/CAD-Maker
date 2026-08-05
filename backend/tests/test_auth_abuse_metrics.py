"""Authentication-abuse telemetry (docs/ops/observability.md)."""


def test_failed_login_is_logged_and_metered(client, auth, caplog):
    import logging

    with caplog.at_level(logging.INFO, logger="sourcecad"):
        r = client.post(
            "/api/auth/login",
            json={"email": auth["user"]["email"], "password": "wrong-password"},
        )
    assert r.status_code == 401
    events = [
        __import__("json").loads(rec.message)
        for rec in caplog.records if rec.message.startswith("{")
    ]
    failed = [e for e in events if e.get("event") == "auth_login_failed"]
    assert failed
    # Never the raw email -- only a fingerprint.
    assert "email" not in failed[-1]
    assert "email_hash" in failed[-1]

    text = client.get("/metrics").content.decode()
    assert 'auth_failures_total{reason="invalid_credentials"}' in text


def test_invalid_token_is_metered(client):
    r = client.get("/api/auth/me", headers={"Authorization": "Bearer not-a-real-token"})
    assert r.status_code == 401
    text = client.get("/metrics").content.decode()
    assert 'auth_failures_total{reason="invalid_token"}' in text


def test_signup_conflict_is_metered(client, auth):
    r = client.post(
        "/api/auth/signup", json={"email": auth["user"]["email"], "password": "password123"}
    )
    assert r.status_code == 409
    text = client.get("/metrics").content.decode()
    assert 'auth_failures_total{reason="signup_conflict"}' in text
