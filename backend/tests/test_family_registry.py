"""Tests for the CAD family registry, prompt classifier, golden benchmark, and
the /api/capabilities endpoint.

These are deterministic and offline (no LLM / CadQuery) so they run fast and pin
the routing/honesty contract. End-to-end generation+export for mounting plate /
spacer / L-bracket / chassis is covered by the existing generation tests; here we
only verify that classification metadata is consistent and stored.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.cad.classification import classify_prompt
from app.cad.families import (
    GENERIC_ASSEMBLY_FAMILY,
    GENERIC_PART_FAMILY,
    DesignMode,
    Maturity,
    all_families,
    family_for_object_type,
    get_family,
    production_ready_families,
)

_GOLDEN = json.loads(
    (Path(__file__).parent / "data" / "golden_prompts.json").read_text()
)["cases"]


# --- Registry integrity ---------------------------------------------------

def test_family_ids_unique():
    ids = [f.family_id for f in all_families()]
    assert len(ids) == len(set(ids)), "duplicate family_id in registry"


def test_single_part_object_type_reverse_map_is_unambiguous():
    """No two SINGLE-PART families claim the same generator object_type, so the
    reverse lookup is deterministic. (Assembly families may share one object_type
    — e.g. tube_chassis vs reference buggy — because they are distinguished by
    detail level at classification time, not by object_type.)"""
    seen: dict[str, str] = {}
    for fam in all_families():
        if fam.design_mode != DesignMode.single_part:
            continue
        for ot in fam.object_types:
            assert ot not in seen, (
                f"object_type '{ot}' claimed by both {seen[ot]} and {fam.family_id}"
            )
            seen[ot] = fam.family_id


@pytest.mark.parametrize("fam", all_families(), ids=lambda f: f.family_id)
def test_every_family_has_examples_and_limitations(fam):
    """Honesty contract: every family documents what it is and what it can't do."""
    assert fam.display_name
    assert fam.known_limitations, f"{fam.family_id} has no known_limitations"
    # Guidance-only families need no example geometry; everything else must show
    # at least one example prompt.
    if fam.family_id != GENERIC_ASSEMBLY_FAMILY:
        assert fam.example_prompts, f"{fam.family_id} has no example_prompts"


@pytest.mark.parametrize("fam", all_families(), ids=lambda f: f.family_id)
def test_maturity_and_export_policy_consistent(fam):
    """Unsupported families produce no export; everything else exports STEP+STL."""
    assert isinstance(fam.maturity, Maturity)
    if fam.maturity == Maturity.unsupported:
        assert fam.export_policy == []
    else:
        assert "stl" in fam.export_policy


def test_generic_fallbacks_exist():
    assert get_family(GENERIC_PART_FAMILY) is not None
    assert get_family(GENERIC_ASSEMBLY_FAMILY) is not None


def test_reverse_lookup_resolves_known_templates():
    assert family_for_object_type("rectangular_bracket").family_id == "mounting_plate"
    assert family_for_object_type("spacer").family_id == "spacer"
    assert family_for_object_type("l_bracket").family_id == "l_bracket"
    assert family_for_object_type("tubular_chassis_assembly").design_mode == DesignMode.assembly
    assert family_for_object_type(None) is None
    assert family_for_object_type("does_not_exist") is None


# --- Classifier behavior --------------------------------------------------

def test_classifier_routes_core_families():
    assert classify_prompt(
        "A rectangular mounting plate 80x40x5mm with two 6mm holes"
    ).family_id == "mounting_plate"
    assert classify_prompt("A spacer 10mm OD, 5mm bore, 12mm long").family_id == "spacer"
    assert classify_prompt(
        "An L bracket with 60mm legs, 5mm thick"
    ).family_id == "l_bracket"


def test_classifier_handles_empty_prompt():
    c = classify_prompt("   ")
    assert c.can_generate_now is False
    assert c.generation_strategy == "needs_clarification"


def test_huge_prompt_does_not_attempt_generation():
    """A whole-machine prompt must be flagged for decomposition (no expensive
    generation), never silently routed to a single-part build."""
    c = classify_prompt(
        "Design a complete car with engine, suspension, transmission, drivetrain, "
        "body panels, and a full interior dashboard"
    )
    assert c.family_id == GENERIC_ASSEMBLY_FAMILY
    assert c.generation_strategy == "needs_decomposition"
    assert c.can_generate_now is False
    assert c.design_mode == "assembly"


def test_supported_chassis_classifies_as_assembly():
    c = classify_prompt("A tubular chassis 2000mm long, 1200mm wide, 1000mm tall")
    assert c.design_mode == "assembly"
    assert c.generation_strategy == "assembly_generator"
    assert c.can_generate_now is True


def test_reference_buggy_distinguished_from_plain_chassis():
    ref = classify_prompt(
        "A detailed welded steel tubular buggy chassis with roll cage and "
        "suspension mounts, 2600mm long"
    )
    assert ref.family_id == "reference_buggy_tubular_chassis"


def test_classification_is_serializable():
    d = classify_prompt("A spacer 10mm OD, 5mm bore, 12mm long").to_dict()
    json.dumps(d)  # must not raise
    assert {"family_id", "generation_strategy", "limitations", "maturity"} <= d.keys()


# --- Golden benchmark -----------------------------------------------------

def test_golden_benchmark_has_minimum_coverage():
    assert len(_GOLDEN) >= 30, "golden benchmark should have at least 30 prompts"


@pytest.mark.parametrize("case", _GOLDEN, ids=[c["name"] for c in _GOLDEN])
def test_golden_prompt_classification(case):
    c = classify_prompt(case["prompt"])
    assert c.family_id == case["expected_family_id"], case["name"]
    assert c.design_mode == case["expected_design_mode"], case["name"]
    assert c.generation_strategy == case["expected_strategy"], case["name"]
    assert c.can_generate_now == case["expected_can_generate"], case["name"]


@pytest.mark.parametrize("case", _GOLDEN, ids=[c["name"] for c in _GOLDEN])
def test_golden_prompt_honesty_contract(case):
    """Decomposition/clarification cases must not claim they can generate or
    export; generatable cases must be allowed to export."""
    c = classify_prompt(case["prompt"])
    if case["expects_decomposition"]:
        assert c.generation_strategy == "needs_decomposition"
        assert case["export_allowed"] is False
    if case["expects_clarification"]:
        assert c.generation_strategy == "needs_clarification"
    if not case["expected_can_generate"]:
        assert case["export_allowed"] is False


def test_every_production_ready_family_has_a_golden_prompt():
    covered = {classify_prompt(c["prompt"]).family_id for c in _GOLDEN}
    for fam in production_ready_families():
        assert fam.family_id in covered, (
            f"production_ready family {fam.family_id} has no golden prompt"
        )


def test_golden_family_ids_are_registered():
    for case in _GOLDEN:
        assert get_family(case["expected_family_id"]) is not None, case["name"]
