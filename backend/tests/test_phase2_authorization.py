"""Phase 2: every route's authentication and object-level authorization.

Two complementary layers:

  1. A *registry* test that walks ``app.routes`` and asserts every route is
     either on an explicit public allowlist or requires authentication. It
     cannot be satisfied by adding a route and forgetting a test — a new
     unauthenticated route fails the build until it is classified.

  2. Behavioural cross-user tests: user A creates a resource, user B tries every
     verb against it, and an unauthenticated client tries too. These are API
     level (through the HTTP app), not service level.

The invariant: knowing another user's resource id must never permit reading,
editing, exporting, downloading, polling, or deleting it — and the failure must
look the same as a resource that does not exist, so ids cannot be enumerated.
"""
from __future__ import annotations

import pytest
from fastapi.routing import APIRoute

# Routes that are intentionally reachable without a session, with the reason.
PUBLIC_ROUTES = {
    ("POST", "/api/auth/login"): "credential exchange",
    ("POST", "/api/auth/signup"): "account creation",
    ("GET", "/health"): "liveness probe",
    ("GET", "/api/provider-status"): "AI capability flags for the pre-login UI",
    ("GET", "/api/capabilities"): "static catalogue of supported edit actions",
    ("GET", "/api/templates"): "static catalogue of part templates",
}

# Routes that take a resource id owned by a user and must enforce ownership.
OWNED_RESOURCE_PREFIXES = ("/api/designs/{design_id}", "/api/drawings/debug/{design_id}")


def _api_routes(app):
    for r in app.routes:
        if not isinstance(r, APIRoute):
            continue
        for method in sorted(r.methods - {"HEAD", "OPTIONS"}):
            yield method, r.path, r


def _requires_auth(route: APIRoute) -> bool:
    seen: list[str] = []

    def walk(dep):
        for sub in dep.dependencies:
            seen.append(getattr(sub.call, "__name__", ""))
            walk(sub)

    walk(route.dependant)
    return "get_current_user" in seen


# --- 1. registry-level coverage -------------------------------------------
def test_every_route_is_authenticated_or_explicitly_public():
    """No route may be anonymous by accident."""
    from app.main import app

    unclassified = []
    for method, path, route in _api_routes(app):
        if _requires_auth(route):
            continue
        if (method, path) in PUBLIC_ROUTES:
            continue
        unclassified.append(f"{method} {path}")
    assert not unclassified, (
        "these routes are reachable without authentication and are not on the "
        "public allowlist:\n" + "\n".join(unclassified)
    )


def test_public_allowlist_has_no_stale_entries():
    """The allowlist must describe reality, so it stays reviewable."""
    from app.main import app

    live = {(m, p) for m, p, _ in _api_routes(app)}
    stale = [f"{m} {p}" for (m, p) in PUBLIC_ROUTES if (m, p) not in live]
    assert not stale, f"public allowlist names routes that no longer exist: {stale}"


# Liveness probes must never be throttled — an orchestrator polling /health
# through a restart storm would otherwise be told the app is unhealthy.
RATE_LIMIT_EXEMPT = {("GET", "/health")}


def test_every_route_has_a_rate_limit_except_the_liveness_probe():
    from app.main import app

    def _has_limit(route: APIRoute) -> bool:
        names = []

        def walk(dep):
            for sub in dep.dependencies:
                names.append(getattr(sub.call, "__name__", ""))
                walk(sub)

        walk(route.dependant)
        # rate_limit() installs a closure named "dependency".
        return "dependency" in names

    missing = [
        f"{m} {p}" for m, p, r in _api_routes(app)
        if not _has_limit(r) and (m, p) not in RATE_LIMIT_EXEMPT
    ]
    assert not missing, f"routes with no rate limit: {missing}"


def test_every_owned_resource_route_requires_auth():
    from app.main import app

    for method, path, route in _api_routes(app):
        if path.startswith(OWNED_RESOURCE_PREFIXES):
            assert _requires_auth(route), f"{method} {path} does not require auth"


# --- 2. behavioural: unauthenticated access -------------------------------
UNAUTH_CASES = [
    ("get", "/api/designs"),
    ("post", "/api/designs/create"),
    ("get", "/api/designs/whatever"),
    ("get", "/api/designs/whatever/files/stl"),
    ("get", "/api/designs/whatever/package"),
    ("get", "/api/designs/whatever/views/top"),
    ("post", "/api/designs/whatever/regenerate"),
    ("post", "/api/designs/whatever/modify"),
    ("post", "/api/designs/whatever/export"),
    ("post", "/api/designs/whatever/checks"),
    ("get", "/api/designs/whatever/feedback"),
    ("get", "/api/drawings/jobs/whatever"),
    ("get", "/api/drawings/debug/whatever"),
    ("get", "/api/drawings/debug/whatever/overlay"),
    ("get", "/api/auth/me"),
]


@pytest.mark.parametrize("method,path", UNAUTH_CASES)
def test_unauthenticated_requests_are_rejected(client, method, path):
    r = client.post(path, json={}) if method == "post" else client.get(path)
    assert r.status_code in (401, 403), f"{method.upper()} {path} -> {r.status_code}"


@pytest.mark.parametrize("token", [
    "", "not-a-token", "Bearer", "a.b.c",
    "eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0.eyJzdWIiOiJhZG1pbiJ9.",  # alg=none
])
def test_malformed_or_forged_tokens_are_rejected(client, token):
    r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


# --- 3. behavioural: cross-user access ------------------------------------
@pytest.fixture
def design_a(client, auth):
    """A real, fully generated design owned by user A."""
    r = client.post(
        "/api/designs/create",
        json={"prompt": "a rectangular bracket 80mm wide 40mm deep 5mm thick "
                        "with two 6mm holes"},
        headers=auth["headers"],
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_owner_can_read_and_export_their_own_design(client, auth, design_a):
    """The positive case — the controls must not break legitimate access."""
    did = design_a["id"]
    assert client.get(f"/api/designs/{did}", headers=auth["headers"]).status_code == 200
    assert client.get(f"/api/designs/{did}/files/stl",
                      headers=auth["headers"]).status_code == 200
    assert client.get(f"/api/designs/{did}/package",
                      headers=auth["headers"]).status_code == 200
    listed = client.get("/api/designs", headers=auth["headers"]).json()
    assert any(d["id"] == did for d in listed)


CROSS_USER_CASES = [
    ("get", "/api/designs/{id}", None),
    ("get", "/api/designs/{id}/files/stl", None),
    ("get", "/api/designs/{id}/files/step", None),
    ("get", "/api/designs/{id}/package", None),
    ("get", "/api/designs/{id}/views/top", None),
    ("get", "/api/designs/{id}/feedback", None),
    ("post", "/api/designs/{id}/feedback", {"rating": "up"}),
    ("post", "/api/designs/{id}/checks", {}),
    ("post", "/api/designs/{id}/export", {}),
    ("post", "/api/designs/{id}/regenerate", {"dimensions": {"width": 90}}),
    ("post", "/api/designs/{id}/modify", {"prompt": "make it wider"}),
    ("post", "/api/designs/{id}/generate-with-defaults", {}),
    # Bodies below are schema-valid so the request reaches the ownership check
    # rather than stopping at request validation.
    ("post", "/api/designs/{id}/localized-edit", {
        "selected_entity_type": "hole", "selected_entity_id": "hole_0",
        "allowed_operation": "change_hole_diameter",
        "natural_language_instruction": "make it 8mm",
    }),
    ("post", "/api/designs/{id}/circle-edit", {
        "selected": {"entity_type": "hole", "entity_id": "hole_0"},
        "instruction": "make it 8mm",
    }),
    ("post", "/api/designs/{id}/face-edit", {
        "instruction": "add a 6mm hole",
        "selection": {"selection_type": "face", "backend_face_id": "face_0"},
    }),
    ("get", "/api/drawings/debug/{id}", None),
    ("get", "/api/drawings/debug/{id}/overlay", None),
]


@pytest.mark.parametrize("method,template,body", CROSS_USER_CASES)
def test_second_user_cannot_touch_another_users_design(
    client, auth2, design_a, method, template, body
):
    """Read, edit, export, download, regenerate — all denied for a non-owner."""
    path = template.format(id=design_a["id"])
    kwargs = {"headers": auth2["headers"]}
    if method == "post":
        kwargs["json"] = body or {}
    r = getattr(client, method)(path, **kwargs)
    assert r.status_code == 404, f"{method.upper()} {path} -> {r.status_code} {r.text[:200]}"


@pytest.mark.parametrize("method,template,body", CROSS_USER_CASES)
def test_unauthorized_and_missing_are_indistinguishable(
    client, auth2, design_a, method, template, body
):
    """A non-owner's 404 must match a genuinely-absent id byte for byte, or the
    difference becomes an existence oracle for enumerating other users' ids."""
    kwargs = {"headers": auth2["headers"]}
    if method == "post":
        kwargs["json"] = body or {}

    real = getattr(client, method)(template.format(id=design_a["id"]), **kwargs)
    absent = getattr(client, method)(
        template.format(id="00000000000000000000000000000000"), **kwargs)

    assert real.status_code == absent.status_code == 404
    assert real.json() == absent.json(), (
        f"{method.upper()} {template}: existing-but-unowned and missing differ "
        f"({real.text[:120]} vs {absent.text[:120]})"
    )


def test_second_user_does_not_see_the_design_in_their_list(client, auth2, design_a):
    listed = client.get("/api/designs", headers=auth2["headers"]).json()
    assert all(d["id"] != design_a["id"] for d in listed)


def test_ownership_survives_regeneration(client, auth, auth2, design_a):
    """Regenerating replaces the export rows; the new artifacts must still be
    owner-scoped (a stale ownership edge would only appear after a rebuild)."""
    did = design_a["id"]
    r = client.post(f"/api/designs/{did}/regenerate",
                    json={"dimensions": {"width": 90}}, headers=auth["headers"])
    assert r.status_code == 200, r.text

    assert client.get(f"/api/designs/{did}/files/stl",
                      headers=auth["headers"]).status_code == 200
    assert client.get(f"/api/designs/{did}/files/stl",
                      headers=auth2["headers"]).status_code == 404
    assert client.get(f"/api/designs/{did}",
                      headers=auth2["headers"]).status_code == 404


def test_client_supplied_owner_fields_are_ignored(client, auth, auth2):
    """A body that claims another user's identity must not change ownership."""
    victim_id = auth["user"]["id"]
    r = client.post(
        "/api/designs/create",
        json={"prompt": "a 20mm cube spacer", "user_id": victim_id,
              "owner_id": victim_id},
        headers=auth2["headers"],
    )
    assert r.status_code == 200, r.text
    created = r.json()["id"]
    # It belongs to the caller (auth2), not the id they claimed.
    assert client.get(f"/api/designs/{created}",
                      headers=auth["headers"]).status_code == 404
    assert client.get(f"/api/designs/{created}",
                      headers=auth2["headers"]).status_code == 200


def test_client_cannot_attach_a_design_to_another_users_project(client, auth, auth2, design_a):
    """project_id is a client-supplied ownership claim — it must be verified."""
    victim_project = design_a["project_id"]
    r = client.post("/api/designs/create",
                    json={"prompt": "a 20mm cube spacer", "project_id": victim_project},
                    headers=auth2["headers"])
    assert r.status_code == 200, r.text
    assert r.json()["project_id"] != victim_project, (
        "a design was filed under another user's project"
    )


# --- 4. jobs are owner-scoped ---------------------------------------------
def test_second_user_cannot_poll_another_users_drawing_job(client, auth, auth2):
    from app.services import drawing_jobs

    job = drawing_jobs.create_job(auth["user"]["id"])
    assert client.get(f"/api/drawings/jobs/{job.id}",
                      headers=auth["headers"]).status_code == 200
    other = client.get(f"/api/drawings/jobs/{job.id}", headers=auth2["headers"])
    missing = client.get("/api/drawings/jobs/does-not-exist",
                         headers=auth2["headers"])
    assert other.status_code == missing.status_code == 404
    assert other.json() == missing.json()


@pytest.mark.parametrize("job_id", [
    "../../etc/passwd", "..%2f..%2fetc%2fpasswd", "a" * 500, "job%00id", "",
])
def test_malformed_job_ids_are_rejected_safely(client, auth2, job_id):
    r = client.get(f"/api/drawings/jobs/{job_id}", headers=auth2["headers"])
    assert r.status_code in (404, 422), r.status_code
    body = r.text.lower()
    for leak in ("traceback", "/users/", "site-packages", 'file "'):
        assert leak not in body


# --- 5. malformed design ids ----------------------------------------------
@pytest.mark.parametrize("design_id", [
    "../../etc/passwd", "..%2f..%2f..%2fetc%2fpasswd", "%2e%2e%2f",
    "a" * 500, "'; DROP TABLE designs;--", "<script>alert(1)</script>",
])
def test_malformed_design_ids_never_leak(client, auth, design_id):
    r = client.get(f"/api/designs/{design_id}", headers=auth["headers"])
    assert r.status_code in (404, 422), r.status_code
    body = r.text.lower()
    for leak in ("traceback", "/users/", "/private/", "site-packages", "sqlalchemy",
                 'file "', "select "):
        assert leak not in body, f"leaked {leak!r}"
