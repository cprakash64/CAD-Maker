"""P0-2: feature metadata + circle-to-edit localized editing."""
from app.cad.features import extract_features, feature_ids
from app.editing.localized import apply_localized_request
from app.export.exporter import generate
from app.schemas.design_spec import DesignSpec, Hole
from app.schemas.editing_spec import LocalizedEditRequest, SelectedFeatureSpec


def _bracket() -> DesignSpec:
    return DesignSpec(
        object_type="rectangular_bracket",
        dimensions={"width": 80, "depth": 40, "thickness": 6},
        holes=[Hole(diameter=6.6, x=-25, y=0), Hole(diameter=6.6, x=25, y=0)],
    )


# --- Feature metadata -----------------------------------------------------
def test_features_have_stable_ids():
    spec = _bracket()
    ids = feature_ids(spec)
    assert "hole_0" in ids and "hole_1" in ids and "body" in ids


def test_feature_ids_stable_after_edit():
    spec = _bracket()
    before = {f.id for f in extract_features(spec)}
    # Change hole 0 diameter via circle-edit; ids must persist.
    req = LocalizedEditRequest(
        selected=SelectedFeatureSpec(entity_type="hole", entity_id="hole_0"),
        instruction="make this hole 8mm", validated_parameters={"diameter": 8},
    )
    new_spec, result = apply_localized_request(spec, req)
    assert result.applied
    after = {f.id for f in extract_features(new_spec)}
    assert {"hole_0", "hole_1"} <= after == before


def test_generation_includes_features():
    gen = generate(_bracket())
    fids = {f["id"] for f in gen.features}
    assert "hole_0" in fids


# --- Circle-to-edit apply -------------------------------------------------
def test_selected_hole_edit_changes_only_that_hole():
    spec = _bracket()
    req = LocalizedEditRequest(
        selected=SelectedFeatureSpec(entity_type="hole", entity_id="hole_0"),
        instruction="make this 8 mm", validated_parameters={"diameter": 8},
    )
    new_spec, result = apply_localized_request(spec, req)
    assert result.applied and result.selected_entity_id == "hole_0"
    assert new_spec.holes[0].diameter == 8.0
    assert new_spec.holes[1].diameter == 6.6  # unchanged


def test_selected_hole_counterbore_inferred():
    spec = _bracket()
    req = LocalizedEditRequest(
        selected=SelectedFeatureSpec(entity_type="hole", entity_id="hole_1"),
        instruction="make this counterbored",
    )
    new_spec, result = apply_localized_request(spec, req)
    assert result.applied and new_spec.holes[1].hole_type == "counterbore"
    assert new_spec.holes[0].hole_type == "simple"  # only the selected one


def test_selected_edge_fillet():
    spec = _bracket()
    req = LocalizedEditRequest(
        selected=SelectedFeatureSpec(entity_type="edge", entity_id="edge_top"),
        instruction="round this edge 3mm",
    )
    gen = generate(spec)  # ensure edge_top exists in features (needs bbox)
    new_spec, result = apply_localized_request(spec, req, gen.bounding_box_mm)
    assert result.applied and new_spec.fillet_radius == 3.0


def test_invalid_feature_id_rejected():
    spec = _bracket()
    req = LocalizedEditRequest(
        selected=SelectedFeatureSpec(entity_type="hole", entity_id="hole_99"),
        instruction="make this 8mm",
    )
    new_spec, result = apply_localized_request(spec, req)
    assert new_spec is None and not result.applied
    assert "not a feature" in result.message


def test_unsupported_selection_explains():
    spec = DesignSpec(object_type="enclosure",
                      dimensions={"width": 90, "depth": 60, "height": 40})
    gen = generate(spec)
    req = LocalizedEditRequest(
        selected=SelectedFeatureSpec(entity_type="body", entity_id="body"),
        operation="add_gusset", instruction="add a gusset",
    )
    new_spec, result = apply_localized_request(spec, req, gen.bounding_box_mm)
    assert new_spec is None and not result.applied and result.message


def test_flange_bolt_hole_edit():
    spec = DesignSpec(object_type="flanged_pipe_branch", dimensions={})
    gen = generate(spec)
    req = LocalizedEditRequest(
        selected=SelectedFeatureSpec(entity_type="bolt_pattern", entity_id="bolt_pattern_main"),
        instruction="increase the bolt holes to 18 mm",
    )
    new_spec, result = apply_localized_request(spec, req, gen.bounding_box_mm)
    assert result.applied
    assert new_spec.dimensions["bolt_hole_diameter_mm"] == 18.0


def test_flange_thicken():
    spec = DesignSpec(object_type="flanged_pipe_branch", dimensions={})
    gen = generate(spec)
    req = LocalizedEditRequest(
        selected=SelectedFeatureSpec(entity_type="flange", entity_id="flange_main_front"),
        instruction="make this flange thicker, 24mm",
    )
    new_spec, result = apply_localized_request(spec, req, gen.bounding_box_mm)
    assert result.applied and new_spec.dimensions["flange_thickness_mm"] == 24.0


# --- API ------------------------------------------------------------------
def test_circle_edit_endpoint(client, auth):
    h = auth["headers"]
    created = client.post(
        "/api/designs/create",
        json={"prompt": "bracket 80x40x6mm with two M6 holes"}, headers=h,
    ).json()
    did, before = created["id"], created["spec_hash"]
    assert any(f["id"] == "hole_0" for f in created["features"])

    r = client.post(
        f"/api/designs/{did}/circle-edit",
        json={
            "selected": {"entity_type": "hole", "entity_id": "hole_0"},
            "instruction": "make this 8mm",
            "validated_parameters": {"diameter": 8},
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert r.json()["spec_hash"] != before
    assert r.json()["spec"]["holes"][0]["diameter"] == 8.0


def test_circle_edit_invalid_feature_returns_message(client, auth):
    h = auth["headers"]
    did = client.post(
        "/api/designs/create", json={"prompt": "bracket 80x40x6mm"}, headers=h
    ).json()["id"]
    r = client.post(
        f"/api/designs/{did}/circle-edit",
        json={"selected": {"entity_type": "hole", "entity_id": "hole_42"},
              "instruction": "make this 8mm"},
        headers=h,
    )
    assert r.status_code == 200
    assert r.json()["clarification_question"]
