"""Silhouette-only rejection gate + route separation + key recess (spec Parts
1, 2, 6, 7, 10).

A dimensioned mechanical drawing (Ø/R callouts, repeated-hole notation, inner
opening, internal dark channel) must NOT be silently accepted as a feature-less
silhouette disk/blob; a logo/icon must NOT be gated. The key's internal dark
channel must become a recess, so the key is not one solid silhouette.
"""
from __future__ import annotations

from pathlib import Path

from app.drawing.silhouette_gate import (
    GeneratedShape,
    classify_drawing_route,
    evaluate_feature_coverage,
    parse_dimension_evidence,
)

DATA = Path(__file__).parent / "data"
KEY = "key_channel.png"

# Callouts from the three field drawings.
BRACKET_CALLOUTS = ["Ø100", "R64", "4xØ14", "4xR13", "R60", "Ø20", "Ø19",
                    "Ø44", "R25", "170", "95", "72", "62"]
FLANGE_CALLOUTS = ["R56", "R50", "8xR8", "8xR6", "8xR12", "All fillet radii 2"]
LOGO_TEXTS = ["USB", "logo"]


# ============================================ dimension evidence (Part 6)

def test_repeated_diameter_and_radius_callouts_parsed():
    ev = parse_dimension_evidence(BRACKET_CALLOUTS)
    assert (4, 14.0) in ev.repeated_diameters      # 4xØ14 → 4 bolt holes
    assert (4, 13.0) in ev.repeated_radii          # 4xR13 → 4 bosses
    assert ev.expected_hole_count >= 4
    assert ev.has_inner_opening is True            # Ø100 outer + smaller circles


def test_repeated_radius_and_global_fillet_parsed():
    ev = parse_dimension_evidence(FLANGE_CALLOUTS)
    assert sorted(ev.repeated_radii) == [(8, 6.0), (8, 8.0), (8, 12.0)]
    assert ev.global_fillet == 2.0
    assert ev.expected_repeated_features == 24


def test_logo_text_is_not_dimensioned_mechanical():
    ev = parse_dimension_evidence(LOGO_TEXTS)
    assert ev.is_dimensioned_mechanical() is False


# ============================================ route separation (Part 1)

def test_route_bracket_is_mechanical_not_logo():
    ev = parse_dimension_evidence(BRACKET_CALLOUTS)
    assert classify_drawing_route(ev) == "annular_bracket_or_flange_plate"


def test_route_logo_is_profile_extrusion():
    ev = parse_dimension_evidence(LOGO_TEXTS)
    assert classify_drawing_route(ev) == "logo_profile_extrusion"


def test_route_key_channel_when_dark_region():
    ev = parse_dimension_evidence([])
    assert classify_drawing_route(ev, has_internal_dark_channel=True) \
        == "key_or_channel_plate"


# ==================================== Test 1: complex bracket not silhouette

def test_complex_bracket_not_silhouette_circle():
    ev = parse_dimension_evidence(BRACKET_CALLOUTS)
    # The failing output: one solid disk, no holes, no arm, no bosses.
    disk = GeneratedShape(hole_count=0, distinct_circular_features=1,
                          has_internal_cutout=False, is_single_disk=True)
    v = evaluate_feature_coverage(ev, disk)
    assert v.silhouette_only is True                 # rejected, not accepted
    assert v.status == "review"
    assert any("inner" in m for m in v.missing)
    assert any("hole" in m for m in v.missing)
    # A model that actually built the features passes.
    good = GeneratedShape(hole_count=7, distinct_circular_features=9,
                          has_internal_cutout=True, is_single_disk=False)
    assert evaluate_feature_coverage(ev, good).silhouette_only is False


# ==================================== Test 3: circular cover inner + bolts

def test_circular_cover_inner_opening_and_bolt_pattern():
    ev = parse_dimension_evidence(FLANGE_CALLOUTS)
    blob = GeneratedShape(hole_count=0, distinct_circular_features=1,
                          has_internal_cutout=False, is_single_disk=True)
    v = evaluate_feature_coverage(ev, blob)
    assert v.silhouette_only is True
    assert any("inner" in m for m in v.missing)
    assert any("repeated" in m or "pattern" in m for m in v.missing)
    assert v.scores.internal_cutout_score == 0.0


# ==================================== Test 4: USB logo not regressed

def test_usb_logo_profile_mode_not_regressed():
    ev = parse_dimension_evidence(LOGO_TEXTS)
    assert classify_drawing_route(ev) == "logo_profile_extrusion"
    # A plain extruded logo silhouette is CORRECT — the gate must pass it.
    logo = GeneratedShape(hole_count=0, distinct_circular_features=0,
                          has_internal_cutout=False, is_single_disk=True)
    v = evaluate_feature_coverage(ev, logo)
    assert v.silhouette_only is False
    assert v.status == "ok"
    assert v.missing == []


# ==================================== Test 2: key internal channel recess

def test_key_dark_channel_is_blind_recess_not_through_cut():
    """The key's dark vertical channel is a SHALLOW blind recess with a floor —
    not a through cut, not a hollow that splits the body into thin walls."""
    from app.cad.plan.compiler import compile_cad_plan
    from app.cad.plan.normalize import normalize_cad_plan
    from app.cad.plan.schema import CadPlan
    from app.drawing.feature_contract import generated_summary_from_ir
    from app.drawing.sketch_to_cad import generate_cad_from_sketch_ir, resolve_recess_depth
    from app.drawing.vectorize import build_sketch_ir

    thickness = 6.0
    ir = build_sketch_ir((DATA / KEY).read_bytes(), thickness_mm=thickness)

    # outer key profile + through center hole preserved
    assert ir.outer is not None
    assert any(c.kind == "circular_hole" and c.through for c in ir.cut_features)

    # the dark vertical channel is a BLIND recess/channel, never a through cut
    recess = [c for c in ir.cut_features
              if c.kind in ("blind_recess", "recessed_channel")]
    assert recess, [c.kind for c in ir.cut_features]
    assert all(c.through is False for c in recess)
    assert not any(c.kind == "through_channel_cut" for c in ir.cut_features)

    # depth policy: shallow, leaves >=50% floor (≈1.8mm on 6mm)
    depth = resolve_recess_depth(thickness)
    assert depth == 1.8
    assert depth < thickness * 0.5 + 1e-6

    # summary counts it as a recess, NOT a through hole
    summ = generated_summary_from_ir(ir)
    assert summ["blind_recesses"] + summ["recessed_channels"] >= 1
    assert summ["through_channel_cuts"] == 0

    plan = normalize_cad_plan(
        generate_cad_from_sketch_ir(ir, default_thickness_mm=thickness), "sketch")
    rfeat = [f for f in plan.features if f.id.startswith("recess")]
    assert rfeat and all(not f.through for f in rfeat)
    assert all(0 < f.p("recess_depth", 0) < thickness for f in rfeat)

    out = compile_cad_plan(plan)
    # full thickness preserved (recess does not go through) + ONE connected solid
    assert abs(out.bbox_mm["z"] - thickness) < 1e-3
    assert len(out.solid.val().Solids()) == 1
    # a floor remains: material was removed but far less than a through cut
    solid_only = CadPlan(object_type="reconstructed_sketch_part", name="s",
                         features=[f for f in plan.features
                                   if not f.id.startswith("recess")])
    removed = compile_cad_plan(solid_only).solid.val().Volume() \
        - out.solid.val().Volume()
    assert removed > 0                                  # the recess was carved
    # removed is a shallow skim, not a full-depth channel
    channel_area_full = removed / depth
    assert removed < channel_area_full * thickness * 0.6


def test_key_internal_channel_recess_preserved():
    from app.cad.plan.compiler import compile_cad_plan
    from app.cad.plan.normalize import normalize_cad_plan
    from app.drawing.dark_regions import detect_internal_dark_regions
    from app.drawing.sketch_to_cad import generate_cad_from_sketch_ir
    from app.drawing.vectorize import build_sketch_ir

    img = (DATA / KEY).read_bytes()

    # The dark channel is detected as a recess/channel, not material.
    regions = detect_internal_dark_regions(img)
    assert regions, "no internal dark region detected"
    assert any(r.kind in ("recessed_channel", "through_channel_cut", "blind_recess")
               for r in regions)

    ir = build_sketch_ir(img, thickness_mm=8)
    recess = [c for c in ir.cut_features
              if c.kind in ("recessed_channel", "blind_recess", "through_channel_cut")]
    assert recess, [c.kind for c in ir.cut_features]
    # The center hole survives as a real circular hole.
    assert any(c.kind == "circular_hole" for c in ir.cut_features)

    plan = normalize_cad_plan(generate_cad_from_sketch_ir(ir, default_thickness_mm=8),
                              "sketch")
    rfeat = [f for f in plan.features if f.id.startswith("recess")]
    assert rfeat, "recess did not reach the plan"
    # A blind recess (not through) with a real depth → the key keeps a floor.
    blind = [f for f in rfeat if not f.through]
    assert blind and all(f.p("recess_depth", 0) > 0 for f in blind)

    out = compile_cad_plan(plan)
    # The recess removed material: the part is NOT a full solid slab.
    from app.cad.plan.schema import CadPlan, Feature
    solid_plan = CadPlan(object_type="reconstructed_sketch_part", name="solid",
                         features=[f for f in plan.features
                                   if not f.id.startswith("recess")])
    solid_vol = compile_cad_plan(solid_plan).solid.val().Volume()
    assert out.solid.val().Volume() < solid_vol - 1.0    # channel carved out
