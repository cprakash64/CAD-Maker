"""Hybrid deterministic raster path for complex flanged pipe branch drawings.

The field failure: a multi-view flanged-branch sheet (plan view with 12xØ1
bolts, front view, Section A-A, shaded isometric) failed outright when OpenAI
vision timed out. These tests pin the fix: the flange face + bolt ring is
detected from PIXELS before any LLM, the family builds deterministically even
under a provider outage, user notes are a vision-free fast path with literal
decimals, and fidelity keeps assumed-scale builds at REVIEW.
"""
from __future__ import annotations

from tests.conftest import TINY_PNG

import io
from pathlib import Path

import pytest

from app.services.raster_flanged_pipe_branch import (
    detect_flanged_branch,
    interp_from_evidence,
    params_from_notes,
)

DATA = Path(__file__).parent / "data"
SHEET = "flanged_pipe_branch_sheet.png"


def _post(client, auth, data: bytes, **form):
    return client.post(
        "/api/drawings/to-cad",
        files={"file": (SHEET, io.BytesIO(data), "image/png")},
        data={"sync": "true", **{k: str(v) for k, v in form.items() if v is not None}},
        headers=auth["headers"],
    )


class _TimeoutProvider:
    name = "openai"

    def interpret_drawing(self, *a, **k):
        raise TimeoutError("APITimeoutError: request timed out")


@pytest.fixture
def vision_outage(monkeypatch):
    import app.drawing.interpret as interp_mod

    monkeypatch.setattr(interp_mod, "get_provider", lambda: _TimeoutProvider())


# ------------------------------------------------------ pixel pre-classification

def test_detects_flanged_branch_without_llm():
    det = detect_flanged_branch((DATA / SHEET).read_bytes())
    assert det is not None
    assert det.bolt_count == 12
    assert det.confidence >= 0.7
    # Ratios from the fixture's geometry: ring 170/220, bolt 15/220, bore 90/220.
    assert det.pcd_ratio == pytest.approx(0.77, abs=0.06)
    assert det.bolt_ratio == pytest.approx(0.068, abs=0.02)
    assert det.bore_ratio == pytest.approx(0.41, abs=0.06)
    # The shaded isometric is excluded: only the three line-art views count.
    assert det.views == 3


def test_simple_profile_sheet_is_not_misclassified():
    """The stepped-outline drawing has no bolt ring — detection must decline so
    profile_extrusion keeps handling it."""
    assert detect_flanged_branch((DATA / "stepped_profile.png").read_bytes()) is None


def test_detection_never_raises_on_garbage():
    assert detect_flanged_branch(b"not an image") is None


# ------------------------------------------------------------ notes fast path

def test_notes_parser_preserves_literal_decimals():
    p = params_from_notes(
        "flanged pipe branch, 12 bolt holes Ø1, flange OD 14.8, bore 10, "
        "height 15, wall 0.5, fillet R0.3")
    assert p is not None
    assert p["bolt_count"] == 12 and p["bolt_dia"] == pytest.approx(1)
    d = p["dims"]
    assert d["flange_outer_diameter_mm"] == pytest.approx(14.8)
    assert d["bore_diameter_mm"] == pytest.approx(10)
    assert d["main_pipe_length_mm"] == pytest.approx(15)
    assert d["wall_thickness_mm"] == pytest.approx(0.5)
    assert d["fillet_radius_mm"] == pytest.approx(0.3)


def test_notes_without_family_cue_do_not_trigger_fast_path():
    assert params_from_notes("plate 80x60 with 4 holes") is None
    assert params_from_notes(None) is None


def test_notes_override_builds_literal_size_part(client, auth):
    """User notes bypass vision AND detection defaults: the part is built at
    the stated literal size (14.8, not the Ø120 assumed-scale default)."""
    png = (DATA / SHEET).read_bytes()
    r = _post(client, auth, png,
              notes="flanged pipe branch, 12 bolt holes Ø1, flange OD 14.8, "
                    "bore 10, height 15, wall 0.5")
    assert r.status_code == 200, r.text
    d = r.json()["design"]
    bb = d["bounding_box_mm"]
    assert bb["y"] == pytest.approx(14.8, abs=0.3)
    assert bb["z"] == pytest.approx(15, abs=0.3)
    fails = [c for c in d["semantic_checks"]
             if not c.get("passed") and c.get("severity") == "critical"]
    assert fails == [], fails
    assert {e["fmt"] for e in d["exports"]} >= {"step", "stl"}
    # Honest status: user-stated numbers are REVIEW, never a clean PASS.
    assert d["drawing_fidelity"]["drawing_fidelity_status"] == "review"
    assert d["validation_status"] != "pass"


# --------------------------------------------- vision timeout → deterministic

def test_vision_timeout_still_generates_flanged_branch(client, auth, vision_outage):
    """THE fix: an OpenAI timeout no longer fails the complex drawing — the
    pixel-detected family builds deterministically, flagged REVIEW."""
    png = (DATA / SHEET).read_bytes()
    r = _post(client, auth, png)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["generated"] is True, out.get("message")
    d = out["design"]
    assert d["object_type"] == "flanged_pipe_branch"
    ids = {f["id"] for f in d["features"]}
    assert {"main_pipe", "branch_pipe",
            "top_flange", "bottom_flange", "branch_flange"} <= ids
    # Family probes all pass: bores continuous, bolt holes open on 3 flanges.
    by_name = {c["name"]: c for c in d["semantic_checks"]}
    for name in ("internal_passage_main", "internal_passage_branch",
                 "flange_present_top", "flange_present_bottom",
                 "flange_present_branch", "bolt_holes_open_top",
                 "bolt_holes_open_bottom", "bolt_holes_open_branch"):
        assert by_name[name]["passed"], by_name[name]
    # Assumed scale → REVIEW with the fallback flag, never PASS; export allowed.
    fid = d["drawing_fidelity"]
    assert fid["used_default_fallback"] is True
    assert fid["drawing_fidelity_status"] == "review"
    assert d["validation_status"] != "pass"
    assert d["download_blocked_reason"] is None
    assert {e["fmt"] for e in d["exports"]} >= {"step", "stl"}


def test_vision_timeout_on_unrecognizable_image_still_fails_cleanly(
        client, auth, vision_outage):
    """No pixel evidence + outage → controlled failure (no fabrication)."""
    r = _post(client, auth, TINY_PNG)
    out = r.json()
    assert out["generated"] is False and out["design"] is None


def test_simple_profile_still_generates_profile_extrusion(client, auth):
    """Regression guard: the hybrid branch route must not steal simple
    outline drawings from profile_extrusion."""
    png = (DATA / "stepped_profile.png").read_bytes()
    d = _post(client, auth, png).json()["design"]
    assert d["object_type"] == "profile_extrusion"


# ----------------------------------------------------- evidence assembly units

def test_interp_from_evidence_detection_only_uses_assumed_scale():
    det = detect_flanged_branch((DATA / SHEET).read_bytes())
    interp = interp_from_evidence(det)
    assert interp is not None
    assert interp.suggested_object_type == "flanged_pipe_branch"
    assert interp.interp_source == "raster_assumed"
    dims = interp.overall_dimensions
    assert dims["flange_outer_diameter_mm"] == pytest.approx(120)  # assumed anchor
    assert dims["bolt_circle_diameter_mm"] == pytest.approx(120 * det.pcd_ratio, rel=0.05)
    assert interp.holes[0].count == 12
    assert interp.generatable_with_assumptions()


def test_interp_from_evidence_notes_pin_units():
    det = detect_flanged_branch((DATA / SHEET).read_bytes())
    notes = params_from_notes("flanged pipe branch, 12 bolt holes, flange OD 14.8, "
                              "height 15, wall 0.5")
    interp = interp_from_evidence(det, notes_params=notes)
    assert interp.overall_dimensions["flange_outer_diameter_mm"] == pytest.approx(14.8)
    assert interp.drawing_units_confidence >= 0.9  # literal mm — never rescaled
    from app.drawing.scale import infer_scale

    scaled = infer_scale(interp)
    assert scaled.scale == 1.0
    assert scaled.dimensions["flange_outer_diameter_mm"] == pytest.approx(14.8)


def test_no_evidence_returns_none():
    assert interp_from_evidence(None, vision=None, notes_params=None) is None
