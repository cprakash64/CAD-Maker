"""Mechanical sketch reconstruction: a plate/bracket drawing is rebuilt as a
feature graph (outer profile + classified cuts + concentric-grouped
counterbores), never a rough silhouette with random holes.

Covers the three field-failure drawing classes plus the invariants that must
survive a forced provider timeout (deterministic CV reconstruction).
"""
from __future__ import annotations

import io
from pathlib import Path

import pytest

DATA = Path(__file__).parent / "data"
PLATE = "rect_rounded_plate_counterbore_slot.png"
BRACKET = "vertical_bracket_boss_holes_cutout.png"
DIAMOND = "symmetric_diamond_plate_arc_slots.png"
FULL_BRACKET = "vertical_bracket_concentric_cutouts.png"


class _TimeoutProvider:
    name = "openai"

    def interpret_drawing(self, *a, **k):
        raise TimeoutError("APITimeoutError: request timed out")


@pytest.fixture
def vision_outage(monkeypatch):
    import app.drawing.interpret as interp_mod

    monkeypatch.setattr(interp_mod, "get_provider", lambda: _TimeoutProvider())


def _post(client, auth, name, **form):
    data = (DATA / name).read_bytes()
    return client.post(
        "/api/drawings/to-cad",
        files={"file": (name, io.BytesIO(data), "image/png")},
        data={"sync": "true", **{k: str(v) for k, v in form.items() if v is not None}},
        headers=auth["headers"],
    ).json()


def _summary(out):
    return (out["design"].get("sketch_ir") or {}).get("summary") or {}


def _common(out):
    """Assertions shared by every sketch reconstruction under a timeout."""
    assert out["generated"] is True, out.get("message")
    d = out["design"]
    assert d["id"]
    assert d["object_type"] == "reconstructed_sketch_part"
    assert d["validation_status"] != "pass"          # estimated dims → REVIEW
    assert d["drawing_fidelity"]["drawing_fidelity_status"] == "review"
    assert d["download_blocked_reason"] is None
    assert {e["fmt"] for e in d["exports"]} >= {"step", "stl"}
    msg = (out.get("message") or "").lower()
    assert "simpler" not in msg and "took too long" not in msg
    return d


# ================================================ rectangular plate

def test_rect_plate_counterbores_grouped_not_random_holes(client, auth, vision_outage):
    out = _post(client, auth, PLATE)
    d = _common(out)
    s = _summary(out)
    assert s["outer_kind"] == "rounded_rectangle"
    assert s["counterbores"] == 4                       # 4 bolts, grouped
    assert s["cut_by_kind"].get("circular_hole") == 1   # one central opening
    assert s["cut_by_kind"].get("rectangular_slot") == 1
    # NOT the "8+ random holes" failure: total through openings is the topology
    # count (4 counterbores + central + slot), never one-per-circle-contour.
    holes = d["dimension_report"]["measured"]["hole_count"]
    assert holes == 6, holes
    assert holes < 10


# ================================================ vertical bracket

def test_bracket_top_group_lower_holes_and_cutout(client, auth, vision_outage):
    out = _post(client, auth, BRACKET)
    d = _common(out)
    s = _summary(out)
    assert "rounded" in s["outer_kind"] or s["outer_kind"] == "symmetric_plate"
    # Top concentric boss+hole grouped as ONE nested feature (not two holes).
    assert s["counterbores"] >= 1
    # Two lower circular holes.
    assert s["cut_by_kind"].get("circular_hole", 0) >= 2
    # Inner rectangular cutout preserved.
    assert s["cut_by_kind"].get("rectangular_slot", 0) >= 1


# ============================== full vertical bracket (concentric + cutouts)

def test_full_vertical_bracket_preserves_all_features(client, auth, vision_outage):
    """The field bracket: an internal base strip used to split the flood interior
    and drop the top concentric group + rectangular cutout. Now every feature
    survives IR → CAD, and the generated feature summary proves it."""
    out = _post(client, auth, FULL_BRACKET)
    d = _common(out)
    assert out["analysis"]["source"] == "sketch_ir"
    g = (d.get("sketch_ir") or {}).get("generated_feature_summary") or {}
    assert g.get("top_concentric_groups", 0) >= 1, g
    assert g.get("rectangular_cutouts", 0) >= 1, g
    assert g.get("lower_counterbore_groups", 0) >= 2, g
    assert g.get("circular_holes", 0) >= 3, g
    assert g.get("arc_or_semicircular_cutouts", 0) >= 1, g
    # No feature loss from IR to CAD: measured through-cuts == IR through count.
    measured = d["dimension_report"]["measured"]["hole_count"]
    assert measured == _summary(out)["through_holes"], (measured, _summary(out))
    # A 3-feature candidate would have been rejected: the IR expects >= 5.
    assert measured >= 5, measured


def test_full_vertical_bracket_ir_preserves_features_unit():
    """Vectorizer-level: an internal full-width line never drops interior holes."""
    from app.drawing.vectorize import build_sketch_ir

    ir = build_sketch_ir((DATA / FULL_BRACKET).read_bytes())
    assert ir is not None and ir.is_usable()
    assert len(ir.nested_features) == 3          # 1 top group + 2 lower counterbores
    top = [n for n in ir.nested_features if n.center[1] > 0]
    lower = [n for n in ir.nested_features if n.center[1] <= 0]
    assert len(top) == 1 and len(lower) == 2
    assert any(c.kind == "rectangular_slot" for c in ir.cut_features)
    assert any(c.kind in ("arc_slot", "semicircular_cutout") for c in ir.cut_features)


# ==================================== concentric hole groups (normalization)

def _bracket_ir():
    from app.drawing.vectorize import build_sketch_ir

    return build_sketch_ir((DATA / FULL_BRACKET).read_bytes())


def _bracket_plan(ir):
    from app.cad.plan.normalize import normalize_cad_plan
    from app.drawing.sketch_to_cad import generate_cad_from_sketch_ir

    return normalize_cad_plan(generate_cad_from_sketch_ir(ir), "sketch")


def test_vertical_bracket_concentric_groups_normalized():
    """3 concentric groups: top (3 circles), lower-left (2), lower-right (2)."""
    ir = _bracket_ir()
    assert len(ir.concentric_groups) == 3
    by_circles = sorted(g.circle_count for g in ir.concentric_groups)
    assert by_circles == [2, 2, 3], by_circles
    top = [g for g in ir.concentric_groups if g.center[1] > 0]
    lower = [g for g in ir.concentric_groups if g.center[1] <= 0]
    assert len(top) == 1 and top[0].circle_count == 3
    assert len(lower) == 2 and all(g.circle_count == 2 for g in lower)


def test_concentric_groups_do_not_create_extra_through_holes():
    """Through-holes = 3 groups (not 7 circle contours). Measured hole count = 5
    (3 groups + rect + arc), never the raw circle count."""
    from app.cad.plan.planner import build_and_validate

    ir = _bracket_ir()
    out = build_and_validate(_bracket_plan(ir))
    # One through-hole per group; the outer/middle rings are NOT cut through.
    n_group_through = len(ir.concentric_groups)
    assert n_group_through == 3
    assert out.result.hole_count == 5, out.result.hole_count   # 3 groups + rect + arc
    assert out.result.hole_count < 7


def test_top_group_preserves_ring_and_through_hole():
    ir = _bracket_ir()
    top = next(g for g in ir.concentric_groups if g.center[1] > 0)
    assert top.circle_count == 3
    assert top.outer_diameter_mm > top.counterbore_or_boss_diameter_mm > top.through_hole_diameter_mm > 0
    # The plan cuts a stepped counterbore (through + ring + outer ring).
    feat = next(f for f in _bracket_plan(ir).features if f.id == top.id)
    assert str(feat.kind).endswith("counterbore")
    assert feat.p("counterbore_diameter", 0) > feat.p("diameter", 0)
    assert feat.p("outer_ring_diameter", 0) > feat.p("counterbore_diameter", 0)


def test_lower_groups_preserve_counterbore_rings():
    ir = _bracket_ir()
    lower = [g for g in ir.concentric_groups if g.center[1] <= 0]
    assert len(lower) == 2
    for g in lower:
        assert g.counterbore_or_boss_diameter_mm > g.through_hole_diameter_mm > 0
        assert g.group_kind == "counterbore_with_through_hole"


def test_group_centers_are_snapped():
    """Every circle in a group shares ONE centre, and the lower groups are
    symmetric across x=0 (mirror pair)."""
    ir = _bracket_ir()
    for g in ir.concentric_groups:
        assert g.generated_metadata.get("snapped_center") is True
    lower = sorted((g for g in ir.concentric_groups if g.center[1] <= 0),
                   key=lambda g: g.center[0])
    left, right = lower[0], lower[1]
    assert abs(left.center[0] + right.center[0]) < 1.0    # mirror across x=0
    assert abs(left.center[1] - right.center[1]) < 1.0    # same height


def test_concentric_group_generation_visual_metadata():
    from app.drawing.feature_contract import generated_summary_from_ir

    ir = _bracket_ir()
    g = generated_summary_from_ir(ir)
    assert g["concentric_groups_generated"] == 3
    assert g["counterbore_or_ring_features"] >= 3
    assert g["through_holes_from_groups"] == 3
    assert g["lower_groups_symmetric"] is True
    # Grouped circles are NOT also emitted as independent circular_hole cuts.
    assert all(c.kind != "circular_hole" for c in ir.cut_features)


def test_concentric_group_contract_rejects_plain_holes():
    """If a build collapses the groups to plain holes, the contract rejects it."""
    from app.cad.plan.planner import build_and_validate
    from app.drawing.feature_contract import (
        check_concentric_group_integrity,
        summary_from_plan,
    )

    ir = _bracket_ir()
    plan = _bracket_plan(ir)
    out = build_and_validate(plan)
    ok, _ = check_concentric_group_integrity(
        ir, plan, summary_from_plan(plan, out.result.hole_count))
    assert ok is True
    # Simulate a build that dropped every counterbore → must be rejected.
    stripped = [f for f in plan.features if not str(f.kind).endswith("counterbore")]
    plan.features = stripped
    bad, reason = check_concentric_group_integrity(
        ir, plan, summary_from_plan(plan, 2))
    assert bad is False and "plain holes" in reason


# ================================================ diamond plate + arc slots

def test_diamond_plate_center_end_holes_and_arc_slots(client, auth, vision_outage):
    out = _post(client, auth, DIAMOND)
    d = _common(out)
    s = _summary(out)
    # Two curved arc slots detected and cut (not dropped, not circles).
    assert s["cut_by_kind"].get("arc_slot") == 2, s["cut_by_kind"]
    # Center hole + two end holes.
    assert s["cut_by_kind"].get("circular_hole") == 3, s["cut_by_kind"]


# ================================================ dimensions as scale (PART E)

def test_dimensions_set_scale_when_legible(client, auth, vision_outage):
    """A parsed overall dimension sets the mm scale instead of the default
    envelope (scale no longer flagged estimated)."""
    out = _post(client, auth, PLATE, notes="overall width 120 mm, plate")
    d = out["design"]
    s = _summary(out)
    assert s["scale_estimated"] is False
    bb = d["bounding_box_mm"]
    assert bb["x"] == pytest.approx(120, abs=3)


# ================================================ vectorizer units

def test_vectorizer_groups_concentric_circles():
    from app.drawing.vectorize import build_sketch_ir

    ir = build_sketch_ir((DATA / PLATE).read_bytes())
    assert ir is not None and ir.is_usable()
    assert ir.outer.kind == "rounded_rectangle"
    assert len(ir.nested_features) == 4
    # Every nested group is a counterbore with an outer recess and inner bore.
    for nf in ir.nested_features:
        assert nf.outer_diameter_mm > nf.inner_diameter_mm > 0


def test_vectorizer_detects_arc_slots():
    from app.drawing.vectorize import build_sketch_ir

    ir = build_sketch_ir((DATA / DIAMOND).read_bytes())
    arc = [c for c in ir.cut_features if c.kind == "arc_slot"]
    assert len(arc) == 2
    # Arc slots keep their traced (curved) outline for the through-cut.
    assert all(len(c.vertices) >= 5 for c in arc)


def test_ir_cad_generation_preserves_topology():
    from app.cad.plan.normalize import normalize_cad_plan
    from app.cad.plan.planner import build_and_validate
    from app.drawing.sketch_to_cad import generate_cad_from_sketch_ir
    from app.drawing.vectorize import build_sketch_ir

    ir = build_sketch_ir((DATA / PLATE).read_bytes())
    plan = normalize_cad_plan(generate_cad_from_sketch_ir(ir), "sketch")
    out = build_and_validate(plan)
    assert out.result.hole_count == 6  # 4 counterbores + central + slot
    bb = out.result.bbox_mm
    assert bb["x"] > 0 and bb["y"] > 0 and bb["z"] > 0


# ================================================ debug artifact (PART J)

def test_debug_endpoint_returns_sketch_ir(client, auth, vision_outage):
    out = _post(client, auth, PLATE)
    design_id = out["design"]["id"]
    r = client.get(f"/api/drawings/debug/{design_id}", headers=auth["headers"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["summary"]["counterbores"] == 4
    assert body["sketch_ir"]["outer_profiles"]
    assert isinstance(body["overlay_available"], bool)


def test_debug_endpoint_owner_scoped(client, auth, auth2, vision_outage):
    out = _post(client, auth, PLATE)
    design_id = out["design"]["id"]
    r = client.get(f"/api/drawings/debug/{design_id}", headers=auth2["headers"])
    assert r.status_code == 404
