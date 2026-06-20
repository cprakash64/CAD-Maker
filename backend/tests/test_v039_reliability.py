"""v0.3.9: schema limits/repair, gear rework, feature-graph fallback, routing."""
import pytest

from app.cad import fallback_graphs as fg
from app.cad.base import CadGenerationError
from app.cad.feature_graph import build_feature_graph
from app.drawing.interpret import interpret_image, _sanitize_drawing_raw
from app.export.exporter import generate
from app.llm.mock_provider import MockLLMProvider
from app.parsing.complex_plan import plan_prompt
from app.parsing.prompt_parser import parse_prompt
from app.schemas.complex_cad import CADFeatureGraph
from app.schemas.design_spec import DesignSpec
from app.schemas.drawing_spec import DrawingInterpretationSpec


# --- Schema over-strictness ----------------------------------------------
def test_long_material_normalized_not_rejected():
    s = DesignSpec(object_type="inline_4_crankshaft",
                   material="machined from forged 4340 steel, polished, " * 5)
    assert s.material == "steel"  # normalized, not a ValidationError


def test_long_drawing_label_sanitized_not_unknown():
    raw = {
        "views": [{"view_type": "top",
                   "dimensions": [{"label": "L" * 600, "value": 80}]}],
        "suggested_object_type": "rectangular_bracket",
        "overall_dimensions": {"width": 80, "depth": 40, "thickness": 5},
        "overall_confidence": 0.85,
    }
    interp = DrawingInterpretationSpec(**_sanitize_drawing_raw(raw))
    assert len(interp.views[0].dimensions[0].label) <= 256
    assert interp.suggested_object_type == "rectangular_bracket"


def test_drawing_repair_pass_keeps_partial(monkeypatch):
    # Provider returns over-long fields; interpret_image must repair, not 0%.
    class P(MockLLMProvider):
        def interpret_drawing(self, image_b64, media_type="image/png", hint=None):
            return {
                "suggested_object_type": "rectangular_bracket",
                "overall_dimensions": {"width": 80, "depth": 40, "thickness": 5},
                "overall_confidence": 0.85,
                "views": [{"view_type": "top",
                           "dimensions": [{"label": "Z" * 900, "value": 10}]}],
            }
    interp = interpret_image(b"x" * 300, "image/png", provider=P())
    assert interp.suggested_object_type == "rectangular_bracket"
    assert interp.overall_confidence >= 0.75


# --- Generate-first must-build prompts ------------------------------------
@pytest.mark.parametrize("prompt", [
    "Drill jig plate 120mm by 80mm, 6mm thick, with 6mm guide holes spaced 25mm and a registration lip.",
    "a hexagonal gear with a 10mm shaft",
    "a pulley with a 10mm shaft hole and 60mm outer diameter",
    "a small bracket with 3 holes",
    "a simple bearing housing for a 20mm shaft",
    "a 90 degree pipe elbow with circular flanges",
])
def test_generation_first_prompts_build(prompt):
    result = plan_prompt(prompt)
    assert result.spec is not None, f"{prompt!r} should generate"
    gen = generate(result.spec)
    assert len(gen.stl_bytes) > 0 and gen.step_bytes[:5] == b"ISO-1"


# --- Gear vs pulley -------------------------------------------------------
def test_hex_gear_is_not_a_plain_pulley():
    r = plan_prompt("a hexagonal gear with a 10mm shaft")
    assert r.spec.object_type == "simple_gear_or_pulley"
    assert r.spec.dimensions.get("hex") == 1.0
    assert r.spec.dimensions.get("tooth_count", 0) == 0  # not the pulley-groove default either


def test_pulley_makes_a_pulley():
    r = plan_prompt("a pulley with a 10mm shaft hole and 60mm outer diameter")
    assert r.spec.object_type == "simple_gear_or_pulley"
    assert "tooth_count" not in r.spec.dimensions and r.spec.dimensions["bore_diameter_mm"] == 10


def test_spur_gear_has_teeth_and_bore():
    r = plan_prompt("spur gear with 32 teeth and 8mm bore")
    assert r.spec.dimensions["tooth_count"] == 32 and r.spec.dimensions["bore_diameter_mm"] == 8


# --- Feature-graph fallback ----------------------------------------------
def test_bearing_housing_routes_to_feature_graph():
    r = plan_prompt("a simple bearing housing for a 20mm shaft")
    assert r.spec.object_type == "feature_graph"
    assert r.spec.feature_graph is not None
    assert len(generate(r.spec).stl_bytes) > 0


def test_hex_spacer_feature_graph_has_hex_prism():
    r = plan_prompt("a hexagonal spacer with a 6mm through hole")
    assert r.spec.object_type == "feature_graph"
    ops = {o["op"] for o in r.spec.feature_graph["operations"]}
    assert "hex_prism" in ops and "cut_hole" in ops


def test_feature_graph_new_ops_build():
    graph = CADFeatureGraph(operations=[
        {"op": "hex_prism", "id": "h", "params": {"diameter": 20, "height": 10}},
        {"op": "rectangular_cutout", "id": "s", "target": "h",
         "params": {"width": 4, "depth": 30, "height": 12}, "at": (0, 0, 5)},
        {"op": "translate", "id": "t", "source": "s", "params": {"dz": 0}},
    ], result_id="t")
    assert build_feature_graph(graph).val().tessellate(0.4)[1]


def test_feature_graph_rejects_unknown_op():
    with pytest.raises(CadGenerationError):
        build_feature_graph(CADFeatureGraph(operations=[{"op": "import_os", "id": "x"}]))


def test_unsupported_decorative_prompt_clarifies():
    r = plan_prompt("a beautiful decorative dragon statue figurine")
    assert r.spec is None and r.clarification_question


# --- Crankshaft long prompt (full, not shortened) ------------------------
LONG_CRANK = (
    "Create a realistic mechanical crankshaft 3D model for a 4-cylinder inline "
    "internal combustion engine, approximately 420 mm long, oriented along the X "
    "axis. Five main bearing journals on the central axis, four connecting-rod "
    "journals offset by the throw radius with standard inline-four phasing, eight "
    "counterweighted crank webs, a keyed front snout for the timing gear, and a "
    "rear flywheel mounting flange with six bolt holes on a bolt circle plus a "
    "central pilot. Machined from forged 4340 steel, heat-treated and polished to "
    "a fine finish, shown as a realistic studio render with brushed-metal material "
    "and soft lighting. " + ("Extra descriptive engineering and styling context. " * 60)
)


def test_full_crankshaft_long_prompt_generates():
    from app.parsing.complex_plan import looks_complex
    assert looks_complex(LONG_CRANK)
    from app.services.design_service import _plan_long_prompt
    result = _plan_long_prompt(LONG_CRANK)
    assert result.spec is not None and result.spec.object_type == "inline_4_crankshaft"
    from app.cad.templates.crankshaft import crankshaft_summary
    s = crankshaft_summary(result.spec)
    assert s["main_journal_count"] == 5 and s["rod_journal_count"] == 4
    assert s["web_count"] == 8 and s["flange_bolt_count"] == 6
    gen = generate(result.spec)
    assert len(gen.stl_bytes) > 0 and gen.step_bytes[:5] == b"ISO-1"


# --- Drawing hint routing -------------------------------------------------
def test_flanged_pipe_hint_routes_and_builds():
    from app.drawing.interpret import to_design_spec
    interp = interpret_image(
        b"x" * 300, "image/png", provider=MockLLMProvider(),
        hint="This is a flanged pipe branch with 12 holes per flange, 90mm main "
        "pipe, 50mm side branch, 10mm wall thickness, 15mm flange thickness.",
    )
    assert interp.suggested_object_type == "flanged_pipe_branch"
    assert interp.is_actionable()
    spec = to_design_spec(interp)
    assert spec is not None and len(generate(spec).stl_bytes) > 0
