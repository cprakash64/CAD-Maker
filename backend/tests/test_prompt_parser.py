"""Prompt parsing: structured spec extraction and clarification handling."""
from app.parsing.prompt_parser import parse_prompt


def test_bracket_prompt_extracts_core_params():
    result = parse_prompt(
        "Make a wall-mounted bracket with two M6 screw holes, 5 mm thick, 80 mm wide."
    )
    assert result.spec is not None
    spec = result.spec
    assert spec.object_type == "rectangular_bracket"
    assert spec.units == "mm"
    assert spec.dimensions["thickness"] == 5
    assert spec.dimensions["width"] == 80
    # Two M6 -> two clearance holes at 6.6mm.
    assert len(spec.holes) == 2
    assert all(abs(h.diameter - 6.6) < 1e-6 for h in spec.holes)


def test_pipe_clamp_missing_diameter_generates_with_default():
    # v0.3.8 generate-first: a missing pipe diameter is non-critical -> build with
    # the template default and surface it as an assumption (don't block).
    result = parse_prompt("I need a pipe clamp, 6mm thick.")
    assert result.spec is not None
    assert result.spec.object_type == "pipe_clamp"
    assert result.spec.dimensions["pipe_diameter"] > 0  # default applied
    assert result.assumptions


def test_pipe_clamp_with_diameter_builds_spec():
    result = parse_prompt("Wall clamp for a 25 mm pipe, 25 mm wide, two M6 holes.")
    assert result.spec is not None
    assert result.spec.object_type == "pipe_clamp"
    assert result.spec.dimensions["pipe_diameter"] == 25


def test_enclosure_type_detection():
    result = parse_prompt("electronics enclosure 100mm wide 60mm deep 40mm tall")
    assert result.spec is not None
    assert result.spec.object_type == "enclosure"


def test_inch_units_detected():
    result = parse_prompt("adapter plate 4 inch wide 4 inch deep 0.25 inch thick")
    assert result.spec is not None
    assert result.spec.units == "inch"


def test_empty_prompt_asks_for_description():
    result = parse_prompt("   ")
    assert result.spec is None
    assert result.clarification_question
