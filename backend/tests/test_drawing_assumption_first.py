"""Drawing-to-CAD must be ASSUMPTION-FIRST.

A recognized mechanical drawing (e.g. flanged_pipe_branch at 0.78 confidence)
must be generatable even with open clarification questions / missing PCD /
unclear units — those become assumptions + warnings on the design, never a
"Needs clarification — can't generate safely yet" dead end. Only non-mechanical
or very-low-confidence interpretations still refuse.
"""
from __future__ import annotations

from app.schemas.drawing_spec import (
    CONFIDENCE_THRESHOLD,
    GENERATE_WITH_ASSUMPTIONS_CONFIDENCE,
    DrawingInterpretationSpec,
)

# The exact failing case from the field: detected flanged_pipe_branch, 0.78
# confidence, missing PCD + unclear units -> clarification questions present.
PIPE_BRANCH_INTERP = {
    "title": "Flanged pipe branch drawing",
    "suggested_object_type": "flanged_pipe_branch",
    "detected_object_type": "flanged pipe branch / tee",
    "overall_confidence": 0.78,
    "drawing_units_confidence": 0.4,
    "overall_dimensions": {
        "main_pipe_outer_diameter_mm": 90,
        "branch_pipe_outer_diameter_mm": 60,
        "wall_thickness_mm": 6,
    },
    "holes": [{"diameter": 12, "count": 4, "callout": "4x Ø12 per flange"}],
    "assumptions": [
        {"field": "units", "assumption": "No unit note visible; assuming mm"},
    ],
    "clarification_questions": [
        {"field": "bolt_circle_diameter", "question": "What is the flange PCD?"},
        {"field": "flange_thickness", "question": "How thick are the flanges?"},
    ],
    "missing_critical_dimensions": ["bolt_circle_diameter_mm"],
}


def test_interpret_spec_gates():
    """Mechanical + conf>=0.45 + open questions => generatable, not actionable."""
    interp = DrawingInterpretationSpec(**PIPE_BRANCH_INTERP)
    assert interp.is_mechanical()
    assert not interp.is_actionable(), "open questions keep strict actionable False"
    assert interp.generatable_with_assumptions(), \
        "a recognized mechanical drawing must be generatable with assumptions"
    # Serialized for the UI.
    dumped = interp.model_dump()
    assert dumped["actionable"] is False
    assert dumped["generate_with_assumptions_available"] is True
    assert GENERATE_WITH_ASSUMPTIONS_CONFIDENCE < CONFIDENCE_THRESHOLD


def test_low_confidence_or_non_mechanical_still_blocked():
    low = DrawingInterpretationSpec(**{**PIPE_BRANCH_INTERP, "overall_confidence": 0.2})
    assert not low.generatable_with_assumptions()

    non_mech = DrawingInterpretationSpec(
        suggested_object_type=None, detected_object_type="a cat photo",
        overall_confidence=0.9,
    )
    assert not non_mech.is_mechanical()
    assert not non_mech.generatable_with_assumptions()

    unsupported = DrawingInterpretationSpec(
        **{**PIPE_BRANCH_INTERP, "unsupported_reason": "image is a photograph, not a drawing"}
    )
    assert not unsupported.generatable_with_assumptions()


def test_confirm_generates_pipe_branch_with_assumptions(client, auth):
    """The confirm endpoint must turn the partially-specified drawing into a
    real feature-graph model with STEP + STL and visible assumptions."""
    r = client.post("/api/drawings/confirm", json=PIPE_BRANCH_INTERP,
                    headers=auth["headers"])
    assert r.status_code == 200, r.text
    d = r.json()

    assert not d["needs_clarification"]
    assert d["route"] == "cad_plan"
    assert "drawing" in (d["route_reason"] or "").lower()
    assert d["preview"] and d["preview"]["triangle_count"] > 0

    # STEP + STL export and download.
    fmts = {e["fmt"] for e in d["exports"]}
    assert {"step", "stl"} <= fmts
    for fmt in ("step", "stl"):
        resp = client.get(f"/api/designs/{d['id']}/files/{fmt}", headers=auth["headers"])
        assert resp.status_code == 200 and len(resp.content) > 0

    # Main pipe + side branch + three flanges with bolt circles.
    ids = {f["id"] for f in d["features"]}
    assert {"main_pipe", "branch_pipe",
            "top_flange", "bottom_flange", "branch_flange"} <= ids

    # Feature audit covers the tee anatomy (position-aware) and passes.
    audited = {i["feature_id"] for i in d["feature_audit"]}
    assert {"main_pipe", "branch_pipe", "main_bore", "branch_bore",
            "top_flange", "bottom_flange", "branch_flange",
            "top_bolt_pattern", "bottom_bolt_pattern",
            "branch_bolt_pattern"} <= audited
    assert d["feature_audit_passed"] is True

    # Drawing questions surfaced as assumptions + non-blocking warnings.
    text = " ".join(d["assumptions"]).lower()
    assert "pcd" in text or "bolt circle" in text or "didn't answer" in text
    assert "units" in text, "unit assumption must be visible"
    assert any("pcd" in w.lower() or "flange" in w.lower() for w in d["warnings"]), \
        "clarification questions must surface as warnings"

    # Drawing dimensions made it into the model (main pipe OD 90 -> envelope).
    bb = d["bounding_box_mm"]
    assert bb["x"] >= 90 and bb["y"] >= 90


def test_confirm_refuses_non_mechanical(client, auth):
    r = client.post(
        "/api/drawings/confirm",
        json={"suggested_object_type": None, "detected_object_type": "landscape photo",
              "overall_confidence": 0.9},
        headers=auth["headers"],
    )
    assert r.status_code == 422

    r2 = client.post(
        "/api/drawings/confirm",
        json={**PIPE_BRANCH_INTERP, "overall_confidence": 0.1},
        headers=auth["headers"],
    )
    assert r2.status_code == 422
