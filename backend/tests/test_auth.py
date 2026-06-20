"""Authentication + per-user isolation."""


def test_signup_login_me(client):
    email = "alice_auth@example.com"
    r = client.post("/api/auth/signup", json={"email": email, "password": "supersecret1"})
    assert r.status_code == 201, r.text
    token = r.json()["access_token"]
    assert r.json()["user"]["email"] == email

    # Login returns a working token.
    r2 = client.post("/api/auth/login", json={"email": email, "password": "supersecret1"})
    assert r2.status_code == 200
    h = {"Authorization": f"Bearer {r2.json()['access_token']}"}
    me = client.get("/api/auth/me", headers=h)
    assert me.status_code == 200 and me.json()["email"] == email
    assert token  # original signup token also issued


def test_duplicate_signup_rejected(client):
    client.post("/api/auth/signup", json={"email": "dup@example.com", "password": "password1"})
    r = client.post("/api/auth/signup", json={"email": "dup@example.com", "password": "password1"})
    assert r.status_code == 409


def test_wrong_password_rejected(client):
    client.post("/api/auth/signup", json={"email": "bob@example.com", "password": "rightpass1"})
    r = client.post("/api/auth/login", json={"email": "bob@example.com", "password": "wrongpass1"})
    assert r.status_code == 401


def test_short_password_rejected(client):
    r = client.post("/api/auth/signup", json={"email": "x@example.com", "password": "short"})
    assert r.status_code == 422


def test_designs_require_auth(client):
    assert client.get("/api/designs").status_code == 401
    assert client.post("/api/designs/create", json={"prompt": "bracket"}).status_code == 401
    assert client.get("/api/auth/me").status_code == 401


def test_invalid_token_rejected(client):
    bad = {"Authorization": "Bearer not-a-real-token"}
    assert client.get("/api/designs", headers=bad).status_code == 401


def test_users_cannot_access_each_others_designs(client, auth, auth2):
    # User A creates a design.
    r = client.post(
        "/api/designs/create",
        json={"prompt": "bracket 80mm wide 5mm thick"},
        headers=auth["headers"],
    )
    did = r.json()["id"]

    # User B cannot read it, edit it, export it, download it, or feedback it.
    hb = auth2["headers"]
    assert client.get(f"/api/designs/{did}", headers=hb).status_code == 404
    assert client.post(
        f"/api/designs/{did}/regenerate", json={"dimensions": {"width": 100}}, headers=hb
    ).status_code == 404
    assert client.post(
        f"/api/designs/{did}/modify", json={"prompt": "make it wider"}, headers=hb
    ).status_code == 404
    assert client.get(f"/api/designs/{did}/files/stl", headers=hb).status_code == 404
    assert client.post(
        f"/api/designs/{did}/feedback", json={"rating": "up"}, headers=hb
    ).status_code == 404

    # And B's list does not include A's design.
    listing = client.get("/api/designs", headers=hb).json()
    assert all(d["id"] != did for d in listing)

    # A still sees it.
    assert client.get(f"/api/designs/{did}", headers=auth["headers"]).status_code == 200
