"""Regression: Drawing → CAD must NEVER produce an unrelated generic/default
part when interpretation fails or degrades.

The field failure: vision timed out on an uploaded stepped-profile drawing, the
job reported generated=false/design_id=null — yet a generic 60×40×6 plate with
12 holes appeared. These tests pin the whole class shut:

* provider OUTAGE (timeout) → generated=false, NO design row created, even
  when the user supplied guidance text (no hint fabrication);
* a stepped-profile drawing builds a profile_extrusion via deterministic
  contour tracing — zero invented holes, outline preserved, never the default
  plate — and, when the scale was assumed, it is REVIEW, never PASS;
* fidelity gating: hint-classified or default-fallback builds can't be PASS,
  and a FAILED fidelity blocks manufacturing export.
"""
from __future__ import annotations

from tests.conftest import TINY_PNG

import io
from pathlib import Path

import pytest

DATA = Path(__file__).parent / "data"


def _post(client, auth, filename: str, data: bytes, media_type="image/png", **form):
    return client.post(
        "/api/drawings/to-cad",
        files={"file": (filename, io.BytesIO(data), media_type)},
        data={"sync": "true", **{k: str(v) for k, v in form.items() if v is not None}},
        headers=auth["headers"],
    )


class _TimeoutProvider:
    """Simulates the field failure: every vision call times out."""

    name = "openai"

    def interpret_drawing(self, *a, **k):
        raise TimeoutError("APITimeoutError: request timed out")


@pytest.fixture
def vision_outage(monkeypatch):
    import app.drawing.interpret as interp_mod

    monkeypatch.setattr(interp_mod, "get_provider", lambda: _TimeoutProvider())


def _design_count(client, auth) -> int:
    r = client.get("/api/designs", headers=auth["headers"])
    return len(r.json())


# ------------------------------------------------- outage → controlled failure

def test_vision_timeout_returns_generated_false_and_creates_no_design(
        client, auth, vision_outage):
    """Un-traceable image + provider timeout → clean failure, zero designs."""
    before = _design_count(client, auth)
    r = _post(client, auth, "drawing.png", TINY_PNG)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["generated"] is False
    assert out["design"] is None
    assert out["message"], "the failure must carry a useful message"
    assert _design_count(client, auth) == before, "no design row may be created"


def test_vision_timeout_with_hint_does_not_fabricate_part(client, auth, vision_outage):
    """THE regression: guidance text must not become an invented part when the
    provider timed out — the hint describes intent, not the drawing."""
    before = _design_count(client, auth)
    r = _post(client, auth, "drawing.png", TINY_PNG,
              notes="mounting plate 60mm x 40mm x 6mm with 12 holes")
    out = r.json()
    assert out["generated"] is False, "hint fabrication after timeout is forbidden"
    assert out["design"] is None
    assert _design_count(client, auth) == before


def test_generate_endpoint_shares_the_same_gating(client, auth, vision_outage):
    """/api/drawings/generate is a thin wrapper over the same pipeline."""
    r = client.post(
        "/api/drawings/generate",
        files={"file": ("drawing.png", io.BytesIO(TINY_PNG), "image/png")},
        data={"sync": "true", "hint": "bracket with holes"},
        headers=auth["headers"])
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["generated"] is False and out["design"] is None


def test_contentless_interpretation_cannot_generate():
    """A 'generic mechanical part' reading with no dims/holes has nothing real
    to build from — it must not pass the generation gate."""
    from app.schemas.drawing_spec import DrawingInterpretationSpec

    empty = DrawingInterpretationSpec(
        suggested_object_type="generic_mechanical_part",
        overall_confidence=0.7)
    assert not empty.generatable_with_assumptions()
    with_dims = DrawingInterpretationSpec(
        suggested_object_type="generic_mechanical_part",
        overall_dimensions={"width": 80.0}, overall_confidence=0.7)
    assert with_dims.generatable_with_assumptions()


# ------------------------------------------- stepped profile → profile_extrusion

def test_stepped_profile_builds_profile_extrusion_not_default_plate(client, auth):
    png = (DATA / "stepped_profile.png").read_bytes()
    r = _post(client, auth, "stepped_profile.png", png)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["generated"] is True
    d = out["design"]
    assert d["object_type"] == "profile_extrusion"
    # Not the invented default 60×40×6 plate with 12 holes.
    bb = d["bounding_box_mm"]
    assert (round(bb["x"]), round(bb["y"]), round(bb["z"])) != (60, 40, 6)
    checks = {c["name"]: c for c in d["semantic_checks"]}
    assert checks["no_invented_holes"]["passed"] is True
    assert checks["profile_outline_preserved"]["passed"] is True
    assert {e["fmt"] for e in d["exports"]} >= {"step", "stl"}


def test_stepped_profile_outline_is_not_a_rectangle(client, auth):
    """The stepped outline must survive into the solid: its volume is well
    below bbox volume (a substituted rectangular plate would fill the bbox)."""
    png = (DATA / "stepped_profile.png").read_bytes()
    d = _post(client, auth, "stepped_profile.png", png).json()["design"]
    bb = d["bounding_box_mm"]
    report = d["dimension_report"]
    vol = report["measured"]["volume_mm3"]
    bbox_vol = bb["x"] * bb["y"] * bb["z"]
    assert vol < 0.9 * bbox_vol, \
        f"solid fills the bbox ({vol}/{bbox_vol}) — outline replaced by a rectangle?"


def test_stepped_profile_zero_holes_enforced(client, auth):
    png = (DATA / "stepped_profile.png").read_bytes()
    d = _post(client, auth, "stepped_profile.png", png).json()["design"]
    assert d["dimension_report"]["measured"]["hole_count"] == 0


def test_stepped_profile_with_assumed_scale_is_review_not_pass(client, auth):
    """Mock mode reads no dimensions → the scale is assumed → REVIEW, not PASS,
    and the fidelity block says why."""
    png = (DATA / "stepped_profile.png").read_bytes()
    d = _post(client, auth, "stepped_profile.png", png).json()["design"]
    fid = d["drawing_fidelity"]
    assert fid["used_default_fallback"] is True
    assert fid["drawing_fidelity_status"] == "review"
    assert d["validation_status"] != "pass"
    assert d["download_blocked_reason"] is None  # review warns, failed blocks


def test_profile_with_drawn_holes_generates_with_holes_preserved(client, auth):
    """Enclosed circles in the drawing are PRESERVED by the deterministic
    profile route: when vision can't read the image, the traced contour + its
    holes still generate a REVIEW-flagged part (never a silent hole drop, and
    never a failure)."""
    png = (DATA / "stepped_profile_2_holes.png").read_bytes()
    out = _post(client, auth, "stepped_profile_2_holes.png", png).json()
    assert out["generated"] is True
    d = out["design"]
    assert d["object_type"] == "profile_extrusion"
    assert d["dimension_report"]["measured"]["hole_count"] == 2
    # Assumed scale → REVIEW, never a clean PASS; export still allowed.
    assert d["drawing_fidelity"]["used_default_fallback"] is True
    assert d["validation_status"] != "pass"
    assert d["download_blocked_reason"] is None
    assert {e["fmt"] for e in d["exports"]} >= {"step", "stl"}


def test_vision_timeout_with_traceable_profile_still_generates(client, auth,
                                                               vision_outage):
    """Requirement: on provider timeout, fall back to the DETERMINISTIC profile
    extraction — a controlled, clearly-flagged result (REVIEW), never generic."""
    png = (DATA / "stepped_profile.png").read_bytes()
    out = _post(client, auth, "stepped_profile.png", png).json()
    assert out["generated"] is True
    d = out["design"]
    assert d["object_type"] == "profile_extrusion"
    assert d["drawing_fidelity"]["used_default_fallback"] is True
    assert d["validation_status"] != "pass"


# ---------------------------------------------------------- fidelity gating

def test_hint_classified_design_is_never_a_clean_pass(client, auth):
    """Dev-workaround builds (classified from text, not the image) are REVIEW."""
    r = client.post(
        "/api/drawings/generate",
        files={"file": ("drawing.png", io.BytesIO(TINY_PNG), "image/png")},
        data={"sync": "true",
              "hint": "flanged pipe branch, 12 holes per flange, 90mm main pipe"},
        headers=auth["headers"])
    out = r.json()
    assert out["generated"] is True
    d = out["design"]
    fid = d["drawing_fidelity"]
    assert fid["used_default_fallback"] is True
    assert d["validation_status"] != "pass", \
        "a part built from text (not the drawing) must not be PASS"


def test_failed_fidelity_blocks_export():
    """Unit: a FAILED fidelity injects a critical validation failure."""
    from app.routers.drawings import _attach_fidelity, _fidelity_report

    fid = _fidelity_report(0.2)
    assert fid["drawing_fidelity_status"] == "failed"
    semantic: dict = {}
    _attach_fidelity(None, semantic, fid)
    v = semantic["dimension_report"]["validation"]
    assert v["status"] == "critical_failure"
    assert any("could not be read" in c for c in v["critical_failures"])


def test_fidelity_report_levels():
    from app.routers.drawings import _fidelity_report

    assert _fidelity_report(0.9)["drawing_fidelity_status"] == "ok"
    assert _fidelity_report(0.6)["drawing_fidelity_status"] == "review"
    assert _fidelity_report(0.9, provider_error=True)["drawing_fidelity_status"] == "review"
    assert _fidelity_report(0.9, used_default_fallback=True)["drawing_fidelity_status"] == "review"
    assert _fidelity_report(0.2)["drawing_fidelity_status"] == "failed"
