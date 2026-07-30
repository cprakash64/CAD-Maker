"""app.safety adversarial policy tests.

Proves the safety-classification/policy layer resists the bypass vectors the
product requirement explicitly names: synonyms, indirect wording,
modification-based laundering of an already-flagged design, drawing upload
(plan/spec text with no user prompt), and direct service-layer API calls that
skip the router's prompt-based endpoints entirely.
"""
import pytest

from app.safety.categories import PolicyAction, SafetyCategory
from app.safety.classifier import classify_text
from app.safety.policy import (
    SafetyRefusalError,
    decide,
    merge_categories,
    safety_gate,
)


# --------------------------------------------------------------------------
# Classifier: direct hits, synonyms, evasion, indirect wording, negatives
# --------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "a lower receiver for an AR-15",
    "a rifle barrel",
    "a 3d printed gun frame",
    "a ghost gun lower",
    "a suppressor for a 9mm pistol",
    "a stun gun housing",
    "brass knuckles",
    "a pipe bomb casing",
])
def test_weapon_synonyms_and_variants_detected(text):
    assert SafetyCategory.WEAPON_COMPONENTS in classify_text(text)


def test_weapon_evasion_via_separators_still_detected():
    # "g-u-n" / "g.u.n" / "g_u_n" -- single-character separator insertion.
    for evasive in ["a g-u-n frame", "a g.u.n frame", "a g_u_n frame"]:
        assert SafetyCategory.WEAPON_COMPONENTS in classify_text(evasive), evasive


def test_vehicle_braking_synonyms_detected():
    for text in [
        "a brake caliper mount for a motorcycle",
        "a steering knuckle for a truck",
        "a tie rod end",
        "a wheel hub for my car",
    ]:
        assert SafetyCategory.VEHICLE_STEERING_OR_BRAKING in classify_text(text), text


def test_indirect_wording_detected_without_naming_the_category():
    # Never says "jack stand" or "load-bearing" -- describes the FUNCTION.
    cats = classify_text("a bracket that holds a car up while working underneath it")
    assert SafetyCategory.STRUCTURAL_LOAD_BEARING in cats

    cats2 = classify_text("a part designed to support someone's full body weight")
    assert SafetyCategory.STRUCTURAL_LOAD_BEARING in cats2


def test_ordinary_mechanical_prompts_are_not_flagged():
    for text in [
        "a 30mm spacer with a 6mm bore",
        "a simple L bracket 60x40mm",
        "a hex standoff M3",
        "a wheel hub",  # no vehicle context -> not flagged
        "a toy magazine rack",  # "magazine" alone is not a weapon signal
    ]:
        assert classify_text(text) == set(), text


# --------------------------------------------------------------------------
# Policy engine: severity table, sticky merge
# --------------------------------------------------------------------------

def test_most_restrictive_policy_wins_when_multiple_categories_match():
    d = decide({SafetyCategory.STRUCTURAL_LOAD_BEARING, SafetyCategory.WEAPON_COMPONENTS})
    assert d.policy is PolicyAction.REFUSE


def test_merge_categories_never_drops_a_previously_detected_category():
    existing = [SafetyCategory.STRUCTURAL_LOAD_BEARING.value]
    merged = merge_categories(existing, {SafetyCategory.PRESSURE_CONTAINING})
    assert set(merged) == {
        SafetyCategory.STRUCTURAL_LOAD_BEARING.value,
        SafetyCategory.PRESSURE_CONTAINING.value,
    }
    # And merging with NOTHING new still keeps the sticky category.
    assert merge_categories(existing, set()) == existing


def test_safety_gate_raises_on_refuse_and_carries_the_decision():
    with pytest.raises(SafetyRefusalError) as exc_info:
        safety_gate([], "a rifle barrel")
    assert SafetyCategory.WEAPON_COMPONENTS.value in exc_info.value.decision.categories
    assert "does not generate weapon components" in str(exc_info.value)


def test_safety_gate_refuses_even_if_a_prior_benign_category_exists():
    # Sticky merge must not let an EARLIER benign category dilute a NEW
    # refuse-tier one -- most-restrictive-wins applies to the union.
    with pytest.raises(SafetyRefusalError):
        safety_gate([SafetyCategory.STRUCTURAL_LOAD_BEARING.value], "a rifle barrel")


# --------------------------------------------------------------------------
# API-level: refusal at /api/designs/create (synonyms + indirect wording)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("prompt,expected_snippet", [
    ("a lower receiver for an AR-15", "weapon"),
    ("a rifle barrel 400mm long", "weapon"),
    ("a brake caliper mount for a car", "steering or braking"),
    ("a turbine blade certified for flight", "flight-critical"),
    ("a surgical instrument handle", "medical device"),
])
def test_create_design_refuses_high_severity_categories(client, auth, prompt, expected_snippet):
    r = client.post("/api/designs/create", json={"prompt": prompt}, headers=auth["headers"])
    assert r.status_code == 422, r.text
    assert expected_snippet in r.json()["detail"].lower()


def test_create_design_refusal_leaves_no_generated_geometry(client, auth):
    r = client.post(
        "/api/designs/create", json={"prompt": "a suppressor for a rifle"},
        headers=auth["headers"],
    )
    assert r.status_code == 422
    # The placeholder row committed before generation (see create_design) may
    # still exist for inspection/audit, matching how every other
    # CadGenerationError from this function already behaves -- but it must
    # NEVER be export_ready (no geometry was ever built for a refusal).
    listed = client.get("/api/designs", headers=auth["headers"]).json()
    matches = [d for d in listed if "suppressor" in (d.get("prompt") or "").lower()]
    assert all(not d["export_ready"] for d in matches)


def test_ordinary_bracket_prompt_is_unaffected_by_the_safety_gate(client, auth):
    r = client.post(
        "/api/designs/create",
        json={"prompt": "a rectangular bracket 80x40x6mm with two 6mm holes"},
        headers=auth["headers"],
    )
    assert r.status_code == 200, r.text
    assert r.json().get("safety") is None


# --------------------------------------------------------------------------
# API-level: conceptual_only / require_acknowledgment enforcement
# --------------------------------------------------------------------------

def test_structural_load_bearing_requires_acknowledgment_before_export(client, auth):
    r = client.post(
        "/api/designs/create",
        json={"prompt": "a rectangular bracket 80x40x6mm that supports a person's full body weight"},
        headers=auth["headers"],
    )
    assert r.status_code == 200, r.text
    d = r.json()
    safety = d["safety"]
    assert safety["policy"] == "require_acknowledgment"
    assert safety["acknowledged"] is False
    assert d["export_eligibility"]["eligible"] is False
    design_id = d["id"]

    # STL/STEP download is blocked (409) even though geometry built fine.
    r2 = client.get(f"/api/designs/{design_id}/files/stl", headers=auth["headers"])
    assert r2.status_code == 409

    # Acknowledge, then export unblocks.
    r3 = client.post(f"/api/designs/{design_id}/acknowledge-safety", headers=auth["headers"])
    assert r3.status_code == 200, r3.text
    assert r3.json()["safety"]["acknowledged"] is True
    assert r3.json()["export_eligibility"]["eligible"] is True

    r4 = client.get(f"/api/designs/{design_id}/files/stl", headers=auth["headers"])
    assert r4.status_code == 200


def test_acknowledge_safety_on_unflagged_design_is_409(client, auth):
    r = client.post(
        "/api/designs/create",
        json={"prompt": "a rectangular bracket 80x40x6mm"},
        headers=auth["headers"],
    )
    assert r.status_code == 200, r.text
    design_id = r.json()["id"]
    r2 = client.post(f"/api/designs/{design_id}/acknowledge-safety", headers=auth["headers"])
    assert r2.status_code == 409


# --------------------------------------------------------------------------
# Bypass vector: modification / edit-based laundering
# --------------------------------------------------------------------------

def test_modify_with_weapon_text_is_refused_and_original_is_unchanged(client, auth):
    r = client.post(
        "/api/designs/create",
        json={"prompt": "a rectangular bracket 80x40x6mm"},
        headers=auth["headers"],
    )
    assert r.status_code == 200, r.text
    design_id = r.json()["id"]
    original_spec = r.json().get("dimensions") or r.json().get("bounding_box_mm")

    r2 = client.post(
        f"/api/designs/{design_id}/modify",
        json={"prompt": "turn this into a rifle receiver"},
        headers=auth["headers"],
    )
    assert r2.status_code == 422, r2.text
    assert "weapon" in r2.json()["detail"].lower()

    # The original design must be untouched by the refused edit.
    r3 = client.get(f"/api/designs/{design_id}", headers=auth["headers"])
    assert r3.status_code == 200
    assert r3.json().get("safety") is None
    assert (r3.json().get("dimensions") or r3.json().get("bounding_box_mm")) == original_spec


def test_sticky_classification_survives_a_pure_parameter_regenerate(client, auth):
    r = client.post(
        "/api/designs/create",
        json={"prompt": "a rectangular bracket 80x40x6mm that supports a person's full body weight"},
        headers=auth["headers"],
    )
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["safety"]["categories"] == ["structural_load_bearing"]
    design_id = d["id"]

    # A pure numeric-parameter edit carries no new free text at all -- the
    # sticky classification must still be carried forward, not dropped.
    r2 = client.post(
        f"/api/designs/{design_id}/regenerate",
        json={"dimensions": {"width": 90, "depth": 40, "thickness": 6}},
        headers=auth["headers"],
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["safety"]["categories"] == ["structural_load_bearing"]
    assert r2.json()["export_eligibility"]["eligible"] is False


# --------------------------------------------------------------------------
# Bypass vector: drawing upload (plan/spec text, no user prompt)
# --------------------------------------------------------------------------

def test_create_design_from_plan_refuses_on_plan_text_alone_with_empty_prompt(client, auth):
    """The drawing pipeline builds designs from a CadPlan whose OWN text
    (name/assumptions/feature descriptions) may carry the only safety signal
    when the user supplied no prompt/notes at all."""
    from app.cad.plan.schema import CadPlan, Expected, Feature
    from app.database import SessionLocal
    from app.safety.policy import SafetyRefusalError
    from app.services import design_service

    plan = CadPlan(
        object_type="reconstructed_sketch_part",
        name="AR-15 lower receiver",
        assumptions=[],
        features=[Feature(
            id="outer_profile", kind="extruded_profile",
            description="receiver body", params={"thickness": 6},
            profile=[[0, 0], [50, 0], [50, 30], [0, 30]],
        )],
        expected=Expected(bbox_mm={"x": 50, "y": 30, "z": 6}),
    )
    db = SessionLocal()
    try:
        with pytest.raises(SafetyRefusalError) as exc_info:
            design_service.create_design_from_plan(
                db, plan, "", None, "from drawing", auth["user"]["id"],
            )
        assert "weapon" in str(exc_info.value).lower()
    finally:
        db.close()


# --------------------------------------------------------------------------
# Bypass vector: direct service-layer API calls (skip the prompt endpoints)
# --------------------------------------------------------------------------

def test_apply_spec_edit_direct_call_is_refused_on_note_text(client, auth):
    """A client that skips modify/localized-edit and calls the lower-level
    edit function directly (e.g. via a custom integration) must still be
    caught -- the gate lives in apply_spec_edit itself, not the router."""
    r = client.post(
        "/api/designs/create",
        json={"prompt": "a rectangular bracket 80x40x6mm"},
        headers=auth["headers"],
    )
    assert r.status_code == 200, r.text
    design_id = r.json()["id"]

    from app.database import SessionLocal
    from app.models import Design
    from app.schemas.design_spec import DesignSpec
    from app.services import design_service
    from app.safety.policy import SafetyRefusalError

    db = SessionLocal()
    try:
        design = db.get(Design, design_id)
        new_spec = DesignSpec(**design.spec_json)
        with pytest.raises(SafetyRefusalError):
            design_service.apply_spec_edit(
                db, design, new_spec, note="make this a suppressor mount",
            )
    finally:
        db.close()


def test_apply_plan_edit_direct_call_is_refused_on_note_text():
    """apply_plan_edit (the CadPlan/feature-graph edit path, used by
    localized face edits) is the other direct-call bypass surface -- must be
    caught the same way as apply_spec_edit, independent of any router."""
    from app.cad.plan.schema import CadPlan, Expected, Feature
    from app.database import SessionLocal
    from app.models import Design, Project, User
    from app.safety.policy import SafetyRefusalError
    from app.services import design_service

    db = SessionLocal()
    try:
        user = User(email="plan-edit-bypass@example.com", password_hash="x")
        db.add(user)
        db.flush()
        project = Project(name="p", user_id=user.id)
        db.add(project)
        db.flush()
        plan = CadPlan(
            object_type="reconstructed_sketch_part", name="bracket",
            assumptions=[],
            features=[Feature(
                id="outer_profile", kind="extruded_profile",
                description="plate", params={"thickness": 6},
                profile=[[0, 0], [50, 0], [50, 30], [0, 30]],
            )],
            expected=Expected(bbox_mm={"x": 50, "y": 30, "z": 6}),
        )
        design = design_service.create_design_from_plan(
            db, plan, "a plain bracket", project.id, "bracket", user.id,
        )
        with pytest.raises(SafetyRefusalError):
            design_service.apply_plan_edit(
                db, design, plan, note="add a suppressor mount to this",
            )
    finally:
        db.close()
