"""Part Family Contract + strict router — the 'no silent substitution' guarantee.

BLACK-BOX through the create_design path the browser uses. Every prompt must
either build the REQUESTED family or honestly report unsupported/substituted —
never a different part silently passed off as exact.
"""
from __future__ import annotations

import math

import pytest


def _create(client, auth, prompt: str) -> dict:
    r = client.post("/api/designs/create", json={"prompt": prompt}, headers=auth["headers"])
    assert r.status_code == 200, r.text
    return r.json()


def _contract(d: dict) -> dict:
    return d.get("part_family_contract") or {}


# === A. fasteners ===========================================================
def test_m12_bolt_is_a_bolt_not_a_cylinder(client, auth):
    d = _create(client, auth, "Make a M12 bolt")
    c = _contract(d)
    assert c["requested_family"] == "bolt"
    assert c["resolved_family"] == "bolt"
    assert d["object_type"] == "bolt"
    # length/head assumed -> REVIEW, never a clean PASS, never a smooth cylinder.
    assert d["validation_status"] in ("warning",)
    assert c["generation_honesty_status"] in ("partial", "exact")
    assert d["generation_outcome"] == "generated_single_part"


def test_m12_threaded_rod_external_thread(client, auth):
    d = _create(client, auth, "Make a M12 threaded rod 50 mm long")
    c = _contract(d)
    assert c["resolved_family"] == "threaded_rod"
    assert d["object_type"] == "threaded_rod"
    thread = ((d["dimension_report"] or {}).get("semantic_audit") or {}).get("thread") or {}
    # external thread is modeled, or clearly cosmetic (REVIEW) — never a smooth
    # cylinder reported as exact PASS.
    rep = thread.get("thread_representation")
    if rep == "modeled":
        assert d["validation_status"] in ("pass", "warning")
        assert thread.get("external_thread_modeled") is True
    else:
        assert d["validation_status"] == "warning"


def test_m12_threaded_rod_is_not_smooth_cylinder(client, auth):
    """The exported rod must carry real external thread geometry OR be marked
    cosmetic — a smooth 12mm cylinder is never a PASS threaded rod."""
    d = _create(client, auth, "Make a M12 threaded rod 30 mm long")
    thread = ((d["dimension_report"] or {}).get("semantic_audit") or {}).get("thread") or {}
    if thread.get("external_thread_modeled"):
        r = client.get(f"/api/designs/{d['id']}/files/stl", headers=auth["headers"])
        from app.cad.semantic_audits import measure_external_thread
        span = measure_external_thread(r.content, 12.0)["bore_radial_span_mm"]
        assert span is not None and span > 0.3, "claimed modeled but shank is smooth"


def test_square_nut_m12(client, auth):
    d = _create(client, auth, "Make a square nut M12")
    c = _contract(d)
    assert c["resolved_family"] == "square_nut"
    assert d["object_type"] == "square_nut"
    assert d["validation_status"] in ("pass", "warning")
    # square body ~19mm across flats for M12
    bb = d["bounding_box_mm"]
    assert bb["x"] == pytest.approx(19.0, abs=0.5)


def test_nyloc_nut_is_not_a_plain_hex_nut(client, auth):
    """A nylon-insert lock nut must NOT silently become a regular hex nut PASS."""
    d = _create(client, auth, "Make a nylon insert lock nut M12")
    c = _contract(d)
    assert c["requested_variant"] == "nyloc"
    assert c["generation_honesty_status"] == "unsupported"
    assert d["generation_outcome"] == "unsupported"
    assert d["object_type"] != "hex_nut"  # never silently substituted
    assert d["validation_status"] != "pass"
    # offers a one-click fallback to the closest supported part
    assert any("hex nut" in (o.get("prompt", "").lower()) for o in d.get("clarification_options", []))


# === B. power transmission ==================================================
def test_gt2_pulley_is_not_a_spur_gear(client, auth):
    d = _create(client, auth,
                "Make a pulley with 20 teeth for a GT2 belt, 6 mm bore, and 8 mm belt width")
    c = _contract(d)
    assert c["resolved_family"] == "timing_pulley_gt2"
    assert d["object_type"] == "timing_pulley_gt2"
    assert d["route"] != "deterministic_spur_gear"
    assert "spur" not in (d["object_type"] or "")
    assert d["validation_status"] in ("pass", "warning")


# === C. couplers ============================================================
def test_shaft_coupler_routes_to_coupler(client, auth):
    d = _create(client, auth,
                "Make a shaft coupler 25 mm long, 20 mm outer diameter, 6 mm bore on one "
                "side, 8 mm bore on the other side, with two M4 set screw holes")
    c = _contract(d)
    assert c["resolved_family"] == "shaft_coupler"
    assert d["object_type"] == "shaft_coupler"
    assert d["title"] and "generic" not in d["title"].lower()
    bb = d["bounding_box_mm"]
    assert bb["z"] == pytest.approx(25.0, abs=0.5)
    assert max(bb["x"], bb["y"]) == pytest.approx(20.0, abs=0.5)


# === D. enclosures (must not fail) ==========================================
def test_electronics_enclosure_generates(client, auth):
    d = _create(client, auth,
                "Make an electronics enclosure 100 mm by 70 mm by 35 mm with 2.5 mm walls, "
                "four screw bosses, lid mounting holes, and USB-C cutout")
    # Core requirement: it must GENERATE, not fail / not be hijacked by a fastener.
    assert d["generation_outcome"] == "generated_single_part"
    assert d["object_type"] in ("electronics_enclosure", "enclosure", "sensor_enclosure")
    assert d["validation_status"] != "critical_failure"


def test_rpi_enclosure_logo_does_not_block(client, auth):
    d = _create(client, auth,
                "Make a Raspberry Pi 4 enclosure with 2.5 mm wall thickness, snap-fit lid, "
                "ventilation slots, and embossed logo area on top")
    # Logo mention must not stop enclosure generation.
    assert d["generation_outcome"] == "generated_single_part"
    assert d["validation_status"] != "critical_failure"


# === router unit tests ======================================================
def test_router_does_not_hijack_fastener_features():
    """A 'screw boss' / 'bolt circle' in a container prompt is a feature, not the part."""
    from app.cad.part_family import detect_part_request

    assert detect_part_request("an enclosure with four screw bosses") is None
    assert detect_part_request("a plate with a bolt circle of six M5 holes") is None
    # but a standalone fastener is still detected
    assert detect_part_request("an M12 bolt").requested_family == "bolt"
    assert detect_part_request("a GT2 pulley with 20 teeth").requested_family == "timing_pulley_gt2"
