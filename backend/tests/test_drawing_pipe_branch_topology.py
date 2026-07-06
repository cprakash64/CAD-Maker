"""A flanged pipe BRANCH/TEE drawing must build a branch assembly (main pipe +
side branch + third flange), never a straight two-flange pipe SPOOL.

The field failure: a multi-view branch sheet (flange front view, pipe
elevations, section A-A, and an isometric showing the side outlet) was labelled
"flanged_pipe_spool" by vision and built as a straight spool — the entire side
branch was dropped. These tests pin the topology detector + the branch route
override, and guard that a genuine STRAIGHT spool is NOT over-corrected into a
branch.
"""
from __future__ import annotations

import io
import math
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

DATA = Path(__file__).parent / "data"
BRANCH_SHEET = "flanged_pipe_branch_sheet.png"


# ------------------------------------------------------------ fixtures

def _straight_spool_sheet() -> bytes:
    """A STRAIGHT flanged spool sheet: a flange front view, ONE long straight
    side elevation, a section view, and a single-tube (elongated) isometric.
    No side branch."""
    W, H = 1200, 940
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    w = 3
    cx, cy, R = 240, 240, 175
    d.ellipse((cx - R, cy - R, cx + R, cy + R), outline="black", width=w)
    d.ellipse((cx - 55, cy - 55, cx + 55, cy + 55), outline="black", width=w)
    for i in range(8):
        a = i * math.pi / 4
        bx, by = cx + 130 * math.cos(a), cy + 130 * math.sin(a)
        d.ellipse((bx - 12, by - 12, bx + 12, by + 12), outline="black", width=w)
    d.rectangle((120, 560, 200, 900), outline="black", width=w)          # elevation
    d.rectangle((98, 560, 222, 590), outline="black", width=w)
    d.rectangle((98, 870, 222, 900), outline="black", width=w)
    d.rectangle((720, 120, 790, 470), outline="black", width=w)          # section
    for yy in range(130, 460, 16):
        d.line((722, yy, 788, yy + 24), fill="black", width=1)
    iso = Image.new("RGB", (430, 320), "white")                          # long tube
    di = ImageDraw.Draw(iso)
    for t in range(0, 100):
        x, y = 40 + t * 3.3, 250 - t * 1.9
        di.ellipse((x - 34, y - 34, x + 34, y + 34), fill=(150, 150, 150))
    img.paste(iso, (760, 590))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class _SpoolLabelProvider:
    """Vision mislabels the branch sheet as a straight spool (the field case)."""

    name = "openai"

    def interpret_drawing(self, *a, **k):
        return {"suggested_object_type": "flanged_pipe_spool",
                "detected_object_type": "flanged_pipe_spool",
                "overall_dimensions": {"flange_outer_diameter_mm": 120.0,
                                       "main_pipe_length_mm": 150.0,
                                       "main_pipe_outer_diameter_mm": 75.0,
                                       "bore_diameter_mm": 50.0},
                "holes": [{"diameter": 12.0, "count": 12}],
                "overall_confidence": 0.72, "drawing_units_confidence": 0.9}


class _SpoolCorrectProvider:
    """Vision reads a genuine STRAIGHT spool correctly."""

    name = "openai"

    def interpret_drawing(self, *a, **k):
        return {"suggested_object_type": "pipe_spool",
                "detected_object_type": "pipe_spool",
                "overall_dimensions": {"flange_outer_diameter_mm": 120.0,
                                       "main_pipe_length_mm": 150.0,
                                       "main_pipe_outer_diameter_mm": 75.0,
                                       "bore_diameter_mm": 50.0},
                "holes": [{"diameter": 12.0, "count": 8}],
                "overall_confidence": 0.72, "drawing_units_confidence": 0.9}


class _TimeoutProvider:
    name = "openai"

    def interpret_drawing(self, *a, **k):
        raise TimeoutError("APITimeoutError: request timed out")


def _use(monkeypatch, provider):
    import app.drawing.interpret as interp_mod

    monkeypatch.setattr(interp_mod, "get_provider", lambda: provider)


def _post(client, auth, name, data, **form):
    return client.post(
        "/api/drawings/to-cad",
        files={"file": (name, io.BytesIO(data), "image/png")},
        data={"sync": "true", **{k: str(v) for k, v in form.items() if v is not None}},
        headers=auth["headers"],
    ).json()


_BRANCH_FEATURES = {"main_pipe", "branch_pipe", "top_flange",
                    "bottom_flange", "branch_flange"}


# ================================================ branch, not spool

def test_pipe_branch_fixture_not_spool(client, auth, monkeypatch):
    """Vision says spool, topology says branch → a flanged branch assembly is
    built (side branch present), never a straight two-flange spool."""
    _use(monkeypatch, _SpoolLabelProvider())
    out = _post(client, auth, "branch.png", (DATA / BRANCH_SHEET).read_bytes())
    assert out["generated"] is True, out.get("message")
    d = out["design"]
    assert d["object_type"] == "flanged_pipe_branch"
    assert d["object_type"] not in ("rim", "wheel_rim", "pipe_spool")
    assert "pipe spool" not in (d.get("title") or "").lower()
    ids = {f["id"] for f in d["features"]}
    assert _BRANCH_FEATURES <= ids, ids
    det = d["pipe_branch_detail"]
    assert det["side_branch_present"] is True
    assert det["branch_flange_present"] is True
    assert det["flange_count"] >= 3
    assert det["pipe_axis_count"] >= 2
    # No wheel geometry.
    assert not any("spoke" in a.lower() for a in d["assumptions"])
    assert {e["fmt"] for e in d["exports"]} >= {"step", "stl"}


def test_pipe_branch_survives_provider_timeout(client, auth, monkeypatch):
    """The deterministic path (forced timeout) also builds the branch."""
    _use(monkeypatch, _TimeoutProvider())
    out = _post(client, auth, "branch.png", (DATA / BRANCH_SHEET).read_bytes())
    assert out["generated"] is True
    d = out["design"]
    assert d["object_type"] == "flanged_pipe_branch"
    assert d["pipe_branch_detail"]["side_branch_present"] is True
    assert _BRANCH_FEATURES <= {f["id"] for f in d["features"]}


def test_pipe_branch_isometric_topology_signal():
    """The classifier reports side-branch evidence for the branch sheet, driven
    by the compact isometric preview + pipe elevations."""
    from app.drawing.pipe_branch_evidence import detect_side_branch
    from app.drawing.preprocess import preprocess_drawing_image

    clean, _ = preprocess_drawing_image((DATA / BRANCH_SHEET).read_bytes())
    topo = detect_side_branch(clean)
    assert topo is not None
    assert topo.side_branch is True
    assert topo.iso_present is True
    assert topo.reason in ("compact_isometric_side_branch",
                           "multiple_flange_faces", "multiple_pipe_elevations")


# ================================================ don't over-correct

def test_straight_pipe_spool_still_allowed(client, auth, monkeypatch):
    """A genuine straight spool (no side branch) must stay a pipe_spool — the
    branch override must not swallow every pipe drawing."""
    _use(monkeypatch, _SpoolCorrectProvider())
    out = _post(client, auth, "spool.png", _straight_spool_sheet())
    assert out["generated"] is True
    d = out["design"]
    assert d["object_type"] == "pipe_spool"
    ids = {f["id"] for f in d["features"]}
    assert "branch_pipe" not in ids and "branch_flange" not in ids


def test_straight_spool_topology_has_no_branch():
    from app.drawing.pipe_branch_evidence import detect_side_branch
    from app.drawing.preprocess import preprocess_drawing_image

    clean, _ = preprocess_drawing_image(_straight_spool_sheet())
    topo = detect_side_branch(clean)
    assert topo is None or topo.side_branch is False


# ================================================ route lock

def test_route_lock_cannot_downgrade_branch_to_spool():
    """A flanged_pipe_branch interpretation always builds the branch family via
    the deterministic branch builder — never a pipe_spool plan."""
    from app.drawing.scale import infer_scale
    from app.routers.drawings import _deterministic_drawing_plan, _drawing_to_prompt
    from app.schemas.drawing_spec import DrawingInterpretationSpec

    interp = DrawingInterpretationSpec(
        suggested_object_type="flanged_pipe_branch",
        detected_object_type="flanged_pipe_branch",
        overall_dimensions={"flange_outer_diameter_mm": 120.0,
                            "main_pipe_outer_diameter_mm": 75.0,
                            "branch_pipe_outer_diameter_mm": 50.0},
        holes=[{"diameter": 12.0, "count": 12}],
        overall_confidence=0.7)
    scaled = infer_scale(interp)
    plan = _deterministic_drawing_plan(interp, scaled, _drawing_to_prompt(interp, scaled))
    assert plan is not None
    assert plan.object_type in ("flanged_pipe_branch", "pipe_tee")
    assert plan.object_type != "pipe_spool"


def test_branch_text_cue_forces_branch():
    """Explicit branch/tee/section-A-A text is a decisive branch signal."""
    from app.drawing.pipe_branch_evidence import (
        has_branch_text_cue,
        has_straight_spool_text_cue,
    )

    assert has_branch_text_cue("flanged pipe branch with side outlet")
    assert has_branch_text_cue("see Section A-A")
    assert not has_branch_text_cue("straight pipe spool, two flanges")
    assert has_straight_spool_text_cue("straight pipe spool piece")


# ================================================ route lock: NEVER the LLM (PART E)

def _forbid_llm(monkeypatch):
    """Make any LLM cad_plan / generic-parser entry point explode, so a test can
    prove the deterministic family builder handled the drawing on its own."""
    from app.services import design_service

    def _boom(*a, **k):
        raise AssertionError("create_design (LLM cad_plan / generic parser) "
                             "must NOT be called for a route-locked drawing family")

    monkeypatch.setattr(design_service, "create_design", _boom)


def test_flanged_pipe_branch_drawing_does_not_call_llm_cad_plan(
        client, auth, monkeypatch):
    """A detected flanged pipe branch builds deterministically; the LLM cad_plan
    path is never reached even though vision mislabelled it a spool."""
    _use(monkeypatch, _SpoolLabelProvider())
    _forbid_llm(monkeypatch)
    out = _post(client, auth, "branch.png", (DATA / BRANCH_SHEET).read_bytes())
    assert out["generated"] is True, out.get("message")
    d = out["design"]
    assert d["object_type"] == "flanged_pipe_branch"
    assert _BRANCH_FEATURES <= {f["id"] for f in d["features"]}


def test_pipe_branch_provider_timeout_still_deterministic_builds(
        client, auth, monkeypatch):
    """A forced provider timeout still yields the deterministic branch — with no
    fall-through to the LLM."""
    _use(monkeypatch, _TimeoutProvider())
    _forbid_llm(monkeypatch)
    out = _post(client, auth, "branch.png", (DATA / BRANCH_SHEET).read_bytes())
    assert out["generated"] is True, out.get("message")
    d = out["design"]
    assert d["object_type"] == "flanged_pipe_branch"
    assert d["pipe_branch_detail"]["side_branch_present"] is True


def test_pipe_branch_route_lock_bypasses_generic_parser(client, auth, monkeypatch):
    """Even if the FIRST deterministic build fails to compile, the route lock
    REPAIRS the deterministic builder — it never calls the generic parser/LLM."""
    _use(monkeypatch, _SpoolLabelProvider())
    _forbid_llm(monkeypatch)
    import app.routers.drawings as drawings_mod

    real = drawings_mod.design_service.create_design_from_plan
    calls = {"n": 0}

    def _flaky(db, plan, *a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            from app.cad.base import CadGenerationError
            raise CadGenerationError("simulated kernel failure on first attempt")
        return real(db, plan, *a, **k)

    monkeypatch.setattr(drawings_mod.design_service, "create_design_from_plan",
                        _flaky)
    out = _post(client, auth, "branch.png", (DATA / BRANCH_SHEET).read_bytes())
    assert out["generated"] is True, out.get("message")
    assert out["design"]["object_type"] == "flanged_pipe_branch"
    assert calls["n"] >= 2, "the repair attempt should have run"


def test_pipe_branch_repeatable_under_forced_timeout(client, auth, monkeypatch):
    """PART 3: the same image, run 5× with the provider forced to time out, must
    produce the SAME route, source, feature counts, and dimensions — no more
    14.9mm one run / 120mm the next."""
    _use(monkeypatch, _TimeoutProvider())
    png = (DATA / BRANCH_SHEET).read_bytes()
    seen = set()
    for _ in range(5):
        out = _post(client, auth, "branch.png", png)
        assert out["generated"] is True, out.get("message")
        d = out["design"]
        bb = d["bounding_box_mm"]
        ids = tuple(sorted(f["id"] for f in d["features"]))
        seen.add((
            d["object_type"],
            out["analysis"]["source"] if out.get("analysis") else None,
            round(bb["x"], 1), round(bb["y"], 1), round(bb["z"], 1),
            len(d["features"]),
            ids,
        ))
    assert len(seen) == 1, f"non-deterministic across runs: {seen}"


def test_pipe_branch_estimated_dimensions_is_review_not_pass(
        client, auth, monkeypatch):
    """A branch built from inferred/estimated dimensions is REVIEW, never a clean
    PASS (Part G)."""
    _use(monkeypatch, _SpoolLabelProvider())
    out = _post(client, auth, "branch.png", (DATA / BRANCH_SHEET).read_bytes())
    assert out["generated"] is True, out.get("message")
    d = out["design"]
    assert d["validation_status"] != "pass", d["validation_status"]
    assert d["drawing_fidelity"]["drawing_fidelity_status"] in ("review", "failed")
