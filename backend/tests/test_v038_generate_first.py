"""v0.3.8: generate-first policy, crankshaft routing, image error visibility, hints."""
import pytest

from app.drawing.interpret import interpret_image
from app.export.exporter import generate
from app.llm.base import LLMProvider
from app.parsing.complex_plan import classify_intent, detect_advanced_template, looks_complex
from app.parsing.policy import is_critical, split_missing
from app.parsing.prompt_parser import parse_prompt
from app.schemas.complex_cad import CADIntentKind


# --- Missing-Information Policy -------------------------------------------
def test_policy_critical_vs_non_critical():
    assert is_critical("object_type")
    assert not is_critical("hole_count")
    assert not is_critical("registration_lip")
    crit, non = split_missing(["object_type", "hole_count", "lip_size", "material"])
    assert crit == ["object_type"]
    assert set(non) == {"hole_count", "lip_size", "material"}


# A provider that reports non-critical missing info (like a cautious LLM would).
class _OverCautiousProvider(LLMProvider):
    name = "overcautious"

    def parse_prompt(self, prompt: str) -> dict:
        return {
            "object_type": "drill_jig",
            "dimensions": {"length": 120, "width": 80, "thickness": 6,
                           "hole_diameter": 6, "hole_spacing": 25},
            "holes": [],
            "missing_required": ["hole_count", "hole_pattern_start", "lip_size"],
            "clarification_question": "How many holes and what lip size?",
        }


def test_generate_first_ignores_non_critical_missing():
    result = parse_prompt("drill jig", provider=_OverCautiousProvider())
    assert result.spec is not None, "non-critical missing must not block generation"
    assert result.spec.object_type == "drill_jig"
    assert any("default" in a.lower() for a in result.assumptions)
    gen = generate(result.spec)
    assert len(gen.stl_bytes) > 0 and gen.step_bytes[:5] == b"ISO-1"


class _UnknownTypeProvider(LLMProvider):
    name = "unknown"

    def parse_prompt(self, prompt: str) -> dict:
        return {"object_type": None, "dimensions": {}, "missing_required": ["object_type"]}


def test_critical_missing_object_type_clarifies():
    result = parse_prompt("make me a thing", provider=_UnknownTypeProvider())
    assert result.spec is None and result.clarification_question


# --- Examples that MUST generate (via the offline mock) -------------------
@pytest.mark.parametrize("prompt,expected", [
    ("Drill jig plate 120mm by 80mm, 6mm thick, with 6mm guide holes spaced 25mm and a registration lip.",
     "drill_jig"),
    ("Mounting bracket 80mm wide 40mm deep 5mm thick with two M6 holes.", "rectangular_bracket"),
    ("Electronics enclosure 100mm wide, 60mm deep, 40mm tall with 2.5mm walls and a screw-down lid.",
     "enclosure"),
    ("Pipe clamp for a 25mm pipe, 6mm thick, with two M6 holes.", "pipe_clamp"),
])
def test_examples_generate(prompt, expected):
    result = parse_prompt(prompt)
    assert result.spec is not None, f"{prompt!r} should generate"
    assert result.spec.object_type == expected
    gen = generate(result.spec)
    assert len(gen.stl_bytes) > 0 and gen.step_bytes[:5] == b"ISO-1"


def test_drill_jig_parses_dimensions_and_defaults():
    r = parse_prompt(
        "Drill jig plate 120mm by 80mm, 6mm thick, with 6mm guide holes spaced 25mm "
        "and a registration lip."
    )
    d = r.spec.dimensions
    assert d["length"] == 120 and d["width"] == 80 and d["thickness"] == 6
    assert d["hole_diameter"] == 6 and d["hole_spacing"] == 25
    assert d.get("lip_height", 0) > 0  # default registration lip
    assert r.assumptions  # defaults surfaced


# --- Crankshaft routing ---------------------------------------------------
CRANK = (
    "Create a realistic crankshaft for a 4-cylinder inline engine with five main "
    "journals, four rod journals, counterweights, a keyed front snout and a "
    "flywheel flange. Throw radius about 45mm."
)


def test_crankshaft_indicators_detected():
    assert detect_advanced_template(CRANK) == "inline_4_crankshaft"
    assert looks_complex(CRANK)
    assert classify_intent(CRANK).template_candidate == "inline_4_crankshaft"


def test_crankshaft_prompt_generates():
    r = parse_prompt(CRANK)
    assert r.spec is not None and r.spec.object_type == "inline_4_crankshaft"
    gen = generate(r.spec)
    assert len(gen.stl_bytes) > 0 and gen.step_bytes[:5] == b"ISO-1"


def test_crankshaft_missing_bolt_pcd_does_not_block():
    from app.cad.templates.crankshaft import crankshaft_summary
    from app.schemas.design_spec import DesignSpec
    spec = DesignSpec(object_type="inline_4_crankshaft", dimensions={})  # no bolt PCD
    s = crankshaft_summary(spec)
    assert s["flange_bolt_count"] == 6
    assert len(generate(spec).stl_bytes) > 0


# OpenAI-style structured response routes to crankshaft (advanced type now allowed).
class _OpenAICrankProvider(LLMProvider):
    name = "openai-fake"

    def parse_prompt(self, prompt: str) -> dict:
        return {"object_type": "inline_4_crankshaft", "dimensions": {"total_length_mm": 420}}


def test_openai_response_maps_to_crankshaft():
    r = parse_prompt(CRANK, provider=_OpenAICrankProvider())
    assert r.spec is not None and r.spec.object_type == "inline_4_crankshaft"


# --- Drawing image error visibility ---------------------------------------
class _ErrorProvider(LLMProvider):
    name = "err"

    def parse_prompt(self, prompt: str) -> dict:
        return {"object_type": "rectangular_bracket", "dimensions": {}}

    def interpret_drawing(self, image_b64, media_type="image/png", hint=None):
        raise RuntimeError("model does not support image input")


def test_provider_error_is_surfaced_not_unknown_zero():
    interp = interpret_image(b"x" * 500, "image/png", provider=_ErrorProvider())
    assert interp.provider_error and "image input" in interp.provider_error
    assert not interp.is_actionable()


def test_hint_fallback_generates_when_image_fails():
    interp = interpret_image(
        b"x" * 500, "image/png", provider=_ErrorProvider(),
        hint="flanged pipe branch, 90mm main pipe, 12 bolts per flange",
    )
    assert interp.suggested_object_type == "flanged_pipe_branch"
    assert interp.is_actionable()


class _OpenAIDrawingProvider(LLMProvider):
    name = "openai-draw"

    def parse_prompt(self, prompt: str) -> dict:
        return {"object_type": "rectangular_bracket", "dimensions": {}}

    def interpret_drawing(self, image_b64, media_type="image/png", hint=None):
        return {
            "title": "Flanged pipe spool",
            "units": "mm",
            "suggested_object_type": "flanged_pipe_branch",
            "detected_object_type": "flanged_pipe_branch",
            "template_candidate": "flanged_pipe_branch",
            "views": [{"view_type": "front"}, {"view_type": "section"}],
            "overall_dimensions": {"main_pipe_outer_diameter_mm": 90, "bolt_count": 12},
            "holes": [{"diameter": 14, "count": 12}],
            "overall_confidence": 0.85,
        }


def test_openai_mocked_image_parses_flanged_pipe_branch():
    from app.drawing.interpret import to_design_spec
    interp = interpret_image(b"x" * 500, "image/png", provider=_OpenAIDrawingProvider())
    assert interp.suggested_object_type == "flanged_pipe_branch"
    assert interp.is_actionable()
    spec = to_design_spec(interp)
    assert spec is not None
    assert len(generate(spec).stl_bytes) > 0
