"""Screwdriver / hand-tool generation + single-part fuse safeguards.

Guards the production failure where "Make a Screwdriver" produced a 3-body
disconnected, export-blocked model. A simple one-object prompt must now build a
connected concept (or clarify) — never a failed disconnected part.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.cad.plan import deterministic
from app.cad.plan.dimension_report import build_dimension_report
from app.cad.plan.normalize import normalize_cad_plan
from app.cad.plan.planner import build_and_validate
from app.cad.plan.schema import CadPlan


def _create(client, auth, prompt):
    return client.post("/api/designs/create", json={"prompt": prompt},
                       headers=auth["headers"])


def _build(prompt):
    plan = normalize_cad_plan(deterministic.plan(prompt), prompt)
    out = build_and_validate(plan)
    rep = build_dimension_report(plan, out.result, out.stl_bytes)
    return plan, out, rep


# --- A) basic screwdriver, deterministic, no OpenAI -----------------------
def test_basic_screwdriver_no_openai_pass_export(client, auth, monkeypatch):
    from app.llm import factory

    def _boom(*a, **k):
        raise AssertionError("screwdriver must build deterministically (no LLM)")

    monkeypatch.setattr(factory, "get_cad_provider", _boom)
    monkeypatch.setattr(factory, "get_provider", _boom)

    r = _create(client, auth, "Make a screwdriver")
    assert r.status_code == 200, r.text
    d = r.json()
    assert (d.get("classification") or {}).get("family_id") == "screwdriver"
    assert d["object_type"] == "screwdriver"
    assert d["needs_decomposition"] is False
    assert d["validation_status"] in ("pass", "warning")
    assert d["validation_status"] != "critical_failure"
    assert {e["fmt"] for e in d["exports"]} == {"stl", "step"}
    measured = (d["dimension_report"] or {}).get("measured", {})
    assert measured.get("components") == 1, "must be ONE fused body"
    assert 180 <= d["bounding_box_mm"]["x"] <= 220
    joined = " ".join(d["assumptions"]).lower()
    assert "handle" in joined and "shaft" in joined and "tip" in joined


# --- B) flat blade screwdriver: dims honoured, single fused body ----------
def test_flat_blade_screwdriver_dimensions():
    plan, out, rep = _build(
        "Make a flat blade screwdriver, 200mm long, 30mm handle diameter, with a "
        "6mm shaft and 10mm wide flat tip.")
    assert plan.object_type == "screwdriver"
    assert out.report.passed
    m = rep["measured"]
    assert m["components"] == 1
    assert abs(m["bbox_mm"]["x"] - 200) <= 0.05 * 200
    assert abs(m["bbox_mm"]["y"] - 30) <= 4 and abs(m["bbox_mm"]["z"] - 30) <= 4
    assert rep["validation"]["status"] != "critical_failure"
    # The flat tip exists as its own feature (not a disconnected blade).
    assert any(f.id == "tip" for f in plan.features)


# --- C) Phillips screwdriver: fused, approximate tip noted -----------------
def test_phillips_screwdriver_fused_and_noted():
    plan, out, rep = _build(
        "Create a Phillips screwdriver, 180mm long, 28mm handle diameter, with a "
        "90mm metal shaft.")
    assert plan.object_type == "screwdriver"
    assert out.report.passed
    assert rep["measured"]["components"] == 1
    assert rep["validation"]["status"] != "critical_failure"
    assert any("phillips" in a.lower() and "approximate" in a.lower()
               for a in plan.assumptions)


# --- D) everyday object generates a connected concept (never a failed model) -
def test_hammer_generates_connected_concept(client, auth):
    """A hammer now has a concept-fallback family: it builds one connected,
    labelled concept solid instead of clarifying or failing."""
    r = _create(client, auth, "Make a hammer")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["needs_clarification"] is False
    assert d["validation_status"] != "critical_failure"
    assert {e["fmt"] for e in d["exports"]} == {"stl", "step"}
    assert any("concept" in a.lower() for a in d["assumptions"])


# --- E) single-part disconnected repair guard -----------------------------
def test_collinear_disconnected_single_part_is_fused():
    """Three collinear cylinders with small gaps must fuse into one body."""
    plan = CadPlan(**{
        "object_type": "tool", "name": "collinear stack",
        "features": [
            {"id": "h", "kind": "cylinder", "axis": "x",
             "params": {"diameter": 30, "height": 100}, "at": [0, 0, 0]},
            {"id": "s", "kind": "cylinder", "axis": "x",
             "params": {"diameter": 6, "height": 90}, "at": [0, 0, 103]},
            {"id": "t", "kind": "cylinder", "axis": "x",
             "params": {"diameter": 5, "height": 12}, "at": [0, 0, 196]},
        ],
    })
    out = build_and_validate(plan)
    rep = build_dimension_report(plan, out.result, out.stl_bytes)
    assert rep["measured"]["components"] == 1, "collinear gaps should be bridged"
    assert any("fused" in w.lower() for w in out.result.warnings)


def test_far_apart_bodies_still_fail_disconnected():
    """The fuse safeguard must NOT mask genuinely separate bodies."""
    plan = CadPlan(**{
        "object_type": "broken", "name": "two far boxes",
        "features": [
            {"id": "a", "kind": "box", "params": {"width": 10, "depth": 10, "height": 10},
             "at": [0, 0, 0]},
            {"id": "b", "kind": "box", "params": {"width": 10, "depth": 10, "height": 10},
             "at": [100, 0, 0]},
        ],
        "expected": {"bbox_mm": {"x": 110, "y": 10, "z": 10}},
    })
    out = build_and_validate(plan)
    rep = build_dimension_report(plan, out.result, out.stl_bytes)
    assert rep["measured"]["components"] == 2
    assert rep["validation"]["status"] == "critical_failure"


# --- registry / classification -------------------------------------------
def test_screwdriver_family_registered():
    from app.cad.families import get_family

    fam = get_family("screwdriver")
    assert fam is not None
    assert fam.design_mode.value == "single_part"
    assert fam.example_prompts and fam.known_limitations
    assert any("phillips" in lim.lower() for lim in fam.known_limitations)
