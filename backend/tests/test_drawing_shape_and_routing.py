"""Two regressions from the provider-timeout fallback work:

1. Non-circular holes were converted to circles — a hexagonal cutout came out
   round. Holes must keep their true shape (hexagon stays a polygon cut).
2. A flanged pipe spool drawing was misrouted to a Wheel rim (a flange's outer
   edge is literally a "rim", so a "flange rim diameter" callout hijacked the
   generic prompt router). Pipe/flange/spool drawings must route to the
   pipe/flange family, never rim/wheel/tire.

All while KEEPING the good behavior: a forced provider timeout still yields
generated=true from the deterministic fallback.
"""
from __future__ import annotations

import io
import math
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

DATA = Path(__file__).parent / "data"


# --------------------------------------------------------------- fixtures

def _hex_and_round_png() -> bytes:
    """A rounded-top bracket profile with a circular hole (bottom) and a
    HEXAGONAL hole (top lobe)."""
    img = Image.new("RGB", (620, 520), "white")
    d = ImageDraw.Draw(img)
    w = 3
    outline = [(120, 460), (120, 200), (160, 120), (300, 90), (440, 120),
               (480, 200), (480, 460)]
    d.line(outline + [outline[0]], fill="black", width=w)
    d.ellipse((254, 314, 346, 406), outline="black", width=w)  # round hole
    hx, hy, hr = 300, 190, 52
    hexpts = [(hx + hr * math.cos(math.pi / 6 + i * math.pi / 3),
               hy + hr * math.sin(math.pi / 6 + i * math.pi / 3)) for i in range(6)]
    d.line(hexpts + [hexpts[0]], fill="black", width=w)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class _TimeoutProvider:
    name = "openai"

    def interpret_drawing(self, *a, **k):
        raise TimeoutError("APITimeoutError: request timed out")


class _SpoolProvider:
    """Vision reads a flanged pipe spool AND (as the field model did) labels the
    flange's outer edge a 'rim' — the exact trigger of the rim misroute."""

    name = "openai"

    def interpret_drawing(self, *a, **k):
        return {
            "suggested_object_type": "flanged_pipe_spool",
            "detected_object_type": "flanged_pipe_spool",
            "overall_dimensions": {"flange_rim_diameter_mm": 120.0,
                                   "main_pipe_length_mm": 150.0,
                                   "main_pipe_outer_diameter_mm": 75.0,
                                   "bore_diameter_mm": 50.0},
            "holes": [{"diameter": 12.0, "count": 12}],
            "overall_confidence": 0.72, "drawing_units_confidence": 0.9,
        }


@pytest.fixture
def vision_outage(monkeypatch):
    import app.drawing.interpret as interp_mod

    monkeypatch.setattr(interp_mod, "get_provider", lambda: _TimeoutProvider())


@pytest.fixture
def spool_vision(monkeypatch):
    import app.drawing.interpret as interp_mod

    monkeypatch.setattr(interp_mod, "get_provider", lambda: _SpoolProvider())


def _post(client, auth, name: str, data: bytes, **form):
    return client.post(
        "/api/drawings/to-cad",
        files={"file": (name, io.BytesIO(data), "image/png")},
        data={"sync": "true", **{k: str(v) for k, v in form.items() if v is not None}},
        headers=auth["headers"],
    )


# ===================================================== PART A: hole shapes

def test_hex_hole_stays_a_polygon_not_a_circle(client, auth, vision_outage):
    """The classifier separates the round hole from the hex hole; the hex is
    cut as a real polygon (its shape metadata is hexagon/polygon, never circle)."""
    out = _post(client, auth, "hex.png", _hex_and_round_png()).json()
    assert out["generated"] is True, out.get("message")
    d = out["design"]
    # Part A: a polygon hole routes to the Sketch IR (which keeps the polygon),
    # not the feature-losing raster_profile extrusion.
    assert d["object_type"] == "reconstructed_sketch_part"
    assert d["dimension_report"]["measured"]["hole_count"] == 2

    shapes = [h.get("shape") for h in
              (out["analysis"]["features"]["through_holes"])]
    assert shapes.count("circle") == 1, shapes
    poly = [s for s in shapes if s not in ("circle", "ellipse")]
    assert len(poly) == 1 and poly[0] in ("hexagon", "regular_polygon",
                                          "arbitrary_polygon"), shapes
    assert shapes.count("circle") != 2, "both holes must NOT be circles"


def test_hex_hole_not_marked_equivalent_area_circle(client, auth, vision_outage):
    """When the exact polygon cut succeeds, NO 'approximated as a circle'
    assumption is recorded."""
    out = _post(client, auth, "hex.png", _hex_and_round_png()).json()
    d = out["design"]
    assert not any("equivalent" in a.lower() or "approximated as" in a.lower()
                   for a in d["assumptions"]), d["assumptions"]


def test_hex_profile_generates_valid_reviewable_model(client, auth, vision_outage):
    """Geometry is buildable and exportable; estimated scale → REVIEW/warning."""
    out = _post(client, auth, "hex.png", _hex_and_round_png()).json()
    d = out["design"]
    assert {e["fmt"] for e in d["exports"]} >= {"step", "stl"}
    assert d["validation_status"] != "pass"
    assert d["download_blocked_reason"] is None
    crit = [c for c in d["semantic_checks"]
            if not c.get("passed") and c.get("severity") == "critical"]
    assert crit == [], crit


def test_polygon_cut_classification_unit():
    """The raster classifier keeps a hexagon polygonal and a circle round."""
    from app.drawing.preprocess import preprocess_drawing_image
    from app.drawing.raster_profile import extract_profile

    clean, _ = preprocess_drawing_image(_hex_and_round_png())
    profile = extract_profile(clean)
    assert profile.hole_count == 2
    kinds = sorted(h.kind for h in profile.holes)
    assert "circle" in kinds
    assert any(k not in ("circle", "ellipse") for k in kinds), kinds
    # The polygon hole carries real vertices (so it can be cut as its shape).
    poly = [h for h in profile.holes if not h.is_round][0]
    assert len(poly.polygon) >= 3


# ============================================ PART B/C/D: flange not rim

def test_flanged_spool_routes_to_pipe_family_not_rim(client, auth, spool_vision):
    """The field bug: detected flanged_pipe_spool → part_family_rim → Wheel rim.
    It must now build a pipe/flange part."""
    out = _post(client, auth, "spool.png", (DATA / "flanged_pipe_branch_sheet.png").read_bytes()).json()
    assert out["generated"] is True, out.get("message")
    d = out["design"]
    assert d["object_type"] not in ("rim", "tire", "wheel_assembly"), d["object_type"]
    assert "rim" not in (d.get("object_type") or "")
    assert "wheel rim" not in (d.get("title") or "").lower()
    assert (d.get("route") or "") != "part_family_rim"
    # It is a pipe/flange family part.
    assert d["object_type"] in ("pipe_spool", "flanged_pipe_branch", "blind_flange",
                                "flange", "pipe_fitting", "pipe_tee"), d["object_type"]
    # No wheel geometry leaked in as assumptions.
    assert not any("spoke" in a.lower() or "wheel rim" in a.lower()
                   for a in d["assumptions"]), d["assumptions"]


def test_flanged_spool_builds_exportable_model(client, auth, spool_vision):
    out = _post(client, auth, "spool.png", (DATA / "flanged_pipe_branch_sheet.png").read_bytes()).json()
    d = out["design"]
    assert {e["fmt"] for e in d["exports"]} >= {"step", "stl"}


def test_normalize_maps_pipe_flange_detections():
    from app.schemas.drawing_spec import normalize_drawing_type

    assert normalize_drawing_type("flanged_pipe_spool") == "pipe_spool"
    assert normalize_drawing_type("pipe_flange") == "blind_flange"
    assert normalize_drawing_type("sectioned_pipe_assembly") == "pipe_spool"
    assert normalize_drawing_type("flange_with_bolt_pattern") == "flange"


def test_is_pipe_flange_detection_helper():
    from app.schemas.drawing_spec import is_pipe_flange_detection

    assert is_pipe_flange_detection("generic_mechanical_part", "flanged_pipe_spool")
    assert is_pipe_flange_detection("pipe_flange")
    assert not is_pipe_flange_detection("rim", "wheel rim")
    assert not is_pipe_flange_detection("generic_mechanical_part", "mounting_plate")


# ===================================================== PART B/D: rim guard

def test_rim_guard_pipe_flange_prompt_never_routes_to_rim():
    """A pipe/flange prompt carrying a 'rim' word (flange rim diameter) does NOT
    route to the wheel rim family."""
    from app.cad.part_family import detect_part_request

    for p in (
        "Create a generic mechanical part. Dimensions from the drawing: "
        "120mm flange rim diameter, 12x 12mm holes. All dimensions in mm.",
        "Create a pipe spool. 120mm flange OD, 50mm bore, 8 bolt holes on the rim.",
        "blind flange, 120mm rim diameter, 8 bolt holes, 50mm bore",
    ):
        req = detect_part_request(p)
        fam = None if req is None else req.requested_family
        assert fam not in ("rim", "tire", "wheel_assembly"), (p, fam)


def test_rim_guard_still_routes_genuine_wheel_rims():
    """A real wheel/rim request STILL routes to the rim family — the guard only
    fires for pipe/flange context."""
    from app.cad.part_family import detect_part_request

    for p in ("wheel rim, 200mm diameter, 5 spokes",
              "aluminium alloy wheel rim 18 inch",
              "make just the rim of a wheel, 150mm"):
        req = detect_part_request(p)
        assert req is not None and req.requested_family == "rim", p


def test_route_lock_rejects_rim_in_create_design(client, auth):
    """route_lock='pipe_flange' hard-blocks the wheel/rim family in
    create_design even for a prompt that WOULD otherwise route to rim — the
    belt-and-braces guarantee over the prompt regex."""
    from app.database import SessionLocal
    from app.services import design_service

    db = SessionLocal()
    try:
        # A bare "wheel rim" prompt routes to rim WITHOUT a lock...
        unlocked = design_service.create_design(
            db, "wheel rim 120mm with 8 spokes", None, "unlocked",
            auth["user"]["id"])
        assert unlocked.object_type == "rim"
        # ...but the same prompt under a pipe/flange lock never becomes a rim.
        locked = design_service.create_design(
            db, "wheel rim 120mm with 8 spokes", None, "locked",
            auth["user"]["id"], route_lock="pipe_flange")
        assert locked.object_type != "rim"
        assert (locked.route or "") != "part_family_rim"
    finally:
        db.close()
