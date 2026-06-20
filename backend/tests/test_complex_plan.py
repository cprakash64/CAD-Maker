"""P0-4: complex-CAD planning + long-prompt routing (esp. the crankshaft)."""
from app.cad.templates.crankshaft import crankshaft_summary
from app.export.exporter import generate
from app.parsing.complex_plan import build_complex_plan, classify_intent, extract_metadata
from app.parsing.prompt_parser import parse_prompt
from app.schemas.complex_cad import CADIntentKind

LONG_CRANKSHAFT_PROMPT = (
    "Create a realistic mechanical crankshaft 3D model for a 4-cylinder inline "
    "internal combustion engine. The crankshaft should be approximately 420 mm long "
    "and oriented horizontally along the X axis. It must have five main bearing "
    "journals aligned on the central rotational axis and four connecting-rod journals "
    "offset from the axis by the throw radius. The rod journals should follow the "
    "standard inline-four phasing so that the outer pair and inner pair are 180 "
    "degrees apart. Include eight crank webs connecting the main journals to the rod "
    "journals, with visible counterweights on the opposite side of each rod journal "
    "for balance. At the front, add a keyed snout for the timing gear and pulley, "
    "with a keyway. At the rear, add a flywheel mounting flange with six bolt holes "
    "arranged on a bolt circle and a central pilot bore. The part should be machined "
    "from forged steel, with smooth fillets where appropriate for stress relief. "
    "Please make it look realistic and polished, like a studio render, with a "
    "brushed metal material. " + ("Additional engineering context applies here. " * 700)
)


def test_long_crankshaft_prompt_is_over_2000_words():
    assert len(LONG_CRANKSHAFT_PROMPT.split()) > 2000


def test_long_crankshaft_prompt_routes_to_crankshaft():
    result = parse_prompt(LONG_CRANKSHAFT_PROMPT)
    assert result.spec is not None
    assert result.spec.object_type == "inline_4_crankshaft"


def test_long_crankshaft_prompt_builds_and_validates_geometry():
    spec = parse_prompt(LONG_CRANKSHAFT_PROMPT).spec
    summary = crankshaft_summary(spec)
    assert summary["main_journal_count"] == 5
    assert summary["rod_journal_count"] == 4
    assert summary["web_count"] == 8
    assert summary["flange_bolt_count"] == 6
    gen = generate(spec)
    assert len(gen.stl_bytes) > 0 and gen.step_bytes[:5] == b"ISO-1"
    assert gen.bounding_box_mm["x"] > gen.bounding_box_mm["y"]  # X-oriented


def test_classify_intent_routes():
    assert classify_intent(LONG_CRANKSHAFT_PROMPT).kind == CADIntentKind.advanced_template
    assert classify_intent("bracket 80x40x5mm with two M6 holes").kind == (
        CADIntentKind.simple_template
    )
    assert classify_intent("a flanged pipe branch 90mm main pipe").template_candidate == (
        "flanged_pipe_branch"
    )
    assert classify_intent("xyzzy quantum flux capacitor please").kind == (
        CADIntentKind.unsupported
    )


def test_metadata_separates_material_and_visual():
    meta = extract_metadata(LONG_CRANKSHAFT_PROMPT)
    assert "steel" in meta["materials"]
    assert any(v in meta["visual_notes"] for v in ("polished", "render", "brushed"))


def test_visual_notes_do_not_break_generation():
    # The visual/material styling must not affect or break CAD generation.
    spec = parse_prompt(LONG_CRANKSHAFT_PROMPT).spec
    assert spec.object_type == "inline_4_crankshaft"
    assert generate(spec).preview.triangle_count > 0


def test_complex_plan_for_crankshaft():
    plan = build_complex_plan(LONG_CRANKSHAFT_PROMPT)
    assert plan.classification.kind == CADIntentKind.advanced_template
    assert plan.template_object_type == "inline_4_crankshaft"
    assert "steel" in plan.materials
    assert plan.visual_notes  # captured as metadata only


def test_complex_plan_unsupported_asks_clarification():
    plan = build_complex_plan("make me an interdimensional warp nacelle")
    assert plan.classification.kind == CADIntentKind.unsupported
    assert plan.clarification_question
