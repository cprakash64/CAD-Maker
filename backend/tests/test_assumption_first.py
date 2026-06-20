"""Assumption-first generation behavior.

A prompt that names a mechanical object with enough MAIN scale must GENERATE
(filling secondary dimensions with assumptions) — never block on missing
secondary dimensions, and never block a compiled model on advisory warnings.

Mirrors the requested ``tests/cad-evals/assumption-first.test.ts`` (implemented in
Python because geometry assertions run against the CadQuery worker).
"""
from __future__ import annotations

import pytest

from app.cad.plan import deterministic
from app.cad.plan.normalize import normalize_cad_plan
from app.cad.plan.planner import build_and_validate
from app.cad.plan.policy import decide_clarification

BEARING = ("Create a compact bearing block for a 20mm shaft. It should have a "
           "rectangular base 90mm long, 45mm wide, and 12mm thick.")
HINGE = ("Create a small hinge bracket with two side ears and a base. The base is "
         "70mm by 40mm by 6mm. The side ears are 30mm tall and 6mm thick, with an "
         "8mm pin hole through both ears.")
ENCLOSURE = ("Create a two-part sensor enclosure with a cylindrical sensor hole on "
             "the front face and four mounting holes on the back.")


def _build(prompt):
    plan = deterministic.plan(prompt)
    assert plan is not None, f"deterministic planner returned None for: {prompt!r}"
    decision = decide_clarification(plan, prompt)
    plan = normalize_cad_plan(plan, prompt)
    out = build_and_validate(plan)
    return plan, decision, out


def _kinds(plan):
    return [f.kind.value for f in plan.features]


# --- Test 1: bearing block --------------------------------------------------
def test_bearing_block_generates_without_clarification():
    plan, decision, out = _build(BEARING)
    assert decision.severity != "fatal", "must not ask for boss height / hole offsets"
    base = next(f for f in plan.features if f.kind.value == "plate")
    assert (base.p("width"), base.p("depth"), base.p("thickness")) == (90.0, 45.0, 12.0)
    assert "boss" in _kinds(plan)
    assert any(abs(f.p("diameter") - 20) < 0.5 for f in plan.features if f.kind.value == "hole")
    text = " ".join(plan.assumptions).lower()
    assert "boss" in text and ("inset" in text or "offset" in text)
    assert out.report.passed and out.step_bytes[:5] == b"ISO-1" and len(out.stl_bytes) > 0


# --- Test 2: hinge bracket — warnings must not block export -----------------
def test_hinge_bracket_generates_and_exports_despite_warnings():
    plan, decision, out = _build(HINGE)
    assert decision.severity != "fatal"
    assert _kinds(plan).count("rectangular_wall") == 2, "two ears"
    assert any(f.kind.value == "hole" for f in plan.features), "pin hole"
    assert plan.expected.pin_hole_count == 1, "intent: one pin hole, not 3 generic holes"
    # A compiled, exported model is shippable even if advisory checks warn.
    assert out.report.passed, "warnings must not block a compiled model"
    assert out.step_bytes[:5] == b"ISO-1" and len(out.stl_bytes) > 0


# --- Test 3: sensor enclosure — generate with defaults ----------------------
def test_sensor_enclosure_generates_with_default_dimensions():
    plan, decision, out = _build(ENCLOSURE)
    assert decision.severity != "fatal", "must not ask 7 questions"
    text = " ".join(plan.assumptions).lower()
    for token in ("enclosure", "wall", "sensor", "mounting"):
        assert token in text, f"assumptions should mention {token}"
    assert "shell" in _kinds(plan) and _kinds(plan).count("hole") >= 5
    assert out.report.passed and out.step_bytes[:5] == b"ISO-1" and len(out.stl_bytes) > 0


# --- Test 4: Drawing-to-CAD whitelist no longer blocks pipe branch ----------
def test_drawing_flanged_pipe_branch_not_unknown():
    from app.schemas.drawing_spec import (
        SUPPORTED_DRAWING_TYPES,
        DrawingInterpretationSpec,
        normalize_drawing_type,
    )

    assert "flanged_pipe_branch" in SUPPORTED_DRAWING_TYPES
    assert "pipe_tee" in SUPPORTED_DRAWING_TYPES
    # Synonyms / unknown-but-mechanical normalize instead of collapsing to None.
    assert normalize_drawing_type("tee fitting") == "pipe_tee"
    assert normalize_drawing_type("flanged pipe branch") == "flanged_pipe_branch"
    assert normalize_drawing_type("some weird pipe thing") == "generic_mechanical_part"
    assert normalize_drawing_type("a beautiful sunset") is None

    interp = DrawingInterpretationSpec(
        suggested_object_type="flanged pipe branch / tee fitting",
        overall_confidence=0.9, units="mm",
        views=[{"view_type": "front"}],
    )
    assert interp.suggested_object_type in SUPPORTED_DRAWING_TYPES
    assert interp.is_actionable(), "detected pipe branch must be actionable, not unknown"


# --- Fatal cases still ask -------------------------------------------------
def test_impossible_enclosure_is_fatal():
    plan = deterministic.plan("enclosure 30mm wide 30mm deep 20mm tall with 20mm walls")
    decision = decide_clarification(plan, "enclosure with 20mm walls")
    assert decision.severity == "fatal" and decision.questions


def test_non_mechanical_prompt_is_fatal():
    # Nothing buildable, not mechanical -> ask.
    from app.cad.plan.schema import CadPlan

    decision = decide_clarification(CadPlan(clarification_required=False), "a poem about the sea")
    assert decision.severity == "fatal"


# --- Acceptance: all six prompts generate, no fatal, STEP+STL ---------------
ACCEPTANCE = [
    BEARING, HINGE, ENCLOSURE,
    ("Create a U-shaped bracket, 80mm wide, 60mm tall, 6mm thick, with two M6 "
     "holes on the base and one 8mm pivot hole through each side wall."),
    ("Create a straight pipe spool 200mm long, 80mm pipe outer diameter, 60mm "
     "bore, with circular flanges on both ends. Each flange is 120mm OD, 12mm "
     "thick, with 8 M8 holes on a 100mm PCD."),
    ("Create a blind flange, 150mm outer diameter, 18mm thick, with 8 M10 bolt "
     "holes on a 120mm PCD and no center bore."),
]


@pytest.mark.parametrize("prompt", ACCEPTANCE)
def test_acceptance_prompts_generate_directly(prompt):
    plan, decision, out = _build(prompt)
    assert decision.severity != "fatal", f"should generate directly: {prompt[:40]}"
    assert out.report.passed, "compiled model must be exportable"
    assert out.step_bytes[:5] == b"ISO-1", "STEP must export for feature-graph models"
    assert len(out.stl_bytes) > 0, "STL must export"
    assert plan.assumptions, "inferred values should be surfaced as assumptions"
