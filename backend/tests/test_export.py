"""Export + deterministic regeneration: same spec -> same file; edits -> new file."""
import tempfile
from pathlib import Path

import cadquery as cq
import pytest

from app.export.exporter import generate, spec_hash
from app.schemas.design_spec import DesignSpec, Hole


def _bracket(width=80):
    return DesignSpec(
        object_type="rectangular_bracket",
        dimensions={"width": width, "depth": 40, "thickness": 5},
    )


def test_export_files_nonempty():
    result = generate(_bracket())
    assert len(result.stl_bytes) > 100
    assert len(result.step_bytes) > 100


def test_regeneration_is_deterministic():
    a = generate(_bracket(80))
    b = generate(_bracket(80))
    assert a.spec_hash == b.spec_hash
    assert a.stl_bytes == b.stl_bytes


def test_parameter_change_produces_different_model():
    a = generate(_bracket(80))
    b = generate(_bracket(120))
    assert a.spec_hash != b.spec_hash
    assert a.stl_bytes != b.stl_bytes
    assert b.bounding_box_mm["x"] > a.bounding_box_mm["x"]


def test_spec_hash_stable_across_key_order():
    s1 = DesignSpec(object_type="spacer", dimensions={"outer_diameter": 12, "length": 20})
    s2 = DesignSpec(object_type="spacer", dimensions={"length": 20, "outer_diameter": 12})
    assert spec_hash(s1) == spec_hash(s2)


@pytest.mark.parametrize(
    "spec",
    [
        DesignSpec(
            object_type="rectangular_bracket",
            dimensions={"width": 80, "depth": 40, "thickness": 6, "corner_radius": 4},
            holes=[
                Hole(diameter=5.5, x=-25, y=0, hole_type="counterbore",
                     counterbore_diameter=10, counterbore_depth=3),
                Hole(diameter=4.5, x=25, y=0, hole_type="countersink",
                     countersink_diameter=9),
            ],
        ),
        DesignSpec(
            object_type="enclosure",
            dimensions={"width": 90, "depth": 60, "height": 35, "wall_thickness": 2.5,
                        "lid_thickness": 2.5, "boss_diameter": 7},
        ),
        DesignSpec(
            object_type="drill_jig",
            dimensions={"length": 100, "width": 60, "thickness": 6,
                        "hole_diameter": 5, "hole_spacing": 20, "lip_height": 8},
        ),
    ],
)
def test_step_reimports_cleanly(spec):
    """A STEP that re-imports through OpenCascade (FreeCAD's kernel) is valid
    geometry, not just non-empty bytes."""
    result = generate(spec)
    with tempfile.NamedTemporaryFile(suffix=".step", delete=False) as tmp:
        path = Path(tmp.name)
    try:
        path.write_bytes(result.step_bytes)
        imported = cq.importers.importStep(str(path))
        solid = imported.val()
        bb = solid.BoundingBox()
        assert bb.xlen > 0 and bb.ylen > 0 and bb.zlen > 0
        # Re-tessellation succeeding confirms a watertight, readable B-rep.
        verts, tris = solid.tessellate(0.5)
        assert len(verts) > 0 and len(tris) > 0
    finally:
        path.unlink(missing_ok=True)
