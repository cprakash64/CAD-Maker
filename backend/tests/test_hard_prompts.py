"""Hard-prompt robustness tests.

The brief's hard prompts (machine frame, engine test stand, drone frame,
motorcycle subframe, electric-skateboard mount) and dedicated medium parts (U
bracket, hinge bracket, clamp block, robotic arm base bracket) must generate
validated, exportable CAD deterministically — never time out or fall back to
generic decomposition. These tests build everything offline (no LLM).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.cad.classification import classify_prompt

_CASES = json.loads(
    (Path(__file__).parent / "data" / "hard_prompts.json").read_text()
)["cases"]
_BY_NAME = {c["name"]: c for c in _CASES}

_FRAME_CASES = [c for c in _CASES if c["expected_strategy"] == "assembly_generator"]
_PART_CASES = [c for c in _CASES if c["expected_strategy"] == "cadplan"]


# --- classification (offline, fast) ---------------------------------------
@pytest.mark.parametrize("case", _CASES, ids=[c["name"] for c in _CASES])
def test_hard_prompt_classification(case):
    c = classify_prompt(case["prompt"])
    assert c.family_id == case["expected_family_id"], case["name"]
    assert c.design_mode == case["expected_design_mode"], case["name"]
    assert c.generation_strategy == case["expected_strategy"], case["name"]
    # The whole point: these are buildable, never decomposition.
    assert c.can_generate_now is True, case["name"]
    assert c.generation_strategy != "needs_decomposition", case["name"]


# --- frame / concept assemblies build, validate & export ------------------
@pytest.fixture(scope="module")
def frame_builds():
    from app.cad.assembly.frame_report import build_frame_report
    from app.cad.assembly.frames import build_frame_family, detect_frame_family
    from app.cad.plan.compiler import export_solid

    out = {}
    for case in _FRAME_CASES:
        fid = detect_frame_family(case["prompt"])
        assert fid == case["expected_family_id"], case["name"]
        build = build_frame_family(case["prompt"], fid)
        stl, step, _ = export_solid(build.solid)
        report = build_frame_report(build, stl, step)
        out[case["name"]] = (build, report, stl, step)
    return out


@pytest.mark.parametrize("case", _FRAME_CASES, ids=[c["name"] for c in _FRAME_CASES])
def test_frame_builds_validate_and_export(frame_builds, case):
    build, report, stl, step = frame_builds[case["name"]]
    status = report["validation"]["status"]
    assert status in case["allowed_validation_status"], (case["name"], status,
                                                         report["validation"])
    assert status != "critical_failure", report["validation"]["critical_failures"]
    assert len(stl) > 0, "empty STL"
    assert step[:5] == b"ISO-1", "STEP did not export as a real B-rep"
    assert report["measured"]["volume_mm3"] > 0


@pytest.mark.parametrize("case", _FRAME_CASES, ids=[c["name"] for c in _FRAME_CASES])
def test_frame_has_required_components(frame_builds, case):
    build, report, _, _ = frame_builds[case["name"]]
    roles = build.roles_present()
    for role in case.get("required_roles", []):
        assert role in roles, f"{case['name']} missing required component '{role}'"
    if "min_components" in case:
        assert build.member_count >= case["min_components"], case["name"]
    if "min_holes" in case:
        assert build.total_holes() >= case["min_holes"], case["name"]


@pytest.mark.parametrize("case", _FRAME_CASES, ids=[c["name"] for c in _FRAME_CASES])
def test_frame_envelope_and_concept_notes(frame_builds, case):
    build, report, _, _ = frame_builds[case["name"]]
    # Approx envelope (where the prompt fixes L/W/H).
    want = case.get("approx_envelope_mm")
    if want:
        got = report["measured"]["bbox_mm"]
        for axis, target in want.items():
            assert abs(got[axis] - target) <= target * 0.25, (
                f"{case['name']} envelope {axis}: {got[axis]} vs ~{target}")
    # No fake claims: concept caveat present.
    joined = " ".join(report["notes"]).lower()
    assert "concept" in joined and "not" in joined, case["name"]
    assert any("certif" in n.lower() or "fea" in n.lower() for n in report["notes"])


def test_drone_motor_diagonal_is_approximately_correct(frame_builds):
    _, report, _, _ = frame_builds["drone_frame"]
    diag = next(c for c in report["comparisons"]
               if c["name"] == "motor_to_motor_diagonal")
    assert diag["within"], diag


def test_skateboard_returns_primary_component_with_decomposition_note(frame_builds):
    build, report, _, _ = frame_builds["skateboard_motor_mount_fallback"]
    assert build.design_mode == "single_part"
    assert build.decomposition_note, "skateboard must explain the decomposition"
    joined = " ".join(report["notes"]).lower()
    assert "motor mount" in joined and "assembly" in joined


# --- dedicated medium single parts (CadPlan feature graph) ----------------
@pytest.fixture(scope="module")
def part_builds():
    from app.cad.plan import deterministic
    from app.cad.plan.planner import build_and_validate

    out = {}
    for case in _PART_CASES:
        plan = deterministic.plan(case["prompt"])
        assert plan is not None, case["name"]
        out[case["name"]] = (plan, build_and_validate(plan))
    return out


@pytest.mark.parametrize("case", _PART_CASES, ids=[c["name"] for c in _PART_CASES])
def test_medium_part_object_type_and_export(part_builds, case):
    plan, outcome = part_builds[case["name"]]
    assert plan.object_type == case["expected_object_type"], case["name"]
    assert outcome.report.passed, outcome.report.diagnostics()
    assert len(outcome.stl_bytes) > 0
    assert outcome.step_bytes[:5] == b"ISO-1"


def test_u_bracket_has_side_walls_not_flat_plate(part_builds):
    plan, _ = part_builds["u_bracket"]
    kinds = [f.kind.value for f in plan.features]
    assert kinds.count("rectangular_wall") >= 2, "U bracket needs two side walls"


def test_hinge_bracket_has_ears_and_pin_hole(part_builds):
    plan, _ = part_builds["hinge_bracket"]
    kinds = [f.kind.value for f in plan.features]
    assert kinds.count("rectangular_wall") >= 2, "hinge needs two ears"
    assert any(f.kind.value == "hole" for f in plan.features), "hinge needs a pin hole"


def test_clamp_block_has_bore_and_bolt_holes(part_builds):
    plan, _ = part_builds["clamp_block"]
    holes = [f for f in plan.features if f.kind.value == "hole"]
    assert len(holes) >= 3, "clamp block needs a tube bore + bolt holes"


def test_robotic_arm_base_has_tower_and_gussets(part_builds):
    plan, _ = part_builds["robotic_arm_base_bracket"]
    kinds = [f.kind.value for f in plan.features]
    assert "rectangular_wall" in kinds, "robotic arm base needs a vertical tower"
    assert kinds.count("gusset") >= 2, "robotic arm base needs side gussets"
    assert plan.object_type == "robotic_arm_base_bracket"


# --- end-to-end API (full pipeline: build, store, export, classify) -------
def _create(client, auth, prompt):
    return client.post("/api/designs/create", json={"prompt": prompt},
                       headers=auth["headers"])


def test_machine_frame_end_to_end(client, auth):
    case = _BY_NAME["machine_frame"]
    r = _create(client, auth, case["prompt"])
    assert r.status_code == 200, r.text  # never a 503 "took too long"
    d = r.json()
    assert d["needs_decomposition"] is False
    assert d["design_mode"] == "assembly"
    assert d["route"] == "assembly"
    assert {e["fmt"] for e in d["exports"]} == {"stl", "step"}
    assert d["validation_status"] != "critical_failure"
    assert d["download_blocked_reason"] is None
    assert d["classification"]["family_id"] == "machine_frame"


def test_skateboard_end_to_end_generates_primary_component(client, auth):
    case = _BY_NAME["skateboard_motor_mount_fallback"]
    r = _create(client, auth, case["prompt"])
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["needs_decomposition"] is False
    assert {e["fmt"] for e in d["exports"]} == {"stl", "step"}
    assert d["validation_status"] != "critical_failure"
    # The assumptions explain that only the primary component was generated.
    assert any("motor mount" in a.lower() for a in d["assumptions"])


def test_robotic_arm_not_a_flat_mounting_plate(client, auth):
    case = _BY_NAME["robotic_arm_base_bracket"]
    r = _create(client, auth, case["prompt"])
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["object_type"] == "robotic_arm_base_bracket"
    assert d["needs_decomposition"] is False
    assert {e["fmt"] for e in d["exports"]} == {"stl", "step"}


def test_cnc_router_separates_base_bed_gantry(client, auth):
    case = _BY_NAME["cnc_router_frame"]
    r = _create(client, auth, case["prompt"])
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["needs_decomposition"] is False
    assert d["design_mode"] == "assembly"
    assert {e["fmt"] for e in d["exports"]} == {"stl", "step"}
    assert d["validation_status"] != "critical_failure"
    groups = (d["dimension_report"] or {}).get("measured", {}).get("groups", {})
    for g in ("base", "bed", "gantry"):
        assert groups.get(g, 0) > 0, f"CNC router missing '{g}' group: {groups}"


# --- deterministic-first: supported hard families never call OpenAI --------
_DETERMINISTIC_PROMPTS = [
    "cnc_router_frame", "machine_frame", "engine_test_stand", "drone_frame",
    "motorcycle_subframe", "skateboard_motor_mount_fallback",
    "robotic_arm_base_bracket",
]


def test_no_openai_call_for_deterministic_hard_families(client, auth, monkeypatch):
    """Supported hard families must build deterministically — the LLM/CAD provider
    factory must never be invoked for them."""
    from app.llm import factory

    calls = {"n": 0}

    def _boom(*a, **k):
        calls["n"] += 1
        raise AssertionError("provider should not be called for a deterministic family")

    monkeypatch.setattr(factory, "get_cad_provider", _boom)
    monkeypatch.setattr(factory, "get_provider", _boom)

    for name in _DETERMINISTIC_PROMPTS:
        r = _create(client, auth, _BY_NAME[name]["prompt"])
        assert r.status_code == 200, (name, r.text)
        d = r.json()
        assert d["needs_decomposition"] is False, name
        assert {e["fmt"] for e in d["exports"]} == {"stl", "step"}, name
        assert d["validation_status"] != "critical_failure", name
    assert calls["n"] == 0


def test_deterministic_families_survive_unavailable_openai(client, auth, monkeypatch):
    """Even if the LLM provider is broken/slow, deterministic hard families still
    generate (no 503 'took too long')."""
    from app.llm import factory
    from app.llm.base import LLMUnavailableError

    def _unavailable(*a, **k):
        raise LLMUnavailableError(
            "Generation took too long and was stopped. Please try a simpler part.")

    monkeypatch.setattr(factory, "get_cad_provider", _unavailable)
    monkeypatch.setattr(factory, "get_provider", _unavailable)

    for name in ("machine_frame", "cnc_router_frame", "robotic_arm_base_bracket"):
        r = _create(client, auth, _BY_NAME[name]["prompt"])
        assert r.status_code == 200, (name, r.text)
        assert {e["fmt"] for e in r.json()["exports"]} == {"stl", "step"}, name


def test_unsupported_huge_decomposes_with_specific_systems(client, auth):
    """Unsupported huge prompts decompose cleanly with specific (non-empty)
    suggested components, no 500/timeout."""
    r = _create(client, auth, (
        "Design a complete humanoid robot with two arms, two legs, a torso, head, "
        "actuators, sensors, battery pack, power distribution and a control computer, "
        "with engine mount, suspension, drivetrain and cooling system subsystems."
    ))
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["needs_decomposition"] is True
    decomp = d["decomposition"]
    assert decomp and decomp["components"], "decomposition must list components"
    # Specific, not generic: more than the single generic fallback entry.
    assert len(decomp["components"]) >= 3
    assert "0 subsystems" not in (decomp.get("reason") or "")


def test_fallback_directive_builds_base_frame_when_unsupported(client, auth):
    """An unsupported huge prompt that says 'if too complex, generate the base
    frame first' builds a base frame instead of generic decomposition."""
    r = _create(client, auth, (
        "Design a complete off-road utility vehicle platform with suspension, "
        "drivetrain, transmission, engine mount, radiator and steering column. "
        "If this is too complex, generate the base frame first."
    ))
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["needs_decomposition"] is False
    assert {e["fmt"] for e in d["exports"]} == {"stl", "step"}
    assert d["validation_status"] != "critical_failure"
