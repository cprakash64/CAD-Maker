"""Phase 6: richer selection metadata (edges/holes/bodies/features), DTO models,
selection_type validation, and deterministic hole/edge edit operations."""
import pytest

from app.cad.selectable_faces import (
    extract_selectable_bodies,
    extract_selectable_edges,
    extract_selectable_features,
    extract_selectable_holes,
)
from app.editing.face_edit import FaceEditError, FaceEditReview, apply_face_edit, classify_edit
from app.export.exporter import build_solid
from app.schemas.api import (
    SelectableBodyDTO,
    SelectableEdgeDTO,
    SelectableFeatureDTO,
    SelectableHoleDTO,
)
from app.schemas.design_spec import DesignSpec, Hole
from app.schemas.editing_spec import FaceLocalizedEditRequest


def _bracket() -> DesignSpec:
    return DesignSpec(
        object_type="rectangular_bracket",
        dimensions={"width": 80, "depth": 40, "thickness": 6},
        holes=[Hole(diameter=6.6, x=-25, y=0), Hole(diameter=6.6, x=25, y=0)],
    )


# --- DTO models ------------------------------------------------------------
def test_selection_dtos_parse():
    SelectableHoleDTO(hole_id="hole_0", diameter_mm=6, center=(0, 0, 3), extra="drop")
    SelectableEdgeDTO(edge_id="e0", start=(0, 0, 0), end=(1, 0, 0), length_mm=1)
    SelectableBodyDTO(body_id="main_body", volume_mm3=1000)
    SelectableFeatureDTO(feature_id="hole_0", feature_type="hole")


# --- extraction ------------------------------------------------------------
def test_extract_holes_from_spec():
    holes = extract_selectable_holes(_bracket())
    ids = [h["hole_id"] for h in holes]
    assert len(ids) == 2
    assert len(set(ids)) == 2, "hole ids must be unique"
    assert all(hid.startswith("hole_") for hid in ids)
    assert holes[0]["diameter_mm"] == 6.6
    assert "resize_hole" in holes[0]["allowed_operations"]
    assert "delete_hole" in holes[0]["allowed_operations"]


def test_hole_ids_are_stable_across_unrelated_edits():
    """A hole's id must not shift just because an earlier hole in the list is
    removed — ids are content-hashed, not positional."""
    spec = _bracket()
    before = {tuple(h["center"]): h["hole_id"] for h in extract_selectable_holes(spec)}
    edited = spec.model_copy(deep=True)
    edited.holes = edited.holes[1:]  # drop the first hole
    after = {tuple(h["center"]): h["hole_id"] for h in extract_selectable_holes(edited)}
    surviving_center = tuple(extract_selectable_holes(edited)[0]["center"])
    assert after[surviving_center] == before[surviving_center]


def test_extract_edges_and_bodies():
    solid = build_solid(_bracket())
    edges = extract_selectable_edges(solid)
    assert edges, "bracket should expose edges"
    assert all("fillet_edge" in e["allowed_operations"] for e in edges)
    assert any(e["edge_kind"] == "linear" for e in edges)
    bodies = extract_selectable_bodies(solid, _bracket(), {"x": 80, "y": 40, "z": 6})
    assert bodies[0]["body_id"] == "main_body"
    assert bodies[0]["volume_mm3"] > 0
    # Capability matrix: body edits aren't implemented yet → no advertised ops.
    assert bodies[0]["allowed_operations"] == []


def test_extract_features_excludes_faces_and_bodies():
    feats = extract_selectable_features(_bracket(), {"x": 80, "y": 40, "z": 6})
    types = {f["feature_type"] for f in feats}
    assert "hole" in types
    assert "face" not in types and "body" not in types


# --- selection_type validation --------------------------------------------
def test_selection_type_accepts_known_and_degrades_unknown():
    for st in ("backend_face", "backend_edge", "backend_hole", "backend_body",
               "backend_feature", "visual_face"):
        req = FaceLocalizedEditRequest(
            instruction="x", selection={"selection_type": st}
        )
        assert req.selection.selection_type == st
    weird = FaceLocalizedEditRequest(instruction="x", selection={"selection_type": "nonsense"})
    assert weird.selection.selection_type == "visual_face"  # degraded, not rejected


# --- deterministic operations ---------------------------------------------
def _req(instruction, quick_action=None, **sel):
    return FaceLocalizedEditRequest(
        instruction=instruction, quick_action=quick_action, selection={**sel}
    )


def test_classify_edge_and_hole_ops():
    assert classify_edit("fillet_edge", "") == "fillet"
    assert classify_edit("chamfer_edge", "") == "chamfer"
    assert classify_edit("resize_hole", "") == "resize_hole"
    assert classify_edit("delete_hole", "") == "delete_hole"


def test_resize_hole():
    spec = _bracket()
    hole_id = extract_selectable_holes(spec)[0]["hole_id"]
    new_spec, out = apply_face_edit(
        spec,
        _req("Resize this hole to 10 mm", "resize_hole", selection_type="backend_hole", hole_id=hole_id),
        {"x": 80, "y": 40, "z": 6},
    )
    assert out.op == "resize_hole"
    assert new_spec.holes[0].diameter == 10.0
    assert new_spec.holes[1].diameter == 6.6  # untouched


def test_delete_hole():
    spec = _bracket()
    hole_id = extract_selectable_holes(spec)[1]["hole_id"]
    new_spec, out = apply_face_edit(
        spec, _req("Delete this hole", "delete_hole", selection_type="backend_hole", hole_id=hole_id),
        {"x": 80, "y": 40, "z": 6},
    )
    assert out.op == "delete_hole"
    assert len(new_spec.holes) == 1


def test_edge_fillet_via_edge_selection():
    new_spec, out = apply_face_edit(
        _bracket(),
        _req("Fillet this edge by 2 mm", "fillet_edge", selection_type="backend_edge", edge_id="edge_x"),
        {"x": 80, "y": 40, "z": 6},
    )
    assert new_spec.fillet_radius == 2.0


def test_resize_hole_rejects_oversized():
    spec = _bracket()
    hole_id = extract_selectable_holes(spec)[0]["hole_id"]
    with pytest.raises(FaceEditError):
        apply_face_edit(
            spec,
            _req("Resize to 200 mm", "resize_hole", selection_type="backend_hole", hole_id=hole_id),
            {"x": 80, "y": 40, "z": 6},
        )


def test_body_and_feature_ops_need_review():
    for qa in ("rename", "material", "duplicate", "mirror", "edit_dimensions", "suppress", "move_hole", "pattern_hole"):
        with pytest.raises(FaceEditReview):
            apply_face_edit(_bracket(), _req("do it", qa), {"x": 80, "y": 40, "z": 6})


def test_delete_missing_hole_reviews_without_error():
    with pytest.raises(FaceEditReview):
        apply_face_edit(
            _bracket(),
            _req("Delete", "delete_hole", selection_type="backend_hole", hole_id="hole_9"),
            {"x": 80, "y": 40, "z": 6},
        )


# --- API -------------------------------------------------------------------
def test_dto_includes_new_selection_lists(client, auth, legacy_engine):
    h = auth["headers"]
    d = client.post(
        "/api/designs/create",
        json={"prompt": "bracket 80x40x6mm with two M6 holes"},
        headers=h,
    ).json()
    assert d["selectable_holes"], "expected selectable_holes"
    assert d["selectable_edges"], "expected selectable_edges"
    assert d["selectable_bodies"], "expected selectable_bodies"
    assert all(x["hole_id"].startswith("hole_") for x in d["selectable_holes"])


def test_api_resize_hole_regenerates(client, auth, legacy_engine):
    h = auth["headers"]
    d = client.post(
        "/api/designs/create",
        json={"prompt": "bracket 80x40x6mm with two M6 holes"},
        headers=h,
    ).json()
    did, before = d["id"], d["spec_hash"]
    hole_id = d["selectable_holes"][0]["hole_id"]
    r = client.post(
        f"/api/designs/{did}/face-edit",
        json={
            "instruction": "Resize this hole to 9 mm",
            "quick_action": "resize_hole",
            "selection": {"selection_type": "backend_hole", "hole_id": hole_id},
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert r.json()["spec_hash"] != before
    assert r.json()["spec"]["holes"][0]["diameter"] == 9.0
