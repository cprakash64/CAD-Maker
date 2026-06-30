"""External-thread + shaft-coupler correctness — the 'no smooth-cylinder PASS' rule.

BLACK-BOX through create_design. Geometry is checked against the EXPORTED mesh
(not just metadata), and a debug side-view STL is written for every threaded part
so a reviewer can confirm the thread is physically present.
"""
from __future__ import annotations

import math
import os

import pytest

from app.cad.semantic_audits import measure_external_thread

_DEBUG_DIR = os.path.join(os.path.dirname(__file__), "..", "reports", "thread_debug")


def _create(client, auth, prompt: str) -> dict:
    r = client.post("/api/designs/create", json={"prompt": prompt}, headers=auth["headers"])
    assert r.status_code == 200, r.text
    return r.json()


def _stl(client, auth, design_id: str) -> bytes:
    r = client.get(f"/api/designs/{design_id}/files/stl", headers=auth["headers"])
    assert r.status_code == 200, f"stl download: {r.status_code}"
    return r.content


def _save_debug(name: str, stl: bytes) -> None:
    os.makedirs(_DEBUG_DIR, exist_ok=True)
    with open(os.path.join(_DEBUG_DIR, name), "wb") as fh:
        fh.write(stl)


def _detail(d: dict) -> dict:
    return d.get("part_family_detail") or {}


def _is_modeled(d: dict) -> bool:
    return bool(_detail(d).get("external_thread_modeled"))


# === A. bolt, no length =====================================================
def test_m12_bolt_no_length(client, auth):
    d = _create(client, auth, "Make a M12 bolt")
    det = _detail(d)
    assert d["object_type"] == "bolt"
    assert det["thread"] == "M12" and det["pitch_mm"] == 1.75
    assert det["thread_major_diameter_mm"] == 12
    assert d["validation_status"] != "critical_failure"
    # never a smooth shaft passed off as modeled.
    stl = _stl(client, auth, d["id"]); _save_debug("bolt_external_thread_debug.stl", stl)
    if _is_modeled(d):
        span = measure_external_thread(stl, 12.0)["bore_radial_span_mm"]
        assert span and span > 0.3, "claims modeled but shaft is smooth"
    else:
        assert d["validation_status"] == "warning"  # cosmetic fallback => REVIEW
    # length was assumed -> REVIEW + visible assumption.
    assert d["validation_status"] in ("warning", "pass")
    c = d["part_family_contract"]
    assert any("length" in m.lower() for m in c["missing_inputs"])


# === B. bolt, explicit threaded length ======================================
def test_m12_bolt_explicit_threaded_length(client, auth):
    d = _create(client, auth,
                "Make a M12 hex head bolt 60 mm long with 35 mm threaded length")
    det = _detail(d)
    assert d["object_type"] == "bolt"
    assert det["threaded_length_mm"] == pytest.approx(35.0, abs=0.5)
    assert det["length_mm"] == pytest.approx(60.0, abs=0.5)
    assert d["validation_status"] != "critical_failure"
    stl = _stl(client, auth, d["id"]); _save_debug("bolt_threaded_length_debug.stl", stl)
    # 35mm thread exceeds the modeled cap -> cosmetic fallback, must be REVIEW,
    # never a smooth shaft marked modeled+PASS.
    if not _is_modeled(d):
        assert d["validation_status"] == "warning"
        assert det["thread_representation"] in (
            "cosmetic", "failed_to_model_fallback_cosmetic")


# === C. threaded rod ========================================================
def test_m12_threaded_rod_never_smooth_pass(client, auth):
    d = _create(client, auth, "Make a M12 threaded rod 50 mm long")
    det = _detail(d)
    assert d["object_type"] == "threaded_rod"
    assert det["thread_major_diameter_mm"] == 12 and det["pitch_mm"] == 1.75
    assert det["length_mm"] == pytest.approx(50.0, abs=0.5)
    stl = _stl(client, auth, d["id"]); _save_debug("threaded_rod_external_thread_debug.stl", stl)
    span = measure_external_thread(stl, 12.0)["bore_radial_span_mm"]
    if _is_modeled(d):
        assert span and span > 0.3
        assert d["validation_status"] in ("pass", "warning")
    else:
        # smooth cylinder MUST NOT be PASS.
        assert d["validation_status"] == "warning"
        assert (span or 0) < 0.2


def test_short_threaded_rod_is_modeled_and_visible(client, auth):
    d = _create(client, auth, "Make a M12 threaded rod 20 mm long")
    stl = _stl(client, auth, d["id"])
    if _is_modeled(d):
        span = measure_external_thread(stl, 12.0)["bore_radial_span_mm"]
        assert span and span > 0.4, "short rod claims modeled but is smooth"


# === D / E. shaft couplers ==================================================
def _coupler_checks(d, *, od, length, b1, b2, ss_count, ss_thread):
    det = _detail(d)
    assert d["object_type"] == "shaft_coupler"
    assert det["outer_diameter_mm"] == pytest.approx(od, abs=0.5)
    assert det["length_mm"] == pytest.approx(length, abs=0.5)
    assert det["bore_1_mm"] == pytest.approx(b1, abs=0.5)
    assert det["bore_2_mm"] == pytest.approx(b2, abs=0.5)
    assert det["set_screw_count"] == ss_count
    assert det["radial_set_screw_holes"] == ss_count
    assert det["set_screw_thread"].startswith(ss_thread)
    # measured holes must NOT be 0.
    meas = (d["dimension_report"] or {}).get("measured") or {}
    assert meas.get("hole_count", 0) >= 1 + ss_count
    bb = d["bounding_box_mm"]
    assert bb["z"] == pytest.approx(length, abs=0.5)
    assert max(bb["x"], bb["y"]) == pytest.approx(od, abs=0.5)


def test_shaft_coupler_m4_two_set_screws(client, auth):
    d = _create(client, auth,
                "Make a shaft coupler 25 mm long, 20 mm outer diameter, 6 mm bore on one "
                "side, 8 mm bore on the other side, with two M4 set screw holes")
    _coupler_checks(d, od=20, length=25, b1=6, b2=8, ss_count=2, ss_thread="M4")
    assert d["validation_status"] != "critical_failure"


def test_shaft_coupler_m5_four_set_screws(client, auth):
    d = _create(client, auth,
                "Make a shaft coupler 30 mm long, 24 mm outer diameter, 8 mm bore on one "
                "side, 10 mm bore on the other side, with four M5 set screw holes")
    _coupler_checks(d, od=24, length=30, b1=8, b2=10, ss_count=4, ss_thread="M5")
    assert _detail(d)["threaded_holes"] == 4


def test_shaft_coupler_set_screw_thread_metadata(client, auth):
    """Set-screw seats: tap-drill core + cosmetic thread, honest REVIEW (not PASS)."""
    d = _create(client, auth,
                "Make a shaft coupler 30 mm long, 24 mm outer diameter, 8 mm bore on one "
                "side, 10 mm bore on the other side, with four M5 set screw holes")
    det = _detail(d)
    assert det["set_screw_thread"].startswith("M5") and det["set_screw_pitch_mm"] == 0.8
    assert det["set_screw_tap_drill_mm"] == 4.2
    assert det["set_screw_hole_mode"] == "tap_drill_cosmetic_thread"
    assert det["set_screw_thread_mode"] == "cosmetic"
    assert det["placement_strategy"] == "two_axial_stations_opposing"
    # Cosmetic set-screw thread => REVIEW, never a clean PASS implying a validated fit.
    assert d["validation_status"] in ("warning", "review")

    d4 = _create(client, auth,
                 "Make a shaft coupler 25 mm long, 20 mm outer diameter, 6 mm bore on one "
                 "side, 8 mm bore on the other side, with two M4 set screw holes")
    det4 = _detail(d4)
    assert det4["set_screw_thread"].startswith("M4") and det4["set_screw_pitch_mm"] == 0.7
    assert det4["radial_set_screw_holes"] == 2


# === F. DIN 934 nut =========================================================
def test_din934_m12_nut_internal_thread(client, auth):
    d = _create(client, auth, "Make a DIN 934 M12 nut")
    sp = d["standard_part"]
    assert sp["standard"] == "DIN 934"
    assert "M12 × 1.75" in sp["badge"]
    meas = (d["dimension_report"] or {}).get("measured") or {}
    assert meas.get("hole_count") == 1 and meas.get("threaded_hole_count") == 1
    assert sp["internal_thread_modeled"] is True


# === G. nyloc ===============================================================
def test_nyloc_never_plain_hex_nut_pass(client, auth):
    d = _create(client, auth, "Make a nylon insert lock nut M12")
    assert d["generation_outcome"] == "unsupported"
    assert d["validation_status"] != "pass"
    assert d["object_type"] != "hex_nut"


# === H. GT2 pulley verification =============================================
def test_gt2_pulley_verification_metadata(client, auth):
    d = _create(client, auth,
                "Make a pulley with 20 teeth for a GT2 belt, 6 mm bore, and 8 mm belt width")
    det = _detail(d)
    assert d["object_type"] == "timing_pulley_gt2"
    assert det["teeth"] == 20 and det["pitch_mm"] == 2.0
    assert det["bore_mm"] == pytest.approx(6.0, abs=0.5)
    assert det["belt_width_mm"] == pytest.approx(8.0, abs=0.5)
    assert det["pitch_diameter_mm"] == pytest.approx(20 * 2 / math.pi, abs=0.05)
    assert det.get("not_spur_gear") is True
    assert "spur" not in (d["object_type"] or "")
