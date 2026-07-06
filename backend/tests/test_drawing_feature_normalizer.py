"""Deterministic drawing-feature normalizer (spec Parts 2, 4, 5, 6, 7).

Proves the post-processing that turns pixel-measured geometry into
callout-accurate CAD:

* concentric circle stacks snap to the EXACT Ø callouts (Ø8.2 / Ø4.8 / Ø3.6
  top, Ø2.4 / Ø1.52 lower) regardless of pixel scale drift;
* plain circular holes snap to their Ø callout;
* a printed R becomes the outer fillet;
* duplicate double-traced holes are merged;
* a rectangular slot is NEVER turned into a circle;
* an un-dimensioned planar cut on a round pipe wall is rejected (the branch
  "accidental notch" failure).
"""
from __future__ import annotations

import json
from pathlib import Path

from app.drawing.feature_normalizer import (
    snap_ir_to_callouts,
    strip_unbacked_pipe_wall_cutouts,
)
from app.drawing.sketch_ir import (
    ConcentricHoleGroup,
    CutFeature,
    MechanicalSketchIR,
    OuterProfile,
)

DATA = Path(__file__).parent / "data"
FIXTURE = DATA / "bracket_concentric_stack_ir.json"


def _ir_from_fixture() -> tuple[MechanicalSketchIR, dict]:
    spec = json.loads(FIXTURE.read_text())
    op = spec["outer_profile"]
    outer = OuterProfile(
        id=op["id"], kind=op["kind"], vertices=op["vertices"],
        bbox_mm=op["bbox_mm"], corner_radius_mm=op["corner_radius_mm"])
    groups = []
    for g in spec["concentric_groups"]:
        circles = list(g["pixel_circles_mm"])  # DESC pixel-space diameters
        groups.append(ConcentricHoleGroup(
            id=g["id"], center=list(g["center"]), circles_mm=circles,
            outer_diameter_mm=circles[0], through_hole_diameter_mm=circles[-1],
            counterbore_or_boss_diameter_mm=circles[-2] if len(circles) >= 2 else circles[0],
            group_kind="counterbore_with_through_hole"))
    cuts = [CutFeature(id=c["id"], kind=c["kind"], center=list(c["center"]),
                       width_mm=c["width_mm"], height_mm=c["height_mm"],
                       vertices=c["vertices"]) for c in spec["cut_features"]]
    ir = MechanicalSketchIR(
        outer_profiles=[outer], concentric_groups=groups,
        nested_features=[g.to_nested() for g in groups], cut_features=cuts,
        default_thickness_mm=spec["default_thickness_mm"])
    return ir, spec


# ============================================ concentric stack callout snapping

def test_top_concentric_stack_snaps_to_exact_callouts():
    ir, spec = _ir_from_fixture()
    dias = spec["callouts"]["diameters_mm"]
    snap_ir_to_callouts(ir, diameter_callouts=dias, radii_callouts=spec["callouts"]["radii_mm"])
    top = next(g for g in ir.concentric_groups if g.center[1] > 0)
    assert top.circles_mm == [8.2, 4.8, 3.6], top.circles_mm
    assert top.outer_diameter_mm == 8.2
    assert top.counterbore_or_boss_diameter_mm == 4.8
    assert top.through_hole_diameter_mm == 3.6
    assert top.associated_dimension_labels == ["Ø8.2", "Ø4.8", "Ø3.6"]
    assert top.generated_metadata.get("diameters_from_callouts") is True


def test_lower_concentric_holes_snap_to_exact_callouts():
    ir, spec = _ir_from_fixture()
    snap_ir_to_callouts(ir, diameter_callouts=spec["callouts"]["diameters_mm"])
    lower = [g for g in ir.concentric_groups if g.center[1] <= 0]
    assert len(lower) == 2
    for g in lower:
        assert g.circles_mm == [2.4, 1.52], g.circles_mm
        assert g.through_hole_diameter_mm == 1.52
        assert g.counterbore_or_boss_diameter_mm == 2.4


def test_nested_view_stays_consistent_after_snap():
    ir, spec = _ir_from_fixture()
    snap_ir_to_callouts(ir, diameter_callouts=spec["callouts"]["diameters_mm"])
    top = next(n for n in ir.nested_features if n.center[1] > 0)
    assert top.inner_diameter_mm == 3.6
    assert top.outer_diameter_mm == 4.8


def test_exact_diameters_preserved_not_pixel_estimates():
    """The whole point: pixel diameters (20.5/12/9) are discarded for the exact
    printed millimetres, never a rounded pixel estimate."""
    ir, spec = _ir_from_fixture()
    snap_ir_to_callouts(ir, diameter_callouts=spec["callouts"]["diameters_mm"])
    all_dias = [d for g in ir.concentric_groups for d in g.circles_mm]
    assert sorted(all_dias) == sorted([8.2, 4.8, 3.6, 2.4, 1.52, 2.4, 1.52])


# ============================================ rectangular slot never a circle

def test_center_rectangular_slot_survives_normalization():
    ir, spec = _ir_from_fixture()
    snap_ir_to_callouts(ir, diameter_callouts=spec["callouts"]["diameters_mm"])
    slots = [c for c in ir.cut_features if c.kind == "rectangular_slot"]
    circles = [c for c in ir.cut_features if c.kind == "circular_hole"]
    assert len(slots) == 1                 # the middle stays a rectangle
    assert circles == []                   # NO phantom central circular hole


# ============================================ fillet from R callout

def test_printed_radius_becomes_outer_fillet():
    ir, spec = _ir_from_fixture()
    assert ir.outer.corner_radius_mm is None
    snap_ir_to_callouts(ir, diameter_callouts=spec["callouts"]["diameters_mm"],
                        radii_callouts=[1.6])
    assert ir.outer.corner_radius_mm == 1.6


# ============================================ robustness / non-regression

def test_no_snap_without_callouts():
    ir, _ = _ir_from_fixture()
    before = [list(g.circles_mm) for g in ir.concentric_groups]
    snap_ir_to_callouts(ir, diameter_callouts=[])
    after = [list(g.circles_mm) for g in ir.concentric_groups]
    assert before == after


def test_out_of_range_callouts_do_not_snap():
    """Callouts nowhere near the geometry (after scale) never force a bad snap."""
    ir, _ = _ir_from_fixture()
    top_before = list(next(g for g in ir.concentric_groups if g.center[1] > 0).circles_mm)
    # A single wildly-off callout can't cover a 3-circle group → group untouched.
    snap_ir_to_callouts(ir, diameter_callouts=[500.0])
    top_after = list(next(g for g in ir.concentric_groups if g.center[1] > 0).circles_mm)
    assert top_after == top_before


def test_duplicate_circular_holes_merged():
    outer = OuterProfile(id="o", kind="polygon",
                         vertices=[[-10, -10], [10, -10], [10, 10], [-10, 10]],
                         bbox_mm={"w": 20, "h": 20})
    dup_a = CutFeature(id="h1", kind="circular_hole", center=[2.0, 2.0], diameter_mm=3.0)
    dup_b = CutFeature(id="h2", kind="circular_hole", center=[2.1, 2.05], diameter_mm=3.1)
    far = CutFeature(id="h3", kind="circular_hole", center=[-5.0, -5.0], diameter_mm=3.0)
    ir = MechanicalSketchIR(outer_profiles=[outer], cut_features=[dup_a, dup_b, far])
    snap_ir_to_callouts(ir, diameter_callouts=[])
    circles = [c for c in ir.cut_features if c.kind == "circular_hole"]
    assert len(circles) == 2               # the co-located pair collapsed to one


# ============================================ pipe-wall false-cutout rejection

class _F:
    def __init__(self, fid, kind, op="cut"):
        self.id, self.kind, self.op = fid, kind, op


class _Plan:
    def __init__(self, features):
        self.features = features
        self.assumptions = []


def test_unbacked_pipe_wall_cutout_is_stripped():
    plan = _Plan([_F("main_pipe", "pipe", op="add"),
                  _F("branch_pipe", "pipe", op="add"),
                  _F("stray_notch", "rectangular_cut", op="cut")])
    n = strip_unbacked_pipe_wall_cutouts(plan, has_cutout_callout=False)
    assert n == 1
    kinds = [f.kind for f in plan.features]
    assert "rectangular_cut" not in kinds
    assert "pipe" in kinds
    assert any("Rejected" in a for a in plan.assumptions)


def test_backed_cutout_is_kept():
    plan = _Plan([_F("main_pipe", "pipe", op="add"),
                  _F("real_slot", "slot", op="cut")])
    n = strip_unbacked_pipe_wall_cutouts(plan, has_cutout_callout=True)
    assert n == 0
    assert any(f.kind == "slot" for f in plan.features)


def test_round_bores_never_stripped():
    """Round holes/bores are legitimate on a pipe — only PLANAR cuts are suspect."""
    plan = _Plan([_F("main_pipe", "pipe", op="add"),
                  _F("bolt", "hole", op="cut")])
    n = strip_unbacked_pipe_wall_cutouts(plan, has_cutout_callout=False)
    assert n == 0
    assert any(f.kind == "hole" for f in plan.features)


# ============================ end-to-end precision: IR → CAD (spec Part 7, 10)

def test_bracket_builds_with_exact_callout_diameters():
    """The acceptance case: snapped IR compiles to a stepped concentric feature at
    Ø8.2 / Ø4.8 / Ø3.6, lower holes at Ø2.4 / Ø1.52, and the middle stays a
    rectangular cut — never a circle. STEP keeps analytic circles."""
    from app.cad.plan.compiler import export_solid
    from app.cad.plan.normalize import normalize_cad_plan
    from app.cad.plan.planner import build_and_validate
    from app.drawing.sketch_to_cad import generate_cad_from_sketch_ir

    ir, spec = _ir_from_fixture()
    snap_ir_to_callouts(ir, diameter_callouts=spec["callouts"]["diameters_mm"],
                        radii_callouts=spec["callouts"]["radii_mm"])
    plan = normalize_cad_plan(generate_cad_from_sketch_ir(ir), "sketch")

    top = next(f for f in plan.features if f.id == "group_top")
    assert top.p("diameter", 0) == 3.6                        # through-hole
    assert top.p("counterbore_diameter", 0) == 4.8            # boss / counterbore
    assert top.p("outer_ring_diameter", 0) == 8.2             # outer lobe

    for gid in ("group_lower_left", "group_lower_right"):
        g = next(f for f in plan.features if f.id == gid)
        assert g.p("diameter", 0) == 1.52
        assert g.p("counterbore_diameter", 0) == 2.4

    # The middle feature is a PLANAR cut (polygon/rectangular), never a hole.
    center = next(f for f in plan.features if f.id == "center_slot")
    assert str(center.kind).split(".")[-1] in ("polygon_cut", "rectangular_cut")
    assert not any(str(f.kind).endswith("hole") and f.id == "center_slot"
                   for f in plan.features)

    out = build_and_validate(plan)
    assert out.result.bbox_mm["x"] > 0 and out.result.bbox_mm["z"] > 0
    _stl, step, _prev = export_solid(out.result.solid)
    assert b"CIRCLE" in step                                  # analytic B-rep circles
