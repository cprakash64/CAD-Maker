"""Self-service privacy controls (app.services.account_service):
data-improvement opt-in (default false), privacy summary, account deletion.
"""


def test_data_improvement_opt_in_defaults_to_false(client, auth):
    r = client.get("/api/auth/me", headers=auth["headers"])
    assert r.status_code == 200, r.text
    assert r.json()["data_improvement_opt_in"] is False


def test_data_improvement_opt_in_is_toggleable(client, auth):
    r = client.put(
        "/api/auth/me/data-improvement-opt-in", json={"opt_in": True},
        headers=auth["headers"],
    )
    assert r.status_code == 200, r.text
    assert r.json()["data_improvement_opt_in"] is True

    r2 = client.get("/api/auth/me", headers=auth["headers"])
    assert r2.json()["data_improvement_opt_in"] is True

    r3 = client.put(
        "/api/auth/me/data-improvement-opt-in", json={"opt_in": False},
        headers=auth["headers"],
    )
    assert r3.json()["data_improvement_opt_in"] is False


def test_privacy_summary_reflects_stored_designs_and_opt_in(client, auth):
    client.post(
        "/api/designs/create", json={"prompt": "a bracket 80x40x6mm"},
        headers=auth["headers"],
    )
    r = client.get("/api/auth/privacy-summary", headers=auth["headers"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["stored"]["designs"] >= 1
    assert body["data_improvement_opt_in"] is False
    assert "NOT used to improve" in body["model_improvement_usage"]
    assert "account_deletion" in body


def test_delete_account_requires_correct_password(client, auth):
    r = client.request(
        "DELETE", "/api/auth/me", json={"password": "wrong-password"},
        headers=auth["headers"],
    )
    assert r.status_code == 401
    # Account must still exist -- login still works.
    r2 = client.get("/api/auth/me", headers=auth["headers"])
    assert r2.status_code == 200


def test_delete_account_removes_all_data_and_revokes_access(client, auth):
    create = client.post(
        "/api/designs/create", json={"prompt": "a bracket 80x40x6mm"},
        headers=auth["headers"],
    )
    design_id = create.json()["id"]

    r = client.request(
        "DELETE", "/api/auth/me", json={"password": "password123"},
        headers=auth["headers"],
    )
    assert r.status_code == 204

    # The design is gone -- even the SAME token can no longer read it (the
    # user row backing that token no longer exists).
    r2 = client.get(f"/api/designs/{design_id}", headers=auth["headers"])
    assert r2.status_code in (401, 404)

    # The email is free again (proves the User row was actually deleted, not
    # soft-deleted/flagged).
    from app.database import SessionLocal
    from app.models import User

    db = SessionLocal()
    try:
        assert db.query(User).filter(User.email == auth["user"]["email"]).first() is None
    finally:
        db.close()


def test_delete_account_does_not_affect_other_accounts(client, auth, auth2):
    client.post(
        "/api/designs/create", json={"prompt": "a bracket 80x40x6mm"},
        headers=auth2["headers"],
    )
    client.request(
        "DELETE", "/api/auth/me", json={"password": "password123"},
        headers=auth["headers"],
    )
    r = client.get("/api/designs", headers=auth2["headers"])
    assert r.status_code == 200
    assert len(r.json()) >= 1
