"""The LunaiCAD product contract (docs/product-contract.md): capability
levels, the deterministic ask-vs-default clarification policy, and the
additive API contract fields.

Covers the six required proof cases plus the core acceptance criterion: the
same request must not sometimes ask and sometimes silently assume under
equivalent conditions.
"""
from __future__ import annotations

from app.cad.families import Maturity, all_families
from app.cad.plan.policy import decide_clarification
from app.cad.plan.schema import CadPlan, Feature


def _create(client, auth, prompt: str) -> dict:
    r = client.post("/api/designs/create", json={"prompt": prompt}, headers=auth["headers"])
    assert r.status_code == 200, r.text
    return r.json()


# --- 1. Topology-changing ambiguity asks a question ------------------------

def test_topology_ambiguity_with_no_geometry_asks():
    plan = CadPlan(
        object_type="unknown_part", features=[],
        clarification_required=True, ambiguity_flags=["topology"],
        clarification_questions=["What is the part's basic shape?"],
    )
    decision = decide_clarification(plan, "")
    assert decision.severity == "fatal"
    assert decision.questions


def test_topology_ambiguity_api_asks(client, auth):
    """A recognized-but-underspecified prompt (no buildable primary shape)
    must ask, never guess -- the pre-existing no-features fatal path."""
    d = _create(client, auth, "make me a blind flange")
    assert d["needs_clarification"] is True
    assert d["unanswered_questions"]
    assert d["export_eligibility"]["eligible"] is False


# --- 2. Low-impact/cosmetic ambiguity uses a visible default ----------------

def test_cosmetic_ambiguity_on_buildable_plan_never_asks():
    plan = CadPlan(
        object_type="blind_flange",
        features=[Feature(id="f1", kind="circular_flange", params={"od": 150, "thickness": 18})],
        clarification_required=True,  # an LLM that over-asked about a cosmetic detail
        ambiguity_flags=["cosmetic_fillet"],
    )
    decision = decide_clarification(plan, "")
    assert decision.severity != "fatal"


def test_cosmetic_ambiguity_api_generates_with_visible_default(client, auth):
    d = _create(
        client, auth,
        "A blind flange, 150mm OD, 18mm thick, 8 M10 clearance holes on 120mm PCD, "
        "no center bore, with an unspecified fillet.",
    )
    assert d["needs_clarification"] is False
    assert d["exports"], "a cosmetic-only ambiguity must still generate exports"
    assert any("fillet" in a.lower() and "default" in a.lower() for a in d["assumptions"]), (
        d["assumptions"]
    )


# --- 3. Fit ambiguity asks a question, even on an otherwise-buildable plan --

def test_fit_ambiguity_on_buildable_plan_is_fatal():
    """The core override this phase adds: an ask-required category wins even
    when the plan already has real, compilable features."""
    plan = CadPlan(
        object_type="blind_flange",
        features=[Feature(id="f1", kind="circular_flange", params={"od": 150, "thickness": 18})],
        clarification_required=False,  # the LLM did NOT think to ask
        ambiguity_flags=["fit"],
    )
    decision = decide_clarification(plan, "")
    assert decision.severity == "fatal"
    assert decision.questions


def test_fit_ambiguity_api_asks_despite_buildable_geometry(client, auth):
    d = _create(
        client, auth,
        "A blind flange, 150mm OD, 18mm thick, 8 M10 clearance holes on 120mm PCD, "
        "no center bore, with an unspecified fit on the bore.",
    )
    assert d["needs_clarification"] is True
    assert d["unanswered_questions"]
    assert not d["exports"]
    assert d["export_eligibility"]["eligible"] is False


# --- 4. Unsupported parts are refused honestly ------------------------------

def test_unsupported_part_refused_honestly(client, auth):
    d = _create(client, auth, "Make a nylon insert lock nut M12")
    contract = d["part_family_contract"]
    assert contract["generation_honesty_status"] == "unsupported"
    assert d["object_type"] != "hex_nut"  # never silently substituted
    assert d["capability_level"] is None  # governed by part_family_contract, not the registry
    assert d["export_eligibility"]["eligible"] is False
    assert d["export_eligibility"]["reason"] == "This part is unsupported."


# --- 5. Capability labels match registry data -------------------------------

def test_every_family_maturity_is_one_of_the_four_standard_levels():
    valid = {m.value for m in Maturity}
    assert valid == {"production_ready", "validated_beta", "experimental", "unsupported"}
    for fam in all_families():
        assert fam.maturity.value in valid


def test_no_family_claims_a_maturity_its_benchmark_does_not_support():
    """If a family's measured eval-harness pass rate is below the threshold
    its OWN maturity level requires, that is a labelling inconsistency this
    test must catch -- capability labels must match registry data, not the
    other way around."""
    for fam in all_families():
        if fam.minimum_benchmark_threshold is None or fam.benchmark_pass_rate is None:
            continue
        assert fam.benchmark_pass_rate >= fam.minimum_benchmark_threshold, (
            f"{fam.family_id}: pass rate {fam.benchmark_pass_rate} below the "
            f"{fam.minimum_benchmark_threshold} required for {fam.maturity.value}"
        )


def test_production_ready_families_are_benchmarked_or_dimension_checked(client):
    """A production_ready claim must be backed by SOME physical validation
    signal -- never the bare default of 'none'."""
    body = client.get("/api/capabilities").json()
    for fam in body["families"]:
        if fam["maturity"] == "production_ready":
            assert fam["physical_validation_status"] in ("benchmarked", "dimension_checked"), fam


def test_capabilities_endpoint_uses_new_vocabulary(client):
    body = client.get("/api/capabilities").json()
    assert set(body["maturity_levels"]) == {
        "production_ready", "validated_beta", "experimental", "unsupported"
    }
    for fam in body["families"]:
        assert "safe_defaults" in fam
        assert "supported_editing_operations" in fam
        assert "physical_validation_status" in fam
        assert "supported_exports" in fam


# --- 6. Assumptions remain editable -----------------------------------------

def test_defaulted_dimension_remains_an_editable_parameter(client, auth):
    """corner_radius is never mentioned in the prompt (a visible default);
    it must still be a real, overridable editable_parameter afterward."""
    d = _create(client, auth, "A rectangular mounting bracket 80mm wide, 40mm deep, 5mm thick.")
    assert "corner_radius" in d["editable_parameters"]
    before_hash = d["spec_hash"]

    dims = dict(d["editable_parameters"])
    dims["corner_radius"] = 10.0
    r = client.post(f"/api/designs/{d['id']}/regenerate", json={"dimensions": dims},
                    headers=auth["headers"])
    assert r.status_code == 200, r.text
    after = r.json()
    assert after["spec_hash"] != before_hash
    assert after["editable_parameters"]["corner_radius"] == 10.0


# --- 7. Determinism: the same request never flips ask vs. default ----------

def test_decide_clarification_is_a_pure_function_of_its_inputs():
    def _make():
        return CadPlan(
            object_type="blind_flange",
            features=[Feature(id="f1", kind="circular_flange",
                              params={"od": 150, "thickness": 18})],
            clarification_required=False,
            ambiguity_flags=["fit"],
        )

    d1 = decide_clarification(_make(), "a blind flange")
    d2 = decide_clarification(_make(), "a blind flange")
    assert (d1.severity, d1.questions, d1.warnings) == (d2.severity, d2.questions, d2.warnings)


def test_same_prompt_twice_gives_the_same_capability_decision(client, auth):
    prompt = (
        "A blind flange, 150mm OD, 18mm thick, 8 M10 clearance holes on 120mm PCD, "
        "no center bore, with an unspecified fit on the bore."
    )
    d1 = _create(client, auth, prompt)
    d2 = _create(client, auth, prompt)
    assert d1["needs_clarification"] == d2["needs_clarification"] is True
    assert d1["unanswered_questions"] == d2["unanswered_questions"]
    assert d1["export_eligibility"] == d2["export_eligibility"]
