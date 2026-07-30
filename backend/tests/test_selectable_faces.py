"""Phase 5: semantic selectable-face metadata — extraction, model, stability,
normalized normals, allowed operations, and DTO presence for several families."""
import math

from app.cad.selectable_faces import extract_selectable_faces
from app.export.exporter import build_solid
from app.schemas.api import SelectableFaceDTO
from app.schemas.design_spec import DesignSpec, Hole


def _bracket() -> DesignSpec:
    return DesignSpec(
        object_type="rectangular_bracket",
        dimensions={"width": 80, "depth": 40, "thickness": 6},
        holes=[Hole(diameter=6.6, x=-25, y=0)],
    )


def _adapter() -> DesignSpec:
    return DesignSpec(
        object_type="adapter_plate",
        dimensions={"width": 60, "depth": 60, "thickness": 5},
    )


def _enclosure() -> DesignSpec:
    return DesignSpec(
        object_type="enclosure",
        dimensions={"width": 90, "depth": 60, "height": 40, "wall_thickness": 2.4},
    )


# --- model -----------------------------------------------------------------
def test_selectable_face_dto_parses_and_ignores_extras():
    dto = SelectableFaceDTO(
        face_id="face_top_abc",
        feature_id="face_top",
        body_id="main_body",
        face_kind="planar",
        label="Top face",
        normal=(0, 0, 1),
        center=(0, 0, 3),
        area_mm2=3200.0,
        bounds_mm={"width": 80, "height": 40, "depth": 0},
        local_frame={"origin": (0, 0, 3), "x_axis": (1, 0, 0), "y_axis": (0, 1, 0), "z_axis": (0, 0, 1)},
        allowed_operations=["add_hole", "fillet_edges"],
        confidence=0.95,
        unknown_extra="dropped",  # extra="ignore"
    )
    assert dto.feature_id == "face_top"
    assert dto.bounds_mm.width == 80


# --- extraction ------------------------------------------------------------
def test_bracket_exposes_top_and_bottom_faces():
    faces = extract_selectable_faces(build_solid(_bracket()), _bracket())
    assert faces, "bracket should expose selectable faces"
    by_feature = {f["feature_id"] for f in faces}
    assert "face_top" in by_feature
    assert "face_bottom" in by_feature
    top = next(f for f in faces if f["feature_id"] == "face_top")
    assert top["face_kind"] == "planar"
    assert top["confidence"] >= 0.9
    assert "add_hole" in top["allowed_operations"]


def test_normals_are_normalized():
    for spec in (_bracket(), _adapter(), _enclosure()):
        for f in extract_selectable_faces(build_solid(spec), spec):
            nx, ny, nz = f["normal"]
            assert math.isclose(math.sqrt(nx * nx + ny * ny + nz * nz), 1.0, abs_tol=1e-3)


def test_allowed_operations_reasonable():
    faces = extract_selectable_faces(build_solid(_bracket()), _bracket())
    top = next(f for f in faces if f["feature_id"] == "face_top")
    # Capability matrix: a big planar face offers only implemented ops.
    assert set(top["allowed_operations"]) == {"add_hole", "fillet_edges", "chamfer_edges"}
    # Unimplemented ops are never advertised as chips.
    for unimpl in ("add_slot", "add_cutout", "add_boss", "add_vent", "pattern"):
        assert unimpl not in top["allowed_operations"]
    # Cylindrical (hole wall) faces advertise nothing (no safe parametric edit) and
    # never claim high confidence.
    cyl = [f for f in faces if f["face_kind"] == "cylindrical"]
    for f in cyl:
        assert f["confidence"] <= 0.65
        assert f["allowed_operations"] == []
        assert "pattern" not in f["allowed_operations"]


def test_face_ids_are_stable_across_regeneration():
    a = extract_selectable_faces(build_solid(_bracket()), _bracket())
    b = extract_selectable_faces(build_solid(_bracket()), _bracket())
    assert [f["face_id"] for f in a] == [f["face_id"] for f in b]


def test_three_families_expose_faces():
    for spec in (_bracket(), _adapter(), _enclosure()):
        faces = extract_selectable_faces(build_solid(spec), spec)
        assert len(faces) >= 3, f"{spec.object_type} exposed too few faces"
        assert all(0.0 <= f["confidence"] <= 1.0 for f in faces)


def test_robust_on_bad_solid_returns_empty():
    class Bad:
        def val(self):
            raise RuntimeError("no solid")

    assert extract_selectable_faces(Bad(), _bracket()) == []


# --- CadPlan (feature-graph) holes -----------------------------------------
def _plate_plan():
    from app.cad.plan import deterministic
    from app.cad.plan.normalize import normalize_cad_plan

    pr = "80 mm x 40 mm x 6 mm mounting plate with two M6 holes"
    return normalize_cad_plan(deterministic.plan(pr), pr)


def test_plan_holes_extracted_with_aligned_ids():
    from app.cad.selectable_faces import extract_selectable_holes_from_plan

    holes = extract_selectable_holes_from_plan(_plate_plan())
    ids = [h["hole_id"] for h in holes]
    assert len(ids) == 2
    assert len(set(ids)) == 2, "hole ids must be unique"
    assert all(hid.startswith("hole_") for hid in ids)
    for h in holes:
        assert h["diameter_mm"] > 0
        assert h["through"] is True
        assert h["allowed_operations"] == ["resize_hole", "delete_hole"]
        # A Z-axis through hole opens on the plate's top face (z = thickness).
        assert h["axis"] == [0.0, 0.0, 1.0]
        assert h["center"][2] == 6.0


def test_plan_hole_ids_match_face_edit_indices():
    """Each hole's ``feature_index`` must index the SAME hole features the
    face-edit plan handlers enumerate, or resize/delete would hit the wrong
    hole. The ``hole_id`` itself is content-hashed and carries no positional
    meaning (see app.cad.selectable_faces._stable_hole_id)."""
    from app.cad.selectable_faces import extract_selectable_holes_from_plan
    from app.editing.face_edit import _plan_hole_features

    plan = _plate_plan()
    holes = extract_selectable_holes_from_plan(plan)
    feats = _plan_hole_features(plan)
    assert len(holes) == len(feats)
    for i, h in enumerate(holes):
        assert h["feature_index"] == i


def test_plan_hole_extraction_robust_on_garbage():
    from app.cad.selectable_faces import extract_selectable_holes_from_plan

    class Bad:
        features = None

    assert extract_selectable_holes_from_plan(Bad()) == []


# --- API -------------------------------------------------------------------
def test_design_dto_includes_selectable_faces(client, auth, legacy_engine):
    h = auth["headers"]
    d = client.post(
        "/api/designs/create",
        json={"prompt": "bracket 80x40x6mm with one M6 hole"},
        headers=h,
    ).json()
    faces = d["selectable_faces"]
    assert faces, "DTO should carry selectable_faces"
    assert any(f["feature_id"] == "face_top" for f in faces)
    # Re-fetch keeps them.
    again = client.get(f"/api/designs/{d['id']}", headers=h).json()
    assert again["selectable_faces"]
