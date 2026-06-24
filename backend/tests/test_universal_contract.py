"""Universal CAD generation contract.

Every prompt must end in exactly one safe terminal state — generated_single_part,
generated_assembly, needs_clarification, needs_decomposition, unsupported, or
failed_safe — and a geometry result is NEVER a critical-failure (broken /
non-manifold) model offered as if it were good.

Covers the prompt-understanding layer, the contract state mapping, and the
regression battery of everyday-object prompts that previously risked broken or
disconnected free-form geometry.
"""
from __future__ import annotations

import pytest

from app.cad.contract import (
    GEOMETRY_OUTCOMES,
    GenerationOutcome,
    contract_metadata,
    is_safe_outcome,
    resolve_outcome,
)
from app.cad.understanding import (
    ROUTE_CLARIFY,
    ROUTE_CONCEPT_FALLBACK,
    ROUTE_DECOMPOSE,
    extract_dimensions,
    extract_features,
    understand_prompt,
)

# The regression battery from the contract spec. Each must end in one of these
# safe states and must never yield a critical-failure geometry.
REGRESSION_PROMPTS = [
    "Make a hammer",
    "Make a wrench",
    "Make a pulley",
    "Make a gear",
    "Make a robot arm",
    "Make a bicycle frame",
    "Make a gearbox",
    "Make a fan blade",
    "Make a shelf bracket",
    "Make a pipe clamp",
]

# A geometry result, a clarification, or a decomposition are all acceptable for
# the battery; a broken/unsupported/failed-safe escape is not.
ACCEPTABLE = {
    GenerationOutcome.generated_single_part.value,
    GenerationOutcome.generated_assembly.value,
    GenerationOutcome.needs_clarification.value,
    GenerationOutcome.needs_decomposition.value,
}


def _create(client, auth, prompt: str):
    return client.post("/api/designs/create", json={"prompt": prompt},
                       headers=auth["headers"])


# --- end-to-end regression battery ----------------------------------------
@pytest.mark.parametrize("prompt", REGRESSION_PROMPTS)
def test_regression_prompt_ends_in_safe_state(client, auth, prompt):
    r = _create(client, auth, prompt)
    assert r.status_code == 200, r.text
    d = r.json()

    outcome = d["generation_outcome"]
    assert outcome in ACCEPTABLE, f"{prompt!r} -> {outcome!r} (not an acceptable state)"

    # A geometry outcome must be genuinely valid — never a critical failure, and
    # it must actually carry exportable files.
    if outcome in {o.value for o in GEOMETRY_OUTCOMES}:
        assert d["validation_status"] != "critical_failure", \
            f"{prompt!r} produced critical-failure geometry"
        assert d["download_blocked_reason"] is None
        assert {e["fmt"] for e in d["exports"]} >= {"stl", "step"}
    else:
        # Non-geometry states must offer guidance, never a broken file.
        assert d["exports"] == []
        assert d["preview"] is None

    # Every design carries an understanding block with the required fields.
    u = d["understanding"]
    assert u is not None
    for key in ("object_type", "family", "dimensions", "features",
                "missing_fields", "complexity", "recommended_route"):
        assert key in u


@pytest.mark.parametrize("prompt", REGRESSION_PROMPTS)
def test_regression_prompt_never_blocks_download_silently(client, auth, prompt):
    """A produced model is always inspectable AND exportable; a non-produced one
    always explains itself (clarification text or a decomposition plan)."""
    d = _create(client, auth, prompt).json()
    if d["generation_outcome"] in {o.value for o in GEOMETRY_OUTCOMES}:
        assert d["exports"]
    else:
        assert d["clarification_question"] or d["decomposition"]


# --- prompt understanding layer -------------------------------------------
def test_understanding_extracts_core_fields():
    u = understand_prompt(
        "A rectangular mounting plate 80x40x5mm with four M6 holes and a fillet"
    )
    assert u.dimensions["length"] == 80
    assert u.dimensions["width"] == 40
    assert u.dimensions["height"] == 5
    assert u.dimensions["hole_diameter"] == 6
    assert "hole" in u.features and "fillet" in u.features
    assert u.recommended_route in (
        "generate_single_part", "generate_assembly")
    # Required-field set is a list of strings (possibly empty).
    assert isinstance(u.missing_fields, list)


def test_extract_dimensions_parses_diameter_and_bore():
    dims = extract_dimensions("a flange 100mm diameter, 10mm thick, 40mm bore")
    assert dims["diameter"] == 100
    assert dims["thickness"] == 10
    assert dims["bore"] == 40


def test_extract_features_dedupes_and_canonicalizes():
    feats = extract_features("a gear with teeth, a hub, a bore and two holes")
    assert "tooth" in feats  # 'teeth' -> 'tooth'
    assert "hub" in feats and "bore" in feats and "hole" in feats


def test_underspecified_machine_prompts_do_not_promise_a_part():
    """Recognizable-but-underspecified everyday objects route to clarify or a
    labelled concept fallback — never a confident single-part promise."""
    for prompt in ("Make a hammer", "Make a wrench"):
        u = understand_prompt(prompt)
        assert u.recommended_route in (ROUTE_CLARIFY, ROUTE_CONCEPT_FALLBACK)


def test_large_machine_prompt_recommends_decomposition():
    # A whole multi-system machine with no supported concept-assembly family
    # (an aircraft fuselage) decomposes rather than promising a single part.
    u = understand_prompt(
        "Design a complete aircraft fuselage with wings, landing gear, cockpit, "
        "avionics bay, fuel system, control surfaces and tail assembly"
    )
    assert u.recommended_route == ROUTE_DECOMPOSE
    assert u.complexity in ("complex", "huge")


def test_supported_concept_assembly_recommends_assembly():
    # A tubular chassis IS a supported concept assembly here, so the contract
    # builds it rather than decomposing.
    u = understand_prompt(
        "A welded tubular space frame chassis 2000mm long, 1200mm wide, 1000mm tall"
    )
    assert u.recommended_route == "generate_assembly"


# --- contract state mapping (unit) ----------------------------------------
class _FakeExport:
    fmt = "stl"


class _FakeDesign:
    def __init__(self, **kw):
        self.route = kw.get("route")
        self.spec_json = kw.get("spec_json")
        self.clarification_question = kw.get("clarification_question")
        self.exports = kw.get("exports", [])
        self.semantic_json = kw.get("semantic_json", {})


def test_resolve_outcome_covers_every_state():
    assert resolve_outcome(_FakeDesign(route="needs_decomposition")) \
        == GenerationOutcome.needs_decomposition
    assert resolve_outcome(_FakeDesign(route="clarification",
                                       clarification_question="?")) \
        == GenerationOutcome.needs_clarification
    assert resolve_outcome(_FakeDesign(route="unsupported")) \
        == GenerationOutcome.unsupported
    assert resolve_outcome(_FakeDesign(route="failed_safe")) \
        == GenerationOutcome.failed_safe
    # Exported single part.
    assert resolve_outcome(_FakeDesign(route="cad_plan", spec_json={"x": 1},
                                       exports=[_FakeExport()])) \
        == GenerationOutcome.generated_single_part
    # Exported assembly.
    assert resolve_outcome(_FakeDesign(route="assembly", exports=[_FakeExport()])) \
        == GenerationOutcome.generated_assembly
    # Exported but critical -> collapses to failed_safe, not a real part.
    crit = _FakeDesign(
        route="precision_template", spec_json={"x": 1}, exports=[_FakeExport()],
        semantic_json={"dimension_report": {"validation": {"status": "critical_failure"}}},
    )
    assert resolve_outcome(crit) == GenerationOutcome.failed_safe
    # Nothing produced and nothing explained -> failed_safe.
    assert resolve_outcome(_FakeDesign(route="cad_plan")) \
        == GenerationOutcome.failed_safe


def test_contract_metadata_is_always_safe():
    meta = contract_metadata(_FakeDesign(route="needs_decomposition"))
    assert meta["is_safe"] is True
    assert meta["produced_geometry"] is False
    assert is_safe_outcome(GenerationOutcome(meta["outcome"]))


# --- failed-safe guard ----------------------------------------------------
def test_unexpected_error_lands_in_failed_safe(client, auth, monkeypatch):
    """An unexpected internal error must yield a 200 `failed_safe` design with no
    broken geometry — never a 500 or a half-built model."""
    from app.services import design_service

    def _boom(db, design, prompt, classification=None):
        raise RuntimeError("kernel exploded")

    monkeypatch.setattr(design_service, "_dispatch_generation", _boom)
    r = _create(client, auth, "a rectangular plate 80x40x5mm")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["generation_outcome"] == GenerationOutcome.failed_safe.value
    assert d["exports"] == []
    assert d["preview"] is None
    assert d["clarification_question"]


# =========================================================================
# Production-readiness layer: concept fallbacks, vague clarification,
# decomposition, telemetry, standards defaults, capability metadata.
# =========================================================================

# Common everyday objects that must now generate a single connected CONCEPT solid.
CONCEPT_PROMPTS = [
    "Make a hammer",
    "Make a wrench",
    "Make pliers",
    "Make a wheel",
    "Make a fan blade",
    "Make a hook",
    "Make a tool holder",
    "Make a simple casing",
    "Make a generic handle",
    "Make a stand",
]

# Specific buildable parts that must generate (not clarify).
GENERATES_PROMPTS = ["Make a shelf bracket", "Make a motor mount"]

# Vague category prompts that must ask for clarification with clickable options.
VAGUE_PROMPTS = ["Make a bracket", "Make a mount", "Make a holder"]


@pytest.mark.parametrize("prompt", CONCEPT_PROMPTS + GENERATES_PROMPTS)
def test_everyday_prompt_generates_valid_concept_cad(client, auth, prompt):
    d = _create(client, auth, prompt).json()
    assert d["generation_outcome"] in {
        GenerationOutcome.generated_single_part.value,
        GenerationOutcome.generated_assembly.value,
    }, f"{prompt!r} -> {d['generation_outcome']!r}"
    # Real, safe geometry: never a critical failure, exportable both formats.
    assert d["validation_status"] != "critical_failure"
    assert d["download_blocked_reason"] is None
    assert {e["fmt"] for e in d["exports"]} >= {"stl", "step"}
    assert all(e["size_bytes"] > 0 for e in d["exports"])


@pytest.mark.parametrize("prompt", CONCEPT_PROMPTS)
def test_concept_prompt_is_labelled_concept_with_assumptions(client, auth, prompt):
    d = _create(client, auth, prompt).json()
    # Documented defaults are surfaced as assumptions (never hidden)...
    assert d["assumptions"], f"{prompt!r} built with no surfaced assumptions"
    # ...and the output is honestly labelled as concept (not manufacturing-ready).
    blob = " ".join(d["assumptions"]).lower()
    assert "concept" in blob, f"{prompt!r} not labelled concept"


@pytest.mark.parametrize("prompt", VAGUE_PROMPTS)
def test_vague_category_prompt_clarifies_with_clickable_options(client, auth, prompt):
    d = _create(client, auth, prompt).json()
    assert d["generation_outcome"] == GenerationOutcome.needs_clarification.value
    assert d["needs_clarification"] is True
    assert d["exports"] == []
    opts = d["clarification_options"]
    assert opts, f"{prompt!r} clarified with no suggested options"
    labels = {o["label"] for o in opts}
    # The promised family suggestions are present and each is a ready-to-run prompt.
    assert {"L bracket", "U bracket", "Rectangular mounting plate", "Tube clamp",
            "Motor mount plate", "Shelf bracket", "Hinge bracket"} <= labels
    for o in opts:
        assert o["prompt"] and len(o["prompt"]) > 20


def test_robot_prompt_is_safe(client, auth):
    d = _create(client, auth, "Make a robot").json()
    assert d["generation_outcome"] in {
        GenerationOutcome.needs_clarification.value,
        GenerationOutcome.needs_decomposition.value,
    }
    assert d["validation_status"] != "critical_failure"


def test_complete_car_decomposes(client, auth):
    d = _create(client, auth, "Make a complete car").json()
    assert d["generation_outcome"] == GenerationOutcome.needs_decomposition.value
    assert d["needs_decomposition"] is True
    assert d["decomposition"]


def test_gearbox_never_ships_critical_geometry(client, auth):
    """A 'gearbox' has no dedicated family; whatever route it takes, it must end
    in a safe state and never a broken/critical model."""
    d = _create(client, auth, "Make a gearbox").json()
    assert d["generation_outcome"] in ACCEPTABLE
    assert d["validation_status"] != "critical_failure"
    assert d["download_blocked_reason"] is None


# --- telemetry ------------------------------------------------------------
@pytest.mark.parametrize("prompt", ["Make a hammer", "Make a bracket", "Make a complete car"])
def test_telemetry_is_persisted(client, auth, prompt):
    d = _create(client, auth, prompt).json()
    t = d["telemetry"]
    assert t is not None
    for key in ("route_selected", "family_selected", "confidence", "missing_fields",
                "generation_outcome", "validation_status", "repair_attempted",
                "export_blocked"):
        assert key in t, f"telemetry missing {key}"
    assert t["generation_outcome"] == d["generation_outcome"]
    assert isinstance(t["repair_attempted"], bool)
    assert isinstance(t["export_blocked"], bool)


# --- standards / internal defaults ----------------------------------------
def test_standards_defaults_are_internal_not_certified():
    from app.cad.standards import defaults as d

    assert d.STANDARDS_CERTIFIED is False
    assert "M6" in d.METRIC_CLEARANCE_HOLES
    assert d.clearance_hole("M6") == 6.6
    assert d.clearance_hole("M8", "loose") == 10.0
    # Unknown size falls back conservatively, never raises.
    assert d.clearance_hole("M99") > 0
    assert d.nearest_plate_thickness(5.4) == 5.0
    assert d.MIN_PRINTABLE_HOLE_MM > 0
    snapshot = d.as_dict()
    assert snapshot["standards_certified"] is False
    assert "machinery" not in snapshot["source"].lower() or "not" in snapshot["source"].lower()


# --- capability metadata --------------------------------------------------
def test_capabilities_expose_grouped_metadata(client):
    body = client.get("/api/capabilities").json()
    assert body["production_ready_families"]
    assert body["concept_ready_families"]
    assert body["needs_clarification_examples"]
    assert body["clarification_suggestions"]
    assert body["unsupported_categories"]
    assert body["known_limitations"]
    # The new concept families are advertised as concept maturity.
    ids = {f["family_id"] for f in body["families"]}
    assert {"hammer", "wrench", "pliers", "wheel", "fan_blade", "hook",
            "tool_holder", "simple_casing"} <= ids
    # Internal defaults are present and clearly not standards-certified.
    assert body["internal_defaults"]["standards_certified"] is False
