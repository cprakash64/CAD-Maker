"""Hex standoff trust — BLACK-BOX through the same create_design path the browser
uses.

The release blocker: "Make a hex standoff." rendered a ROUND cylinder (routed
through the round-spacer / LLM path) yet reported PASS. These tests pin: (1) every
hex standoff routes to the deterministic hex-prism builder, (2) the across-flats
dimension is preserved EXACTLY with across-corners derived, (3) the EXPORTED STL
silhouette actually has six flat sides, and (4) a round cylinder can never PASS
as a hex standoff.
"""
from __future__ import annotations

import math

import pytest

from app.cad.semantic_audits import audit_hex_standoff, measure_hex_sides

ROUTE = "deterministic_hex_standoff"
COS30 = math.cos(math.pi / 6.0)


def _create(client, auth, prompt: str) -> dict:
    r = client.post("/api/designs/create", json={"prompt": prompt}, headers=auth["headers"])
    assert r.status_code == 200, r.text
    return r.json()


def _download(client, auth, design_id: str, fmt: str) -> bytes:
    r = client.get(f"/api/designs/{design_id}/files/{fmt}", headers=auth["headers"])
    assert r.status_code == 200, f"{fmt} download failed: {r.status_code} {r.text[:200]}"
    return r.content


def _hex_meta(d: dict) -> dict:
    return ((d.get("dimension_report") or {}).get("semantic_audit") or {}).get("hex") or {}


# --- routing + geometry (black box) ---------------------------------------
def test_make_a_hex_standoff_routes_deterministic_and_is_six_sided(client, auth):
    d = _create(client, auth, "Make a hex standoff.")
    assert d["route"] == ROUTE, "hex standoff must use the deterministic hex route"
    assert d["generation_outcome"] == "generated_single_part"
    assert d["validation_status"] == "pass"
    assert d["download_blocked_reason"] is None

    dbg = d["hex_debug"]
    assert dbg["family"] == "hex_standoff" and dbg["route"] == ROUTE
    assert dbg["hex_six_sided"] is True
    assert dbg["measured_corner_count"] == 6

    # The EXPORTED STL silhouette (not metadata) must actually be hexagonal.
    stl = _download(client, auth, d["id"], "stl")
    assert measure_hex_sides(stl)["corner_count"] == 6, "exported body is not a hexagon"
    assert len(_download(client, auth, d["id"], "step")) > 0


def test_hex_standoff_length_only(client, auth):
    d = _create(client, auth, "Create a 25mm long hex standoff.")
    assert d["route"] == ROUTE
    assert d["validation_status"] == "pass"
    assert d["bounding_box_mm"]["z"] == pytest.approx(25, abs=0.2)
    assert d["hex_debug"]["hex_six_sided"] is True


def test_hex_standoff_across_flats_and_m4_bore(client, auth):
    d = _create(
        client, auth,
        "Create a 25mm long hex standoff, 12mm across flats, with M4 through hole.")
    assert d["route"] == ROUTE
    assert d["validation_status"] == "pass"
    dbg = d["hex_debug"]
    # Across-flats preserved EXACTLY; across-corners derived (12 / cos30).
    assert dbg["across_flats"] == pytest.approx(12.0, abs=0.01)
    assert dbg["across_corners"] == pytest.approx(12.0 / COS30, abs=0.05)
    # M4 through hole -> clearance bore (not the literal "4").
    assert dbg["bore_diameter"] == pytest.approx(4.5, abs=0.01)
    assert dbg["length"] == pytest.approx(25, abs=0.01)

    # Measured bbox: across-flats one way, across-corners the other.
    bb = d["bounding_box_mm"]
    assert min(bb["x"], bb["y"]) == pytest.approx(12.0, abs=0.2)
    assert max(bb["x"], bb["y"]) == pytest.approx(12.0 / COS30, abs=0.2)
    assert bb["z"] == pytest.approx(25, abs=0.2)


def test_hex_spacer_explicit_bore(client, auth):
    d = _create(
        client, auth,
        "Create a 25mm long hex spacer, 12mm across flats, 4.5mm through bore.")
    assert d["route"] == ROUTE
    assert d["validation_status"] == "pass"
    dbg = d["hex_debug"]
    assert dbg["across_flats"] == pytest.approx(12.0, abs=0.01)
    assert dbg["bore_diameter"] == pytest.approx(4.5, abs=0.01)
    assert dbg["hex_six_sided"] is True


# --- a plain round spacer must NOT be stolen by the hex route ---------------
def test_round_spacer_is_not_hex(client, auth):
    d = _create(client, auth, "A spacer 10mm OD, 5mm bore, 12mm long")
    assert d["route"] != ROUTE, "a round spacer must not use the hex route"
    assert d["hex_debug"] is None


# --- the core trust guarantee ----------------------------------------------
def test_round_cylinder_never_passes_as_hex():
    """A round cylinder measured against the hex audit must be a CRITICAL
    mismatch — the exact 'hex standoff rendered round' failure this guards."""
    import cadquery as cq
    from cadquery import exporters

    from app.export.exporter import generate
    from app.schemas.design_spec import DesignSpec

    # A true hex passes.
    hex_spec = DesignSpec(object_type="hex_standoff",
                          dimensions={"across_flats": 12, "length": 25, "bore_diameter": 4.5})
    n = measure_hex_sides(generate(hex_spec).stl_bytes)["corner_count"]
    assert n == 6
    assert audit_hex_standoff(object_type="hex_standoff", measured_corner_count=n,
                              hex_intent=True) == []

    # A round cylinder (many outer edges) is a critical mismatch.
    disc = cq.Workplane("XY").circle(6).extrude(25).faces(">Z").workplane().hole(4.5)
    exporters.export(disc, "/tmp/_audit_hex_disc.stl")
    rn = measure_hex_sides(open("/tmp/_audit_hex_disc.stl", "rb").read())["corner_count"]
    assert rn >= 10
    issues = audit_hex_standoff(object_type="hex_standoff", measured_corner_count=rn,
                                hex_intent=True)
    crit = [i for i in issues if i.severity == "critical"]
    assert crit and crit[0].check == "hex_is_round"


# --- parser unit tests -----------------------------------------------------
def test_parser_keeps_across_flats_and_resolves_metric_bore():
    from app.cad.hex_standoff import is_hex_standoff_prompt, parse_hex_params

    assert is_hex_standoff_prompt("Make a hex standoff.")
    assert is_hex_standoff_prompt("hexagonal spacer 8mm across flats")
    assert not is_hex_standoff_prompt("a round spacer 10mm OD")
    assert not is_hex_standoff_prompt("an M6 hex nut")  # hex nut is a different part

    p = parse_hex_params("25mm long hex standoff, 12mm across flats, with M4 through hole")
    assert p["across_flats_mm"] == 12.0
    assert p["length_mm"] == 25.0
    assert p["bore_diameter_mm"] == 4.5  # M4 clearance, not the literal "4"
    assert p["across_corners_mm"] == pytest.approx(12.0 / COS30, abs=0.01)
