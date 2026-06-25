"""Beta-readiness expectation control — BLACK-BOX through /api/designs/create.

These pin the launch trust rules: a generated CONCEPT assembly must never show a
plain manufacturing PASS, its export wording must say "concept", validated single
parts still show PASS, a parametric bore must not conflict with "No holes yet",
and every create/export/feedback emits telemetry with the fields used to
prioritise beta fixes.
"""
from __future__ import annotations

import json

import pytest


def _create(client, auth, prompt: str) -> dict:
    r = client.post("/api/designs/create", json={"prompt": prompt}, headers=auth["headers"])
    assert r.status_code == 200, f"{prompt!r}: {r.status_code} {r.text[:200]}"
    return r.json()


def _pres(d: dict) -> dict:
    p = d.get("presentation")
    assert p is not None, "every design must carry a presentation block"
    return p


# === 1. Concept assemblies never show a plain manufacturing PASS ============
CONCEPT_PROMPTS = [
    "Create a CNC router frame with gantry, bed, and linear rails.",
    "Create a machine frame with a base, uprights, and a top plate.",
    "Design an engine test stand with a frame and mounting rails.",
    "Build a complete drone frame with arms, motor mounts, and a battery tray.",
]


@pytest.mark.parametrize("prompt", CONCEPT_PROMPTS)
def test_concept_assembly_never_plain_pass(client, auth, prompt):
    d = _create(client, auth, prompt)
    if d["generation_outcome"] != "generated_assembly":
        pytest.skip(f"{prompt!r} did not route to a generated assembly")
    p = _pres(d)
    assert p["is_concept"] is True
    # The badge is never a plain manufacturing PASS.
    assert p["status_badge"] != "PASS"
    assert p["status_badge"] in ("CONCEPT", "REVIEW")
    # Engineering-review wording is present.
    assert "review" in p["status_detail"].lower()
    assert p["concept_notice"] and "not structurally certified" in p["concept_notice"].lower()


def test_cnc_router_frame_not_manufacturing_pass(client, auth):
    """The named regression: a CNC router frame must not present plain PASS as a
    final manufacturing status."""
    d = _create(client, auth, "Create a CNC router frame with gantry, bed, and linear rails.")
    assert d["generation_outcome"] == "generated_assembly"
    p = _pres(d)
    assert p["status_badge"] == "CONCEPT"
    assert p["status_detail"] == "Geometry PASS · Engineering review required"
    assert p["is_concept"] is True


# === 3. Single validated parts still show PASS ==============================
def test_single_part_shows_pass(client, auth):
    d = _create(client, auth, "A rectangular mounting plate 80x40x5mm with four 6mm holes")
    assert d["generation_outcome"] == "generated_single_part"
    p = _pres(d)
    assert p["status_badge"] == "PASS"
    assert p["is_concept"] is False
    assert p["export_kind"] == "validated"


# === 2 & 6. Export copy differs for concept vs validated ====================
def test_export_copy_differs_concept_vs_validated(client, auth):
    concept = _pres(_create(client, auth,
                            "Create a CNC router frame with gantry, bed, and linear rails."))
    part = _pres(_create(client, auth,
                         "A rectangular mounting plate 80x40x5mm with four 6mm holes"))
    assert concept["export_kind"] == "concept"
    assert part["export_kind"] == "validated"
    # Concept export labels are explicitly "concept".
    assert concept["export_labels"]["step"] == "Export concept STEP"
    assert concept["export_labels"]["stl"] == "Export concept STL"
    assert concept["export_labels"]["package"] == "CAD concept package"
    assert "concept" not in part["export_labels"]["step"].lower()
    assert concept["export_labels"] != part["export_labels"]
    # Concept export carries the not-certified notice; a validated part does not.
    assert concept["export_notice"] and "review before fabrication" in concept["export_notice"].lower()
    assert part["export_notice"] is None


# === 4. Beta disclaimer is always present ==================================
def test_beta_notice_present_everywhere(client, auth):
    for prompt in ("A rectangular mounting plate 80x40x5mm with four 6mm holes",
                   "Create a CNC router frame with gantry, bed, and linear rails.",
                   "Make a bracket."):
        p = _pres(_create(client, auth, prompt))
        assert "verify dimensions" in p["beta_notice"].lower()


# === 3 (cont). Parametric bores must not conflict with "No holes yet" =======
@pytest.mark.parametrize("prompt", [
    "Make a hex standoff.",
    "Create a 24 tooth spur gear.",
    "Create a smooth pulley, 80mm diameter, 12mm thick, 10mm bore.",
])
def test_parametric_bore_has_feature_not_no_holes(client, auth, prompt):
    d = _create(client, auth, prompt)
    p = _pres(d)
    measured = (d.get("dimension_report") or {}).get("measured") or {}
    assert measured.get("hole_count", 0) >= 1, "this family has a measured bore"
    # Because measured holes > 0, the manual hole editor is hidden and the bore is
    # surfaced as a parametric feature — never "No holes yet".
    assert p["manual_hole_editing"] is False
    assert p["parametric_holes"], "parametric bore must be surfaced as a feature"
    bore = p["parametric_holes"][0]
    assert bore["label"] == "Parametric bore"
    assert bore["diameter_mm"] > 0 and bore["through"] is True


def test_plate_with_manual_holes_keeps_editor(client, auth):
    """A plate's holes ARE editable (spec.holes), so the manual editor stays and
    there is no parametric-bore feature."""
    d = _create(client, auth, "A rectangular mounting plate 80x40x5mm with four 6mm holes")
    p = _pres(d)
    assert p["manual_hole_editing"] is True
    assert p["parametric_holes"] == []


# === 5. Telemetry on create / export / feedback ============================
def _events(caplog, name: str) -> list[dict]:
    out = []
    for rec in caplog.records:
        msg = rec.getMessage()
        if f'"event": "{name}"' not in msg:
            continue
        try:
            out.append(json.loads(msg))
        except ValueError:
            pass
    return out


def test_create_export_feedback_emit_telemetry(client, auth, caplog):
    import logging

    with caplog.at_level(logging.INFO, logger="sourcecad"):
        d = _create(client, auth, "A rectangular mounting plate 80x40x5mm with four 6mm holes")
        client.get(f"/api/designs/{d['id']}/files/stl", headers=auth["headers"])
        client.post(f"/api/designs/{d['id']}/feedback",
                    json={"rating": "up", "categories": [], "comment": None},
                    headers=auth["headers"])

    created = _events(caplog, "design_created")
    assert created, "create must emit design_created telemetry"
    ev = created[-1]
    for field in ("prompt", "route", "family", "title", "generation_outcome",
                  "validation_status", "export_allowed", "is_concept"):
        assert field in ev, f"telemetry missing {field}"
    assert ev["export_clicked"] is False

    exported = _events(caplog, "design_exported")
    assert exported and exported[-1]["export_clicked"] is True
    assert exported[-1]["export_kind"] == "validated"

    fb = _events(caplog, "design_feedback")
    assert fb and fb[-1]["user_feedback"] == "yes"
