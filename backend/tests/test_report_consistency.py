"""Report-consistency trust — BLACK-BOX through the same HTTP /api/designs/create
path the browser uses.

These pin the manual-browser regressions: a gear/pulley/hex bore that the
measured panel reported as "Holes: 0", a flange that reported an impossible hole
count (more through than total), and generic/wrong family titles ("simple gear
or pulley", "mechanical part"). For every prompt we assert route, title,
validation status, generation outcome, bbox, hole_count, through_hole_count,
export-allowed, and that the generated summary uses the correct family label.
"""
from __future__ import annotations

import pytest

from app.cad.semantic_audits import measure_radial_teeth


def _create(client, auth, prompt: str) -> dict:
    r = client.post("/api/designs/create", json={"prompt": prompt}, headers=auth["headers"])
    assert r.status_code == 200, f"{prompt!r}: {r.status_code} {r.text[:200]}"
    return r.json()


def _measured(d: dict) -> dict:
    return (d.get("dimension_report") or {}).get("measured") or {}


def _download(client, auth, design_id: str, fmt: str) -> bytes:
    r = client.get(f"/api/designs/{design_id}/files/{fmt}", headers=auth["headers"])
    assert r.status_code == 200, f"{fmt}: {r.status_code}"
    return r.content


def _assert_export_allowed(client, auth, d: dict):
    assert d["download_blocked_reason"] is None
    assert _download(client, auth, d["id"], "stl")
    assert _download(client, auth, d["id"], "step")


# === 1. Gear: visible teeth + bore counted + "spur gear" label ==============
def test_make_a_gear_report(client, auth):
    d = _create(client, auth, "Make a gear.")
    assert d["route"] == "deterministic_spur_gear"
    assert d["title"] == "Spur gear"
    assert d["object_type"] != "simple gear or pulley"
    assert d["generation_outcome"] == "generated_single_part"
    assert d["validation_status"] == "pass"
    m = _measured(d)
    assert m["hole_count"] == 1, "center bore must count as 1 hole"
    assert m["through_hole_count"] == 1
    assert "gear / pulley" not in (d["explanation"] or "").lower()
    _assert_export_allowed(client, auth, d)
    # Exported silhouette actually has teeth.
    assert measure_radial_teeth(_download(client, auth, d["id"], "stl"))["depth_ratio"] >= 0.05


def test_60_tooth_gear_report(client, auth):
    d = _create(client, auth,
                "Create a 60 tooth gear, 120mm outside diameter, 10mm thick, 12mm bore.")
    assert d["route"] == "deterministic_spur_gear"
    assert d["title"] == "Spur gear"
    assert d["validation_status"] == "pass"
    m = _measured(d)
    assert m["hole_count"] == 1 and m["through_hole_count"] == 1
    assert d["bounding_box_mm"]["x"] == pytest.approx(120, abs=1.0)
    assert d["bounding_box_mm"]["z"] == pytest.approx(10, abs=0.5)
    assert "gear / pulley" not in (d["explanation"] or "").lower()
    _assert_export_allowed(client, auth, d)


# === 2. Pulley: smooth (no teeth) + bore counted + "Pulley" label ===========
def test_smooth_pulley_report(client, auth):
    d = _create(client, auth, "Create a smooth pulley, 80mm diameter, 12mm thick, 10mm bore.")
    assert d["route"] != "deterministic_spur_gear", "a pulley is not a gear"
    assert d["title"] == "Pulley"
    assert d["gear_debug"] is None, "a pulley must not be classified as a gear"
    assert d["validation_status"] in ("pass", "warning")
    m = _measured(d)
    assert m["hole_count"] == 1, "pulley center bore must count as 1 hole"
    assert m["through_hole_count"] == 1
    summary = (d["explanation"] or "").lower()
    assert "gear" not in summary or "no teeth" in summary, summary
    assert d["bounding_box_mm"]["x"] == pytest.approx(80, abs=1.0)
    _assert_export_allowed(client, auth, d)
    # The exported silhouette is smooth (no teeth).
    assert measure_radial_teeth(_download(client, auth, d["id"], "stl"))["depth_ratio"] < 0.05


# === 3. Hex standoff: bore counted + AF/AC distinguished + "Hex standoff" ====
def test_make_a_hex_standoff_report(client, auth):
    d = _create(client, auth, "Make a hex standoff.")
    assert d["route"] == "deterministic_hex_standoff"
    assert d["title"] == "Hex standoff"
    assert d["validation_status"] == "pass"
    m = _measured(d)
    assert m["hole_count"] == 1, "hex through bore must count as 1 hole"
    assert m["through_hole_count"] == 1
    assert "hex standoff" in (d["explanation"] or "").lower()
    assert "mechanical part" not in (d["explanation"] or "").lower()
    _assert_export_allowed(client, auth, d)


def test_hex_standoff_af_report(client, auth):
    d = _create(client, auth,
                "Create a 25mm long hex standoff, 12mm across flats, M4 through bore.")
    assert d["route"] == "deterministic_hex_standoff"
    assert d["title"] == "Hex standoff"
    assert d["validation_status"] == "pass"
    m = _measured(d)
    assert m["hole_count"] == 1 and m["through_hole_count"] == 1
    summary = (d["explanation"] or "").lower()
    # Across-flats and across-corners are clearly distinguished.
    assert "across flats" in summary and "across corners" in summary
    bb = d["bounding_box_mm"]
    assert min(bb["x"], bb["y"]) == pytest.approx(12.0, abs=0.2)
    assert bb["z"] == pytest.approx(25, abs=0.2)
    _assert_export_allowed(client, auth, d)


# === 4. Round mounting flange: 7 holes / 7 through (never impossible) ========
def test_round_mounting_flange_report(client, auth):
    d = _create(
        client, auth,
        "Create a round mounting flange, 100mm OD, 8mm thick, 30mm center bore, "
        "six 9mm holes on an 80mm bolt circle.")
    assert d["title"] == "Flange"
    assert d["generation_outcome"] == "generated_single_part"
    assert d["validation_status"] == "pass"
    m = _measured(d)
    # 1 center bore + 6 bolt holes = 7, all through. Never more through than total.
    assert m["hole_count"] == 7, m
    assert m["through_hole_count"] == 7, m
    assert m["through_hole_count"] <= m["hole_count"], "impossible hole count"
    assert d["bounding_box_mm"]["x"] == pytest.approx(100, abs=1.0)
    assert d["bounding_box_mm"]["z"] == pytest.approx(8, abs=0.5)
    _assert_export_allowed(client, auth, d)


# === 5. Vague prompt: needs_clarification with visible clickable options =====
def test_vague_bracket_has_clickable_suggestions(client, auth):
    d = _create(client, auth, "Make a bracket.")
    assert d["generation_outcome"] == "needs_clarification"
    assert d["validation_status"] != "critical_failure"  # not "failed"
    opts = d["clarification_options"]
    assert opts and len(opts) >= 5, "clarification must offer clickable suggestions"
    labels = {o["label"] for o in opts}
    assert {"L bracket", "U bracket", "Rectangular mounting plate", "Hinge bracket",
            "Shelf bracket", "Tube clamp", "Motor mount plate"} <= labels
    # Every suggestion is a complete, ready-to-run prompt.
    for o in opts:
        assert o["prompt"] and len(o["prompt"]) > 15


# === 7. Report-consistency safety net (unit) ================================
def test_summary_claims_hole_but_none_measured_cannot_pass():
    """A summary that asserts a bore/hole while the measured hole_count is 0 is an
    inconsistent report — reconciled status must be 'warning', never 'pass'."""
    from types import SimpleNamespace

    from app.services.design_service import reconciled_validation_status

    base_report = {
        "validation": {"status": "pass"},
        "within_tolerance": None,
        "measured": {"hole_count": 0},
    }
    # Claims a bore but measured none -> downgraded.
    claim = SimpleNamespace(
        explanation="A flange with four M6 mounting holes on a bolt circle.",
        assumptions=[],
        semantic_json={"dimension_report": base_report})
    assert reconciled_validation_status(claim) == "warning"

    # A genuinely solid part that says "no through bore" is consistent -> stays pass.
    solid = SimpleNamespace(
        explanation="A hex standoff. Solid body (no through bore).",
        assumptions=[],
        semantic_json={"dimension_report": base_report})
    assert reconciled_validation_status(solid) == "pass"

    # A part whose summary claims a bore AND measured it -> stays pass.
    ok = SimpleNamespace(
        explanation="A spacer with a center bore.",
        assumptions=[],
        semantic_json={"dimension_report": {
            "validation": {"status": "pass"}, "within_tolerance": None,
            "measured": {"hole_count": 1}}})
    assert reconciled_validation_status(ok) == "pass"
