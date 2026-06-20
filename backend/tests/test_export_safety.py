"""Export/download safety for critical-failure designs.

A design whose validation_status is "critical_failure" must NOT be exportable as
a manufacturable file (STEP/STL/package) through the normal API, but must stay
fully inspectable. pass/warning designs export normally.
"""
from __future__ import annotations

import pytest

from app.config import settings

L_BRACKET = (
    "Make an L bracket with 60mm legs, 5mm thickness, 20mm width, "
    "and two 6mm mounting holes on each face."
)

# A genuinely broken build: two non-touching solids -> critical_failure.
DISCONNECTED_PLAN = {
    "object_type": "broken", "name": "two disconnected boxes",
    "features": [
        {"id": "a", "kind": "box", "params": {"width": 10, "depth": 10, "height": 10},
         "at": [0, 0, 0]},
        {"id": "b", "kind": "box", "params": {"width": 10, "depth": 10, "height": 10},
         "at": [100, 0, 0]},
    ],
    "expected": {"bbox_mm": {"x": 110, "y": 10, "z": 10}},
}

# A printable-advisory-only build (sub-1mm hole) -> warning, not critical.
TINY_HOLE_PLAN = {
    "object_type": "plate", "name": "tiny hole plate",
    "features": [
        {"id": "base", "kind": "plate", "params": {"width": 50, "depth": 50, "thickness": 5}},
        {"id": "h", "kind": "hole", "op": "cut", "params": {"diameter": 0.8},
         "through": True, "at": [0, 0, 0]},
    ],
    "expected": {"bbox_mm": {"x": 50, "y": 50, "z": 5}, "hole_count": 1, "through_hole_count": 1},
}


def _store_plan_for_user(user_id: str, plan_dict: dict, prompt: str = "x") -> str:
    """Compile + persist a CadPlan into a design owned by `user_id`; return id."""
    from app.cad.plan.planner import build_and_validate
    from app.cad.plan.schema import CadPlan
    from app.database import SessionLocal
    from app.models import Design, Project
    from app.services import design_service

    db = SessionLocal()
    try:
        proj = Project(name="test", user_id=user_id)
        db.add(proj)
        db.flush()
        design = Design(project_id=proj.id, prompt=prompt)
        db.add(design)
        db.flush()
        plan = CadPlan(**plan_dict)
        outcome = build_and_validate(plan)
        design_service._store_plan(db, design, plan, outcome, 0, None)
        db.commit()
        return design.id
    finally:
        db.close()


def _create(client, auth, prompt: str) -> dict:
    r = client.post("/api/designs/create", json={"prompt": prompt}, headers=auth["headers"])
    assert r.status_code == 200, r.text
    return r.json()


# --- pass: exports normally ----------------------------------------------
def test_passing_design_exports_normally(client, auth):
    d = _create(client, auth, L_BRACKET)
    assert d["validation_status"] == "pass"
    assert d["download_blocked_reason"] is None
    for fmt in ("stl", "step"):
        r = client.get(f"/api/designs/{d['id']}/files/{fmt}", headers=auth["headers"])
        assert r.status_code == 200, r.text
        assert r.content


# --- warning: exports normally (NOT blocked) ------------------------------
def test_warning_design_is_not_blocked(client, auth):
    did = _store_plan_for_user(auth["user"]["id"], TINY_HOLE_PLAN)
    d = client.get(f"/api/designs/{did}", headers=auth["headers"]).json()
    assert d["validation_status"] == "warning"
    assert d["download_blocked_reason"] is None
    r = client.get(f"/api/designs/{did}/files/stl", headers=auth["headers"])
    assert r.status_code == 200, r.text


# --- critical: blocked, with a clear 409 ----------------------------------
def test_critical_design_export_is_blocked(client, auth):
    did = _store_plan_for_user(auth["user"]["id"], DISCONNECTED_PLAN)
    d = client.get(f"/api/designs/{did}", headers=auth["headers"]).json()
    assert d["validation_status"] == "critical_failure"
    assert d["download_blocked_reason"]

    for fmt in ("stl", "step"):
        r = client.get(f"/api/designs/{did}/files/{fmt}", headers=auth["headers"])
        assert r.status_code == 409, r.text
        assert "failed validation" in r.json()["detail"].lower()


def test_failed_design_remains_inspectable(client, auth):
    did = _store_plan_for_user(auth["user"]["id"], DISCONNECTED_PLAN)
    d = client.get(f"/api/designs/{did}", headers=auth["headers"]).json()
    # Still fully readable: preview + critical detail are present.
    assert d["preview"] is not None
    assert d["validation_critical_failures"]
    assert any("disconnected" in c.lower() for c in d["validation_critical_failures"])


# --- dev override (honored only when DEV_MODE) ----------------------------
def test_dev_override_allows_failed_download(client, auth, monkeypatch):
    did = _store_plan_for_user(auth["user"]["id"], DISCONNECTED_PLAN)
    monkeypatch.setattr(settings, "dev_mode", True)
    r = client.get(f"/api/designs/{did}/files/stl?allow_failed=true", headers=auth["headers"])
    assert r.status_code == 200, r.text


def test_override_ignored_when_not_dev_mode(client, auth, monkeypatch):
    did = _store_plan_for_user(auth["user"]["id"], DISCONNECTED_PLAN)
    monkeypatch.setattr(settings, "dev_mode", False)
    r = client.get(f"/api/designs/{did}/files/stl?allow_failed=true", headers=auth["headers"])
    assert r.status_code == 409, r.text


# --- L-bracket regression: now passes, so export is allowed ---------------
def test_l_bracket_export_allowed_after_fix(client, auth):
    d = _create(client, auth, L_BRACKET)
    assert d["validation_status"] == "pass"
    r = client.get(f"/api/designs/{d['id']}/files/step", headers=auth["headers"])
    assert r.status_code == 200, r.text
