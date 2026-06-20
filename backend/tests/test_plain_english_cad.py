"""Plain-English → CAD eval suite (the 10 task prompts).

Runs each prompt through the OFFLINE deterministic planner → compiler → validator
(so the suite needs no API key) and asserts the acceptance criteria: the right
PRIMITIVE composition is produced, geometry validates, STEP+STL are exported, and
— critically — NO legacy whole-part template name is chosen (the mis-routing bug).

The same prompts are runnable against the live OpenAI planner via
``python -m scripts.run_cad_evals --provider openai``.
"""
from __future__ import annotations

import pytest

from app.cad.plan import deterministic
from app.cad.plan.planner import build_and_validate
from app.cad.plan.schema import Feature

# Whole-part templates that must NEVER be selected for these mechanical prompts.
LEGACY_TEMPLATES = {
    "rectangular_bracket", "l_bracket", "enclosure", "spacer", "pipe_clamp",
    "drill_jig", "handle", "adapter_plate", "inline_4_crankshaft",
    "flanged_pipe_branch", "simple_gear_or_pulley",
}

PROMPTS = {
    "plate": "Create a rectangular mounting plate 120mm long, 80mm wide, 8mm thick, with four M6 clearance holes 10mm from each corner and 3mm rounded edges.",
    "nema": "Create a motor mounting plate for a NEMA 17 stepper motor, 70mm square, 5mm thick, with a 22mm center bore and four M3 clearance holes on a 31mm square bolt pattern.",
    "flange": "Create a blind flange, 150mm outer diameter, 18mm thick, with 8 M10 clearance holes on a 120mm PCD and no center bore.",
    "spool": "Create a straight pipe spool 200mm long, 80mm pipe outer diameter, 60mm bore, with circular flanges on both ends. Each flange is 120mm OD, 12mm thick, with 8 M8 holes on a 100mm PCD.",
    "tee": "Create a T pipe fitting with a 75mm main pipe, 50mm branch pipe, 5mm wall thickness, and circular flanges on all three ends.",
    "vise": "Create a small vise jaw 100mm long, 30mm tall, 20mm thick, with two M6 mounting holes and a 90 degree V groove along the top.",
    "ubracket": "Create a U-shaped bracket, 80mm wide, 60mm tall, 6mm thick, with two M6 holes on the base and one 8mm pivot hole through each side wall.",
    "bearing": "Create a bearing block for a 20mm shaft with a 90mm by 45mm by 12mm base, a raised cylindrical boss 45mm OD and 30mm tall, a 20mm through bore, and four M6 mounting holes.",
    "lbracket": "Create an L bracket with a 100mm by 60mm base plate, 8mm thick, and a vertical support wall centered on the base, 60mm tall and 8mm thick. Add four M6 holes in the base.",
    "hinge": "Create a hinge bracket with a 70mm by 40mm by 6mm base and two side ears, each 30mm tall and 6mm thick, with an 8mm pin hole through both ears.",
}


def _kinds(plan) -> list[str]:
    return [f.kind.value for f in plan.features]


def _of_kind(plan, kind: str) -> list[Feature]:
    return [f for f in plan.features if f.kind.value == kind]


@pytest.fixture(scope="module")
def built():
    """Compile + validate every prompt once (CadQuery is heavy)."""
    out = {}
    for key, prompt in PROMPTS.items():
        plan = deterministic.plan(prompt)
        out[key] = (plan, build_and_validate(plan))
    return out


@pytest.mark.parametrize("key", list(PROMPTS))
def test_builds_validates_and_exports(built, key):
    plan, outcome = built[key]
    assert plan.object_type not in LEGACY_TEMPLATES, f"chose legacy template {plan.object_type}"
    assert outcome.report.passed, f"{key} validation failed: {outcome.report.diagnostics()}"
    assert len(outcome.stl_bytes) > 0 and outcome.step_bytes[:5] == b"ISO-1", "missing STEP/STL"


def test_plate(built):
    plan, out = built["plate"]
    assert _of_kind(plan, "plate"), "no plate feature"
    assert _of_kind(plan, "fillet"), "no rounded edges"
    holes = _of_kind(plan, "hole")
    assert len(holes) == 4
    assert all(abs(h.p("diameter") - 6.6) < 0.01 for h in holes), "M6 clearance != 6.6mm"
    assert out.result.bbox_mm == {"x": 120.0, "y": 80.0, "z": 8.0}


def test_nema(built):
    plan, out = built["nema"]
    assert out.result.bbox_mm == {"x": 70.0, "y": 70.0, "z": 5.0}
    bore = [h for h in _of_kind(plan, "hole") if abs(h.p("diameter") - 22) < 0.5]
    assert bore, "no 22mm center bore"
    pat = _of_kind(plan, "hole_pattern_rect")
    assert pat and abs(pat[0].p("pattern") - 31) < 0.01, "no 31mm square pattern"
    assert abs(pat[0].p("diameter") - 3.4) < 0.01, "M3 clearance != 3.4mm"


def test_blind_flange_is_circular_not_plate(built):
    plan, out = built["flange"]
    assert "flange" in plan.object_type and plan.object_type != "adapter_plate"
    fl = _of_kind(plan, "circular_flange")
    assert fl, "blind flange is not a circular_flange"
    f = fl[0]
    assert abs(f.p("od") - 150) < 0.01 and abs(f.p("thickness") - 18) < 0.01
    assert int(f.p("bolt_count")) == 8
    assert f.p("bore") == 0, "blind flange must have NO center bore"
    assert out.result.bbox_mm["x"] == out.result.bbox_mm["y"] == 150.0, "not circular"


def test_pipe_spool_is_straight_not_tee(built):
    plan, out = built["spool"]
    assert plan.object_type == "pipe_spool"
    assert "tee" not in plan.object_type and plan.object_type != "flanged_pipe_branch"
    sp = _of_kind(plan, "pipe_spool")[0]
    assert abs(sp.p("od") - 80) < 0.01 and abs(sp.p("id", 0, "bore") - 60) < 0.01
    assert out.result.hole_count == 16, "expected 16 total flange holes (8 per flange)"


def test_tee_has_main_branch_and_three_flanges(built):
    plan, _ = built["tee"]
    assert plan.object_type == "pipe_tee" and plan.object_type != "flanged_pipe_branch"
    assert len(_of_kind(plan, "pipe")) >= 2, "tee needs a main run and a branch"
    assert len(_of_kind(plan, "circular_flange")) == 3, "three flanged ends"


def test_vise_jaw_has_v_groove(built):
    plan, _ = built["vise"]
    assert plan.object_type == "vise_jaw"
    assert _of_kind(plan, "box"), "jaw body"
    assert len(_of_kind(plan, "hole")) == 2
    assert _of_kind(plan, "v_groove"), "no V groove"


def test_u_bracket_is_base_plus_walls_not_enclosure(built):
    plan, out = built["ubracket"]
    assert plan.object_type == "u_bracket" and plan.object_type != "enclosure"
    assert len(_of_kind(plan, "plate")) == 1, "needs a base plate"
    assert len(_of_kind(plan, "rectangular_wall")) == 2, "needs two side walls"
    assert len(_of_kind(plan, "hole")) == 4, "2 base holes + 2 pivot holes"


def test_bearing_block_builds_without_string_crash(built):
    plan, out = built["bearing"]
    assert plan.object_type == "bearing_block"
    base = _of_kind(plan, "plate")[0]
    assert (base.p("width"), base.p("depth"), base.p("thickness")) == (90.0, 45.0, 12.0)
    assert _of_kind(plan, "boss"), "no raised boss"
    bore = [h for h in _of_kind(plan, "hole") if abs(h.p("diameter") - 20) < 0.5]
    assert bore, "no 20mm through bore"
    mounts = [h for h in _of_kind(plan, "hole") if abs(h.p("diameter") - 6.6) < 0.5]
    assert len(mounts) == 4, "four M6 mounting holes"


def test_l_bracket(built):
    plan, _ = built["lbracket"]
    assert plan.object_type not in LEGACY_TEMPLATES
    assert _of_kind(plan, "plate") and _of_kind(plan, "rectangular_wall")
    assert len(_of_kind(plan, "hole")) == 4


def test_hinge_bracket_has_ears_and_coaxial_pin(built):
    plan, out = built["hinge"]
    assert plan.object_type == "hinge_bracket"
    assert len(_of_kind(plan, "rectangular_wall")) == 2, "two ears"
    pin = _of_kind(plan, "hole")
    assert pin and abs(pin[0].p("diameter") - 8) < 0.5, "no 8mm pin hole"
    assert out.result.bbox_mm["z"] > 6, "ears should rise above the base (not a flat plate)"


# --- API-level acceptance: the production primary route (CAD_ENGINE=feature_graph)
# turns these prompts into CadPlan-built designs with STEP+STL, not legacy templates.
ACCEPTANCE = {
    "flange": ("blind_flange", PROMPTS["flange"]),
    "spool": ("pipe_spool", PROMPTS["spool"]),
    "ubracket": ("u_bracket", PROMPTS["ubracket"]),
    "bearing": ("bearing_block", PROMPTS["bearing"]),
}


@pytest.mark.parametrize("key", list(ACCEPTANCE))
def test_create_design_uses_cad_plan_route(client, auth, key):
    object_type, prompt = ACCEPTANCE[key]
    r = client.post("/api/designs/create", json={"prompt": prompt}, headers=auth["headers"])
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["needs_clarification"] is False
    assert d["route"] == "cad_plan", f"{key} did not use the feature-graph route"
    assert d["object_type"] == object_type and object_type not in LEGACY_TEMPLATES
    assert {e["fmt"] for e in d["exports"]} == {"stl", "step"}
    assert all(e["size_bytes"] > 0 for e in d["exports"])
    assert d["semantic_passed"] is True


def test_create_design_clarifies_when_core_dim_missing(client, auth):
    r = client.post("/api/designs/create", json={"prompt": "make me a blind flange"},
                    headers=auth["headers"])
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["needs_clarification"] is True
    assert d["clarification_questions"], "should ask for the missing flange dimensions"
    assert d["preview"] is None
