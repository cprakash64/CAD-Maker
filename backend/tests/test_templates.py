"""Every template must build a valid solid and export non-empty STL + STEP."""
import pytest

from app.cad.registry import all_templates, get_template
from app.export.exporter import generate
from app.schemas.design_spec import DesignSpec, Hole

SAMPLES = {
    "rectangular_bracket": dict(
        dimensions={"width": 80, "depth": 40, "thickness": 5},
        holes=[Hole(diameter=6, x=-25, y=0), Hole(diameter=6, x=25, y=0)],
        fillet_radius=3,
    ),
    "l_bracket": dict(
        dimensions={
            "length": 60,
            "width": 40,
            "height": 60,
            "thickness": 5,
            "hole_diameter": 6,
        }
    ),
    "enclosure": dict(
        dimensions={
            "width": 80,
            "depth": 60,
            "height": 40,
            "wall_thickness": 2.5,
            "lid_thickness": 2.5,
        }
    ),
    "spacer": dict(dimensions={"outer_diameter": 12, "length": 20, "bore_diameter": 6.4}),
    "hex_standoff": dict(dimensions={"across_flats": 12, "length": 20, "bore_diameter": 4.5}),
    "pipe_clamp": dict(
        dimensions={
            "pipe_diameter": 25,
            "width": 25,
            "thickness": 6,
            "ear_width": 18,
            "hole_diameter": 6,
        }
    ),
    "drill_jig": dict(
        dimensions={
            "length": 100,
            "width": 60,
            "thickness": 6,
            "hole_diameter": 5,
            "hole_spacing": 20,
        }
    ),
    "handle": dict(
        dimensions={"diameter": 30, "height": 25, "bore_diameter": 8, "bore_depth": 12}
    ),
    "adapter_plate": dict(
        dimensions={"width": 100, "depth": 100, "thickness": 6, "center_bore": 30},
        holes=[Hole(diameter=6, x=40, y=40), Hole(diameter=6, x=-40, y=-40)],
    ),
    "inline_4_crankshaft": dict(dimensions={}),  # all engineered defaults
    "flanged_pipe_branch": dict(dimensions={}),
    "simple_gear_or_pulley": dict(dimensions={"tooth_count": 18}),
}


def test_all_object_types_have_samples():
    assert set(SAMPLES) == set(all_templates())


@pytest.mark.parametrize("object_type", list(SAMPLES))
def test_template_builds_and_exports(object_type):
    spec = DesignSpec(object_type=object_type, **SAMPLES[object_type])
    result = generate(spec)
    assert len(result.stl_bytes) > 0, "STL must be non-empty"
    assert len(result.step_bytes) > 0, "STEP must be non-empty"
    # STEP files are ASCII and start with ISO-10303.
    assert result.step_bytes[:5] == b"ISO-1"
    assert result.preview.triangle_count > 0
    assert result.preview.vertex_count > 0
    for axis in ("x", "y", "z"):
        assert result.bounding_box_mm[axis] > 0


def test_missing_required_dimension_raises():
    from app.cad.base import CadGenerationError

    # pipe_clamp requires pipe_diameter via the generator's resolve step only if
    # marked required; here we verify resolve fills defaults and stays valid.
    spec = DesignSpec(object_type="spacer", dimensions={"outer_diameter": 5})
    # bore default 6.4 > od 5 -> generator must reject.
    with pytest.raises(CadGenerationError):
        get_template("spacer").build(spec)
