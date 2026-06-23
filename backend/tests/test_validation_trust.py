"""Trust / reliability regressions from manual-testing screenshots.

Each test pins one of the production trust issues: a simple part must not time
out, a vague prompt must clarify (never emit a failed model), validation must
never PASS when dimensions are materially wrong, unsupported huge assemblies must
decompose cleanly (not misroute / SCAD / 500), and the robotic-arm audit must
not contradict a PASS. Everything runs offline (mock provider / deterministic).
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.services.design_service import reconciled_validation_status


def _create(client, auth, prompt):
    return client.post("/api/designs/create", json={"prompt": prompt},
                       headers=auth["headers"])


# --- 1) L-bracket: deterministic, no OpenAI, fast, pass, export ------------
L_BRACKET = ("Make an L bracket with 60mm legs, 5mm thickness, 20mm width, and "
             "two 6mm mounting holes on each face.")


def test_l_bracket_builds_deterministically_without_openai(client, auth, monkeypatch):
    from app.llm import factory

    def _boom(*a, **k):
        raise AssertionError("L bracket must not call the LLM provider")

    monkeypatch.setattr(factory, "get_cad_provider", _boom)
    monkeypatch.setattr(factory, "get_provider", _boom)

    r = _create(client, auth, L_BRACKET)
    assert r.status_code == 200, r.text          # never a 503 "took too long"
    d = r.json()
    assert d["needs_decomposition"] is False
    assert d["needs_clarification"] is False
    assert d["validation_status"] == "pass", d["validation_status"]
    assert {e["fmt"] for e in d["exports"]} == {"stl", "step"}
    bbox = d["bounding_box_mm"]
    for axis, want in (("x", 60), ("y", 20), ("z", 60)):
        assert abs(bbox[axis] - want) <= 2.0, (axis, bbox[axis])
    measured = (d["dimension_report"] or {}).get("measured", {})
    assert measured.get("hole_count") == 4
    assert measured.get("through_hole_count") == 4
    assert measured.get("components") == 1     # single fused body


# --- 2) vague bracket: clarify, never a failed/critical model --------------
def test_vague_bracket_clarifies_and_is_never_critical(client, auth):
    r = _create(client, auth, "Make a bracket.")
    assert r.status_code == 200, r.text
    d = r.json()
    # Option A (preferred): asks for clarification with useful questions.
    assert d["needs_clarification"] is True
    assert d["clarification_question"]
    # Whatever happens, a vague prompt must never yield a failed/critical model.
    assert d["validation_status"] != "critical_failure"
    assert d["download_blocked_reason"] is None


def test_specific_bracket_still_generates(client, auth):
    """The vague gate must NOT swallow a prompt that has a type + dimensions."""
    r = _create(client, auth, "Make an L bracket with 60mm legs, 5mm thick, 20mm wide.")
    d = r.json()
    assert d["needs_clarification"] is False
    assert {e["fmt"] for e in d["exports"]} == {"stl", "step"}


# --- 3) motorcycle subframe height: honest dimensions ---------------------
MOTORCYCLE = ("Create a motorcycle rear subframe concept, 850mm long, 350mm wide, "
              "and 450mm high, using 25mm steel tubes. Include seat rails, rear "
              "shock mount tabs, tail-light bracket, battery tray, side-panel tabs, "
              "and triangulated bracing.")


def test_motorcycle_height_within_tolerance_or_not_pass():
    from app.cad.assembly.frame_report import build_frame_report
    from app.cad.assembly.frames import build_frame_family
    from app.cad.plan.compiler import export_solid

    build = build_frame_family(MOTORCYCLE, "motorcycle_subframe")
    stl, step, _ = export_solid(build.solid)
    report = build_frame_report(build, stl, step)
    measured_h = report["measured"]["bbox_mm"]["z"]
    status = report["validation"]["status"]
    within = abs(measured_h - 450.0) <= 0.20 * 450.0
    # Either the height is actually close to requested, OR validation must NOT
    # report PASS — a 262mm-vs-450mm "PASS" is the bug this guards against.
    assert within or status != "pass", (measured_h, status)
    assert status != "critical_failure"


# --- 4) unsupported huge jet engine: clean decomposition ------------------
JET = ("Create a complete commercial jet engine with all compressor stages, "
       "turbine blades, combustion chamber, fuel injectors, gearbox, sensors, "
       "casing, bearings, cooling channels, and assembly constraints.")


def test_jet_engine_decomposes_with_specific_components(client, auth):
    r = _create(client, auth, JET)
    assert r.status_code == 200, r.text          # no 500 / no timeout
    d = r.json()
    assert d["needs_decomposition"] is True
    assert d["route"] == "needs_decomposition"
    assert d["has_program"] is False             # not the SCAD generator
    decomp = d["decomposition"]
    assert decomp and decomp["components"]
    blob = " ".join(decomp["components"]).lower()
    hits = sum(w in blob for w in
               ("compressor", "turbine", "combustion", "casing", "gearbox",
                "fuel injector", "bearing"))
    assert hits >= 3, decomp["components"]
    assert "0 subsystems" not in (decomp.get("reason") or "")


# --- 5) electric car skateboard platform: decompose, NOT chassis ----------
EV_PLATFORM = ("Create a complete electric car skateboard platform with "
               "suspension, battery enclosure, motors, steering rack, brakes, "
               "thermal management, wiring, chassis rails, crash structures, and "
               "body mounting points.")


def test_ev_skateboard_platform_does_not_route_to_tubular_chassis(client, auth):
    r = _create(client, auth, EV_PLATFORM)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["needs_decomposition"] is True
    assert d["object_type"] != "tubular_chassis_assembly"
    cls = d.get("classification") or {}
    assert cls.get("family_id") not in (
        "tube_chassis", "reference_buggy_tubular_chassis")


def test_negative_routing_assertions():
    """Direct routing checks (no geometry build)."""
    from app.cad.assembly.frames import detect_frame_family
    from app.cad.complexity import assess_complexity, detect_assembly_family

    # EV platform must not be a tubular chassis and must not be a frame family.
    assert detect_assembly_family(EV_PLATFORM) is None
    assert detect_frame_family(EV_PLATFORM) is None
    assert assess_complexity(EV_PLATFORM).is_complex is True
    # Jet engine is complex (decomposes) and not a frame family.
    assert detect_frame_family(JET) is None
    assert assess_complexity(JET).is_complex is True


# --- 6) robotic arm base bracket: audit must not contradict PASS ----------
ROBOTIC_ARM = ("Create a robotic arm base bracket with a 160mm circular base "
               "plate, six M8 mounting holes, a vertical support tower, two side "
               "gussets, and a 50mm bearing pocket at the top.")


def test_robotic_arm_audit_has_no_base_plate_contradiction(client, auth):
    r = _create(client, auth, ROBOTIC_ARM)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["object_type"] == "robotic_arm_base_bracket"
    # Audit recognizes the circular base — no missing-base_plate warning.
    assert d["feature_audit_passed"] in (True, None)
    for w in d.get("warnings", []):
        assert "base_plate" not in w.lower(), w
    # No contradiction: a PASS must not coexist with a missing-feature audit.
    if d["validation_status"] == "pass":
        assert d["feature_audit_passed"] is not False


# --- 7) validation status consistency -------------------------------------
def test_status_cannot_be_pass_on_dimension_mismatch():
    class _D:
        semantic_json = {
            "dimension_report": {"validation": {"status": "pass"},
                                 "within_tolerance": False}}
    assert reconciled_validation_status(_D()) == "warning"


def test_status_cannot_be_pass_on_failed_audit():
    class _D:
        semantic_json = {
            "dimension_report": {"validation": {"status": "pass"},
                                 "within_tolerance": True},
            "feature_audit": {"passed": False}}
    assert reconciled_validation_status(_D()) == "warning"


def test_status_critical_stays_critical():
    class _D:
        semantic_json = {"dimension_report": {"validation": {"status": "critical_failure"}}}
    assert reconciled_validation_status(_D()) == "critical_failure"


def test_clean_status_stays_pass():
    class _D:
        semantic_json = {
            "dimension_report": {"validation": {"status": "pass"},
                                 "within_tolerance": True},
            "feature_audit": {"passed": True}}
    assert reconciled_validation_status(_D()) == "pass"
