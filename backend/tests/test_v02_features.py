"""v0.2 features: hole types, modifications, new checks, retry, explanation."""
import pytest
from pydantic import ValidationError

from app.explain import explain
from app.export.exporter import generate
from app.llm.base import LLMProvider
from app.manufacturability.checks import Severity, run_checks
from app.parsing.modification_parser import parse_and_apply
from app.parsing.prompt_parser import parse_prompt
from app.schemas.design_spec import (
    DesignModification,
    DesignSpec,
    Hole,
    HoleType,
    apply_modification,
)


def _bracket(**over):
    base = dict(
        object_type="rectangular_bracket",
        dimensions={"width": 80, "depth": 40, "thickness": 6},
        holes=[Hole(diameter=6.6, x=-25, y=0), Hole(diameter=6.6, x=25, y=0)],
    )
    base.update(over)
    return DesignSpec(**base)


# --- Hole types -----------------------------------------------------------
def test_counterbore_and_countersink_build_and_export():
    spec = DesignSpec(
        object_type="rectangular_bracket",
        dimensions={"width": 90, "depth": 50, "thickness": 6},
        holes=[
            Hole(diameter=5.5, x=-25, y=0, hole_type="counterbore",
                 counterbore_diameter=10, counterbore_depth=3),
            Hole(diameter=4.5, x=25, y=0, hole_type="countersink",
                 countersink_diameter=9),
        ],
    )
    gen = generate(spec)
    assert len(gen.stl_bytes) > 0 and gen.step_bytes[:5] == b"ISO-1"


def test_hole_type_inferred_from_feature_dims():
    h = Hole(diameter=5, x=0, y=0, counterbore_diameter=9, counterbore_depth=2)
    assert h.hole_type == HoleType.counterbore


def test_countersink_requires_larger_diameter():
    with pytest.raises(ValidationError):
        Hole(diameter=6, x=0, y=0, hole_type="countersink", countersink_diameter=5)


def test_fillet_and_chamfer_mutually_exclusive():
    with pytest.raises(ValidationError):
        _bracket(fillet_radius=2, chamfer_size=2)


# --- Modifications --------------------------------------------------------
def test_make_wider_scales_width():
    spec = _bracket()
    new = apply_modification(spec, DesignModification(scale_dimensions={"width": 1.25}))
    assert new.dimensions["width"] == pytest.approx(100.0)


def test_move_holes_apart_spreads_them():
    spec = _bracket()
    new = apply_modification(spec, DesignModification(hole_spread_factor=1.3))
    assert new.holes[1].x == pytest.approx(32.5)


def test_set_wall_thickness_absolute():
    spec = DesignSpec(
        object_type="enclosure",
        dimensions={"width": 100, "depth": 60, "height": 40, "wall_thickness": 2.5},
    )
    new = apply_modification(spec, DesignModification(set_dimensions={"wall_thickness": 4}))
    assert new.dimensions["wall_thickness"] == 4


def test_add_rounded_edges_sets_fillet():
    spec = _bracket()
    new = apply_modification(spec, DesignModification(set_fillet_radius=3))
    assert new.fillet_radius == 3


@pytest.mark.parametrize(
    "prompt,checker",
    [
        ("make it wider", lambda s, o: o.dimensions["width"] > s.dimensions["width"]),
        ("make it 120mm wide", lambda s, o: o.dimensions["width"] == 120),
        ("move the holes farther apart", lambda s, o: abs(o.holes[1].x) > abs(s.holes[1].x)),
        ("add rounded edges", lambda s, o: (o.fillet_radius or 0) > 0),
    ],
)
def test_modification_prompts_via_mock(prompt, checker):
    spec = _bracket()
    result = parse_and_apply(prompt, spec)
    assert result.spec is not None, f"{prompt!r} produced no spec"
    assert checker(spec, result.spec)
    # And it still builds.
    gen = generate(result.spec)
    assert len(gen.stl_bytes) > 0


def test_nonsense_modification_asks_for_clarification():
    result = parse_and_apply("make it fly", _bracket())
    assert result.spec is None
    assert result.clarification_question


def test_modification_regenerates_different_model():
    spec = _bracket()
    a = generate(spec)
    new = parse_and_apply("make it 120mm wide", spec).spec
    b = generate(new)
    assert a.spec_hash != b.spec_hash and a.stl_bytes != b.stl_bytes


# --- New manufacturability checks ----------------------------------------
def _check(results, name):
    return next(r for r in results if r.check == name)


def test_hole_spacing_warns_when_too_close():
    spec = DesignSpec(
        object_type="rectangular_bracket",
        dimensions={"width": 80, "depth": 40, "thickness": 5},
        holes=[Hole(diameter=10, x=-3, y=0), Hole(diameter=10, x=3, y=0)],
    )
    res = _check(run_checks(spec), "hole_spacing_0_1")
    assert res.passed is False and res.severity == Severity.warning


def test_material_assumption_always_present():
    assert any(r.check == "material_assumption" for r in run_checks(_bracket()))


def test_print_min_feature_error_for_sub_mm_wall():
    spec = DesignSpec(
        object_type="rectangular_bracket",
        manufacturing_method="fdm_3d_print",
        dimensions={"width": 60, "depth": 30, "thickness": 0.6},
    )
    res = _check(run_checks(spec), "print_min_feature")
    assert res.passed is False and res.severity == Severity.error


# --- Retry / repair path --------------------------------------------------
class _BadThenGoodProvider(LLMProvider):
    name = "badgood"

    def parse_prompt(self, prompt: str) -> dict:
        # Invalid: negative dimension.
        return {"object_type": "rectangular_bracket", "dimensions": {"width": -5}}

    def repair(self, prompt: str, previous: dict, errors: str) -> dict | None:
        assert "width" in errors
        return {"object_type": "rectangular_bracket", "dimensions": {"width": 80}}


def test_validation_failure_retries_once_then_succeeds():
    result = parse_prompt("bracket", provider=_BadThenGoodProvider())
    assert result.spec is not None
    assert result.spec.dimensions["width"] == 80


class _AlwaysBadProvider(LLMProvider):
    name = "bad"

    def parse_prompt(self, prompt: str) -> dict:
        return {"object_type": "rectangular_bracket", "dimensions": {"width": -5}}


def test_validation_failure_without_repair_returns_clarification():
    result = parse_prompt("bracket", provider=_AlwaysBadProvider())
    assert result.spec is None and result.clarification_question


# --- Explanation ----------------------------------------------------------
def test_explanation_mentions_type_and_holes():
    text = explain(_bracket())
    assert "bracket" in text.lower()
    assert "hole" in text.lower()
    assert "PLA" in text or "pla" in text.lower()
