"""The flanged-pipe-branch field-failure class: decimal-loss inflation,
LLM-free deterministic building, continuous internal bore, and PASS denied for
watertight-but-unfaithful geometry.

The fixture mirrors the uploaded drawing: a vertical flanged branch fitting
dimensioned 14.8 / 15 / Ø12 / 0.5 with a "12xØ1" bolt callout (drawing-scale
values), plus the OCR-inflated variant (148 / 150 / Ø120 / 5 / 12xØ10 read with
lost decimal points).
"""
from __future__ import annotations

import pytest

from app.cad.pipe_branch import PipeBranchSpec, build_plan, validate_pipe_branch
from app.cad.plan.compiler import compile_cad_plan
from app.cad.plan.planner import build_and_validate
from app.cad.plan.schema import CadPlan, Feature
from app.drawing.fallback import spec_from_interpretation
from app.drawing.normalize import normalize_dimensions
from app.drawing.scale import infer_scale
from app.schemas.drawing_spec import DrawingInterpretationSpec

# The uploaded drawing, as the vision pass SHOULD read it (decimals intact).
DRAWING_INTERP = {
    "title": "Flanged pipe branch drawing",
    "suggested_object_type": "flanged_pipe_branch",
    "detected_object_type": "flanged pipe branch / tee",
    "overall_confidence": 0.8,
    "drawing_units_confidence": 0.4,
    "overall_dimensions": {
        "flange_outer_diameter_mm": 12,
        "main_pipe_length_mm": 14.8,
        "branch_pipe_outer_diameter_mm": 4.8,
        "main_pipe_outer_diameter_mm": 7.2,
        "wall_thickness_mm": 0.5,
        "flange_thickness_mm": 1.0,
    },
    "holes": [{"diameter": 1, "count": 12, "callout": "12xØ1"}],
}
# The same drawing with decimal points LOST by OCR (the observed field bug).
INFLATED_INTERP = {
    **DRAWING_INTERP,
    "overall_dimensions": {
        "flange_outer_diameter_mm": 1200,   # Ø120 misread ×10
        "main_pipe_length_mm": 1480,        # 14.8 → 148 → misread again
        "branch_pipe_outer_diameter_mm": 480,
        "main_pipe_outer_diameter_mm": 720,
        "wall_thickness_mm": 50,
        "flange_thickness_mm": 100,
    },
    "holes": [{"diameter": 100, "count": 12}],
}


# ---------------------------------------------------------- analysis + scaling

def test_drawing_analysis_produces_faithful_spec():
    interp = DrawingInterpretationSpec(**DRAWING_INTERP)
    spec = spec_from_interpretation(interp)
    assert spec.flange_od == pytest.approx(120)
    assert spec.main_len == pytest.approx(148)
    assert spec.main_od == pytest.approx(72)
    assert spec.wall == pytest.approx(5)
    assert spec.bolt_count == 12 and spec.bolt_dia == pytest.approx(10)


def test_decimal_preservation_no_false_inflation():
    """Values that carry decimals are trusted as written (×1)."""
    n = normalize_dimensions(
        {"flange_outer_diameter_mm": 120.0, "main_pipe_length_mm": 148.2,
         "wall_thickness_mm": 5.5}, [10.0])
    assert n.factor == 1.0
    assert n.dimensions["main_pipe_length_mm"] == pytest.approx(148.2)


def test_anti_inflation_corrects_lost_decimals():
    """An all-×10 OCR-inflated set is corrected ÷10 with a visible assumption."""
    scaled = infer_scale(DrawingInterpretationSpec(**INFLATED_INTERP))
    assert scaled.scale == pytest.approx(0.1)
    assert scaled.dimensions["flange_outer_diameter_mm"] == pytest.approx(120)
    assert scaled.dimensions["main_pipe_length_mm"] == pytest.approx(148)
    assert scaled.holes[0].diameter == pytest.approx(10)
    assert any("decimal" in a for a in scaled.assumptions)


def test_mixed_single_key_decimal_loss_repaired():
    n = normalize_dimensions(
        {"flange_outer_diameter_mm": 120, "main_pipe_length_mm": 148,
         "main_pipe_outer_diameter_mm": 72, "wall_thickness_mm": 50}, [10])
    assert n.factor == 1.0
    assert n.dimensions["wall_thickness_mm"] == pytest.approx(5)
    assert any("decimal" in a for a in n.assumptions)


# ------------------------------------------------- deterministic direct route

def test_confirm_builds_without_llm_cad_plan(client, auth, monkeypatch):
    """Recognized family → the LLM cad_plan step is never invoked."""
    import app.llm.factory as factory

    class _Exploding:
        name = "must-not-be-called"

        def plan_cad(self, *a, **k):
            raise AssertionError("cad_plan LLM must not be consulted")

        def __getattr__(self, item):  # any other provider use is also a failure
            raise AssertionError(f"LLM provider used unexpectedly: {item}")

    monkeypatch.setattr(factory, "get_cad_provider", lambda: _Exploding())
    r = client.post("/api/drawings/confirm", json=DRAWING_INTERP,
                    headers=auth["headers"])
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["feature_audit_passed"] is True
    assert d["validation_status"] == "pass"
    assert {e["fmt"] for e in d["exports"]} >= {"step", "stl"}
    bb = d["bounding_box_mm"]
    assert bb["z"] == pytest.approx(148, abs=1)
    assert bb["y"] == pytest.approx(120, abs=1)


# ------------------------------------------------------ geometry faithfulness

def _drawing_spec() -> PipeBranchSpec:
    return PipeBranchSpec(
        main_od=72, main_id=62, main_len=148, branch_od=48, branch_id=38,
        branch_len=74, flange_od=120, flange_thk=10,
        bolt_count=12, bolt_dia=10, pcd=95)


def test_internal_passage_continuous():
    """Union-then-bore: the branch bore opens into the main bore (the field bug
    was the main pipe's wall sealing the branch mouth)."""
    out = build_and_validate(build_plan(_drawing_spec()))
    by_name = {c.name: c for c in out.report.checks}
    assert by_name["internal_passage_main"].passed, by_name["internal_passage_main"].actual
    assert by_name["internal_passage_branch"].passed, by_name["internal_passage_branch"].actual
    assert out.report.dimension_report["validation"]["status"] == "pass"


def test_bolt_holes_open_even_when_grazing_the_pipe_wall():
    """Bolt holes whose edge grazes the main pipe OD must still be fully open
    (deferred flange cuts)."""
    spec = PipeBranchSpec(main_od=90, main_id=78, main_len=162.5, branch_od=60,
                          branch_id=48, branch_len=81.2, flange_od=130,
                          flange_thk=13, branch_flange_od=100,
                          bolt_count=4, bolt_dia=12, pcd=100, branch_pcd=70)
    out = build_and_validate(build_plan(spec))
    fails = [c.name for c in out.report.checks if not c.passed]
    assert fails == [], fails


def test_blocked_bore_fails_validation():
    """A model whose branch mouth is sealed (pre-hollowed tubes unioned) must
    FAIL the continuity probe — this is exactly the old failure mode."""
    import cadquery as cq

    s = _drawing_spec()
    plan = build_plan(s)
    # Rebuild the OLD wrong geometry by hand: hollow tubes unioned directly.
    main = (cq.Workplane("XY").circle(s.main_od / 2).circle(s.main_id / 2)
            .extrude(s.main_len).translate((0, 0, -s.main_len / 2)))
    branch = (cq.Workplane("XY").circle(s.branch_od / 2).circle(s.branch_id / 2)
              .extrude(s.branch_len).rotate((0, 0, 0), (0, 1, 0), 90))
    wrong = main.union(branch)
    good = compile_cad_plan(plan)
    from app.cad.plan.compiler import CadPlanResult

    fake = CadPlanResult(solid=wrong, bbox_mm=good.bbox_mm,
                         hole_count=good.hole_count,
                         through_hole_count=good.through_hole_count,
                         feature_count=good.feature_count)
    checks = validate_pipe_branch(plan, fake)
    by_name = {c.name: c for c in checks}
    assert not by_name["internal_passage_branch"].passed, \
        "a sealed branch mouth must fail the continuity probe"


def test_unfaithful_generic_model_cannot_pass():
    """Watertight-but-generic geometry (no flanges, wrong sizes) is
    critical_failure, never PASS — the misleading-PASS field bug."""
    bad = CadPlan(object_type="flanged_pipe_branch", name="generic tee", features=[
        Feature(id="main_pipe", kind="pipe", axis="z", description="main pipe",
                params={"od": 40, "id": 30, "length": 80}, at=[0, 0, -40]),
        Feature(id="branch_pipe", kind="pipe", axis="x", description="branch pipe",
                params={"od": 30, "id": 22, "length": 40}, at=[0, 0, 0]),
    ])
    out = build_and_validate(bad)
    v = out.report.dimension_report["validation"]
    assert v["status"] == "critical_failure"
    assert any("drawing-faithful" in c for c in v["critical_failures"])
    # And the export gate honors it end-to-end via reconciled status.


def test_min_wall_thickness_enforced():
    spec = PipeBranchSpec(main_od=72, main_id=71, main_len=148, branch_od=48,
                          branch_id=38, branch_len=74, flange_od=120,
                          flange_thk=10, bolt_count=12, bolt_dia=10, pcd=95)
    out = build_and_validate(build_plan(spec))
    by_name = {c.name: c for c in out.report.checks}
    assert not by_name["min_wall_thickness"].passed
    assert out.report.dimension_report["validation"]["status"] == "critical_failure"
