"""Point-and-prompt localized editing: schema, apply logic, and API."""
import pytest
from pydantic import ValidationError

from app.editing.localized import UnsupportedLocalizedEdit, apply_localized
from app.export.exporter import generate
from app.schemas.design_spec import DesignSpec, Hole
from app.schemas.editing_spec import LocalizedModificationSpec


def _bracket() -> DesignSpec:
    return DesignSpec(
        object_type="rectangular_bracket",
        dimensions={"width": 80, "depth": 40, "thickness": 6},
        holes=[Hole(diameter=6.6, x=-25, y=0), Hole(diameter=6.6, x=25, y=0)],
    )


# --- Schema ---------------------------------------------------------------
def test_localized_spec_validation():
    m = LocalizedModificationSpec(
        selected_entity_type="hole",
        selected_entity_id="0",
        allowed_operation="change_hole_diameter",
        natural_language_instruction="make this hole 8mm",
    )
    assert m.allowed_operation == "change_hole_diameter"


def test_localized_spec_rejects_bad_operation():
    with pytest.raises(ValidationError):
        LocalizedModificationSpec(
            selected_entity_type="hole",
            selected_entity_id="0",
            allowed_operation="frobnicate",
            natural_language_instruction="x",
        )


# --- Apply logic ----------------------------------------------------------
def test_selected_hole_diameter_edit():
    spec = _bracket()
    m = LocalizedModificationSpec(
        selected_entity_type="hole", selected_entity_id="0",
        allowed_operation="change_hole_diameter",
        natural_language_instruction="make this hole 8 mm",
    )
    new, msg = apply_localized(spec, m)
    assert new.holes[0].diameter == 8.0
    assert generate(new).preview.triangle_count > 0


def test_selected_hole_counterbore_edit():
    spec = _bracket()
    m = LocalizedModificationSpec(
        selected_entity_type="hole", selected_entity_id="1",
        allowed_operation="add_counterbore",
        natural_language_instruction="make this counterbored",
    )
    new, _ = apply_localized(spec, m)
    assert new.holes[1].hole_type == "counterbore"
    assert new.holes[1].counterbore_diameter > new.holes[1].diameter


def test_selected_edge_fillet_edit():
    spec = _bracket()
    m = LocalizedModificationSpec(
        selected_entity_type="edge", selected_entity_id="e3",
        allowed_operation="add_fillet",
        natural_language_instruction="round this edge 3mm",
    )
    new, _ = apply_localized(spec, m)
    assert new.fillet_radius == 3.0


def test_enclosure_add_vents():
    spec = DesignSpec(
        object_type="enclosure",
        dimensions={"width": 90, "depth": 60, "height": 40, "wall_thickness": 2.5},
    )
    m = LocalizedModificationSpec(
        selected_entity_type="face", selected_entity_id="wall+Y",
        allowed_operation="add_cutout",
        natural_language_instruction="add vents here",
    )
    new, msg = apply_localized(spec, m)
    assert new.dimensions["vent_count"] >= 1
    assert generate(new).preview.triangle_count > 0


def test_unsupported_operation_explains():
    spec = DesignSpec(object_type="enclosure", dimensions={"width": 90, "depth": 60, "height": 40})
    m = LocalizedModificationSpec(
        selected_entity_type="body", selected_entity_id="b",
        allowed_operation="add_gusset",
        natural_language_instruction="add a gusset",
    )
    with pytest.raises(UnsupportedLocalizedEdit):
        apply_localized(spec, m)


# --- API ------------------------------------------------------------------
def test_localized_edit_endpoint(client, auth):
    h = auth["headers"]
    did = client.post(
        "/api/designs/create",
        json={"prompt": "bracket 80x40x6mm with two M6 holes"},
        headers=h,
    ).json()
    design_id, before = did["id"], did["spec_hash"]

    r = client.post(
        f"/api/designs/{design_id}/localized-edit",
        json={
            "selected_entity_type": "hole",
            "selected_entity_id": "0",
            "allowed_operation": "change_hole_diameter",
            "natural_language_instruction": "make this hole 8mm",
            "validated_parameters": {"diameter": 8},
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert r.json()["spec_hash"] != before
    assert r.json()["spec"]["holes"][0]["diameter"] == 8.0


def test_localized_edit_unsupported_returns_explanation(client, auth, legacy_engine):
    # Localized edits operate on a DesignSpec (legacy template path).
    h = auth["headers"]
    did = client.post(
        "/api/designs/create", json={"prompt": "enclosure 90x60x40mm"}, headers=h
    ).json()["id"]
    r = client.post(
        f"/api/designs/{did}/localized-edit",
        json={
            "selected_entity_type": "body",
            "selected_entity_id": "b",
            "allowed_operation": "add_gusset",
            "natural_language_instruction": "add a gusset",
        },
        headers=h,
    )
    assert r.status_code == 200
    assert r.json()["clarification_question"]
