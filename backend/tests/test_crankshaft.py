"""Inline-4 crankshaft template: routing, export, geometry sanity."""
from app.cad.templates.crankshaft import crankshaft_summary
from app.export.exporter import generate
from app.parsing.prompt_parser import parse_prompt
from app.schemas.design_spec import DesignSpec

PROMPT = (
    "Create a realistic mechanical crankshaft 3D model for a 4-cylinder inline "
    "internal combustion engine, about 420mm long."
)


def test_crankshaft_prompt_routes_to_template():
    result = parse_prompt(PROMPT)
    assert result.spec is not None
    assert result.spec.object_type == "inline_4_crankshaft"


def test_crankshaft_exports_stl_and_step():
    spec = parse_prompt(PROMPT).spec
    gen = generate(spec)
    assert len(gen.stl_bytes) > 0
    assert gen.step_bytes[:5] == b"ISO-1"
    assert gen.preview.triangle_count > 0


def test_crankshaft_geometry_sanity():
    spec = DesignSpec(object_type="inline_4_crankshaft", dimensions={})
    summary = crankshaft_summary(spec)
    assert summary["main_journal_count"] == 5
    assert summary["rod_journal_count"] == 4
    assert summary["web_count"] == 8
    assert summary["flange_bolt_count"] == 6
    assert summary["counterweights"] is True
    assert summary["phases"] == [0, 180, 180, 0]


def test_crankshaft_oriented_along_x():
    spec = DesignSpec(object_type="inline_4_crankshaft", dimensions={})
    bb = generate(spec).bounding_box_mm
    # Horizontal: the longest extent is the X (rotational) axis.
    assert bb["x"] > bb["y"] and bb["x"] > bb["z"]
    # Throw + counterweights give meaningful off-axis extent.
    assert bb["y"] > spec.dimensions.get("throw_radius_mm", 45)


def test_crankshaft_custom_bolt_count():
    spec = DesignSpec(
        object_type="inline_4_crankshaft", dimensions={"flywheel_bolt_count": 8}
    )
    assert crankshaft_summary(spec)["flange_bolt_count"] == 8
    gen = generate(spec)
    assert len(gen.stl_bytes) > 0
