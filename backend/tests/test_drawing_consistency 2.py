"""Consistency + completeness invariants for Drawing → CAD.

The field complaints these lock shut:
* the SAME image must take the SAME route / topology every run — never a bad
  generic cube once and a correct reconstructed sketch later;
* a drawing with usable linework must NEVER fall back to a generic cube /
  mounting block;
* a flanged pipe branch must have bolt holes drilled on ALL THREE flanges;
* a stepped profile must not invent holes;
* drawing-derived models with estimated dimensions are REVIEW, not PASS.
"""
from __future__ import annotations

import io
import math
from pathlib import Path

import pytest

DATA = Path(__file__).parent / "data"
PLATE = "rect_rounded_plate_counterbore_slot.png"
DIAMOND = "symmetric_diamond_plate_arc_slots.png"
BRACKET = "vertical_bracket_boss_holes_cutout.png"
BRANCH = "flanged_pipe_branch_sheet.png"
STEPPED = "stepped_profile.png"

_GENERIC_BLOCK_TYPES = {"generic_mechanical_part", "mounting_plate", "box",
                        "generic_block", "cube", "rim", "wheel_assembly"}


class _Timeout:
    name = "openai"

    def interpret_drawing(self, *a, **k):
        raise TimeoutError("APITimeoutError: request timed out")


class _GenericVision:
    """Vision returns a vague 'generic mechanical part' — the reading that used
    to route a real drawing to a cube."""

    name = "openai"

    def interpret_drawing(self, *a, **k):
        return {"suggested_object_type": "generic_mechanical_part",
                "overall_dimensions": {"width": 90.0}, "overall_confidence": 0.7}


def _set(monkeypatch, provider):
    import app.drawing.interpret as interp_mod

    monkeypatch.setattr(interp_mod, "get_provider", lambda: provider)


def _post(client, auth, name):
    data = (DATA / name).read_bytes()
    return client.post(
        "/api/drawings/to-cad",
        files={"file": (name, io.BytesIO(data), "image/png")},
        data={"sync": "true"}, headers=auth["headers"],
    ).json()


# ============================================ determinism + no cube

def test_same_image_same_result_no_cube(client, auth, monkeypatch):
    """Alternating vision-success / vision-timeout on the SAME image yields the
    same route, topology and status every run — and never a generic cube."""
    results = []
    for i in range(5):
        _set(monkeypatch, _GenericVision() if i % 2 == 0 else _Timeout())
        out = _post(client, auth, PLATE)
        assert out["generated"] is True
        d = out["design"]
        s = (d.get("sketch_ir") or {}).get("summary") or {}
        assert d["object_type"] not in _GENERIC_BLOCK_TYPES
        results.append((d["object_type"], s.get("counterbores"),
                        tuple(sorted((s.get("cut_by_kind") or {}).items())),
                        d["validation_status"]))
    assert len(set(results)) == 1, results
    # And it is the reconstructed sketch, not a cube.
    assert results[0][0] == "reconstructed_sketch_part"
    assert results[0][1] == 4


def test_generic_vision_plate_is_not_a_cube(client, auth, monkeypatch):
    """A drawing the vision model reads only as 'generic mechanical part' still
    reconstructs from CV linework (sketch or profile), never a generic block."""
    _set(monkeypatch, _GenericVision())
    for name in (PLATE, DIAMOND, BRACKET, STEPPED):
        out = _post(client, auth, name)
        assert out["generated"] is True, (name, out.get("message"))
        d = out["design"]
        assert d["object_type"] in ("reconstructed_sketch_part", "profile_extrusion"), \
            (name, d["object_type"])
        assert d["object_type"] not in _GENERIC_BLOCK_TYPES


def test_route_stable_across_vision_and_timeout_for_all_fixtures(client, auth, monkeypatch):
    for name in (PLATE, DIAMOND, BRACKET, STEPPED):
        routes = set()
        for prov in (_GenericVision(), _Timeout()):
            _set(monkeypatch, prov)
            out = _post(client, auth, name)
            routes.add(out["design"]["object_type"])
        assert len(routes) == 1, (name, routes)


# ============================================ pipe branch holes on all flanges

def test_pipe_branch_holes_all_flanges(client, auth, monkeypatch):
    _set(monkeypatch, _Timeout())
    out = _post(client, auth, BRANCH)
    d = out["design"]
    assert d["object_type"] == "flanged_pipe_branch"
    det = d["pipe_branch_detail"]
    assert det["side_branch_present"] is True
    assert det["flange_count"] >= 3
    # Each flange's bolt circle is drilled — the geometric probes for all three
    # flange planes pass, and the measured hole count covers 3 circles + bores.
    checks = {c["name"]: c for c in d["semantic_checks"]}
    for plane in ("top", "bottom", "branch"):
        assert checks[f"bolt_holes_open_{plane}"]["passed"], plane
    assert d["dimension_report"]["measured"]["hole_count"] >= 3 * 8
    # No wheel geometry.
    assert not any("spoke" in a.lower() for a in d["assumptions"])


def test_pipe_branch_bolt_holes_geometric():
    """Builder-level proof: bolt holes are physically open on the top AND bottom
    flange planes (not only the side branch)."""
    import cadquery as cq

    from app.cad.pipe_branch import PipeBranchSpec, build_plan
    from app.cad.plan.normalize import normalize_cad_plan
    from app.cad.plan.planner import build_and_validate

    spec = PipeBranchSpec(main_od=72, main_id=62, main_len=148, branch_od=48,
                          branch_id=38, branch_len=74, flange_od=120, flange_thk=10,
                          bolt_count=12, bolt_dia=10, pcd=95)
    out = build_and_validate(normalize_cad_plan(build_plan(spec), "branch"))
    solid = out.result.solid.val()

    def open_bolts(z):
        n = 0
        for k in range(12):
            a = 2 * math.pi * k / 12
            x, y = (95 / 2) * math.cos(a), (95 / 2) * math.sin(a)
            probe = cq.Solid.makeCylinder(3.5, 6, cq.Vector(x, y, z - 3),
                                          cq.Vector(0, 0, 1))
            inter = solid.intersect(probe)
            if (inter.Volume() if inter.Solids() else 0.0) < 1.0:
                n += 1
        return n

    assert open_bolts(69) == 12    # top flange
    assert open_bolts(-69) == 12   # bottom flange


def test_pipe_branch_from_drawing_is_review_not_pass(client, auth, monkeypatch):
    """A drawing-derived branch (proportions inferred from pixels) is REVIEW."""
    _set(monkeypatch, _Timeout())
    d = _post(client, auth, BRANCH)["design"]
    assert d["validation_status"] != "pass"
    assert d["drawing_fidelity"]["drawing_fidelity_status"] == "review"


# ============================================ stepped profile / no invented holes

def test_stepped_profile_no_invented_holes(client, auth, monkeypatch):
    _set(monkeypatch, _Timeout())
    d = _post(client, auth, STEPPED)["design"]
    assert d["object_type"] == "profile_extrusion"
    assert d["dimension_report"]["measured"]["hole_count"] == 0
    # The stepped outline is not a plain rectangle (volume well under bbox).
    bb = d["bounding_box_mm"]
    vol = d["dimension_report"]["measured"]["volume_mm3"]
    assert vol < 0.9 * bb["x"] * bb["y"] * bb["z"]


# ============================================ topology contract unit

def test_topology_coverage_rejects_zero_holes():
    from app.drawing.sketch_ir import CutFeature, MechanicalSketchIR, OuterProfile, Scale
    from app.drawing.topology import topology_coverage

    ir = MechanicalSketchIR(
        scale=Scale(px_per_mm=1.0, estimated=True),
        outer_profiles=[OuterProfile(id="o", kind="polygon",
                                     vertices=[[0, 0], [10, 0], [10, 10]])],
        cut_features=[CutFeature(id=f"c{i}", kind="circular_hole", diameter_mm=2)
                      for i in range(5)])

    class _D:  # a design that compiled to 0 holes
        semantic_json = {"dimension_report": {"measured": {"hole_count": 0}}}
        features_json = []

    cov = topology_coverage(ir, _D())
    assert not cov.acceptable
    assert cov.expected_cuts == 5 and cov.actual_cuts == 0


def test_topology_coverage_accepts_full_match():
    from app.drawing.sketch_ir import CutFeature, MechanicalSketchIR, OuterProfile
    from app.drawing.topology import topology_coverage

    ir = MechanicalSketchIR(
        outer_profiles=[OuterProfile(id="o", kind="polygon",
                                     vertices=[[0, 0], [10, 0], [10, 10]])],
        cut_features=[CutFeature(id="c1", kind="circular_hole", diameter_mm=2)])

    class _D:
        semantic_json = {"dimension_report": {"measured": {"hole_count": 1}}}
        features_json = [{"type": "hole"}]

    assert topology_coverage(ir, _D()).acceptable


# ============================================ debug artifact (PART F)

def test_debug_endpoint_and_overlay(client, auth, monkeypatch):
    _set(monkeypatch, _Timeout())
    design_id = _post(client, auth, PLATE)["design"]["id"]
    r = client.get(f"/api/drawings/debug/{design_id}", headers=auth["headers"])
    assert r.status_code == 200
    body = r.json()
    assert body["summary"]["counterbores"] == 4
    assert body["sketch_ir"]["outer_profiles"]
    if body["overlay_available"]:
        ov = client.get(body["overlay_url"], headers=auth["headers"])
        assert ov.status_code == 200
        assert ov.headers["content-type"] == "image/png"
