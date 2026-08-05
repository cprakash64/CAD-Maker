"""Version history: a snapshot is captured after every successful edit (never
on creation, never on a rejected edit), and restoring replays a snapshot
through the same validation pipeline as any other edit."""
from app.services import design_service


def _bracket(client, headers) -> dict:
    return client.post(
        "/api/designs/create",
        json={"prompt": "bracket 80x40x6mm with two M6 holes"},
        headers=headers,
    ).json()


def test_no_versions_before_first_edit(client, auth, legacy_engine):
    h = auth["headers"]
    d = _bracket(client, h)
    r = client.get(f"/api/designs/{d['id']}/versions", headers=h)
    assert r.status_code == 200
    assert r.json() == []


def test_first_edit_creates_baseline_plus_edit_version(client, auth, legacy_engine):
    """The first successful edit captures BOTH the pre-edit baseline (version 1)
    and the edit itself (version 2), so a user can restore all the way back."""
    h = auth["headers"]
    d = _bracket(client, h)
    did = d["id"]
    before_dia = d["spec"]["holes"][0]["diameter"]

    r = client.post(
        f"/api/designs/{did}/regenerate",
        json={"dimensions": {"width": 80, "depth": 40, "thickness": 6},
              "holes": [{"diameter": 9.0, "x": -25, "y": 0}, {"diameter": 6.6, "x": 25, "y": 0}]},
        headers=h,
    )
    assert r.status_code == 200, r.text

    versions = client.get(f"/api/designs/{did}/versions", headers=h).json()
    assert len(versions) == 2
    # Newest first.
    assert versions[0]["version_number"] == 2
    assert versions[0]["edit_kind"] == "regenerate"
    assert versions[1]["version_number"] == 1
    assert versions[1]["edit_kind"] == "create"

    diff_fields = {e["field"] for e in versions[0]["diff"]}
    assert "holes[0].diameter" in diff_fields
    entry = next(e for e in versions[0]["diff"] if e["field"] == "holes[0].diameter")
    assert entry["old"] == before_dia
    assert entry["new"] == 9.0


def test_edit_response_includes_last_edit_diff(client, auth, legacy_engine):
    h = auth["headers"]
    d = _bracket(client, h)
    did = d["id"]
    r = client.post(
        f"/api/designs/{did}/regenerate",
        json={"dimensions": {"width": 80, "depth": 40, "thickness": 6},
              "holes": [{"diameter": 9.0, "x": -25, "y": 0}, {"diameter": 6.6, "x": 25, "y": 0}]},
        headers=h,
    )
    body = r.json()
    assert body["latest_version_number"] == 2
    assert any(e["field"] == "holes[0].diameter" for e in body["last_edit_diff"])


def test_second_edit_does_not_recreate_baseline(client, auth, legacy_engine):
    h = auth["headers"]
    d = _bracket(client, h)
    did = d["id"]
    client.post(
        f"/api/designs/{did}/regenerate",
        json={"dimensions": {"width": 80, "depth": 40, "thickness": 6},
              "holes": [{"diameter": 9.0, "x": -25, "y": 0}, {"diameter": 6.6, "x": 25, "y": 0}]},
        headers=h,
    )
    client.post(
        f"/api/designs/{did}/regenerate",
        json={"dimensions": {"width": 80, "depth": 40, "thickness": 6},
              "holes": [{"diameter": 9.0, "x": -25, "y": 0}, {"diameter": 8.0, "x": 25, "y": 0}]},
        headers=h,
    )
    versions = client.get(f"/api/designs/{did}/versions", headers=h).json()
    assert [v["version_number"] for v in versions] == [3, 2, 1]
    kinds = {v["version_number"]: v["edit_kind"] for v in versions}
    assert kinds[1] == "create"
    assert kinds[2] == "regenerate"
    assert kinds[3] == "regenerate"


def test_rejected_edit_does_not_create_an_edit_version(client, auth, legacy_engine, monkeypatch):
    """A critically-rejected edit still may capture the baseline (state as it
    existed before the attempt), but never a version for the rejected edit
    itself."""
    h = auth["headers"]
    d = _bracket(client, h)
    did = d["id"]
    hole_id = d["selectable_holes"][0]["hole_id"]

    state = {"n": 0}

    def fake_is_critical(_design):
        state["n"] += 1
        return state["n"] > 1

    monkeypatch.setattr(design_service, "is_critical_failure", fake_is_critical)
    r = client.post(
        f"/api/designs/{did}/face-edit",
        json={"instruction": "Resize this hole to 9 mm", "quick_action": "resize_hole",
              "selection": {"selection_type": "backend_hole", "hole_id": hole_id}},
        headers=h,
    )
    assert r.status_code == 422, r.text
    monkeypatch.undo()

    versions = client.get(f"/api/designs/{did}/versions", headers=h).json()
    assert len(versions) == 1
    assert versions[0]["edit_kind"] == "create"


def test_restore_replays_through_validation_pipeline(client, auth, legacy_engine):
    h = auth["headers"]
    d = _bracket(client, h)
    did = d["id"]
    original_dia = d["spec"]["holes"][0]["diameter"]

    client.post(
        f"/api/designs/{did}/regenerate",
        json={"dimensions": {"width": 80, "depth": 40, "thickness": 6},
              "holes": [{"diameter": 9.0, "x": -25, "y": 0}, {"diameter": 6.6, "x": 25, "y": 0}]},
        headers=h,
    )
    versions = client.get(f"/api/designs/{did}/versions", headers=h).json()
    baseline = next(v for v in versions if v["version_number"] == 1)

    r = client.post(f"/api/designs/{did}/versions/{baseline['id']}/restore", headers=h)
    assert r.status_code == 200, r.text
    restored = r.json()
    assert restored["spec"]["holes"][0]["diameter"] == original_dia

    versions_after = client.get(f"/api/designs/{did}/versions", headers=h).json()
    assert [v["version_number"] for v in versions_after] == [3, 2, 1]
    assert versions_after[0]["edit_kind"] == "restore"


def test_restore_unknown_version_404s(client, auth, legacy_engine):
    h = auth["headers"]
    did = _bracket(client, h)["id"]
    r = client.post(f"/api/designs/{did}/versions/nonexistent/restore", headers=h)
    assert r.status_code == 404
