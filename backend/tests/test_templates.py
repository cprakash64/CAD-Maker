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
    "rpi4_enclosure": dict(dimensions={"wall_thickness": 2.5}),
    "rpi5_enclosure": dict(dimensions={"wall_thickness": 2.5, "logo": 1.0}),
    "board_enclosure": dict(dimensions={"wall_thickness": 2.5}, preset_id="arduino_uno_r3"),
    "motor_mount": dict(dimensions={"nema_size": 17, "bolt_spacing": 31.0,
                                    "bolt_hole": 3.4, "pilot_diameter": 23.0,
                                    "plate_size": 50.0, "thickness": 6.0}),
    "bearing_holder": dict(dimensions={"bearing_outer": 22.0, "bearing_bore": 8.0,
                                       "bearing_width": 7.0, "lip": 1.5, "wall": 4.0,
                                       "thickness": 11.0}),
    "generic_fitted_box": dict(dimensions={"board_length": 80.0, "board_width": 50.0,
                                           "board_height": 15.0, "wall_thickness": 2.5,
                                           "mount_hole": 3.0, "mount_count": 4.0}),
    "phone_holder": dict(dimensions={"phone_width": 71.6, "phone_depth": 7.8,
                                     "phone_length": 147.6, "fit_clearance": 1.5,
                                     "lean_deg": 15.0, "wall": 4.0}),
    "tire": dict(dimensions={"outer_diameter": 100.0, "inner_diameter": 60.0,
                             "width": 30.0}),
    "rim": dict(dimensions={"rim_diameter": 100.0, "width": 30.0,
                            "center_bore": 20.0, "spoke_count": 5.0}),
    "wheel_assembly": dict(dimensions={"outer_diameter": 100.0, "inner_diameter": 60.0,
                                       "width": 30.0, "rim_diameter": 60.0,
                                       "center_bore": 20.0, "spoke_count": 5.0}),
    "spacer": dict(dimensions={"outer_diameter": 12, "length": 20, "bore_diameter": 6.4}),
    "hex_standoff": dict(dimensions={"across_flats": 12, "length": 20, "bore_diameter": 4.5}),
    "hex_nut": dict(dimensions={"across_flats": 18, "height": 10.8, "bore_diameter": 10.1, "chamfer": 1.18}),
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
    "square_nut": dict(dimensions={"width": 19, "height": 10,
                                   "thread_major_diameter": 12, "thread_pitch": 1.75}),
    "bolt": dict(dimensions={"thread_major_diameter": 12, "thread_pitch": 1.75,
                             "length": 20, "head_across_flats": 18, "head_height": 7.5}),
    "threaded_rod": dict(dimensions={"thread_major_diameter": 12, "thread_pitch": 1.75,
                                     "length": 20}),
    "shaft_coupler": dict(dimensions={"length": 25, "outer_diameter": 20, "bore_1": 6,
                                      "bore_2": 8, "set_screw_diameter": 4,
                                      "set_screw_count": 2}),
    "timing_pulley_gt2": dict(dimensions={"teeth": 20, "belt_width": 8,
                                          "bore_diameter": 6, "flange": 1}),
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
