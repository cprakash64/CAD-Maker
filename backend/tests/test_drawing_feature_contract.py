"""Drawing feature-preservation contract (Part B): detected features are a
contract — a candidate that drops a required cut is rejected, never accepted on
a valid mesh alone."""
from __future__ import annotations

from pathlib import Path

from app.drawing.feature_contract import (
    contract_from_sketch_ir,
    evaluate_coverage,
    summary_from_plan,
)

DATA = Path(__file__).parent / "data"


def _built(name: str):
    """Build the sketch IR → plan → solid for a committed fixture."""
    from app.cad.plan.normalize import normalize_cad_plan
    from app.cad.plan.planner import build_and_validate
    from app.drawing.preprocess import preprocess_drawing_image
    from app.drawing.sketch_to_cad import generate_cad_from_sketch_ir
    from app.drawing.vectorize import build_sketch_ir

    img, _ = preprocess_drawing_image((DATA / name).read_bytes())
    ir = build_sketch_ir(img)
    plan = normalize_cad_plan(generate_cad_from_sketch_ir(ir), "sketch")
    out = build_and_validate(plan)
    return ir, plan, out.result.hole_count


def test_contract_counts_every_detected_cut():
    ir, _, _ = _built("vertical_bracket_boss_holes_cutout.png")
    c = contract_from_sketch_ir(ir)
    # Bracket: 2 circular holes + 1 rectangular cutout + 1 counterbore group.
    assert c.expected_circular_holes >= 2
    assert c.expected_rectangular_cutouts >= 1
    assert c.expected_counterbore_groups >= 1
    assert c.total_features() == len(ir.cut_features) + len(ir.nested_features)


def test_full_coverage_when_all_features_built():
    ir, plan, holes = _built("vertical_bracket_boss_holes_cutout.png")
    c = contract_from_sketch_ir(ir)
    s = summary_from_plan(plan, holes)
    cov = evaluate_coverage(c, s, ir, plan)
    assert cov.score == 1.0, cov.missing
    assert cov.acceptable is True


def test_hex_polygon_hole_is_preserved_not_a_plain_hole():
    ir, plan, holes = _built("bracket_profile_hex.png")
    c = contract_from_sketch_ir(ir)
    s = summary_from_plan(plan, holes)
    assert c.expected_polygon_holes == 1
    assert s.actual_polygon_holes == 1        # polygon_cut, not a plain hole
    cov = evaluate_coverage(c, s, ir, plan)
    assert cov.acceptable and cov.score == 1.0


def test_dropped_polygon_hole_is_rejected():
    """If a build silently drops the polygon hole, the contract rejects it —
    a valid mesh with fewer features is NOT acceptable."""
    ir, plan, holes = _built("bracket_profile_hex.png")
    c = contract_from_sketch_ir(ir)
    # Simulate a build that dropped the polygon cut feature.
    stripped = [f for f in plan.features if str(f.kind).split(".")[-1] != "polygon_cut"]
    plan.features = stripped
    s = summary_from_plan(plan, holes - 1)
    cov = evaluate_coverage(c, s, ir, plan)
    assert cov.acceptable is False
    assert "polygon_hole" in cov.reason or any("polygon" in m for m in cov.missing)


def test_dropped_rectangular_cutout_is_rejected():
    ir, plan, holes = _built("vertical_bracket_boss_holes_cutout.png")
    c = contract_from_sketch_ir(ir)
    stripped = [f for f in plan.features
                if str(f.kind).split(".")[-1] not in ("polygon_cut", "rectangular_cut")]
    plan.features = stripped
    s = summary_from_plan(plan, holes - 1)
    cov = evaluate_coverage(c, s, ir, plan)
    assert cov.acceptable is False


def test_five_features_but_three_built_is_rejected():
    """The exact field failure: IR detected 5 features, only 3 survived."""
    ir, plan, holes = _built("vertical_bracket_boss_holes_cutout.png")
    c = contract_from_sketch_ir(ir)
    # Keep only the outer profile + 2 features → coverage well below floor.
    keep = plan.features[:3]
    plan.features = keep
    s = summary_from_plan(plan, 2)
    cov = evaluate_coverage(c, s, ir, plan)
    assert cov.acceptable is False
    assert cov.matched_total < cov.expected_total
