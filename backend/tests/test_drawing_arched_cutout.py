"""Arched / curved-top window cutout must NEVER collapse to a circular hole
(spec: the second internal cutout from the top on the bracket drawing).

The failure: an arched rectangular window (flat bottom + vertical sides + arc
top) traced to >=10 arc segments and the vertex-count roundness test read it as
a circle. Roundness is now a geometric circle fit (radial residual + no long
straight edge + ~360° coverage), so a flat-bottomed window is classified
``arched_rectangular_cutout`` and cut as its true profile.
"""
from __future__ import annotations

from pathlib import Path

DATA = Path(__file__).parent / "data"
ARCHED = "bracket_arched_window.png"


def _ir():
    from app.drawing.vectorize import build_sketch_ir

    return build_sketch_ir((DATA / ARCHED).read_bytes())


# ============================================ vectorizer classification

def test_middle_cutout_is_arched_not_circular():
    ir = _ir()
    arched = [c for c in ir.cut_features if c.kind == "arched_rectangular_cutout"]
    assert len(arched) == 1, [c.kind for c in ir.cut_features]
    mid = arched[0]
    assert mid.center[1] > 0                      # upper-middle of the plate
    assert mid.kind != "circular_hole"
    assert len(mid.vertices) >= 5                 # real traced profile is kept


def test_lower_holes_still_circular_no_regression():
    ir = _ir()
    circles = [c for c in ir.cut_features if c.kind == "circular_hole"]
    assert len(circles) == 2                       # the two lower round holes
    assert all(c.center[1] < 0 for c in circles)   # both in the lower body


def test_top_concentric_group_survives():
    ir = _ir()
    assert len(ir.concentric_groups) >= 1
    top = [g for g in ir.concentric_groups if g.center[1] > 0]
    assert top and top[0].circle_count >= 2


def test_no_circular_hole_in_middle_body():
    """The exact bug: NO circular hole may appear where the arched window is."""
    ir = _ir()
    mid_circles = [c for c in ir.cut_features
                   if c.kind == "circular_hole" and c.center[1] > 0]
    assert mid_circles == []


# ============================================ IR → CAD (profile cut, not cylinder)

def test_arched_cut_builds_as_profile_not_hole():
    from app.cad.plan.normalize import normalize_cad_plan
    from app.cad.plan.planner import build_and_validate
    from app.drawing.sketch_to_cad import generate_cad_from_sketch_ir

    ir = _ir()
    arched = next(c for c in ir.cut_features if c.kind == "arched_rectangular_cutout")
    plan = normalize_cad_plan(generate_cad_from_sketch_ir(ir), "sketch")
    feat = next(f for f in plan.features if f.id == arched.id)
    kind = str(feat.kind).split(".")[-1]
    assert kind == "polygon_cut"                   # profile cut, NOT a hole/cylinder
    assert kind not in ("hole", "counterbore")
    assert feat.profile and len(feat.profile) >= 5
    out = build_and_validate(plan)
    assert out.result.bbox_mm["z"] > 0             # compiles to a real solid


# ============================================ feature contract (shape class)

def test_contract_expects_arched_and_flags_middle_not_circular():
    from app.drawing.feature_contract import (
        contract_from_sketch_ir,
        generated_summary_from_ir,
    )

    ir = _ir()
    c = contract_from_sketch_ir(ir)
    assert c.expected_arched_cutouts == 1
    g = generated_summary_from_ir(ir)
    assert g["arched_rectangular_cutouts"] == 1
    assert g["middle_circular_hole"] is False


def test_contract_rejects_circularized_middle_cutout():
    """If the build turned the arched window into a plain circular hole, the
    contract must REJECT it (shape class, not just hole count)."""
    from app.cad.plan.schema import CadPlan, Feature
    from app.drawing.feature_contract import (
        contract_from_sketch_ir,
        evaluate_coverage,
        summary_from_plan,
    )

    ir = _ir()
    arched = next(c for c in ir.cut_features if c.kind == "arched_rectangular_cutout")
    contract = contract_from_sketch_ir(ir)

    # A bad plan: the arched window built as a round hole (the field bug).
    bad = CadPlan(object_type="reconstructed_sketch_part", name="bad", features=[
        Feature(id="outer_profile", kind="extruded_profile", params={"thickness": 4},
                profile=[[-6, -9], [6, -9], [6, 9], [-6, 9]]),
        Feature(id=arched.id, kind="hole", op="cut", params={"diameter": 6},
                at=[0, 5, 0], through=True),
    ])
    cov = evaluate_coverage(contract, summary_from_plan(bad, 1))
    assert cov.acceptable is False
    assert "arched" in cov.reason or "major feature" in cov.reason


# ============================================ raster classifier unit

def test_raster_classifier_distinguishes_arched_from_circle():
    """Direct unit: a synthetic circle stays round; a flat-bottomed arched window
    does not — guards both the false-positive and false-negative directions."""
    import numpy as np

    from app.drawing.raster_profile import _classify_hole

    def region(mask):
        ys, xs = np.nonzero(mask)
        return {"area": int(mask.sum()), "mask": mask,
                "bbox": (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))}

    # A filled circle.
    yy, xx = np.mgrid[0:80, 0:80]
    circle = (xx - 40) ** 2 + (yy - 40) ** 2 <= 30 ** 2
    assert _classify_hole(np, region(circle), 0, 80).is_round is True

    # A filled arched window: flat bottom, vertical sides, semicircular top.
    wy, wx = np.mgrid[0:90, 0:80]
    win = np.zeros((90, 80), dtype=bool)
    win[45:80, 15:65] = True                       # rectangular lower body
    top = ((wx - 40) ** 2 + (wy - 45) ** 2 <= 25 ** 2) & (wy <= 45)
    win |= top                                      # semicircle cap on top
    hole = _classify_hole(np, region(win), 0, 90)
    assert hole.is_round is False
    assert hole.kind in ("arched_rectangular_cutout", "arbitrary_polygon")
