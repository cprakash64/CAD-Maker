"""Provider timeout must NEVER fail a drawing that carries usable linework.

The field failure: a complex multi-view sheet segmented into 9 regions, the
OpenAI interpretation timed out (APITimeoutError → llm_budget_exhausted →
LLMUnavailableError), and the job ended generated=false / design_id=null while
the simple single-profile drawing survived only via contour tracing. These
tests pin the fix end-to-end:

* forced provider timeout + any fixture with detectable geometry → a job that
  ends generated=true with a design id (REVIEW-flagged, export allowed);
* the deterministic fallback picks the MAIN view of a segmented sheet (not the
  title block / section frame) and preserves drawn holes;
* the user never sees the "try a simpler part" budget message when fallback
  generation succeeded;
* only images with NO usable linework may still end generated=false.

Fixtures: stepped_profile.png (simple dimensioned profile),
bracket_profile_hex.png (rounded-left bracket, 2 round holes + hex cutout),
multiview_bracket_sheet.png (main view + side view + hatched section + title
block, each view well under 1% of the sheet), and
flanged_pipe_branch_sheet.png (multi-view flanged fitting).
"""
from __future__ import annotations

import io
import time
from pathlib import Path

import pytest

DATA = Path(__file__).parent / "data"
FIXTURES = ("stepped_profile.png", "bracket_profile_hex.png",
            "multiview_bracket_sheet.png", "flanged_pipe_branch_sheet.png")


class _TimeoutProvider:
    """Simulates the field failure: every vision call times out."""

    name = "openai"

    def interpret_drawing(self, *a, **k):
        raise TimeoutError("APITimeoutError: request timed out")


@pytest.fixture
def vision_outage(monkeypatch):
    import app.drawing.interpret as interp_mod

    monkeypatch.setattr(interp_mod, "get_provider", lambda: _TimeoutProvider())


def _post_sync(client, auth, filename: str, data: bytes, **form):
    return client.post(
        "/api/drawings/to-cad",
        files={"file": (filename, io.BytesIO(data), "image/png")},
        data={"sync": "true", **{k: str(v) for k, v in form.items() if v is not None}},
        headers=auth["headers"],
    )


def _poll(client, auth, job_id: str, timeout_s: float = 180.0) -> dict:
    deadline = time.time() + timeout_s
    stages: list[str] = []
    while time.time() < deadline:
        r = client.get(f"/api/drawings/jobs/{job_id}", headers=auth["headers"])
        assert r.status_code == 200, r.text
        job = r.json()
        if not stages or stages[-1] != job["stage"]:
            stages.append(job["stage"])
        if job["status"] in ("done", "failed"):
            job["_stages"] = stages
            return job
        time.sleep(0.2)
    raise AssertionError(f"job {job_id} did not finish in {timeout_s}s")


# ---------------------------------------- every fixture survives the timeout

@pytest.mark.parametrize("fixture", FIXTURES)
def test_forced_timeout_still_generates_for_all_fixtures(
        client, auth, vision_outage, fixture):
    """THE acceptance gate: APITimeoutError on interpretation never yields
    generated=false when the image carries usable linework."""
    png = (DATA / fixture).read_bytes()
    r = _post_sync(client, auth, fixture, png)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["generated"] is True, f"{fixture}: {out.get('message')}"
    d = out["design"]
    assert d and d["id"]
    assert {e["fmt"] for e in d["exports"]} >= {"step", "stl"}
    # Estimated dimensions → REVIEW/warning, never a clean PASS; export allowed.
    assert d["validation_status"] != "pass"
    assert d["drawing_fidelity"]["drawing_fidelity_status"] == "review"
    assert d["download_blocked_reason"] is None
    # The budget error must not leak into the user-facing message.
    msg = (out.get("message") or "").lower()
    assert "simpler" not in msg and "took too long" not in msg


def test_forced_timeout_async_job_ends_with_design_id(client, auth, vision_outage):
    """The complex segmented sheet, through the REAL async job flow: 202 →
    polling → done with generated=true and a non-null design_id."""
    png = (DATA / "multiview_bracket_sheet.png").read_bytes()
    r = client.post(
        "/api/drawings/to-cad",
        files={"file": ("sheet.png", io.BytesIO(png), "image/png")},
        headers=auth["headers"])
    assert r.status_code == 202, r.text
    job = _poll(client, auth, r.json()["job_id"])
    assert job["status"] == "done", job
    assert job["design_id"], "segmented sheet must not end design_id=null"
    assert job["result"]["generated"] is True
    assert "fallback_generating" in job["_stages"]


def test_generate_endpoint_fallback_matches(client, auth, vision_outage):
    """/api/drawings/generate (the endpoint from the field logs) shares the
    fallback: the segmented sheet generates instead of generated=false."""
    png = (DATA / "multiview_bracket_sheet.png").read_bytes()
    r = client.post(
        "/api/drawings/generate",
        files={"file": ("sheet.png", io.BytesIO(png), "image/png")},
        data={"sync": "true"},
        headers=auth["headers"])
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["generated"] is True
    assert out["design"]["id"]


# --------------------------------------------- fallback picks the right view

def test_segmented_fallback_prefers_main_view_and_keeps_holes(
        client, auth, vision_outage):
    """The main stepped view wins over the title block, the hatched section
    frame, and the thin side view — and its drawn hole survives into the
    solid."""
    png = (DATA / "multiview_bracket_sheet.png").read_bytes()
    out = _post_sync(client, auth, "multiview_bracket_sheet.png", png).json()
    assert out["generated"] is True
    d = out["design"]
    assert d["object_type"] == "profile_extrusion"
    assert d["dimension_report"]["measured"]["hole_count"] == 1
    bb = d["bounding_box_mm"]
    # Main view aspect ≈ 180:120 — a title block (23:1) or side view (1:4.3)
    # substitution would show up immediately in the bbox shape.
    assert 1.1 < bb["x"] / bb["y"] < 2.2, bb


def test_bracket_with_hex_cutout_preserves_three_openings(
        client, auth, vision_outage):
    """Rounded bracket: two round holes + hex cutout = 3 openings. Because a
    polygon hole is present, the Sketch IR route wins over raster_profile (Part
    A) so the hexagon survives as a polygon cut, not an approximated circle."""
    png = (DATA / "bracket_profile_hex.png").read_bytes()
    out = _post_sync(client, auth, "bracket_profile_hex.png", png).json()
    assert out["generated"] is True
    d = out["design"]
    assert d["object_type"] == "reconstructed_sketch_part"  # sketch_ir won
    assert d["dimension_report"]["measured"]["hole_count"] == 3
    assert d["validation_status"] != "pass"  # estimated scale → REVIEW


# ------------------------------------------ Sketch IR vs raster_profile (PART A)

def _sketch_summary(out):
    return (out["design"].get("sketch_ir") or {}).get("summary") or {}


def test_sketch_ir_wins_over_raster_profile_when_polygon_hole_detected(
        client, auth, vision_outage):
    """A profile drawing carrying a polygon hole must NOT take the feature-losing
    raster_profile route — the Sketch IR (which keeps the polygon) wins."""
    png = (DATA / "bracket_profile_hex.png").read_bytes()
    out = _post_sync(client, auth, "bracket_profile_hex.png", png).json()
    assert out["generated"] is True
    assert out["design"]["object_type"] == "reconstructed_sketch_part"
    s = _sketch_summary(out)
    assert s.get("cut_by_kind", {}).get("polygon_hole") == 1


def test_hex_profile_final_source_is_sketch_ir(client, auth, vision_outage):
    """The persisted analysis records the winning source as sketch_ir, never
    raster_profile, for a drawing whose IR has a polygon hole."""
    png = (DATA / "bracket_profile_hex.png").read_bytes()
    out = _post_sync(client, auth, "bracket_profile_hex.png", png).json()
    analysis = out.get("analysis") or {}
    assert analysis.get("source") == "sketch_ir", analysis.get("source")


def test_hex_polygon_not_downgraded_to_circle(client, auth, vision_outage):
    """The hexagon stays a polygon cut (>=5 traced vertices), never collapsed to
    a circular hole."""
    png = (DATA / "bracket_profile_hex.png").read_bytes()
    out = _post_sync(client, auth, "bracket_profile_hex.png", png).json()
    ir = (out["design"].get("sketch_ir") or {}).get("ir") or {}
    polys = [c for c in ir.get("cut_features", []) if c["kind"] == "polygon_hole"]
    assert len(polys) == 1
    assert len(polys[0]["vertices"]) >= 5, polys[0]
    # And a polygon_cut feature actually reached the built plan.
    kinds = {f.get("type") for f in out["design"].get("features", [])}
    assert "polygon_cut" in kinds or _sketch_summary(out).get(
        "cut_by_kind", {}).get("polygon_hole") == 1


def test_sketch_ir_features_not_silently_dropped(client, auth, vision_outage):
    """The measured through-hole count matches the IR's through-feature count —
    no detected opening is dropped between IR and CAD."""
    png = (DATA / "bracket_profile_hex.png").read_bytes()
    out = _post_sync(client, auth, "bracket_profile_hex.png", png).json()
    s = _sketch_summary(out)
    measured = out["design"]["dimension_report"]["measured"]["hole_count"]
    assert measured == s["through_holes"], (measured, s)


def test_no_usable_linework_still_fails_cleanly(client, auth, vision_outage):
    """The ONLY remaining generated=false case: no detectable geometry."""
    out = _post_sync(client, auth, "noise.png", b"\x89PNG not a real image").json()
    assert out["generated"] is False
    assert out["design"] is None
    assert out["message"]


def test_llm_unavailable_error_also_triggers_fallback(client, auth, monkeypatch):
    """LLMUnavailableError (budget exhausted) takes the same fallback path as
    a raw APITimeoutError."""
    from app.llm.base import LLMUnavailableError

    class _Unavailable:
        name = "openai"

        def interpret_drawing(self, *a, **k):
            raise LLMUnavailableError(
                "Generation took too long and was stopped. Please try a simpler "
                "or more specific part, then try again.")

    import app.drawing.interpret as interp_mod

    monkeypatch.setattr(interp_mod, "get_provider", lambda: _Unavailable())
    png = (DATA / "multiview_bracket_sheet.png").read_bytes()
    out = _post_sync(client, auth, "sheet.png", png).json()
    assert out["generated"] is True
    msg = (out.get("message") or "").lower()
    assert "simpler" not in msg, "the budget error must not reach the user"


# ------------------------------------------------------- warning transparency

def test_fallback_message_explains_estimation(client, auth, vision_outage):
    png = (DATA / "multiview_bracket_sheet.png").read_bytes()
    out = _post_sync(client, auth, "sheet.png", png).json()
    assert out["generated"] is True
    assert "best-effort" in (out.get("message") or "").lower()
    d = out["design"]
    assert any("estimated" in a.lower() or "assumed" in a.lower() or
               "envelope" in a.lower() for a in d["assumptions"]), d["assumptions"]


# ------------------------------------------------------------- unit coverage

def test_dimension_label_parser_classifies_callouts():
    from app.drawing.dim_labels import parse_dimension_labels

    p = parse_dimension_labels(["50  30  10 20 18", "44 46 42", "144°",
                                "R1.6", "Ø4", "12xØ1", "THK 6"])
    assert max(p.linear) == 50.0
    assert p.envelope_mm == 50.0
    assert 144.0 in p.angles and 144.0 not in p.linear
    assert 1.6 in p.radii
    assert 4.0 in p.diameters
    assert (12, 1.0) in p.hole_callouts
    assert p.thickness == 6.0


def test_dimension_labels_scale_profile_when_available():
    """A parsed '50' label scales the traced contour instead of the 100mm
    default envelope (best-effort dimension handling)."""
    from app.drawing.preprocess import preprocess_drawing_image
    from app.drawing.raster_profile import extract_profile
    from app.services.drawing_to_spec import plan_from_raster_profile

    clean, _ = preprocess_drawing_image((DATA / "stepped_profile.png").read_bytes())
    profile = extract_profile(clean)
    assert profile is not None
    plan, used_default = plan_from_raster_profile(
        profile, None, dimension_texts=["50", "30", "R1.6"])
    assert used_default is True  # still flagged: label-derived scale is REVIEW
    assert plan.expected.bbox_mm["x"] == pytest.approx(50, abs=1) or \
        plan.expected.bbox_mm["y"] == pytest.approx(50, abs=1)
    assert any("estimated" in a.lower() for a in plan.assumptions)


def test_preprocess_flattens_transparency_and_downscales():
    import io as _io

    from PIL import Image

    from app.drawing.preprocess import preprocess_drawing_image

    img = Image.new("RGBA", (2400, 1800), (0, 0, 0, 0))  # fully transparent
    from PIL import ImageDraw

    d = ImageDraw.Draw(img)
    d.rectangle((200, 200, 2200, 1600), outline=(0, 0, 0, 255), width=6)
    buf = _io.BytesIO()
    img.save(buf, format="PNG")
    out, notes = preprocess_drawing_image(buf.getvalue(), max_side=1600)
    cleaned = Image.open(_io.BytesIO(out))
    assert max(cleaned.size) <= 1600
    assert cleaned.mode == "RGB"
    # Background is white, not black (transparency flattened).
    assert cleaned.getpixel((2, 2)) == (255, 255, 255)


def test_fallback_never_raises_on_garbage():
    from app.services.drawing_best_effort import generate_best_effort_from_drawing

    assert generate_best_effort_from_drawing(b"not an image") is None
