"""Drawing mode must produce ACCURATE CAD or refuse — never a wrong model.

Covers the goal's acceptance ladder: a planner model that fails the required-
feature audit is repaired, then rebuilt from the deterministic STRUCTURED
fallback (DrawingPipeBranchSpec — main run on Z, branch on X, three flanges,
hollow bores, repeated bolt patterns); only if that also fails does the request
return "Could not generate accurate CAD" with diagnostics. Plus crankshaft
topology validation (valid BRep, single fused body) gating the precision
template.
"""
from __future__ import annotations

import pytest

from app.cad.plan.audit import audit_plan
from app.drawing.fallback import plan_from_spec, spec_from_interpretation
from app.schemas.drawing_spec import DrawingInterpretationSpec
from tests.test_drawing_auto_generate import DRAWING_INTERP, DRAWING_PROMPT

ANATOMY_IDS = {"main_pipe", "branch_pipe", "main_bore", "branch_bore",
               "top_flange", "bottom_flange", "branch_flange",
               "top_bolt_pattern", "bottom_bolt_pattern", "branch_bolt_pattern"}


# --- structured fallback builder ----------------------------------------------
def test_structured_spec_from_drawing():
    interp = DrawingInterpretationSpec(**DRAWING_INTERP)
    spec = spec_from_interpretation(interp)
    assert spec.flange_od == 120 and spec.main_len == 148  # ×10 scale honored
    assert spec.bolt_count == 12 and spec.bolt_dia == 10
    assert spec.main_bore > 0 and spec.branch_bore > 0


def test_fallback_plan_anatomy_and_audit():
    interp = DrawingInterpretationSpec(**DRAWING_INTERP)
    plan = plan_from_spec(spec_from_interpretation(interp))
    by_id = {f.id: f for f in plan.features}
    assert set(by_id) == {"main_pipe", "branch_pipe", "top_flange",
                          "bottom_flange", "branch_flange"}
    assert by_id["main_pipe"].axis == "z", "main run must be VERTICAL"
    assert by_id["branch_pipe"].axis == "x", "branch must be perpendicular"
    assert by_id["top_flange"].at[2] > 0 > by_id["bottom_flange"].at[2]
    assert all(int(by_id[f].p("bolt_count")) == 12
               for f in ("top_flange", "bottom_flange", "branch_flange"))
    audit = audit_plan(DRAWING_PROMPT, plan)
    assert audit.passed, [i for i in audit.failures()]
    assert {i.feature_id for i in audit.items} >= ANATOMY_IDS


# --- audit hard-failures (goal tests 2–5) --------------------------------------
def _good_plan():
    interp = DrawingInterpretationSpec(**DRAWING_INTERP)
    return plan_from_spec(spec_from_interpretation(interp))


def test_audit_fails_missing_bottom_flange():
    plan = _good_plan()
    plan.features = [f for f in plan.features if f.id != "bottom_flange"]
    audit = audit_plan(DRAWING_PROMPT, plan)
    failed = {i.feature_id for i in audit.failures()}
    assert "bottom_flange" in failed and "bottom_bolt_pattern" in failed


def test_audit_fails_missing_main_bore():
    plan = _good_plan()
    next(f for f in plan.features if f.id == "main_pipe").params["id"] = 0
    # (a solid rod can't compile as `pipe`; audit checks the plan directly)
    audit = audit_plan(DRAWING_PROMPT, plan)
    assert "main_bore" in {i.feature_id for i in audit.failures()}


@pytest.mark.parametrize("per_flange", [4, 8], ids=["12_total", "24_total"])
def test_audit_fails_with_too_few_bolt_holes(per_flange):
    plan = _good_plan()
    for f in plan.features:
        if f.id.endswith("_flange"):
            f.params["bolt_count"] = per_flange
    audit = audit_plan(DRAWING_PROMPT, plan)
    failed = {i.feature_id for i in audit.failures()}
    assert {"top_bolt_pattern", "bottom_bolt_pattern", "branch_bolt_pattern"} <= failed


def test_audit_fails_horizontal_generic_tee():
    """The drawing shows a VERTICAL main run; a horizontal tee is wrong anatomy."""
    plan = _good_plan()
    main = next(f for f in plan.features if f.id == "main_pipe")
    branch = next(f for f in plan.features if f.id == "branch_pipe")
    main.axis, branch.axis = "x", "z"  # rotated into a horizontal generic tee
    audit = audit_plan(DRAWING_PROMPT, plan)
    item = next(i for i in audit.items if i.feature_id == "main_pipe")
    assert not item.satisfied and "horizontal" in item.detail


# --- drawing-mode acceptance ladder (goal test 6) ------------------------------
class _WrongPlanProvider:
    """Simulates the live LLM emitting a wrong model for the drawing: a
    horizontal generic tee with one solid pipe, no bottom flange, 4-bolt
    patterns — exactly the field failure (6/10 audit)."""

    name = "wrong-llm"

    def plan_cad(self, prompt: str, feedback: str | None = None) -> dict:
        return {
            "object_type": "pipe_tee",
            "features": [
                {"id": "main_pipe", "kind": "pipe", "axis": "x",
                 "description": "main pipe", "params": {"od": 72, "length": 148}},
                {"id": "branch_pipe", "kind": "pipe", "axis": "z",
                 "description": "branch pipe",
                 "params": {"od": 48, "id": 38, "length": 74}},
                {"id": "top_flange", "kind": "circular_flange", "axis": "x",
                 "description": "top flange",
                 "params": {"od": 120, "thickness": 10, "pcd": 95,
                            "bolt_count": 4, "bolt_diameter": 10, "bore": 62},
                 "at": [64, 0, 0]},
                {"id": "branch_flange", "kind": "circular_flange", "axis": "z",
                 "description": "branch flange",
                 "params": {"od": 96, "thickness": 10, "pcd": 71,
                            "bolt_count": 4, "bolt_diameter": 10, "bore": 38},
                 "at": [0, 0, 64]},
            ],
        }


def test_drawing_mode_rescues_wrong_llm_plan(client, auth, monkeypatch):
    """A 6/10-audit LLM model must NOT ship: the deterministic structured
    fallback rebuilds it and the final design passes the full audit."""
    import app.llm.factory as factory

    monkeypatch.setattr(factory, "get_cad_provider", lambda: _WrongPlanProvider())
    r = client.post("/api/drawings/confirm", json=DRAWING_INTERP, headers=auth["headers"])
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["feature_audit_passed"] is True, \
        f"drawing mode accepted a failing audit: {[i for i in d['feature_audit'] if not i['satisfied']]}"
    ids = {f["id"] for f in d["features"]}
    assert {"main_pipe", "branch_pipe", "top_flange", "bottom_flange",
            "branch_flange"} <= ids
    assert {e["fmt"] for e in d["exports"]} >= {"step", "stl"}
    assert d["auto_repaired"] is True, "the fallback rebuild must be visible"


def test_drawing_mode_refuses_when_fallback_unavailable(client, auth, monkeypatch):
    """Wrong plan + no fallback => 'Could not generate accurate CAD', no design."""
    import app.drawing.fallback as fb
    import app.llm.factory as factory

    monkeypatch.setattr(factory, "get_cad_provider", lambda: _WrongPlanProvider())
    monkeypatch.setattr(fb, "drawing_fallback_plan", lambda *a, **k: None)
    r = client.post("/api/drawings/confirm", json=DRAWING_INTERP, headers=auth["headers"])
    assert r.status_code == 422, r.text
    assert "Could not generate accurate CAD" in r.json()["detail"]


# --- crankshaft topology (goal test 7) ------------------------------------------
def test_crankshaft_passes_topology_or_is_rejected():
    """The precision crankshaft must export only VALID fused geometry; the
    topology gate raises (-> unsupported with diagnostics) otherwise."""
    from app.cad.base import CadGenerationError
    from app.cad.topology import validate_topology
    from app.export.exporter import generate
    from app.schemas.design_spec import DesignSpec

    try:
        result = generate(DesignSpec(object_type="inline_4_crankshaft", dimensions={}))
    except CadGenerationError as exc:
        assert "topology validation" in str(exc)
        return  # rejected with diagnostics is the acceptable alternative
    # Accepted => it must genuinely be a single valid fused body.
    from app.export.exporter import build_solid

    solid = build_solid(DesignSpec(object_type="inline_4_crankshaft", dimensions={}))
    report = validate_topology(solid, result.stl_bytes)
    assert report.ok, report.problems
    assert report.valid_brep and report.components == 1


def test_topology_catches_floating_solids():
    import cadquery as cq

    from app.cad.topology import validate_topology
    from app.export.exporter import _export_bytes

    floating = (cq.Workplane("XY").box(10, 10, 10)
                .union(cq.Workplane("XY").box(5, 5, 5).translate((50, 50, 50))))
    report = validate_topology(floating, _export_bytes(floating, ".stl"))
    assert not report.ok
    assert any("disconnected" in p for p in report.problems)
