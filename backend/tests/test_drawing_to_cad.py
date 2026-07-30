"""Drawing → CAD file-upload pipeline (POST /api/drawings/to-cad).

DXF/SVG parse deterministically (exact profiles + hole positions, no vision),
PDFs render to an image for the vision provider, images go straight to vision.
Every path lands in the SAME validated design pipeline: assumptions listed,
validation run, STEP/STL exported, critical failures gated.
"""
from __future__ import annotations

from tests.conftest import TINY_PNG

import io
from pathlib import Path

import pytest

DATA = Path(__file__).parent / "data"


def _post(client, auth, filename: str, data: bytes, media_type: str = "application/octet-stream",
          **form):
    """Opt-in sync form (the default async 202 + job flow is covered by
    test_drawing_jobs.py)."""
    return client.post(
        "/api/drawings/to-cad",
        files={"file": (filename, io.BytesIO(data), media_type)},
        data={"sync": "true", **{k: str(v) for k, v in form.items() if v is not None}},
        headers=auth["headers"],
    )


# ------------------------------------------------------------------ file types

def test_invalid_file_type_rejected_with_useful_error(client, auth):
    r = _post(client, auth, "notes.txt", b"this is not a drawing", "text/plain")
    assert r.status_code == 415
    detail = r.json()["detail"]
    assert "PNG" in detail and "DXF" in detail, detail


def test_empty_file_rejected(client, auth):
    r = _post(client, auth, "empty.svg", b"", "image/svg+xml")
    assert r.status_code == 400


def test_mislabeled_extension_detected_by_magic_bytes(client, auth):
    """A .png that actually contains SVG must route to the SVG parser."""
    svg = (DATA / "simple_flange.svg").read_bytes()
    r = _post(client, auth, "drawing.png", svg, "image/png")
    assert r.status_code == 200, r.text
    assert r.json()["analysis"]["source"] == "svg"


def test_unreadable_svg_is_422(client, auth):
    r = _post(client, auth, "broken.svg", b"<svg><notclosed</svg>", "image/svg+xml")
    assert r.status_code == 422


# ------------------------------------------------------------------- SVG path

def test_svg_adapter_plate_generates_validated_model(client, auth):
    """Rect outline + 4 corner holes + center bore -> plate with 5 exact
    holes; the design builds and is fully inspectable (bbox, assumptions),
    but since the drawing shows NO depth/thickness at all, the final
    manufacturable export is blocked (docs/drawing-to-cad-beta.md's
    unresolved-dimension gate) -- depth is a CRITICAL category, so this is a
    disclosed default that must NOT be silently exported, not merely a
    disclosed default that's fine to ship. See
    test_svg_adapter_plate_thickness_override_resolves_and_unblocks_export
    for the resolution path."""
    svg = (DATA / "simple_adapter_plate.svg").read_bytes()
    r = _post(client, auth, "simple_adapter_plate.svg", svg, "image/svg+xml")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["generated"] is True
    a = out["analysis"]
    assert a["source"] == "svg"
    assert a["outer_profile"]["kind"] == "rectangle"
    assert a["overall_dimensions"]["width_mm"] == pytest.approx(80)
    assert a["overall_dimensions"]["height_mm"] == pytest.approx(60)

    d = out["design"]
    assert {e["fmt"] for e in d["exports"]} >= {"step", "stl"}
    bb = d["bounding_box_mm"]
    assert bb["x"] == pytest.approx(80, abs=1)
    assert bb["y"] == pytest.approx(60, abs=1)
    assert bb["z"] == pytest.approx(6, abs=0.5)  # plate default depth
    # Missing depth generates with a visible assumption, not a silent guess...
    assert any("assumed" in s.lower() and "6" in s for s in d["assumptions"]), d["assumptions"]
    assert not d["needs_clarification"]
    # ...but a critical unresolved dimension still blocks the FINAL export.
    assert d["download_blocked_reason"] is not None
    fidelity = d["drawing_fidelity"]
    assert fidelity["drawing_fidelity_status"] == "failed"
    assert "depth" in fidelity["critical_unresolved"]


def test_svg_adapter_plate_thickness_override_resolves_and_unblocks_export(client, auth):
    """The resolution path: an explicit thickness_mm override on the SAME
    fixture (no depth on the drawing) resolves the previously-critical
    dimension, and the export is no longer blocked."""
    svg = (DATA / "simple_adapter_plate.svg").read_bytes()
    r = _post(client, auth, "simple_adapter_plate.svg", svg, "image/svg+xml", thickness_mm=6)
    assert r.status_code == 200, r.text
    d = r.json()["design"]
    assert d["download_blocked_reason"] is None
    assert d["drawing_fidelity"]["critical_unresolved"] == []
    assert d["bounding_box_mm"]["z"] == pytest.approx(6, abs=0.5)


def test_svg_hole_count_preserved(client, auth):
    """The four Ø6 corner holes + Ø20 center bore survive into the model
    (validation counts real openings in the compiled solid)."""
    svg = (DATA / "simple_adapter_plate.svg").read_bytes()
    r = _post(client, auth, "simple_adapter_plate.svg", svg, "image/svg+xml")
    assert r.status_code == 200
    d = r.json()["design"]
    hole_checks = [c for c in d["semantic_checks"] if "hole" in c["name"]]
    assert hole_checks, "hole-count validation must run"
    assert all(c["passed"] for c in hole_checks), hole_checks


def test_svg_flange_builds_circular_part_with_bolt_circle(client, auth):
    """simple_flange.svg has no thickness annotation either (see its file
    comment: only diameters are dimensioned) -- same critical-unresolved-
    depth gate as the adapter plate. The bolt-circle geometry is still
    extracted correctly; only the FINAL export is blocked."""
    svg = (DATA / "simple_flange.svg").read_bytes()
    r = _post(client, auth, "simple_flange.svg", svg, "image/svg+xml")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["generated"] is True
    a = out["analysis"]
    assert a["outer_profile"]["kind"] == "circle"
    assert a["outer_profile"]["diameter_mm"] == pytest.approx(120)
    assert a["features"]["patterns"], "6-hole bolt circle must be kept as a pattern"
    pat = a["features"]["patterns"][0]
    assert pat["count"] == 6
    assert pat["hole_diameter_mm"] == pytest.approx(10)
    assert pat["pitch_circle_diameter_mm"] == pytest.approx(90, abs=1)

    d = out["design"]
    bb = d["bounding_box_mm"]
    assert bb["x"] == pytest.approx(120, abs=1)
    assert {e["fmt"] for e in d["exports"]} >= {"step", "stl"}
    assert d["download_blocked_reason"] is not None
    assert "depth" in d["drawing_fidelity"]["critical_unresolved"]


def test_svg_flange_thickness_override_unblocks_export(client, auth):
    svg = (DATA / "simple_flange.svg").read_bytes()
    r = _post(client, auth, "simple_flange.svg", svg, "image/svg+xml", thickness_mm=10)
    assert r.status_code == 200, r.text
    d = r.json()["design"]
    assert d["download_blocked_reason"] is None


def test_drawing_built_design_is_capped_beta_and_flags_review(client, auth):
    """Every drawing-built design carries the Drawing -> CAD beta signal
    (docs/drawing-to-cad-beta.md) regardless of the underlying family's own
    text-prompt maturity, and flags mandatory review whenever fidelity isn't
    a clean "ok" or a critical dimension was never resolved."""
    svg = (DATA / "simple_flange.svg").read_bytes()
    blocked = _post(client, auth, "simple_flange.svg", svg, "image/svg+xml").json()["design"]
    assert blocked["drawing_beta"] is True
    assert blocked["drawing_review_required"] is True
    assert blocked["capability_level"] in {"validated_beta", "experimental"}

    resolved = _post(client, auth, "simple_flange.svg", svg, "image/svg+xml",
                     thickness_mm=10).json()["design"]
    assert resolved["drawing_beta"] is True
    # Fidelity may still be "review" (assumed/estimated data) even once the
    # critical block is resolved -- review-required only tracks fidelity/
    # critical-unresolved, not a permanently-set flag.
    assert resolved["drawing_review_required"] == (
        resolved["drawing_fidelity"]["drawing_fidelity_status"] != "ok"
        or bool(resolved["drawing_fidelity"]["critical_unresolved"]))


# ------------------------------------------------------------------- DXF path

def test_dxf_plate_4_holes_preserves_hole_count_and_annotation_depth(client, auth):
    dxf = (DATA / "simple_plate_4_holes.dxf").read_bytes()
    r = _post(client, auth, "simple_plate_4_holes.dxf", dxf, "application/dxf")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["generated"] is True
    a = out["analysis"]
    assert a["source"] == "dxf"
    assert a["outer_profile"]["kind"] == "rectangle"
    assert len(a["features"]["through_holes"]) == 4
    assert all(h["diameter_mm"] == pytest.approx(8) for h in a["features"]["through_holes"])
    # "THK 8" annotation drives the depth (not the family default).
    assert a["inferred_depth_mm"] == pytest.approx(8)

    d = out["design"]
    bb = d["bounding_box_mm"]
    assert bb["x"] == pytest.approx(100, abs=1)
    assert bb["y"] == pytest.approx(50, abs=1)
    assert bb["z"] == pytest.approx(8, abs=0.5)
    hole_checks = [c for c in d["semantic_checks"] if "hole" in c["name"]]
    assert hole_checks and all(c["passed"] for c in hole_checks), hole_checks
    assert {e["fmt"] for e in d["exports"]} >= {"step", "stl"}


def test_dxf_thickness_override_beats_annotation(client, auth):
    dxf = (DATA / "simple_plate_4_holes.dxf").read_bytes()
    r = _post(client, auth, "simple_plate_4_holes.dxf", dxf, thickness_mm=12)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["analysis"]["inferred_depth_mm"] == pytest.approx(12)
    assert out["design"]["bounding_box_mm"]["z"] == pytest.approx(12, abs=0.5)


def test_units_inch_scales_vector_geometry(client, auth):
    svg = (DATA / "simple_plate_4_holes.dxf").read_bytes()
    r = _post(client, auth, "simple_plate_4_holes.dxf", svg, units="inch")
    assert r.status_code == 200, r.text
    a = r.json()["analysis"]
    assert a["overall_dimensions"]["width_mm"] == pytest.approx(100 * 25.4)


# --------------------------------------------------------------- image / hints

def test_image_with_hint_generates(client, auth):
    """Mock provider classifies from the notes text (offline vision stand-in)."""
    r = _post(client, auth, "plate.png", TINY_PNG, "image/png",
              notes="rectangular mounting plate 90mm long 40mm wide 5mm thick "
                    "with 4 corner holes 5mm")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["generated"] is True
    d = out["design"]
    assert {e["fmt"] for e in d["exports"]} >= {"step", "stl"}


def test_unreadable_image_returns_failed_state_with_guidance(client, auth):
    """Mock mode can't read pixels: no hint → graceful failed state (no 5xx),
    with a message telling the user what to do."""
    r = _post(client, auth, "plate.png", TINY_PNG, "image/png")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["generated"] is False
    assert out["design"] is None
    assert out["message"]


def test_invalid_units_rejected(client, auth):
    r = _post(client, auth, "plate.png", b"\x89PNG fake", "image/png", units="furlongs")
    assert r.status_code == 422


def test_alias_route_drawing_to_cad(client, auth):
    svg = (DATA / "simple_flange.svg").read_bytes()
    r = client.post("/api/drawing-to-cad",
                    files={"file": ("f.svg", io.BytesIO(svg), "image/svg+xml")},
                    data={"sync": "true"}, headers=auth["headers"])
    assert r.status_code == 200, r.text
    assert r.json()["generated"] is True


def test_requires_auth(client):
    r = client.post("/api/drawings/to-cad",
                    files={"file": ("f.svg", io.BytesIO(b"<svg/>"), "image/svg+xml")})
    assert r.status_code in (401, 403)


# ------------------------------------------------------------------- PDF path

def test_pdf_renders_and_flows_to_vision(client, auth):
    """A rendered PDF reaches the vision path; in mock mode the hint carries
    the classification, proving render + hint plumbing works end to end."""
    pdfium = pytest.importorskip("pypdfium2")
    doc = pdfium.PdfDocument.new()
    doc.new_page(300, 300)
    buf = io.BytesIO()
    doc.save(buf)
    r = _post(client, auth, "drawing.pdf", buf.getvalue(), "application/pdf",
              notes="blind flange 120mm outer diameter 10mm thick with 6 bolt "
                    "holes 10mm")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["generated"] is True, out["message"]
    assert out["analysis"]["source"] == "vision"
    assert {e["fmt"] for e in out["design"]["exports"]} >= {"step", "stl"}


# --------------------------------------------------- validation / export gating

def test_successful_generation_returns_export_urls(client, auth):
    # thickness_mm supplied: this test is about export URL/download wiring,
    # not the unresolved-depth gate (covered separately) -- resolve depth
    # explicitly so the file is actually downloadable.
    svg = (DATA / "simple_adapter_plate.svg").read_bytes()
    r = _post(client, auth, "simple_adapter_plate.svg", svg, thickness_mm=6)
    assert r.status_code == 200
    d = r.json()["design"]
    for e in d["exports"]:
        assert e["url"] and f"/api/designs/{d['id']}/files/" in e["url"]
    # And the files actually download for the owner.
    fr = client.get(f"/api/designs/{d['id']}/files/step", headers=auth["headers"])
    assert fr.status_code == 200


def test_critical_failure_blocks_export(client, auth, monkeypatch):
    """When validation reports a critical failure, the design is inspectable
    but STEP/STL download is refused with recovery guidance."""
    from app.services import design_service

    svg = (DATA / "simple_adapter_plate.svg").read_bytes()
    r = _post(client, auth, "simple_adapter_plate.svg", svg)
    d = r.json()["design"]

    monkeypatch.setattr(design_service, "is_critical_failure", lambda design: True)
    fr = client.get(f"/api/designs/{d['id']}/files/step", headers=auth["headers"])
    assert fr.status_code == 409
    assert fr.json()["detail"]


# ------------------------------------------------------------- analysis schema

def test_analysis_shape_is_stable(client, auth):
    """The frontend relies on these analysis fields; keep the contract."""
    svg = (DATA / "simple_flange.svg").read_bytes()
    r = _post(client, auth, "simple_flange.svg", svg)
    a = r.json()["analysis"]
    for key in ("source", "units", "drawing_type", "overall_dimensions",
                "inferred_depth_mm", "outer_profile", "features",
                "assumptions", "ambiguities", "recommended_family",
                "confidence_score", "needs_clarification",
                "clarification_questions", "dimension_annotations"):
        assert key in a, f"missing analysis key {key}"
    assert a["features"].keys() >= {"through_holes", "patterns", "slots"}
