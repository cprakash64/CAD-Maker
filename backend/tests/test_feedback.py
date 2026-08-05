"""Feedback API: submit, update, validate, query, and traceability links."""


def _make_design(client, headers) -> str:
    r = client.post(
        "/api/designs/create",
        json={"prompt": "bracket 80mm wide 40mm deep 5mm thick with two M6 holes"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_submit_thumbs_down_with_categories(client, auth):
    h = auth["headers"]
    did = _make_design(client, h)
    r = client.post(
        f"/api/designs/{did}/feedback",
        json={
            "rating": "down",
            "categories": ["wrong_dimensions", "bad_geometry"],
            "comment": "holes too close",
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["rating"] == "down"
    assert set(body["categories"]) == {"wrong_dimensions", "bad_geometry"}
    assert body["comment"] == "holes too close"


def test_feedback_appears_on_design_and_is_queryable(client, auth):
    h = auth["headers"]
    did = _make_design(client, h)
    client.post(f"/api/designs/{did}/feedback", json={"rating": "up"}, headers=h)

    # On the design payload.
    d = client.get(f"/api/designs/{did}", headers=h).json()
    assert d["my_feedback"]["rating"] == "up"

    # And via the feedback endpoint.
    fb = client.get(f"/api/designs/{did}/feedback", headers=h).json()
    assert fb["rating"] == "up"
    assert fb["design_id"] == did


def test_resubmitting_feedback_updates_in_place(client, auth):
    h = auth["headers"]
    did = _make_design(client, h)
    client.post(f"/api/designs/{did}/feedback", json={"rating": "up"}, headers=h)
    client.post(
        f"/api/designs/{did}/feedback",
        json={"rating": "down", "categories": ["other"]},
        headers=h,
    )
    fb = client.get(f"/api/designs/{did}/feedback", headers=h).json()
    assert fb["rating"] == "down" and fb["categories"] == ["other"]


def test_invalid_rating_and_category_rejected(client, auth):
    h = auth["headers"]
    did = _make_design(client, h)
    assert client.post(
        f"/api/designs/{did}/feedback", json={"rating": "meh"}, headers=h
    ).status_code == 422
    assert client.post(
        f"/api/designs/{did}/feedback",
        json={"rating": "down", "categories": ["nonexistent"]},
        headers=h,
    ).status_code == 422


def test_feedback_requires_auth(client):
    assert client.post("/api/designs/x/feedback", json={"rating": "up"}).status_code == 401


def test_feedback_snapshots_spec_hash(client, auth):
    """Feedback is linked to the design's current spec for traceability."""
    h = auth["headers"]
    did = _make_design(client, h)
    design = client.get(f"/api/designs/{did}", headers=h).json()
    client.post(f"/api/designs/{did}/feedback", json={"rating": "up"}, headers=h)

    from app.database import SessionLocal
    from app.models import Feedback

    db = SessionLocal()
    try:
        fb = db.query(Feedback).filter(Feedback.design_id == did).one()
        assert fb.spec_hash == design["spec_hash"]
        assert fb.object_type == design["object_type"]
        assert fb.user_id == auth["user"]["id"]
    finally:
        db.close()


def test_report_bad_result_without_consent_drops_reason(client, auth):
    h = auth["headers"]
    did = _make_design(client, h)
    r = client.post(
        f"/api/designs/{did}/report-bad-result",
        json={
            "categories": ["wrong_dimensions"],
            "reason": "the holes are in the wrong place",
            "consent": False,
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["categories"] == ["wrong_dimensions"]
    assert body["consent"] is False
    # Privacy-conscious: free text is never persisted without consent, even
    # though it was sent in the request.
    assert body["reason"] is None

    from app.database import SessionLocal
    from app.models import Feedback

    db = SessionLocal()
    try:
        fb = db.query(Feedback).filter(Feedback.design_id == did).one()
        assert fb.report_reason is None
        assert fb.is_bad_result_report is True
    finally:
        db.close()


def test_report_bad_result_with_consent_keeps_reason_and_snapshots_versions(client, auth):
    h = auth["headers"]
    did = _make_design(client, h)
    r = client.post(
        f"/api/designs/{did}/report-bad-result",
        json={
            "categories": ["bad_geometry"],
            "reason": "walls look too thin near the boss",
            "consent": True,
            "print_success": False,
            "fit_success": None,
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["reason"] == "walls look too thin near the boss"
    assert body["consent"] is True
    assert body["print_success"] is False
    assert body["fit_success"] is None
    # A design_version_number of at least 1 (baseline) and a prompt_version
    # string are snapshotted at report time, not left null.
    assert body["design_version_number"] is not None
    assert body["prompt_version"]


def test_report_bad_result_requires_auth(client):
    assert client.post(
        "/api/designs/x/report-bad-result", json={"categories": []}
    ).status_code == 401


def test_report_bad_result_rejects_unknown_category(client, auth):
    h = auth["headers"]
    did = _make_design(client, h)
    r = client.post(
        f"/api/designs/{did}/report-bad-result",
        json={"categories": ["not_a_real_category"], "consent": False},
        headers=h,
    )
    assert r.status_code == 422
