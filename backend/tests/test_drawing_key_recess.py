"""The key's internal dark strip is a SHALLOW blind recess, not a through slot.

Reproduces the RUNTIME failure the synthetic shaded fixture missed: on the real
upload the dark strip is a solid bar that (a) is drawn in ink, possibly touching
the outline, and (b) splits the shaft into white strips that the slot classifier
turned into ``rounded_slot``/``arc_slot`` (logs showed cut_by_kind=
{rounded_slot:2,arc_slot:1}, internal_dark_regions:0).

Fix (vectorize key_recess_v2): a morphological-erosion detector finds the dark
FILL (separating thick fills from thin strokes even when connected), then
NEUTRALIZES it so the side strips stop being read as holes.

``key_black_bar.png`` reproduces that exact geometry. Drop the real upload at
``tests/fixtures/drawings/key_real_runtime.png`` to run the exact-image test.
"""
from __future__ import annotations

import io
from collections import Counter
from pathlib import Path

import pytest

DATA = Path(__file__).parent / "data"
BLACK_BAR = "key_black_bar.png"
SHADED = "key_shaded_channel.png"
REAL_RUNTIME = Path(__file__).parent / "fixtures" / "drawings" / "key_real_runtime.png"
_RECESS = {"recessed_channel", "blind_recess"}


class _TimeoutProvider:
    name = "openai"

    def interpret_drawing(self, *a, **k):
        raise TimeoutError("APITimeoutError: request timed out")


@pytest.fixture
def vision_outage(monkeypatch):
    import app.drawing.interpret as interp_mod

    monkeypatch.setattr(interp_mod, "get_provider", lambda: _TimeoutProvider())


def _post(client, auth, path_or_name, **form):
    p = path_or_name if isinstance(path_or_name, Path) else DATA / path_or_name
    return client.post(
        "/api/drawings/to-cad",
        files={"file": (p.name, io.BytesIO(p.read_bytes()), "image/png")},
        data={"sync": "true", **{k: str(v) for k, v in form.items() if v is not None}},
        headers=auth["headers"],
    ).json()


def _kinds(image_bytes, thickness=6):
    from app.drawing.vectorize import build_sketch_ir

    ir = build_sketch_ir(image_bytes, thickness_mm=thickness)
    return ir, Counter(c.kind for c in ir.cut_features)


# ============================ detector runs + classifies the strip

def test_detector_runs_and_emits_started_log():
    """The detector fires at the START of vectorization (smoke log). If this is
    missing at runtime, the detector is not wired."""
    import app.drawing.vectorize as vec

    seen = []
    orig = vec.log_event
    vec.log_event = lambda e, **k: (seen.append(e), orig(e, **k))[1]
    try:
        vec.build_sketch_ir((DATA / BLACK_BAR).read_bytes(), thickness_mm=6)
    finally:
        vec.log_event = orig
    assert "drawing_recess_detector_started" in seen
    assert "drawing_dark_region_candidate" in seen
    assert "drawing_dark_regions_neutralized" in seen


def test_black_bar_strip_is_recess_not_slots():
    """The split-shaft failure: the bar → recessed_channel, the side strips are
    neutralized (no slots), the center hole stays a through-hole."""
    ir, kinds = _kinds((DATA / BLACK_BAR).read_bytes())
    assert kinds["recessed_channel"] == 1
    assert kinds["circular_hole"] == 1
    assert kinds["rounded_slot"] == 0
    assert kinds["arc_slot"] == 0
    assert dict(kinds) == {"recessed_channel": 1, "circular_hole": 1}
    recess = [c for c in ir.cut_features if c.kind in _RECESS]
    assert all(c.through is False for c in recess)
    assert any(c.kind == "circular_hole" and c.through for c in ir.cut_features)


@pytest.mark.parametrize("fixture", [BLACK_BAR, SHADED])
def test_key_strip_never_through_channel(fixture):
    ir, kinds = _kinds((DATA / fixture).read_bytes())
    assert kinds["recessed_channel"] >= 1
    assert not any(c.kind == "through_channel_cut" for c in ir.cut_features)


# ============================ full end-to-end + CAD geometry

def test_key_recess_full_build_keeps_floor_and_single_solid():
    from app.cad.plan.compiler import compile_cad_plan
    from app.cad.plan.normalize import normalize_cad_plan
    from app.cad.plan.schema import CadPlan
    from app.drawing.feature_contract import generated_summary_from_ir
    from app.drawing.sketch_to_cad import generate_cad_from_sketch_ir, resolve_recess_depth
    from app.drawing.vectorize import build_sketch_ir

    thickness = 6.0
    ir = build_sketch_ir((DATA / BLACK_BAR).read_bytes(), thickness_mm=thickness)
    s = generated_summary_from_ir(ir)
    assert s == {**s, "through_holes": 1, "recessed_channels": 1,
                 "internal_dark_regions": 1, "through_channel_cuts": 0}
    assert resolve_recess_depth(thickness) == 1.8

    plan = normalize_cad_plan(
        generate_cad_from_sketch_ir(ir, default_thickness_mm=thickness), "sketch")
    rfeat = [f for f in plan.features if f.id.startswith("recess")]
    assert rfeat and all(not f.through and f.p("recess_depth", 0) == 1.8 for f in rfeat)

    built = compile_cad_plan(plan)
    assert abs(built.bbox_mm["z"] - thickness) < 1e-3      # full 6mm body
    assert len(built.solid.val().Solids()) == 1            # not split into walls
    solid_only = CadPlan(object_type="reconstructed_sketch_part", name="s",
                         features=[f for f in plan.features
                                   if not f.id.startswith("recess")])
    removed = compile_cad_plan(solid_only).solid.val().Volume() \
        - built.solid.val().Volume()
    assert removed > 0                                     # recess carved
    assert removed < (removed / 1.8) * thickness * 0.6     # shallow, has a floor


def test_key_teeth_are_solid_not_hollow():
    """The right-side teeth are part of the SOLID outer silhouette — never
    hollow cutouts / slots. Only the head hole is through, only the shaft strip
    is a recess."""
    import cadquery as cq

    from app.cad.plan.compiler import compile_cad_plan
    from app.cad.plan.normalize import normalize_cad_plan
    from app.drawing.sketch_to_cad import generate_cad_from_sketch_ir
    from app.drawing.vectorize import build_sketch_ir

    thickness = 6.0
    ir = build_sketch_ir((DATA / "key_teeth_tabs.png").read_bytes(),
                         thickness_mm=thickness)
    kinds = Counter(c.kind for c in ir.cut_features)
    # NO cutouts in the teeth: no slots / polygons / rect cutouts anywhere.
    assert kinds["rounded_slot"] == 0 and kinds["arc_slot"] == 0
    assert kinds["rectangular_slot"] == 0 and kinds["polygon_hole"] == 0
    assert kinds["arbitrary_cutout"] == 0
    assert kinds["recessed_channel"] == 1 and kinds["circular_hole"] == 1
    through_holes = sum(1 for c in ir.cut_features if c.kind == "circular_hole")
    assert through_holes == 1

    # the teeth protrude on the outer silhouette (right extent beyond centre)
    verts = ir.outer.vertices
    xs = [v[0] for v in verts]
    assert max(xs) > 0.15 * ir.outer.bbox_mm["w"]
    tip = max(verts, key=lambda v: v[0])             # a tooth tip vertex

    plan = normalize_cad_plan(
        generate_cad_from_sketch_ir(ir, default_thickness_mm=thickness), "sketch")
    out = compile_cad_plan(plan)
    assert len(out.solid.val().Solids()) == 1        # one connected solid
    assert abs(out.bbox_mm["z"] - thickness) < 1e-3  # full height everywhere
    # material is present just inside the tooth tip (the tooth tab is SOLID)
    probe = cq.Workplane("XY").box(4, 4, thickness).translate(
        (tip[0] - 3, tip[1], thickness / 2))
    assert out.solid.intersect(probe).val().Volume() > 1.0


def test_key_head_circle_is_through_hole_and_teeth_remain_solid():
    """The oval head's circular feature is a true THROUGH-HOLE (even with a thin
    ring), the teeth stay solid, and only the shaft strip is a blind recess."""
    import cadquery as cq

    from app.cad.plan.compiler import compile_cad_plan
    from app.cad.plan.normalize import normalize_cad_plan
    from app.drawing.sketch_to_cad import generate_cad_from_sketch_ir
    from app.drawing.vectorize import build_sketch_ir

    thickness = 6.0
    ir = build_sketch_ir((DATA / "key_oval_head.png").read_bytes(),
                         thickness_mm=thickness)
    kinds = Counter(c.kind for c in ir.cut_features)
    # exactly one through-hole (the head circle), one blind recess, no tooth cuts
    assert kinds["circular_hole"] == 1
    assert kinds["recessed_channel"] == 1
    assert kinds["rounded_slot"] == 0 and kinds["arc_slot"] == 0
    assert kinds["rectangular_slot"] == 0 and kinds["polygon_hole"] == 0
    head = next(c for c in ir.cut_features if c.kind == "circular_hole")
    assert head.through is True
    assert head.center[1] > 0                          # in the (upper) head

    plan = normalize_cad_plan(
        generate_cad_from_sketch_ir(ir, default_thickness_mm=thickness), "sketch")
    assert plan.expected.through_hole_count == 1
    out = compile_cad_plan(plan)
    assert len(out.solid.val().Solids()) == 1          # one connected solid
    assert abs(out.bbox_mm["z"] - thickness) < 1e-3    # full thickness

    # the head hole is EMPTY through the full thickness
    axis = cq.Workplane("XY").circle(max(1.0, (head.diameter_mm or 4) * 0.3)) \
        .extrude(thickness + 4).translate((head.center[0], head.center[1], -2))
    assert out.solid.intersect(axis).val().Volume() < 0.5
    # a tooth tip is SOLID material
    tip = max(ir.outer.vertices, key=lambda v: v[0])
    probe = cq.Workplane("XY").box(4, 4, thickness).translate(
        (tip[0] - 3, tip[1], thickness / 2))
    assert out.solid.intersect(probe).val().Volume() > 1.0


def test_key_final_candidate_is_sketch_not_profile(client, auth, vision_outage):
    out = _post(client, auth, BLACK_BAR, thickness=6)
    assert out["generated"] is True, out.get("message")
    d = out["design"]
    assert d["object_type"] == "reconstructed_sketch_part"
    assert d["object_type"] != "profile_extrusion"
    ir_dto = (d.get("sketch_ir") or {}).get("ir") or {}
    kinds = [c.get("kind") for c in (ir_dto.get("cut_features") or [])]
    assert "recessed_channel" in kinds
    assert "rounded_slot" not in kinds and "arc_slot" not in kinds
    assert "circular_hole" in kinds


# ============================ the EXACT real upload (skipped unless present)

@pytest.mark.skipif(not REAL_RUNTIME.exists(),
                    reason="drop the real key upload at "
                           "tests/fixtures/drawings/key_real_runtime.png to run")
def test_real_runtime_key_image_recess_not_slots(client, auth, vision_outage):
    """Runs the exact browser-uploaded key image through the real /to-cad path."""
    out = _post(client, auth, REAL_RUNTIME, thickness=6)
    assert out["generated"] is True, out.get("message")
    d = out["design"]
    assert d["object_type"] == "reconstructed_sketch_part"
    ir_dto = (d.get("sketch_ir") or {}).get("ir") or {}
    kinds = Counter(c.get("kind") for c in (ir_dto.get("cut_features") or []))
    assert kinds["recessed_channel"] + kinds.get("blind_recess", 0) >= 1
    assert kinds["rounded_slot"] == 0 and kinds["arc_slot"] == 0
    assert kinds["circular_hole"] >= 1
    assert not any(c.get("kind") == "through_channel_cut"
                   for c in (ir_dto.get("cut_features") or []))
