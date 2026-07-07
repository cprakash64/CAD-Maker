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
    detail = r.json()["detail"]
    # Structured, calm rejection (an oversized hole is an invalid edit).
    assert detail["code"] == "invalid_edit"
    assert detail["safe_to_retry"] is True
    assert "too large" in detail["message"].lower()
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
    assert new_hole["allowed_operations"] == ["resize_hole", "delete_hole"]
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
    """A bare cylindrical-face selection (no hole_id) has no safe parametric edit
    → structured unsupported_operation, never a crash, and the part is unchanged."""
    h = auth["headers"]
    d = _make_cad_plan_plate(client, h)
    did, before_hash = d["id"], d["spec_hash"]
    r = client.post(
        f"/api/designs/{did}/face-edit",
        json={
            "instruction": "Resize this hole to 8 mm",
            "quick_action": "resize_hole",
            # A cylindrical face selection with NO hole_id and no numeric feature.
            "selection": {"selection_type": "backend_face", "face_kind": "cylindrical",
                          "feature_id": "cylindrical_face", "normal": [1, 0, 0]},
        },
        headers=h,
    )
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["code"] == "unsupported_operation"
    assert detail["safe_to_retry"] is True
    assert detail["selection_type"] == "backend_face"
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


# --- plate-like family normalization (CadPlan add_hole / edge treatment) ----
# The LLM planner labels parts freely (e.g. "adapter/mounting plate"), so the
# editable-plate gate must recognise plate-like families structurally, not by an
# exact object_type match — while still refusing round/threaded/tube/assembly
# parts. These are pure (no DB) tests over the plan handlers.
from app.cad.plan.schema import CadPlan, Feature  # noqa: E402
from app.editing.face_edit import (  # noqa: E402
    _plan_is_plate_like,
    apply_face_edit_to_plan,
)


def _flat_plate_plan(object_type: str) -> CadPlan:
    """An 80×40×6 flat plate with two holes, labelled `object_type`."""
    return CadPlan(
        object_type=object_type,
        name=object_type,
        features=[
            Feature(id="base_plate", kind="plate",
                    params={"width": 80, "depth": 40, "thickness": 6}, at=[0, 0, 0]),
            Feature(id="mh0", kind="hole", op="cut", through=True, axis="z",
                    params={"diameter": 6}, at=[-25, 0, 0]),
            Feature(id="mh1", kind="hole", op="cut", through=True, axis="z",
                    params={"diameter": 6}, at=[25, 0, 0]),
        ],
    )


def _plan_hole_req() -> FaceLocalizedEditRequest:
    return FaceLocalizedEditRequest(
        instruction="Add a 10 mm through hole centered on this face",
        quick_action="hole",
        selection={"feature_id": "face_top", "face_kind": "planar", "normal": [0, 0, 1]},
    )


_PLATE_BBOX = {"x": 80.0, "y": 40.0, "z": 6.0}


@pytest.mark.parametrize(
    "object_type",
    [
        "adapter/mounting plate",   # the exact live-failure label
        "adapter mounting plate",
        "adapter_mounting_plate",
        "adapter plate",
        "adapter_plate",
        "mounting plate",
        "mounting_plate",
        "flat plate",
        "rectangular_bracket",
        "Cover Plate",              # arbitrary *_plate label + odd casing
    ],
)
def test_plan_add_hole_supported_on_plate_like_families(object_type):
    plan = _flat_plate_plan(object_type)
    new_plan, outcome = apply_face_edit_to_plan(plan, _plan_hole_req(), _PLATE_BBOX)
    assert outcome.op == "add_hole"
    assert len([f for f in new_plan.features if f.kind.value == "hole"]) == 3


def test_plan_add_hole_structural_fallback_for_unknown_flat_name():
    """An unrecognised label but a genuinely flat box graph is still drillable."""
    plan = _flat_plate_plan("weird_widget_xyz")
    new_plan, outcome = apply_face_edit_to_plan(plan, _plan_hole_req(), _PLATE_BBOX)
    assert outcome.op == "add_hole"
    assert len([f for f in new_plan.features if f.kind.value == "hole"]) == 3


@pytest.mark.parametrize(
    "object_type",
    ["tire", "flanged_pipe_branch", "bolt", "hex_nut", "timing_pulley_gt2", "wheel_assembly"],
)
def test_plan_add_hole_rejected_on_non_plate_families(object_type):
    """Round / threaded / tube / assembly parts are refused even if a plate-shaped
    body sneaks into the graph — hole-adding must never open up to these."""
    plan = _flat_plate_plan(object_type)  # deliberately a plate body + disallowed name
    assert _plan_is_plate_like(plan) is False
    with pytest.raises(FaceEditReview) as exc:
        apply_face_edit_to_plan(plan, _plan_hole_req(), _PLATE_BBOX)
    # The rejection stays helpful for genuinely unsupported parts.
    assert "flat plate" in str(exc.value).lower()


def test_plan_add_hole_rejected_on_structurally_non_flat_body():
    """A non-plate body (pipe) with an unknown name is rejected structurally."""
    plan = CadPlan(
        object_type="mystery_part",
        name="mystery",
        features=[Feature(id="body", kind="pipe", params={"od": 40, "length": 60})],
    )
    assert _plan_is_plate_like(plan) is False
    with pytest.raises(FaceEditReview):
        apply_face_edit_to_plan(plan, _plan_hole_req(), _PLATE_BBOX)


# --- selection-aware classification + structured unsupported (Phase 8) -------
from app.editing.face_edit import (  # noqa: E402
    FaceEditUnsupported,
    UNSUPPORTED_EDIT_MESSAGE,
    _selection_kind,
)
from app.schemas.editing_spec import FaceSelectionSpec  # noqa: E402


@pytest.mark.parametrize(
    "instruction,expected",
    [
        ("make the hole 5mm", "resize_hole"),
        ("make this 5 mm", "resize_hole"),
        ("resize this to 5mm", "resize_hole"),
        ("change diameter to 5 mm", "resize_hole"),
        ("hole 8mm", "resize_hole"),
        ("add a hole 5mm", "resize_hole"),   # hole selected → never add_hole
        ("delete this", "delete_hole"),
        ("remove this hole", "delete_hole"),
    ],
)
def test_classify_selected_hole_prioritizes_hole_ops(instruction, expected):
    sel = FaceSelectionSpec(selection_type="backend_hole", hole_id="hole_2",
                            face_kind="cylindrical")
    assert classify_edit(None, instruction, sel) == expected


def test_classify_selected_edge_prioritizes_edge_ops():
    edge = FaceSelectionSpec(selection_type="backend_edge", edge_id="edge_1")
    assert classify_edit(None, "fillet", edge) == "fillet"
    assert classify_edit(None, "round this edge", edge) == "fillet"
    assert classify_edit(None, "chamfer", edge) == "chamfer"
    assert classify_edit(None, "bevel this edge", edge) == "chamfer"


def test_classify_cylindrical_face_and_body_are_unsupported():
    cyl = FaceSelectionSpec(selection_type="backend_face", face_kind="cylindrical")
    body = FaceSelectionSpec(selection_type="backend_body", body_id="main_body")
    # A pattern chip/keyword on a cylindrical face never routes to a fake op.
    assert classify_edit("pattern", "pattern", cyl) == "unsupported_selection"
    assert classify_edit(None, "do something", cyl) == "unsupported_selection"
    assert classify_edit(None, "change material", body) == "unsupported_selection"


def test_selection_kind_derivation():
    assert _selection_kind(FaceSelectionSpec(selection_type="backend_hole")) == "hole"
    assert _selection_kind(FaceSelectionSpec(hole_id="hole_0")) == "hole"
    assert _selection_kind(FaceSelectionSpec(selection_type="backend_edge")) == "edge"
    assert _selection_kind(FaceSelectionSpec(selection_type="backend_body")) == "body"
    assert _selection_kind(FaceSelectionSpec(selection_type="backend_face",
                                             face_kind="planar")) == "face"
    assert _selection_kind(None) == "face"


def test_cad_plan_selected_hole_freetext_resize(client, auth):
    """The exact live bug: selected hole + typed 'make the hole 5mm' → resize."""
    h = auth["headers"]
    did = _make_cad_plan_plate(client, h)["id"]
    # Add a center hole (hole_2) first so there's a fresh hole to resize.
    add = client.post(
        f"/api/designs/{did}/face-edit",
        json={"instruction": "Add a 10 mm through hole", "quick_action": "hole",
              "selection": _top_face_selection()},
        headers=h,
    )
    assert add.status_code == 200, add.text
    before_hash = add.json()["spec_hash"]

    # No quick_action — free-typed instruction on a selected hole.
    r = client.post(
        f"/api/designs/{did}/face-edit",
        json={"instruction": "make the hole 5mm", "selection": _hole_selection("hole_2")},
        headers=h,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["spec_hash"] != before_hash
    resized = next(hh for hh in body["selectable_holes"] if hh["hole_id"] == "hole_2")
    assert resized["diameter_mm"] == 5.0
    # Real CAD regenerated.
    assert {e["fmt"] for e in body["exports"]} >= {"stl", "step"}


def test_cad_plan_selected_hole_bare_size(client, auth):
    """Selected hole + 'hole 8mm' (no quick_action) → resize_hole → 200."""
    h = auth["headers"]
    did = _make_cad_plan_plate(client, h)["id"]
    r = client.post(
        f"/api/designs/{did}/face-edit",
        json={"instruction": "hole 8mm", "selection": _hole_selection("hole_0")},
        headers=h,
    )
    assert r.status_code == 200, r.text
    resized = next(hh for hh in r.json()["selectable_holes"] if hh["hole_id"] == "hole_0")
    assert resized["diameter_mm"] == 8.0


def test_cad_plan_cylindrical_face_pattern_structured_unsupported(client, auth):
    """Selected cylindrical face + Pattern → structured unsupported_operation."""
    h = auth["headers"]
    d = _make_cad_plan_plate(client, h)
    did, before_hash = d["id"], d["spec_hash"]
    r = client.post(
        f"/api/designs/{did}/face-edit",
        json={
            "instruction": "pattern this",
            "quick_action": "pattern",
            "selection": {"selection_type": "backend_face", "face_kind": "cylindrical",
                          "feature_id": "cyl_face", "normal": [1, 0, 0]},
        },
        headers=h,
    )
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["code"] == "unsupported_operation"
    assert detail["message"] == UNSUPPORTED_EDIT_MESSAGE
    assert detail["safe_to_retry"] is True
    # The design is not corrupted by an unsupported attempt.
    after = client.get(f"/api/designs/{did}", headers=h).json()
    assert after["spec_hash"] == before_hash


def test_cad_plan_planar_add_hole_still_works(client, auth):
    """Regression guard: add_hole on a planar plate face still succeeds."""
    h = auth["headers"]
    d = _make_cad_plan_plate(client, h)
    did, before_hash = d["id"], d["spec_hash"]
    r = client.post(
        f"/api/designs/{did}/face-edit",
        json={"instruction": "Add a 10 mm through hole centered on this face",
              "quick_action": "add_hole", "selection": _top_face_selection()},
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert r.json()["spec_hash"] != before_hash
    assert len(r.json()["selectable_holes"]) == 3
