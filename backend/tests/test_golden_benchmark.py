"""Golden dimensional benchmark — the dimension-drift safety net.

Each part in tests/data/golden_parts.json is an explicit, hand-authored CadPlan
with KNOWN dimensions. We compile it deterministically (no LLM), measure the real
BRep + mesh geometry, and assert the generated part matches the requested
dimensions within the central tolerance policy (app.cad.tolerance) and is
3D-print ready. If a future change makes the compiler drift dimensions, lose a
hole, fuse/split bodies, or break watertightness, these tests FAIL.

This is the core trust guarantee: "requested dimensions are preserved exactly
(within tolerance) and the export is printable."
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.cad.plan.dimension_report import build_dimension_report
from app.cad.plan.planner import build_and_validate
from app.cad.plan.schema import CadPlan
from app.cad.tolerance import length_tolerance, within

_DATA = Path(__file__).parent / "data" / "golden_parts.json"
PARTS = json.loads(_DATA.read_text())["parts"]


@pytest.fixture(scope="module")
def built():
    """Compile + validate + measure every golden part once (CadQuery is heavy)."""
    out = {}
    for part in PARTS:
        plan = CadPlan(**part["plan"])
        outcome = build_and_validate(plan)
        report = build_dimension_report(plan, outcome.result, outcome.stl_bytes)
        out[part["name"]] = (part["expected"], outcome, report)
    return out


@pytest.mark.parametrize("part", PARTS, ids=[p["name"] for p in PARTS])
def test_golden_part_exports(built, part):
    """The model compiles, validates (exportable), and writes non-empty STEP+STL."""
    _, outcome, _ = built[part["name"]]
    assert outcome.report.passed, outcome.report.diagnostics()
    assert len(outcome.stl_bytes) > 0
    assert outcome.step_bytes[:5] == b"ISO-1", "STEP did not export as a real B-rep"


@pytest.mark.parametrize("part", PARTS, ids=[p["name"] for p in PARTS])
def test_golden_part_dimensions_within_tolerance(built, part):
    """Measured bounding box matches the requested envelope within tolerance."""
    expected, _, report = built[part["name"]]
    measured = report["measured"]["bbox_mm"]
    for axis, want in expected["bbox_mm"].items():
        got = measured[axis]
        tol = length_tolerance(want)
        assert within(want, got, tol), (
            f"{part['name']} {axis}: requested {want}mm, measured {got}mm "
            f"(tolerance ±{tol:.3f}mm)"
        )
    # The report's own verdict must agree (guards the comparison machinery too).
    assert report["within_tolerance"] in (True, None)


@pytest.mark.parametrize("part", PARTS, ids=[p["name"] for p in PARTS])
def test_golden_part_holes_and_volume(built, part):
    """Hole counts and kernel volume don't drift."""
    expected, _, report = built[part["name"]]
    measured = report["measured"]
    if "hole_count" in expected:
        assert measured["hole_count"] == expected["hole_count"], (
            f"{part['name']}: hole count drifted"
        )
    if "through_hole_count" in expected:
        assert measured["through_hole_count"] == expected["through_hole_count"]
    if "volume_mm3" in expected:
        want = expected["volume_mm3"]
        tol = want * expected.get("volume_tol_frac", 0.02)
        assert abs(measured["volume_mm3"] - want) <= tol, (
            f"{part['name']}: volume {measured['volume_mm3']}mm³ drifted from {want}mm³"
        )


@pytest.mark.parametrize("part", PARTS, ids=[p["name"] for p in PARTS])
def test_golden_part_print_readiness(built, part):
    """Watertightness, single-body and overall printability hold."""
    expected, _, report = built[part["name"]]
    measured = report["measured"]
    pr = report["print_readiness"]
    assert measured["watertight"] == expected["watertight"], (
        f"{part['name']}: watertight={measured['watertight']}, "
        f"expected {expected['watertight']}; issues={pr['issues']}"
    )
    assert measured["components"] <= expected["max_components"]
    assert pr["printable"] == expected["printable"], pr["issues"]
    assert measured["volume_mm3"] > 0


def test_tolerance_policy_catches_drift():
    """Sanity: a deliberately wrong expectation is rejected by the tolerance check.

    Proves the benchmark actually fails on drift rather than always passing."""
    plan = CadPlan(**{
        "object_type": "mounting_plate", "name": "drift probe",
        "features": [{"id": "base", "kind": "plate",
                      "params": {"width": 100, "depth": 60, "thickness": 6}}],
        "expected": {"bbox_mm": {"x": 100, "y": 60, "z": 6}},
    })
    outcome = build_and_validate(plan)
    report = build_dimension_report(plan, outcome.result, outcome.stl_bytes)
    x = report["measured"]["bbox_mm"]["x"]
    # Real x is ~100mm; a 90mm expectation is well outside tolerance -> not within.
    assert not within(90.0, x, length_tolerance(90.0))
