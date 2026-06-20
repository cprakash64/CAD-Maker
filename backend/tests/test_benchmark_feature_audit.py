"""Benchmark prompts must be SEMANTICALLY correct, not merely exportable.

Each benchmark goes through the real API (`POST /api/designs/create`, mock
provider → deterministic feature-graph planner → CadQuery compiler) and must:

  * generate without a fatal clarification (assumption-first),
  * route through the CadPlan feature graph (never a whole-part template),
  * export non-empty STEP **and** STL, downloadable via the files endpoint,
  * list assumptions whenever values were inferred,
  * carry a feature-level audit whose required features ALL pass — so wrong
    primary geometry fails this suite even when the files exist,
  * preserve the explicitly requested dimensions (bounding box).

A model that compiles+exports but lacks the requested mechanical features (e.g.
a plain block for a tube-clamp prompt, a flange WITH a center bore when "no
center bore" was asked) is a FAILURE here.
"""
from __future__ import annotations

import pytest

CLAMP = ("Create a part that looks like a clamp block for holding a 25mm round "
         "tube, with two bolts to tighten it and a flat mounting base.")
BEARING = "Create a compact bearing block for a 20mm shaft with a 90mm by 45mm by 12mm base."
HINGE = ("Create a small hinge bracket with two side ears and a base. The base is "
         "70mm by 40mm by 6mm. The side ears are 30mm tall and 6mm thick, with an "
         "8mm pin hole through both ears.")
FLANGE = ("Create a blind flange, 150mm outer diameter, 18mm thick, with 8 M10 "
          "clearance holes on a 120mm PCD and no center bore.")
SPOOL = ("Create a straight pipe spool 200mm long, 80mm pipe outer diameter, 60mm "
         "bore, with circular flanges on both ends. Each flange is 120mm OD, 12mm "
         "thick, with 8 M8 holes on a 100mm PCD.")
U_BRACKET = ("Create a U-shaped bracket, 80mm wide, 60mm tall, 6mm thick, with two "
             "M6 holes on the base and one 8mm pivot hole through each side wall.")
ENCLOSURE = ("Create a two-part sensor enclosure with a cylindrical sensor hole on "
             "the front face and four mounting holes on the back.")

# Regression families that must keep working through the same pipeline.
NEMA = "NEMA 17 stepper motor mounting plate, 5mm thick"
L_BRACKET = "L bracket 100mm by 60mm base, 8mm thick, wall 60mm tall, with 4 M6 holes"
VISE = ("Vise jaw 100mm long, 30mm tall, 20mm thick with a 90 degree V groove "
        "and 2 M6 mounting holes")
ADAPTER = "Adapter plate 120mm long, 80mm wide, 10mm thick with 4 M6 corner holes"


def _create(client, auth, prompt: str) -> dict:
    r = client.post("/api/designs/create", json={"prompt": prompt}, headers=auth["headers"])
    assert r.status_code == 200, r.text
    return r.json()


def _assert_generated(client, auth, d: dict) -> None:
    """Common completion contract: model + STEP/STL downloads + audit passed."""
    assert not d["needs_clarification"], f"fatal clarification: {d['clarification_question']}"
    assert d["route"] == "cad_plan", f"expected feature-graph route, got {d['route']}"
    assert d["preview"] and d["preview"]["triangle_count"] > 0, "empty model"

    fmts = {e["fmt"] for e in d["exports"]}
    assert {"step", "stl"} <= fmts, f"missing exports: {fmts}"
    for e in d["exports"]:
        assert e["size_bytes"] > 0
        # The file must actually download through the owner-checked endpoint.
        r = client.get(f"/api/designs/{d['id']}/files/{e['fmt']}", headers=auth["headers"])
        assert r.status_code == 200 and len(r.content) > 0, f"{e['fmt']} download failed"

    assert d["assumptions"], "inferred values must be listed as assumptions"
    assert d["feature_audit"], "feature audit report missing"
    failures = [i for i in d["feature_audit"] if not i["satisfied"]]
    assert not failures, f"feature audit failed: {failures}"
    assert d["feature_audit_passed"] is True


def _audited(d: dict) -> set[str]:
    return {i["feature_id"] for i in d["feature_audit"]}


def _bbox(d: dict) -> tuple[float, float, float]:
    bb = d["bounding_box_mm"]
    return bb["x"], bb["y"], bb["z"]


# --- the seven benchmarks -----------------------------------------------------
def test_clamp_block_is_a_real_clamp(client, auth):
    d = _create(client, auth, CLAMP)
    _assert_generated(client, auth, d)
    # The audit must have actually checked the clamp anatomy, not vacuously passed.
    assert {"clamp_body", "tube_bore", "clamp_gap", "tightening_bolt_holes",
            "base_plate", "mounting_holes"} <= _audited(d)
    ids = {f["id"] for f in d["features"]}
    assert "tube_bore" in ids and "clamp_gap" in ids
    assert {"tightening_bolt_1", "tightening_bolt_2"} <= ids


def test_bearing_block_has_boss_and_bore(client, auth):
    d = _create(client, auth, BEARING)
    _assert_generated(client, auth, d)
    assert {"bearing_boss", "shaft_bore", "base_plate", "mounting_holes"} <= _audited(d)
    x, y, z = _bbox(d)
    assert (x, y) == (90.0, 45.0), "requested base size must be preserved"
    assert z > 12.0, "boss must rise above the 12mm base"
    text = " ".join(d["assumptions"]).lower()
    assert "boss" in text, "inferred boss dimensions must be stated"


def test_hinge_bracket_ears_and_pin(client, auth):
    d = _create(client, auth, HINGE)
    _assert_generated(client, auth, d)
    assert {"hinge_ears", "pin_hole", "base_plate"} <= _audited(d)
    x, y, z = _bbox(d)
    assert (x, y) == (70.0, 40.0)
    assert z == pytest.approx(36.0), "total height = 6mm base + 30mm ears"


def test_blind_flange_circular_no_center_bore(client, auth):
    d = _create(client, auth, FLANGE)
    _assert_generated(client, auth, d)
    audited = _audited(d)
    assert {"flange_body", "bolt_circle", "center_bore"} <= audited
    bore_item = next(i for i in d["feature_audit"] if i["feature_id"] == "center_bore")
    assert bore_item["forbidden"] and bore_item["satisfied"], "center bore must be ABSENT"
    x, y, z = _bbox(d)
    assert x == pytest.approx(150.0) and y == pytest.approx(150.0), "must be circular Ø150"
    assert z == pytest.approx(18.0)


def test_pipe_spool_straight_not_tee(client, auth):
    d = _create(client, auth, SPOOL)
    _assert_generated(client, auth, d)
    audited = _audited(d)
    assert {"pipe_body", "flange_body", "bolt_circle", "branch_pipe"} <= audited
    branch = next(i for i in d["feature_audit"] if i["feature_id"] == "branch_pipe")
    assert branch["forbidden"] and branch["satisfied"], "must NOT be a tee"
    x, y, z = _bbox(d)
    assert z == pytest.approx(200.0), "spool length preserved"
    assert x == pytest.approx(120.0) and y == pytest.approx(120.0), \
        "envelope = flange OD both ways (straight, no side branch)"


def test_u_bracket_walls_and_pivots(client, auth):
    d = _create(client, auth, U_BRACKET)
    _assert_generated(client, auth, d)
    assert {"base_plate", "side_walls", "mounting_holes", "pin_hole"} <= _audited(d)
    x, y, z = _bbox(d)
    assert x == pytest.approx(80.0) and z == pytest.approx(60.0)


def test_sensor_enclosure_generates_with_defaults(client, auth):
    d = _create(client, auth, ENCLOSURE)
    _assert_generated(client, auth, d)
    assert {"enclosure_body", "enclosure_shell", "sensor_hole",
            "mounting_holes"} <= _audited(d)
    text = " ".join(d["assumptions"]).lower()
    assert "wall" in text, "wall-thickness assumption must be stated"
    x, y, z = _bbox(d)
    assert min(x, y, z) > 0, "default enclosure dimensions generated"


# --- regression families --------------------------------------------------------
@pytest.mark.parametrize("prompt", [NEMA, L_BRACKET, VISE, ADAPTER],
                         ids=["nema_plate", "l_bracket", "vise_jaw", "adapter_plate"])
def test_regression_families_still_generate(client, auth, prompt):
    d = _create(client, auth, prompt)
    _assert_generated(client, auth, d)


# --- wrong primary geometry must FAIL even though files exist -------------------
def test_wrong_primary_geometry_fails_audit():
    """A plain block that compiles and exports for a clamp prompt must fail the
    feature audit — files existing is not success."""
    from app.cad.plan.audit import audit_plan
    from app.cad.plan.planner import build_and_validate
    from app.cad.plan.schema import CadPlan, Feature

    wrong = CadPlan(object_type="tube_clamp_block", features=[
        Feature(id="body", kind="box", description="a rough block",
                params={"width": 60, "depth": 50, "height": 50}),
    ])
    out = build_and_validate(wrong)
    assert out.report.passed and len(out.step_bytes) > 0 and len(out.stl_bytes) > 0, \
        "the wrong model exports fine — which is exactly why the audit must exist"
    audit = audit_plan(CLAMP, wrong, out.result)
    assert not audit.passed
    missing = {i.feature_id for i in audit.failures()}
    assert {"tube_bore", "clamp_gap", "tightening_bolt_holes"} <= missing


def test_flange_with_center_bore_fails_audit():
    from app.cad.plan.audit import audit_plan
    from app.cad.plan.planner import build_and_validate
    from app.cad.plan.schema import CadPlan, Feature

    wrong = CadPlan(object_type="blind_flange", features=[
        Feature(id="flange_body", kind="circular_flange", description="flange",
                params={"od": 150, "thickness": 18, "pcd": 120, "bolt_count": 8,
                        "bolt_diameter": 11, "bore": 40}),
    ])
    out = build_and_validate(wrong)
    audit = audit_plan(FLANGE, wrong, out.result)
    assert not audit.passed
    assert any(i.feature_id == "center_bore" and i.forbidden and not i.satisfied
               for i in audit.failures())


def test_tee_for_straight_spool_prompt_fails_audit():
    from app.cad.plan import deterministic
    from app.cad.plan.audit import audit_plan

    tee_plan = deterministic._plan_pipe_tee(SPOOL.lower())
    audit = audit_plan(SPOOL, tee_plan)
    assert not audit.passed, "a tee must fail the straight-spool audit"
    assert any(i.feature_id == "branch_pipe" and i.forbidden and not i.satisfied
               for i in audit.failures())
