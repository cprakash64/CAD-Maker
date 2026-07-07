"""Phase 7: production hardening — critical-failure guard on localized edits and
a lean edit-history record."""
from app.services import design_service


def _bracket(client, headers) -> dict:
    return client.post(
        "/api/designs/create",
        json={"prompt": "bracket 80x40x6mm with two M6 holes"},
        headers=headers,
    ).json()


def _resize_body(hole_id="hole_0", mm=9):
    return {
        "instruction": f"Resize this hole to {mm} mm",
        "quick_action": "resize_hole",
        "selection": {"selection_type": "backend_hole", "hole_id": hole_id},
    }


def test_critical_edit_rejected_and_original_preserved(client, auth, legacy_engine, monkeypatch):
    """An edit that turns a valid design critical is rolled back; the original
    geometry/exports stay intact."""
    h = auth["headers"]
    d = _bracket(client, h)
    did, before_hash = d["id"], d["spec_hash"]
    before_dia = d["spec"]["holes"][0]["diameter"]

    # Patch only after the design exists: the design was valid before the edit,
    # and the *edited* design reports critical (2nd+ call within the request).
    state = {"n": 0}

    def fake_is_critical(_design):
        state["n"] += 1
        return state["n"] > 1  # 1st call (original) valid, later (edited) critical

    monkeypatch.setattr(design_service, "is_critical_failure", fake_is_critical)

    r = client.post(f"/api/designs/{did}/face-edit", json=_resize_body(), headers=h)
    assert r.status_code == 422, r.text
    assert "not applied" in r.json()["detail"].lower()

    monkeypatch.undo()  # restore for the verification fetch
    after = client.get(f"/api/designs/{did}", headers=h).json()
    assert after["spec_hash"] == before_hash  # untouched
    assert after["spec"]["holes"][0]["diameter"] == before_dia


def test_edit_history_is_compact(client, auth, legacy_engine):
    """The stored edit trail omits bulky triangle_indices / local_frame."""
    from app.database import SessionLocal
    from app.models import Design

    h = auth["headers"]
    did = _bracket(client, h)["id"]
    # Send a fat selection payload (triangle_indices + local_frame) — it must not
    # be persisted verbatim.
    client.post(
        f"/api/designs/{did}/face-edit",
        json={
            "instruction": "Resize this hole to 8 mm",
            "quick_action": "resize_hole",
            "selection": {
                "selection_type": "backend_hole",
                "hole_id": "hole_0",
                "triangle_indices": list(range(5000)),
                "local_frame": {"origin": [0, 0, 0], "normal": [0, 0, 1], "tangent": [1, 0, 0], "bitangent": [0, 1, 0]},
            },
        },
        headers=h,
    )
    db = SessionLocal()
    try:
        design = db.get(Design, did)
        edits = (design.semantic_json or {}).get("localized_edits") or []
        assert edits, "edit history should be recorded"
        sel = edits[-1]["selection"]
        assert "triangle_indices" not in sel
        assert "local_frame" not in sel
        assert sel["hole_id"] == "hole_0"
    finally:
        db.close()


def test_valid_edit_still_applies_with_guard(client, auth, legacy_engine):
    """The guard never blocks a legitimate edit that passes validation."""
    h = auth["headers"]
    d = _bracket(client, h)
    r = client.post(f"/api/designs/{d['id']}/face-edit", json=_resize_body(mm=9), headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["spec"]["holes"][0]["diameter"] == 9.0
