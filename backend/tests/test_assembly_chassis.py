"""Concept-assembly generation for the supported tubular-chassis family.

The exact sports-car chassis prompt must now generate a simplified, previewable,
exportable assembly (validated with the assembly profile) instead of only
returning needs_decomposition — while unsupported huge prompts still decompose
and ordinary single parts are unaffected.
"""
from __future__ import annotations

import io
import os
import zipfile

import pytest
from fastapi.testclient import TestClient

REQUIRED_SYSTEMS = {
    "main_frame", "roll_cage", "transmission_tunnel", "side_impact",
    "suspension_mounts", "engine_mounts", "radiator_mount", "fuel_tank_mount",
    "seat_mounts",
}
REQUIRED_ZONES = {"front", "engine_bay", "cabin", "roll_cage", "rear"}

# The exact long sports-car chassis prompt from the brief.
SPORTS_CAR = (
    "Create a detailed 3D CAD model of a rear-wheel-drive sports car chassis frame "
    "using welded steel tubular construction. The frame should include a strong "
    "rectangular main structure, front and rear suspension mounting points, engine "
    "bay, transmission tunnel, floor cross-members, roll cage structure, dashboard "
    "support bar, side-impact bars, and mounting brackets for seats, steering column, "
    "fuel tank, radiator, and body panels.\n\n"
    "Use round steel tubes with realistic wall thickness and clean welded joints. "
    "Design the chassis for a two-seat coupe with a front-mounted engine and "
    "rear-wheel drive layout. Keep the overall proportions similar to a compact "
    "sports car: approximately 4200 mm long, 1800 mm wide, and 1200 mm high. Include "
    "symmetrical left and right geometry, triangulated bracing for rigidity, and "
    "clearly separated front, passenger cabin, and rear sections.\n\n"
    "Make the model manufacturable, structurally realistic, and fully parametric, "
    "with organized components for the main frame, roll cage, suspension mounts, "
    "engine mounts, and cross braces."
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


# 4: exports STEP/STL + package download (with cut-list + caveat).
def test_assembly_exports_and_packages(chassis):
    d, c, h = chassis["design"], chassis["client"], chassis["headers"]
    assert {e["fmt"] for e in d["exports"]} == {"stl", "step"}
    for fmt in ("stl", "step"):
        r = c.get(f"/api/designs/{d['id']}/files/{fmt}", headers=h)
        assert r.status_code == 200 and r.content
    pkg = c.get(f"/api/designs/{d['id']}/package", headers=h)
    assert pkg.status_code == 200, pkg.text
    assert pkg.content[:2] == b"PK"  # zip
    zf = zipfile.ZipFile(io.BytesIO(pkg.content))
    names = set(zf.namelist())
    assert "assembly_metadata.json" in names
    assert "tube_cut_list.csv" in names
    assert "component_list.json" in names
    cutlist = zf.read("tube_cut_list.csv").decode()
    assert "cut_length_mm" in cutlist and "od_mm" in cutlist
    readme = zf.read("README.txt").decode().lower()
    assert "concept" in readme and "certified" in readme  # honest caveat


# 5: detailed counts — many tubes and named components.
def test_assembly_detail_counts(chassis):
    measured = chassis["design"]["dimension_report"]["measured"]
    assert measured["tube_count"] >= 70
    assert measured["component_count"] >= 90


# 6: required zones AND systems present; representative component types.
def test_assembly_zones_systems_and_components(chassis):
    rep = chassis["design"]["dimension_report"]
    assert REQUIRED_ZONES <= set(rep["zones"]["present"])
    assert rep["zones"]["missing"] == []
    assert REQUIRED_SYSTEMS <= set(rep["systems"]["present"])
    assert rep["systems"]["missing"] == []
    types = {c["type"] for c in rep["components"]}
    for required in ("lower_rail", "upper_rail", "roll_cage_bar", "cross_member",
                     "transmission_tunnel", "side_impact_bar", "diagonal_brace",
                     "engine_mount", "seat_mount", "gusset", "dashboard_support"):
        assert required in types, f"missing component type: {required}"


# 7: approximate envelope ~ 4200 x 1800 x 1200 mm.
def test_assembly_envelope_within_tolerance(chassis):
    bb = chassis["design"]["dimension_report"]["measured"]["bbox_mm"]
    assert abs(bb["x"] - 4200) <= 4200 * 0.20
    assert abs(bb["y"] - 1800) <= 1800 * 0.20
    assert abs(bb["z"] - 1200) <= 1200 * 0.20
    assert chassis["design"]["dimensions_within_tolerance"] is True


# 8: left/right symmetry holds (every *_left has a *_right).
def test_assembly_symmetry(chassis):
    comps = chassis["design"]["dimension_report"]["components"]
    ids = {c["id"] for c in comps}
    lefts = [i for i in ids if i.endswith("_left")]
    assert lefts, "expected mirrored left/right members"
    assert all(i[:-5] + "_right" in ids for i in lefts)
    warnings = " ".join(chassis["design"]["dimension_report"]["validation"]["warnings"]).lower()
    assert "symmetry" not in warnings


# 9: multi-body is fine for assembly — pass or warning, never critical.
def test_assembly_does_not_require_single_body(chassis):
    d = chassis["design"]
    assert d["validation_status"] in ("pass", "warning")
    assert d["validation_status"] != "critical_failure"
    assert d["validation_critical_failures"] == []
    measured = d["dimension_report"]["measured"]
    assert measured["mesh_components"] > 1  # genuinely an assembly of bodies
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
