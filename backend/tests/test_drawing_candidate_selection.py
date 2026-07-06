"""Final candidate SELECTION correctness (the runtime bug the silhouette gate
alone did not fix).

Non-negotiable: a dimensioned mechanical drawing must NEVER be saved as a
profile_extrusion PASS when a richer sketch_ir candidate exists or the silhouette
gate rejects missing features. These tests assert the FINAL selected design
(source / object_type / validation_status), not just intermediate parser output.
"""
from __future__ import annotations

import io
from pathlib import Path

import pytest

DATA = Path(__file__).parent / "data"
FLANGE = "flange_cover.png"
KEY = "key_channel.png"


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


# ============================ flange cover never falls back to profile

def test_dimensioned_flange_cover_never_selects_profile_fallback(client, auth,
                                                                 vision_outage):
    out = _post(client, auth, FLANGE)
    assert out["generated"] is True, out.get("message")
    d = out["design"]
    # The final selected model is the feature-rich sketch reconstruction, NEVER a
    # silhouette profile extrusion.
    assert d["object_type"] != "profile_extrusion"
    assert d["object_type"] == "reconstructed_sketch_part"
    assert out["analysis"]["source"] == "sketch_ir"


def test_flange_cover_carries_internal_features_not_a_blob(client, auth, vision_outage):
    out = _post(client, auth, FLANGE)
    d = out["design"]
    # It carries the detected concentric bolt/boss groups — not one solid disk.
    ir = (d.get("sketch_ir") or {})
    summary = ir.get("summary") or {}
    assert summary.get("concentric_groups", 0) >= 4 or summary.get("through_holes", 0) >= 4


# ============================ rejected sketch → REVIEW, not profile PASS

def test_rejected_sketch_ir_does_not_fallback_to_profile_pass(client, auth,
                                                              vision_outage):
    """Whatever the coverage verdict, a dimensioned drawing keeps the rich sketch
    (as REVIEW when imperfect) instead of a silhouette profile_extrusion PASS."""
    out = _post(client, auth, FLANGE)
    d = out["design"]
    assert d["object_type"] == "reconstructed_sketch_part"
    # never a clean PASS silhouette: either a real reconstruction, or REVIEW.
    if d["validation_status"] == "pass":
        # a PASS is only allowed because the features were actually rebuilt
        assert (d.get("sketch_ir") or {}).get("summary", {}).get("through_holes", 0) >= 4


# ============================ final guard forces REVIEW for dimensioned profile

def test_final_guard_blocks_silhouette_pass_for_dimensioned_drawing():
    """Unit: the mechanical-route decision blocks a profile_extrusion PASS when
    the drawing carries engineering callouts."""
    from app.routers.drawings import _drawing_route_and_mechanical
    from app.drawing.silhouette_gate import parse_dimension_evidence

    ev = parse_dimension_evidence(["R56", "R50", "8xR8", "8xR6", "8xR12"])
    route, mechanical = _drawing_route_and_mechanical(ev, profile=None)
    assert mechanical is True
    assert route != "logo_profile_extrusion"


def test_mechanical_route_helper_geometric_override():
    """A sketch IR with concentric groups is mechanical even with no text."""
    from app.drawing.vectorize import build_sketch_ir
    from app.routers.drawings import _drawing_route_and_mechanical
    from app.drawing.silhouette_gate import parse_dimension_evidence

    ir = build_sketch_ir((DATA / FLANGE).read_bytes())
    _route, mechanical = _drawing_route_and_mechanical(
        parse_dimension_evidence([]), ir=ir)
    assert mechanical is True


# ============================ USB / simple profile NOT blocked

def test_simple_round_hole_profile_still_uses_profile_extrusion(client, auth,
                                                                vision_outage):
    """A plain outline with a couple of round holes is NOT mechanical (raster
    preserves round holes) — it must still take the profile route, not be forced
    into REVIEW."""
    out = _post(client, auth, "stepped_profile_2_holes.png")
    assert out["generated"] is True
    d = out["design"]
    assert d["object_type"] == "profile_extrusion"


# ============================ key recess preserved end-to-end

def test_key_channel_still_preserved(client, auth, vision_outage):
    out = _post(client, auth, KEY, thickness=8)
    assert out["generated"] is True
    d = out["design"]
    assert d["object_type"] == "reconstructed_sketch_part"
    ir = (d.get("sketch_ir") or {}).get("ir") or {}
    cuts = ir.get("cut_features") or []
    kinds = {c.get("kind") for c in cuts}
    assert {"recessed_channel", "blind_recess", "through_channel_cut"} & kinds, kinds
    assert "circular_hole" in kinds        # center hole preserved
