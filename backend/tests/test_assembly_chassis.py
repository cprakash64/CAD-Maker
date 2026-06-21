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
    "main_frame", "roll_cage", "side_impact", "transmission_tunnel",
    "floor_panels", "suspension_tabs", "engine_mounts", "steering_column_mount",
    "radiator_mount", "fuel_tank_mount", "body_panel_tabs",
}
REQUIRED_ZONES = {
    "front_nose", "front_suspension", "engine_bay", "floor", "cockpit",
    "roll_cage", "side_impact", "rear_suspension", "rear_frame",
}

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


# 1 + 2: not only decomposition; route/design_mode/style indicate reference assembly.
def test_generates_assembly_not_decomposition(chassis):
    d = chassis["design"]
    assert d["needs_decomposition"] is False
    assert d["needs_clarification"] is False
    assert d["route"] == "assembly"
    assert d["design_mode"] == "assembly"
    assert d["validation_status"] != "critical_failure"
    assert d["dimension_report"]["measured"]["chassis_style"] == "reference_buggy_tubular_chassis"


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
    assert "plate_list.csv" in names
    assert "component_list.json" in names
    cutlist = zf.read("tube_cut_list.csv").decode()
    assert "cut_length_mm" in cutlist and "od_mm" in cutlist
    platelist = zf.read("plate_list.csv").decode()
    assert "bolt_holes" in platelist and "slots" in platelist
    meta = zf.read("assembly_metadata.json").decode().lower()
    assert "reference_buggy_tubular_chassis" in meta
    readme = zf.read("README.txt").decode().lower()
    assert "concept" in readme and "certified" in readme  # honest caveat


# 5: reference-grade counts — many tubes, components, plates, holes, slots.
def test_assembly_detail_counts(chassis):
    m = chassis["design"]["dimension_report"]["measured"]
    assert m["tube_count"] >= 140
    assert m["component_count"] >= 190
    assert m["plate_count"] >= 25
    assert m["hole_feature_count"] >= 40
    assert m["slot_feature_count"] >= 4
    assert m["suspension_tab_count"] >= 16


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
                     "nose_perimeter", "rear_hoop", "roof_rail", "engine_mount",
                     "suspension_tab", "side_skid_plate", "floor_pan",
                     "gusset", "dashboard_support", "front_bulkhead",
                     "steering_column_bracket"):
        assert required in types, f"missing component type: {required}"


# 6b: reference-grade detail — section presence, style, snapshot, material.
def test_assembly_reference_grade(chassis):
    rep = chassis["design"]["dimension_report"]
    m = rep["measured"]
    assert m["chassis_style"] == "reference_buggy_tubular_chassis"
    assert m["gusset_count"] >= 12
    assert m["side_plate_count"] >= 2
    assert m["floor_plate_count"] >= 2
    assert m["roof_member_count"] >= 8
    assert m["front_nose_present"] is True
    assert m["rear_frame_present"] is True
    assert m["steering_column_mount_present"] is True
    assert m["floor_panels_present"] is True
    assert m["side_impact_present"] is True
    snap = rep["snapshot"]
    assert snap["chassis_style"] == "reference_buggy_tubular_chassis"
    assert snap["tube_groups"] and snap["plate_groups"]
    assert snap["symmetry_pairs"] > 0
    assert rep["recommended_material"]["appearance"] == "dark_steel"


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
