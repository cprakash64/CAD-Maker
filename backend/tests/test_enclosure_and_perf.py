"""Deterministic enclosure routing + /api/designs list-endpoint performance.

These cover the production blockers where a Raspberry Pi enclosure prompt hit the
LLM planner and timed out (503 after ~3 min), and where GET /api/designs took
6–14s because it loaded every design's heavy JSON payload with an N+1 export probe.
"""
from __future__ import annotations

import time

HEAVY_KEYS = {"preview", "preview_json", "semantic_json", "spec", "spec_json",
              "features", "features_json", "dimension_report", "program_code",
              "positions", "indices"}


def _create(client, auth, prompt: str) -> dict:
    r = client.post("/api/designs/create", json={"prompt": prompt}, headers=auth["headers"])
    assert r.status_code == 200, r.text
    return r.json()


# === RPi enclosure: deterministic, no LLM planner, fast ======================
def _no_llm(monkeypatch):
    """Make any CAD-planner LLM call raise, so a passing test PROVES the prompt was
    handled by the offline deterministic route (the gpt-5.5 cad_plan timeout bug)."""
    def _boom(*a, **k):
        raise AssertionError("LLM cad planner was called — should be deterministic")
    monkeypatch.setattr("app.llm.factory.get_cad_provider", _boom)


def test_rpi4_enclosure_routes_deterministic_not_llm(client, auth, monkeypatch):
    _no_llm(monkeypatch)
    t0 = time.perf_counter()
    d = _create(
        client, auth,
        "Make a Raspberry Pi 4 enclosure with 2.5 mm wall thickness, snap-fit lid, "
        "ventilation slots, and embossed logo area on top")
    elapsed = time.perf_counter() - t0

    # Now routed to the accurate device preset (rpi4_enclosure), not a generic box.
    assert d["object_type"] in ("electronics_enclosure", "sensor_enclosure",
                                "rpi4_enclosure")
    # No clarification stall, real geometry, finished quickly (seconds, not minutes).
    assert not d.get("needs_clarification")
    assert d["bounding_box_mm"] is not None
    assert d["validation_status"] != "critical_failure"
    assert elapsed < 30, f"enclosure generation took {elapsed:.1f}s"

    # Secondary cosmetic features are flagged as REVIEW assumptions, never silently
    # dropped and never blocking generation.
    blob = " ".join(d.get("assumptions") or []).lower()
    assert "snap-fit" in blob or "snap fit" in blob
    assert "logo" in blob
    assert "vent" in blob


def test_rpi_enclosure_uses_board_preset_dimensions(client, auth, monkeypatch):
    _no_llm(monkeypatch)
    d = _create(client, auth, "Raspberry Pi 5 enclosure with 2 mm walls")
    bb = d["bounding_box_mm"]
    # Board-footprint preset (85x56mm board -> ~95x66mm outer box).
    assert 80 <= max(bb["x"], bb["y"]) <= 120


# === /api/designs list performance ==========================================
def test_designs_list_is_lightweight_and_paginated(client, auth):
    # Create a handful of designs (deterministic prompts, no LLM).
    for i in range(6):
        _create(client, auth, f"Make a {20 + i} mm cube")

    t0 = time.perf_counter()
    r = client.get("/api/designs", headers=auth["headers"])
    elapsed = time.perf_counter() - t0
    assert r.status_code == 200
    items = r.json()
    assert len(items) >= 6

    # Summaries only — no heavy geometry / report payload in the list response.
    for it in items:
        assert not (HEAVY_KEYS & set(it.keys())), f"heavy key leaked: {set(it.keys())}"
        assert set(it.keys()) <= {
            "id", "project_id", "prompt", "object_type", "title",
            "created_at", "updated_at", "needs_clarification", "export_ready"}

    # Comfortably under the old 6–14s; in-process this should be well under 1s.
    assert elapsed < 2.0, f"list endpoint took {elapsed:.2f}s"

    # Pagination: limit caps the page size.
    r2 = client.get("/api/designs?limit=3", headers=auth["headers"])
    assert r2.status_code == 200
    assert len(r2.json()) == 3
