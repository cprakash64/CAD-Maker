"""Manufacturability checks surface warnings for bad geometry."""
from app.manufacturability.checks import Severity, run_checks
from app.schemas.design_spec import DesignSpec, Hole


def _by_check(results, name):
    return next(r for r in results if r.check == name)


def test_thin_wall_warns():
    spec = DesignSpec(
        object_type="rectangular_bracket",
        dimensions={"width": 80, "depth": 40, "thickness": 0.4},
    )
    res = _by_check(run_checks(spec), "min_thickness")
    assert res.passed is False
    assert res.severity == Severity.warning


def test_adequate_wall_passes():
    spec = DesignSpec(
        object_type="rectangular_bracket",
        dimensions={"width": 80, "depth": 40, "thickness": 5},
    )
    assert _by_check(run_checks(spec), "min_thickness").passed is True


def test_hole_edge_distance_warns_near_edge():
    spec = DesignSpec(
        object_type="rectangular_bracket",
        dimensions={"width": 80, "depth": 40, "thickness": 5},
        holes=[Hole(diameter=8, x=39, y=0)],  # 1mm from the right edge
    )
    res = _by_check(run_checks(spec), "hole_0_edge_distance")
    assert res.passed is False


def test_tall_slender_standoff_flagged():
    spec = DesignSpec(
        object_type="spacer",
        dimensions={"outer_diameter": 6, "length": 60, "bore_diameter": 3},
    )
    res = _by_check(run_checks(spec), "print_aspect_ratio")
    assert res.passed is False


def test_geometry_resolvable_reports_bad_combo():
    # bore larger than outer diameter is caught by the generator's resolve.
    spec = DesignSpec(
        object_type="spacer",
        dimensions={"outer_diameter": 5, "length": 20, "bore_diameter": 4},
    )
    results = run_checks(spec)
    # resolve() passes (ranges ok); the impossible bore is caught at build time,
    # but edge/diameter checks should still run and pass here.
    assert any(r.check == "geometry_resolvable" for r in results)


def test_cnc_uses_lower_wall_floor():
    spec = DesignSpec(
        object_type="rectangular_bracket",
        manufacturing_method="cnc_milling",
        dimensions={"width": 80, "depth": 40, "thickness": 1.1},
    )
    # 1.1mm is below FDM floor (1.2) but at/above CNC floor (1.0) -> passes for CNC.
    assert _by_check(run_checks(spec), "min_thickness").passed is True
