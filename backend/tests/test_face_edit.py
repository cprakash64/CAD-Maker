"""Phase 4: localized visual-face edits — classification, deterministic handlers,
validation, and the /face-edit API (applied / rejected / needs_review)."""
import pytest

from app.editing.face_edit import (
    FaceEditError,
    FaceEditReview,
    apply_face_edit,
    classify_edit,
)
from app.schemas.design_spec import DesignSpec, Hole
from app.schemas.editing_spec import FaceLocalizedEditRequest


def _bracket() -> DesignSpec:
    return DesignSpec(
        object_type="rectangular_bracket",
        dimensions={"width": 80, "depth": 40, "thickness": 6},
        holes=[Hole(diameter=6.6, x=-25, y=0)],
    )


def _enclosure() -> DesignSpec:
    return DesignSpec(
        object_type="enclosure",
        dimensions={"width": 90, "depth": 60, "height": 40, "wall_thickness": 2.4},
    )


def _req(instruction: str, quick_action=None, **sel) -> FaceLocalizedEditRequest:
    selection = {
        "selection_type": "visual_face",
        "frontend_visual_face_id": "vf_0",
        "feature_id": "face_top",
        "face_kind": "planar",
        "clicked_point": [0, 0, 3],
        "center": [0, 0, 3],
        "normal": [0, 0, 1],
        **sel,
    }
    return FaceLocalizedEditRequest(
        instruction=instruction, quick_action=quick_action, selection=selection
    )


_PLATE_BBOX = {"x": 80.0, "y": 40.0, "z": 6.0}


# --- classification --------------------------------------------------------
def test_classify_prefers_quick_action_over_text():
    assert classify_edit("hole", "do something odd") == "add_hole"
    assert classify_edit("chamfer", "") == "chamfer"


def test_classify_from_keywords():
    assert classify_edit(None, "Add a 10 mm through hole") == "add_hole"
    assert classify_edit(None, "Add ventilation slots") == "add_vent"
    assert classify_edit(None, "Fillet the edges 2 mm") == "fillet"
    assert classify_edit(None, "Create a symmetric pattern") == "pattern"
    assert classify_edit(None, "make it prettier") == "freeform_local_edit"


# --- deterministic handlers ------------------------------------------------
def test_add_through_hole_on_planar_face():
    spec = _bracket()
    new_spec, outcome = apply_face_edit(
        spec, _req("Add a 10 mm through hole centered on this face", "hole"), _PLATE_BBOX
    )
    assert outcome.op == "add_hole"
    assert len(new_spec.holes) == len(spec.holes) + 1
    added = new_spec.holes[-1]
    assert added.diameter == 10.0
    assert (added.x, added.y) == (0.0, 0.0)
    assert new_spec.object_type == "rectangular_bracket"  # not a mesh edit


def test_reject_oversized_hole():
    with pytest.raises(FaceEditError):
        apply_face_edit(_bracket(), _req("Add a 200 mm hole", "hole"), _PLATE_BBOX)


def test_reject_zero_diameter_hole():
    with pytest.raises(FaceEditError):
        apply_face_edit(_bracket(), _req("Add a 0 mm hole", "hole"), _PLATE_BBOX)


def test_hole_on_curved_face_needs_review():
    with pytest.raises(FaceEditReview):
        apply_face_edit(
            _bracket(), _req("Add a hole", "hole", face_kind="curved"), _PLATE_BBOX
        )


def test_hole_on_side_face_needs_review():
    with pytest.raises(FaceEditReview):
        apply_face_edit(
            _bracket(), _req("Add a hole", "hole", feature_id="face_+X"), _PLATE_BBOX
        )


def test_slot_and_boss_need_review():
    for qa in ("slot", "cutout", "boss", "pattern"):
        with pytest.raises(FaceEditReview):
            apply_face_edit(_bracket(), _req("do it", qa), _PLATE_BBOX)


def test_fillet_and_chamfer_apply():
    spec = _bracket()
    filleted, out1 = apply_face_edit(spec, _req("Fillet the edges by 3 mm", "fillet"), _PLATE_BBOX)
    assert filleted.fillet_radius == 3.0 and filleted.chamfer_size is None
    chamfered, out2 = apply_face_edit(spec, _req("Chamfer the edges by 1 mm", "chamfer"), _PLATE_BBOX)
    assert chamfered.chamfer_size == 1.0 and chamfered.fillet_radius is None


def test_add_vent_on_enclosure():
    new_spec, outcome = apply_face_edit(
        _enclosure(),
        _req("Add evenly spaced ventilation slots", "vent", feature_id="face_+Y"),
        {"x": 90.0, "y": 60.0, "z": 40.0},
    )
    assert outcome.op == "add_vent"
    assert new_spec.dimensions.get("vent_count", 0) >= 1


def test_hole_unsupported_on_non_plate():
    with pytest.raises(FaceEditReview):
        apply_face_edit(_enclosure(), _req("Add a 5 mm hole", "hole"), _PLATE_BBOX)


# --- API -------------------------------------------------------------------
def _make_bracket(client, headers) -> dict:
    return client.post(
        "/api/designs/create",
        json={"prompt": "bracket 80x40x6mm with one M6 hole"},
        headers=headers,
    ).json()


def test_face_edit_endpoint_adds_hole(client, auth, legacy_engine):
    h = auth["headers"]
    d = _make_bracket(client, h)
    did, before_hash = d["id"], d["spec_hash"]
    before_holes = len(d["spec"]["holes"])

    r = client.post(
        f"/api/designs/{did}/face-edit",
        json={
            "instruction": "Add a 10 mm through hole centered on this face",
            "quick_action": "hole",
            "selection": {
                "selection_type": "visual_face",
                "frontend_visual_face_id": "vf_1",
                "feature_id": "face_top",
                "face_kind": "planar",
                "clicked_point": [0, 0, 3],
                "center": [0, 0, 3],
                "normal": [0, 0, 1],
            },
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["spec_hash"] != before_hash
    assert len(body["spec"]["holes"]) == before_holes + 1
    # STL/STEP exports were regenerated (real CAD, not a mesh hack).
    assert {e["fmt"] for e in body["exports"]} >= {"stl", "step"}


def test_face_edit_oversized_hole_rejected_unchanged(client, auth, legacy_engine):
    h = auth["headers"]
    d = _make_bracket(client, h)
    did, before_hash = d["id"], d["spec_hash"]

    r = client.post(
        f"/api/designs/{did}/face-edit",
        json={
            "instruction": "Add a 500 mm hole",
            "quick_action": "hole",
            "selection": {"feature_id": "face_top", "face_kind": "planar", "normal": [0, 0, 1]},
        },
        headers=h,
    )
    assert r.status_code == 422
    assert "safely" in r.json()["detail"].lower()
    # Original design is untouched.
    after = client.get(f"/api/designs/{did}", headers=h).json()
    assert after["spec_hash"] == before_hash


def test_face_edit_unsupported_curved_needs_review(client, auth, legacy_engine):
    h = auth["headers"]
    d = _make_bracket(client, h)
    did, before_hash = d["id"], d["spec_hash"]

    r = client.post(
        f"/api/designs/{did}/face-edit",
        json={
            "instruction": "Cut a rectangular opening on this face",
            "quick_action": "cutout",
            "selection": {"feature_id": "face_top", "face_kind": "planar", "normal": [0, 0, 1]},
        },
        headers=h,
    )
    assert r.status_code == 422
    after = client.get(f"/api/designs/{did}", headers=h).json()
    assert after["spec_hash"] == before_hash


def test_face_edit_records_history(client, auth, legacy_engine):
    h = auth["headers"]
    did = _make_bracket(client, h)["id"]
    # One applied, one rejected.
    client.post(
        f"/api/designs/{did}/face-edit",
        json={
            "instruction": "Add a 8 mm through hole",
            "quick_action": "hole",
            "selection": {"feature_id": "face_top", "face_kind": "planar", "normal": [0, 0, 1]},
        },
        headers=h,
    )
    client.post(
        f"/api/designs/{did}/face-edit",
        json={
            "instruction": "Add a cylindrical boss",
            "quick_action": "boss",
            "selection": {"feature_id": "face_top", "face_kind": "planar", "normal": [0, 0, 1]},
        },
        headers=h,
    )
    from app.database import SessionLocal
    from app.models import Design

    db = SessionLocal()
    try:
        design = db.get(Design, did)
        edits = (design.semantic_json or {}).get("localized_edits") or []
        statuses = [e["status"] for e in edits]
        assert "applied" in statuses
        assert "needs_review" in statuses
    finally:
        db.close()


def test_face_edit_missing_instruction_rejected(client, auth, legacy_engine):
    h = auth["headers"]
    did = _make_bracket(client, h)["id"]
    r = client.post(
        f"/api/designs/{did}/face-edit",
        json={"quick_action": "hole", "selection": {"feature_id": "face_top"}},
        headers=h,
    )
    assert r.status_code == 422  # pydantic: instruction required


# --- CadPlan (feature-graph) face edits ------------------------------------
# These parts have spec_json = None; their editable source is the stored feature
# graph (semantic_json['cad_plan']). No legacy_engine fixture → the default
# feature-graph engine builds a route='cad_plan' mounting_plate.


def _make_cad_plan_plate(client, headers) -> dict:
    """A route='cad_plan', object_type='mounting_plate' design (no DesignSpec)."""
    d = client.post(
        "/api/designs/create",
        json={"prompt": "80 mm x 40 mm x 6 mm mounting plate with two M6 holes"},
        headers=headers,
    ).json()
    assert d["route"] == "cad_plan", d
    assert d["spec"] is None  # CadPlan-built: no DesignSpec
    return d


def _top_face_selection(**over) -> dict:
    return {
        "selection_type": "visual_face",
        "frontend_visual_face_id": "vf_1",
        "feature_id": "face_top",
        "face_kind": "planar",
        "clicked_point": [0, 0, 3],
        "center": [0, 0, 3],
        "normal": [0, 0, 1],
        **over,
    }


def test_cad_plan_stores_editable_feature_graph(client, auth):
    """The generated cad_plan design persists its parametric source so a later
    face edit can reload it (the root-cause fix)."""
    from app.database import SessionLocal
    from app.models import Design

    did = _make_cad_plan_plate(client, auth["headers"])["id"]
    db = SessionLocal()
    try:
        design = db.get(Design, did)
        assert design.spec_json is None
        plan = (design.semantic_json or {}).get("cad_plan")
        assert plan and plan.get("object_type") == "mounting_plate"
        assert any(f["kind"] == "hole" for f in plan["features"])
    finally:
        db.close()


def test_cad_plan_face_edit_adds_hole(client, auth):
    h = auth["headers"]
    d = _make_cad_plan_plate(client, h)
    did, before_hash = d["id"], d["spec_hash"]

    r = client.post(
        f"/api/designs/{did}/face-edit",
        json={
            "instruction": "Add a 10 mm through hole centered on this face",
            "quick_action": "hole",
            "selection": _top_face_selection(),
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # Real CAD regenerated: new geometry hash + fresh STL/STEP exports.
    assert body["spec_hash"] != before_hash
    assert {e["fmt"] for e in body["exports"]} >= {"stl", "step"}
    # The edit is still a CadPlan part (not converted to a mesh edit).
    assert body["route"] == "cad_plan"
    assert body["spec"] is None
    # An applied audit entry was recorded.
    assert any("through hole" in a.lower() for a in body["assumptions"])


def test_cad_plan_face_edit_oversized_hole_rejected_unchanged(client, auth):
    h = auth["headers"]
    d = _make_cad_plan_plate(client, h)
    did, before_hash = d["id"], d["spec_hash"]

    r = client.post(
        f"/api/designs/{did}/face-edit",
        json={
            "instruction": "Add a 500 mm hole",
            "quick_action": "hole",
            "selection": _top_face_selection(),
        },
        headers=h,
    )
    assert r.status_code == 422
    # Original design is byte-for-byte unchanged (failed edit never corrupts it).
    after = client.get(f"/api/designs/{did}", headers=h).json()
    assert after["spec_hash"] == before_hash
    assert after["route"] == "cad_plan"


def test_cad_plan_face_edit_fillet_applies(client, auth):
    h = auth["headers"]
    d = _make_cad_plan_plate(client, h)
    did, before_hash = d["id"], d["spec_hash"]

    r = client.post(
        f"/api/designs/{did}/face-edit",
        json={
            "instruction": "Round the edges with a 3 mm fillet",
            "quick_action": "fillet",
            "selection": _top_face_selection(),
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert r.json()["spec_hash"] != before_hash


def _hole_selection(hole_id: str, **over) -> dict:
    """A backend_hole selection payload as the frontend now sends for a hole."""
    return {
        "selection_type": "backend_hole",
        "selection_kind": "hole",
        "hole_id": hole_id,
        "feature_id": "holes",
        "face_kind": "cylindrical",
        "center": [0, 0, 6],
        "normal": [0, 0, 1],
        **over,
    }


def test_cad_plan_new_hole_appears_in_selectable_holes(client, auth):
    """After adding a center hole, the DTO's selectable_holes lists all three
    holes (the two originals + the new one) so the viewer can select it."""
    h = auth["headers"]
    d = _make_cad_plan_plate(client, h)
    did = d["id"]
    assert len(d["selectable_holes"]) == 2  # the two M6 holes

    r = client.post(
        f"/api/designs/{did}/face-edit",
        json={
            "instruction": "Add a 10 mm through hole centered on this face",
            "quick_action": "hole",
            "selection": _top_face_selection(),
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    holes = r.json()["selectable_holes"]
    assert len(holes) == 3
    ids = {hh["hole_id"] for hh in holes}
    assert ids == {"hole_0", "hole_1", "hole_2"}
    new_hole = next(hh for hh in holes if hh["hole_id"] == "hole_2")
    assert new_hole["diameter_mm"] == 10.0
    assert new_hole["allowed_operations"] == [
        "resize_hole", "move_hole", "delete_hole", "pattern_hole",
    ]
    # Re-fetch persists them.
    again = client.get(f"/api/designs/{did}", headers=h).json()
    assert len(again["selectable_holes"]) == 3


def test_cad_plan_resize_new_hole_by_id(client, auth):
    h = auth["headers"]
    did = _make_cad_plan_plate(client, h)["id"]
    # Add the center hole (hole_2).
    add = client.post(
        f"/api/designs/{did}/face-edit",
        json={"instruction": "Add a 10 mm through hole", "quick_action": "hole",
              "selection": _top_face_selection()},
        headers=h,
    )
    assert add.status_code == 200, add.text
    before_hash = add.json()["spec_hash"]

    r = client.post(
        f"/api/designs/{did}/face-edit",
        json={"instruction": "Resize this hole to 8 mm", "quick_action": "resize_hole",
              "selection": _hole_selection("hole_2")},
        headers=h,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["spec_hash"] != before_hash
    resized = next(hh for hh in body["selectable_holes"] if hh["hole_id"] == "hole_2")
    assert resized["diameter_mm"] == 8.0


def test_cad_plan_delete_new_hole_by_id(client, auth):
    h = auth["headers"]
    did = _make_cad_plan_plate(client, h)["id"]
    client.post(
        f"/api/designs/{did}/face-edit",
        json={"instruction": "Add a 10 mm through hole", "quick_action": "hole",
              "selection": _top_face_selection()},
        headers=h,
    )
    r = client.post(
        f"/api/designs/{did}/face-edit",
        json={"instruction": "Delete this hole", "quick_action": "delete_hole",
              "selection": _hole_selection("hole_2")},
        headers=h,
    )
    assert r.status_code == 200, r.text
    holes = r.json()["selectable_holes"]
    assert len(holes) == 2
    assert "hole_2" not in {hh["hole_id"] for hh in holes}


def test_cad_plan_cylindrical_face_without_hole_id_helpful_error(client, auth):
    """Backend fallback safety: a cylindrical-face selection carrying no hole_id
    (a mis-sent payload) returns a helpful 422, never a crash, and leaves the part
    unchanged."""
    h = auth["headers"]
    d = _make_cad_plan_plate(client, h)
    did, before_hash = d["id"], d["spec_hash"]
    r = client.post(
        f"/api/designs/{did}/face-edit",
        json={
            "instruction": "Resize this hole to 8 mm",
            "quick_action": "resize_hole",
            # A cylindrical face selection with NO hole_id and no numeric feature.
            "selection": {"selection_type": "visual_face", "face_kind": "cylindrical",
                          "feature_id": "cylindrical_face", "normal": [1, 0, 0]},
        },
        headers=h,
    )
    assert r.status_code == 422
    assert "hole" in r.json()["detail"].lower()
    after = client.get(f"/api/designs/{did}", headers=h).json()
    assert after["spec_hash"] == before_hash


def test_face_edit_409_only_when_no_editable_source(client, auth):
    """409 is reserved for designs with neither a DesignSpec nor a stored plan —
    e.g. a clarification-only design that never produced a model."""
    h = auth["headers"]
    # A prompt the planner can't size → clarification, no geometry, no spec/plan.
    d = client.post(
        "/api/designs/create",
        json={"prompt": "a plate"},
        headers=h,
    ).json()
    assert d["spec"] is None
    r = client.post(
        f"/api/designs/{d['id']}/face-edit",
        json={
            "instruction": "Add a 10 mm hole",
            "quick_action": "hole",
            "selection": _top_face_selection(),
        },
        headers=h,
    )
    assert r.status_code == 409, r.text
