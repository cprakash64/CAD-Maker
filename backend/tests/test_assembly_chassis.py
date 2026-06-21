"""Concept-assembly generation for the supported tubular-chassis family.

The exact sports-car chassis prompt must now generate a simplified, previewable,
exportable assembly (validated with the assembly profile) instead of only
returning needs_decomposition — while unsupported huge prompts still decompose
and ordinary single parts are unaffected.
"""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

SPORTS_CAR = (
    "Create a detailed 3D CAD model of a rear-wheel-drive sports car chassis frame "
    "using welded steel tubular construction. The frame should include a strong "
    "rectangular main structure, front and rear suspension mounting points, engine "
    "bay, transmission tunnel, floor cross-members, roll cage structure, dashboard "
    "support bar, side-impact bars, and mounting brackets for seats, steering column, "
    "fuel tank, radiator, and body panels. Use round steel tubes with realistic wall "
    "thickness and clean welded joints. Keep the overall proportions similar to a "
    "compact sports car: approximately 4200 mm long, 1800 mm wide, and 1200 mm high. "
    "Include symmetrical left and right geometry and triangulated bracing. Make the "
    "model manufacturable and fully parametric, with organized components for the main "
    "frame, roll cage, suspension mounts, engine mounts, and cross braces."
)

# Complex but NOT a supported assembly family -> must still decompose.
AIRCRAFT = (
    "Design a complete aircraft fuselage with wings, landing gear, cockpit, avionics "
    "bay, fuel system, control surfaces, cargo hold and tail assembly."
)


@pytest.fixture(scope="module")
def chassis():
    """Generate the chassis assembly once (CadQuery is heavy)."""
    client = TestClient(app_module())
    email = f"asm_{os.getpid()}@example.com"
    r = client.post("/api/auth/signup", json={"email": email, "password": "password123"})
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    resp = client.post("/api/designs/create", json={"prompt": SPORTS_CAR}, headers=headers)
    assert resp.status_code == 200, resp.text
    return {"client": client, "headers": headers, "design": resp.json()}


def app_module():
    from app.main import app

    return app


# 1 + 2: not only decomposition; route/design_mode indicate assembly.
def test_generates_assembly_not_decomposition(chassis):
    d = chassis["design"]
    assert d["needs_decomposition"] is False
    assert d["needs_clarification"] is False
    assert d["route"] == "assembly"
    assert d["design_mode"] == "assembly"


# 3: previewable model.
def test_assembly_is_previewable(chassis):
    d = chassis["design"]
    assert d["preview"] is not None
    assert d["preview"]["triangle_count"] > 0


# 4: exports STEP/STL + package download.
def test_assembly_exports_and_packages(chassis):
    d, c, h = chassis["design"], chassis["client"], chassis["headers"]
    assert {e["fmt"] for e in d["exports"]} == {"stl", "step"}
    for fmt in ("stl", "step"):
        r = c.get(f"/api/designs/{d['id']}/files/{fmt}", headers=h)
        assert r.status_code == 200 and r.content
    pkg = c.get(f"/api/designs/{d['id']}/package", headers=h)
    assert pkg.status_code == 200, pkg.text
    assert pkg.content[:2] == b"PK"  # zip


# 5: named components incl. main frame, roll cage, cross members, tunnel, side bars, mounts.
def test_assembly_named_components(chassis):
    rep = chassis["design"]["dimension_report"]
    types = {c["type"] for c in rep["components"]}
    for required in ("lower_rail", "roll_cage_bar", "cross_member",
                     "transmission_tunnel", "side_impact_bar",
                     "engine_mount", "seat_mount", "suspension_mount"):
        assert required in types, f"missing component type: {required}"
    assert set(rep["sections"]["present"]) >= {
        "main_frame", "front_bay", "cabin", "rear_bay", "roll_cage"
    }
    assert rep["sections"]["missing"] == []


# 6: approximate envelope ~ 4200 x 1800 x 1200 mm.
def test_assembly_envelope_within_tolerance(chassis):
    bb = chassis["design"]["dimension_report"]["measured"]["bbox_mm"]
    assert abs(bb["x"] - 4200) <= 4200 * 0.20
    assert abs(bb["y"] - 1800) <= 1800 * 0.20
    assert abs(bb["z"] - 1200) <= 1200 * 0.20
    assert chassis["design"]["dimensions_within_tolerance"] is True


# 7: multi-body is fine for assembly — not a critical failure.
def test_assembly_does_not_require_single_body(chassis):
    d = chassis["design"]
    assert d["validation_status"] in ("pass", "warning")
    assert d["validation_status"] != "critical_failure"
    assert d["validation_critical_failures"] == []
    measured = d["dimension_report"]["measured"]
    assert measured["mesh_components"] > 1  # genuinely an assembly of bodies
    # No critical mentions single body / disconnected.
    joined = " ".join(d["validation_critical_failures"]).lower()
    assert "single" not in joined and "disconnected" not in joined


# 9: unsupported huge prompt still decomposes quickly.
def test_unsupported_assembly_still_decomposes(client, auth):
    r = client.post("/api/designs/create", json={"prompt": AIRCRAFT}, headers=auth["headers"])
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["needs_decomposition"] is True
    assert d["design_mode"] is None
    assert d["route"] == "needs_decomposition"
    assert d["exports"] == []


# 8: ordinary single parts are unaffected by the assembly path.
@pytest.mark.parametrize("prompt", [
    "Create a rectangular mounting plate 120mm long, 80mm wide, 8mm thick, with four M6 holes",
    "A round standoff spacer 10mm outer diameter, 20mm long, 4mm bore",
    "Make an L bracket with 60mm legs, 5mm thickness, 20mm width, and two 6mm holes on each face.",
])
def test_single_parts_not_assembly(client, auth, prompt):
    r = client.post("/api/designs/create", json={"prompt": prompt}, headers=auth["headers"])
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["needs_decomposition"] is False
    assert d["design_mode"] != "assembly"
    assert d["route"] != "assembly"
