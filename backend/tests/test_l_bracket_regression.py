"""Regression for the exact L-bracket prompt that previously produced
disconnected bodies, dimension drift (87.5mm tall), and missing holes.

It must now build ONE fused, in-tolerance solid (60×20×60), with two 6mm holes
through each face (4 total / 4 through), watertight + manifold, and NO critical
validation failures. Also asserts the severity classifier actually fails a
deliberately broken (disconnected) build — so "pass" is meaningful.
"""
from __future__ import annotations

PROMPT = (
    "Make an L bracket with 60mm legs, 5mm thickness, 20mm width, "
    "and two 6mm mounting holes on each face."
)


def test_l_bracket_builds_and_passes_validation(client, auth):
    r = client.post("/api/designs/create", json={"prompt": PROMPT}, headers=auth["headers"])
    assert r.status_code == 200, r.text
    d = r.json()

    assert d["needs_clarification"] is False
    assert {e["fmt"] for e in d["exports"]} == {"stl", "step"}
    assert all(e["size_bytes"] > 0 for e in d["exports"])

    # Overall validation severity: no critical failures.
    assert d["validation_status"] == "pass", d.get("validation_critical_failures")
    assert d["validation_critical_failures"] == []
    assert d["dimensions_within_tolerance"] is True

    measured = d["dimension_report"]["measured"]
    pr = d["print_readiness"]

    # bbox 60 x 20 x 60 within tolerance.
    assert abs(measured["bbox_mm"]["x"] - 60.0) <= 1.5
    assert abs(measured["bbox_mm"]["y"] - 20.0) <= 1.0
    assert abs(measured["bbox_mm"]["z"] - 60.0) <= 1.5

    # Two 6mm holes through each face: 4 holes, 4 through (and 4 by mesh genus).
    assert measured["hole_count"] == 4
    assert measured["through_hole_count"] == 4
    assert measured["through_holes_genus"] == 4

    # Single fused body, watertight + manifold.
    assert pr["single_body"] is True
    assert measured["components"] == 1
    assert measured["watertight"] is True
    assert measured["manifold"] is True


def test_disconnected_geometry_is_a_critical_failure():
    """A build with two non-touching solids must be classified critical_failure
    (guards that the severity system isn't trivially always-pass)."""
    from app.cad.plan.dimension_report import build_dimension_report
    from app.cad.plan.planner import build_and_validate
    from app.cad.plan.schema import CadPlan

    plan = CadPlan(**{
        "object_type": "broken", "name": "two disconnected boxes",
        "features": [
            {"id": "a", "kind": "box", "params": {"width": 10, "depth": 10, "height": 10},
             "at": [0, 0, 0]},
            {"id": "b", "kind": "box", "params": {"width": 10, "depth": 10, "height": 10},
             "at": [100, 0, 0]},
        ],
        "expected": {"bbox_mm": {"x": 110, "y": 10, "z": 10}},
    })
    out = build_and_validate(plan)
    rep = build_dimension_report(plan, out.result, out.stl_bytes)

    assert rep["measured"]["components"] == 2
    val = rep["validation"]
    assert val["status"] == "critical_failure"
    assert any("Disconnected" in c for c in val["critical_failures"])
