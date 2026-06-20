"""v0.4-GEN: schema robustness, GenerationRouter, feature-graph v2, self-repair,
restricted SCAD generator, and the 10 success-criteria prompts."""
import pytest

from app.cad import fallback_graphs as fg
from app.cad.base import CadGenerationError
from app.cad.feature_graph import build_feature_graph
from app.export.exporter import generate
from app.generation.router import route_prompt
from app.generation.scad_generate import plan_to_design
from app.generation.scad_runner import lint_scad, scad_source_from_plan
from app.generation.self_check import repair_spec
from app.parsing.complex_plan import looks_complex, plan_prompt
from app.schemas.complex_cad import CADFeatureGraph
from app.schemas.design_spec import DesignSpec, Hole
from app.schemas.drawing_spec import DrawingInterpretationSpec
from app.schemas.generation import GeneralCADPlan, GenerationRouteKind
from app.services.design_service import _plan_long_prompt


# --- 1/6 schema robustness ------------------------------------------------
@pytest.mark.parametrize("bad,expected", [(None, 90.0), ("Ø90", 90.0), (45, 60.0), (200, 140.0)])
def test_countersink_angle_repaired_not_rejected(bad, expected):
    h = Hole(diameter=5, x=0, y=0, hole_type="countersink", countersink_diameter=10,
             countersink_angle=bad)
    assert h.countersink_angle == expected


def test_dimensions_accept_numeric_strings():
    s = DesignSpec(object_type="rectangular_bracket",
                   dimensions={"width": "80", "depth": "approx 40mm", "thickness": "Ø5", "junk": "n/a"})
    assert s.dimensions == {"width": 80.0, "depth": 40.0, "thickness": 5.0}


def test_drawing_overall_vertical_height_string_does_not_break():
    d = DrawingInterpretationSpec(
        suggested_object_type="rectangular_bracket",
        overall_dimensions={"overall_vertical_height": "approx 90mm", "width": "80", "bad": "tbd"},
        overall_confidence=0.8,
    )
    assert d.overall_dimensions == {"overall_vertical_height": 90.0, "width": 80.0}


def test_drill_jig_countersink_prompt_generates():
    # A drill jig whose holes carry a (previously fatal) countersink_angle.
    spec = DesignSpec(
        object_type="drill_jig",
        dimensions={"length": 120, "width": 80, "thickness": 6, "hole_diameter": 6, "hole_spacing": 25},
        holes=[Hole(diameter=6, x=-20, y=0, hole_type="countersink", countersink_diameter=11,
                    countersink_angle=None)],
    )
    assert len(generate(spec).stl_bytes) > 0


# --- 1 GenerationRouter ---------------------------------------------------
@pytest.mark.parametrize("prompt,kind", [
    ("a mounting bracket 80mm wide 40mm deep 5mm thick", GenerationRouteKind.precision_template),
    ("a simple bearing housing for a 20mm shaft", GenerationRouteKind.feature_graph),
    ("a hexagonal spacer with a 6mm through hole", GenerationRouteKind.feature_graph),
    ("a decorative dragon statue", GenerationRouteKind.clarification),
])
def test_router_decisions(prompt, kind):
    assert route_prompt(prompt).route == kind


# --- 7 gear self-repair ---------------------------------------------------
def test_repair_adds_gear_teeth_when_model_forgot():
    spec = DesignSpec(object_type="simple_gear_or_pulley", dimensions={"outer_diameter_mm": 60})
    repaired, notes = repair_spec("a spur gear with a 10mm shaft", spec)
    assert repaired.dimensions["tooth_count"] == 24
    assert repaired.dimensions["bore_diameter_mm"] == 10
    assert notes


def test_repair_hexagonal_gear_uses_hex_not_pulley():
    spec = DesignSpec(object_type="simple_gear_or_pulley", dimensions={"outer_diameter_mm": 60})
    repaired, _ = repair_spec("a hexagonal gear with a 10mm shaft", spec)
    assert repaired.dimensions["hex"] == 1.0 and repaired.dimensions.get("tooth_count", 0) == 0


def test_hex_gear_via_pipeline_not_plain_disk():
    r = plan_prompt("a hexagonal gear with a 10mm shaft")
    assert r.spec.object_type == "simple_gear_or_pulley"
    assert r.spec.dimensions.get("hex") == 1.0
    assert len(generate(r.spec).stl_bytes) > 0


def test_pulley_is_grooved_no_teeth():
    r = plan_prompt("a pulley with a 10mm shaft hole and 60mm outer diameter")
    assert "tooth_count" not in r.spec.dimensions and r.spec.dimensions["bore_diameter_mm"] == 10


def test_spur_gear_has_teeth_and_bore():
    r = plan_prompt("spur gear with 32 teeth and 8mm bore")
    assert r.spec.dimensions["tooth_count"] == 32 and r.spec.dimensions["bore_diameter_mm"] == 8


# --- 3 feature graph v2 ---------------------------------------------------
def test_feature_graph_v2_ops_build():
    g = CADFeatureGraph(operations=[
        {"op": "tube", "id": "t", "params": {"radius": 15, "inner_radius": 8, "height": 20}},
        {"op": "box", "id": "b", "params": {"width": 40, "depth": 40, "height": 8}, "at": (0, 0, -4)},
        {"op": "union", "id": "u", "target": "b", "tool": "t"},
        {"op": "counterbore", "id": "cb", "target": "u",
         "params": {"radius": 3, "depth": 10, "cap_radius": 6, "cap_depth": 3}, "at": (15, 0, -4)},
        {"op": "slot", "id": "sl", "target": "cb", "params": {"length": 20, "width": 5, "depth": 10},
         "at": (-12, 0, -4)},
    ], result_id="sl")
    assert build_feature_graph(g).val().tessellate(0.4)[1]


def test_flange_plate_and_shaft_collar_build():
    for graph in (fg.flange_plate(bolt_count=8, bolt_circle=100), fg.shaft_collar(bore=12)):
        spec = DesignSpec(object_type="feature_graph", feature_graph=graph)
        assert len(generate(spec).stl_bytes) > 0


# --- 4 restricted SCAD generator (no binary needed for these) -------------
def test_scad_source_from_plan_and_lint_ok():
    plan = GeneralCADPlan(primitives=[
        {"kind": "box", "id": "b", "params": {"width": 40, "depth": 40, "height": 20}},
        {"kind": "cylinder", "id": "c", "params": {"radius": 5, "height": 30}, "op": "subtract"},
    ])
    src = scad_source_from_plan(plan)
    assert "cube(" in src and "difference()" in src
    lint_scad(src)  # must not raise


def test_scad_lint_rejects_forbidden_tokens():
    for bad in ["include <x.scad>", "import(\"a.stl\");", "use <lib>", "surface(\"f.png\")"]:
        with pytest.raises(CadGenerationError):
            lint_scad(bad)


def test_general_plan_compiles_to_buildable_design():
    plan = {"object_name": "block", "units": "mm",
            "primitives": [{"kind": "box", "id": "b", "params": {"width": 40, "depth": 40, "height": 20}}],
            "holes": [{"diameter": 10, "x": 0, "y": 0}]}
    result = plan_to_design(plan)
    assert result.spec is not None and result.spec.object_type == "feature_graph"
    assert len(generate(result.spec).stl_bytes) > 0
    assert result.export_formats == ["stl", "step"]


# --- success-criteria prompts all generate --------------------------------
SUCCESS = [
    "Drill jig plate 120mm by 80mm, 6mm thick, with 6mm guide holes spaced 25mm and a registration lip.",
    "a hexagonal gear with a 10mm shaft",
    "a pulley with a 10mm shaft hole and 60mm outer diameter",
    "a simple bearing housing for a 20mm shaft",
    "a rectangular block with a stepped slot and two counterbored holes",
    "a hexagonal spacer with a 6mm through hole",
    "a 90 degree pipe elbow with circular flanges",
    "a flange plate with 8 holes on a 100mm bolt circle",
    "a shaft collar with an M6 clamp screw",
]


@pytest.mark.parametrize("prompt", SUCCESS)
def test_success_prompts_generate(prompt):
    r = _plan_long_prompt(prompt) if looks_complex(prompt) else plan_prompt(prompt)
    assert r.spec is not None, f"{prompt!r} did not generate"
    gen = generate(r.spec)
    assert len(gen.stl_bytes) > 0 and gen.step_bytes[:5] == b"ISO-1"


def test_flange_plate_routes_to_feature_graph():
    r = plan_prompt("a flange plate with 8 holes on a 100mm bolt circle")
    assert r.spec.object_type == "feature_graph"
    ops = [o["op"] for o in r.spec.feature_graph["operations"]]
    assert ops.count("cut_hole") >= 8  # 8 bolt holes


# --- drawing hint generates -----------------------------------------------
def test_flanged_pipe_hint_generates():
    from app.drawing.interpret import interpret_image, to_design_spec
    from app.llm.mock_provider import MockLLMProvider
    interp = interpret_image(
        b"x" * 300, "image/png", provider=MockLLMProvider(),
        hint="This is a flanged pipe branch with 12 holes per flange, 90mm main pipe, "
        "50mm side branch, 10mm wall thickness, 15mm flange thickness.",
    )
    assert interp.suggested_object_type == "flanged_pipe_branch" and interp.is_actionable()
    spec = to_design_spec(interp)
    assert spec is not None and len(generate(spec).stl_bytes) > 0
