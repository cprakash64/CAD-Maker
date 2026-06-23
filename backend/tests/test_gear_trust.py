"""Gear trust — BLACK-BOX through the same create_design path the browser uses.

The release blocker: "Make a gear." rendered a smooth disc/pulley with a bore yet
reported PASS, because in production gears went through the LLM/CadPlan path which
has no gear audit. These tests pin: (1) every gear routes to the deterministic
spur-gear builder, (2) the EXPORTED STL silhouette actually has teeth, and (3) a
smooth disc labelled as a gear can never PASS.
"""
from __future__ import annotations

import pytest

from app.cad.semantic_audits import audit_gear, measure_radial_teeth

ROUTE = "deterministic_spur_gear"


def _create(client, auth, prompt: str) -> dict:
    r = client.post("/api/designs/create", json={"prompt": prompt}, headers=auth["headers"])
    assert r.status_code == 200, r.text
    return r.json()


def _download(client, auth, design_id: str, fmt: str) -> bytes:
    r = client.get(f"/api/designs/{design_id}/files/{fmt}", headers=auth["headers"])
    assert r.status_code == 200, f"{fmt} download failed: {r.status_code} {r.text[:200]}"
    return r.content


def _gear_meta(d: dict) -> dict:
    """The gear semantic-audit block from the dimension report."""
    return ((d.get("dimension_report") or {}).get("semantic_audit") or {}).get("gear") or {}


# --- routing + geometry (black box) ---------------------------------------
def test_make_a_gear_routes_deterministic_and_has_visible_teeth(client, auth):
    d = _create(client, auth, "Make a gear.")
    assert d["route"] == ROUTE, "gear must use the deterministic spur-gear route"
    assert d["generation_outcome"] == "generated_single_part"
    assert d["validation_status"] == "pass"
    assert d["download_blocked_reason"] is None

    gear = _gear_meta(d)
    assert gear["tooth_count"] == 24
    assert gear["gear_visible_teeth"] is True
    assert gear["measured_tooth_count"] >= 20  # ~24 on the silhouette

    # Debug block exposed in the response for the UI / dev verification.
    dbg = d["gear_debug"]
    assert dbg["family"] == "gear" and dbg["route"] == ROUTE
    assert dbg["tooth_count"] == 24 and dbg["module"] == 2.0
    assert dbg["outside_diameter"] == pytest.approx(52, abs=1.0)
    assert dbg["root_diameter"] > 0 and dbg["bore_diameter"] == 8.0
    assert dbg["bore_shape"] == "circular"
    assert dbg["gear_visible_teeth"] is True

    # The EXPORTED STL silhouette (not metadata) must actually be toothed.
    stl = _download(client, auth, d["id"], "stl")
    teeth = measure_radial_teeth(stl)
    assert teeth["depth_ratio"] >= 0.05, "exported gear is a smooth disc — no teeth"
    assert 20 <= teeth["tooth_count"] <= 28, teeth["tooth_count"]
    assert len(_download(client, auth, d["id"], "step")) > 0


def test_60_tooth_gear_dimensions_and_teeth(client, auth):
    d = _create(
        client, auth,
        "Make a 60 tooth gear, 120mm outside diameter, 10mm thick, with a 12mm center bore.")
    assert d["route"] == ROUTE
    assert d["validation_status"] == "pass"
    assert d["bounding_box_mm"]["x"] == pytest.approx(120, abs=1.0)
    assert d["bounding_box_mm"]["z"] == pytest.approx(10, abs=0.5)

    gear = _gear_meta(d)
    assert gear["tooth_count"] == 60
    assert gear["gear_visible_teeth"] is True

    stl = _download(client, auth, d["id"], "stl")
    teeth = measure_radial_teeth(stl)
    assert 54 <= teeth["tooth_count"] <= 66, teeth["tooth_count"]  # ≈ 60


def test_40_tooth_spur_gear_module_2(client, auth):
    d = _create(client, auth, "Create a 40 tooth spur gear, module 2, 8mm bore, 10mm thick.")
    assert d["route"] == ROUTE
    assert d["validation_status"] == "pass"
    # module 2 * (40 + 2) = 84mm outside diameter.
    assert d["bounding_box_mm"]["x"] == pytest.approx(84, abs=1.0)
    stl = _download(client, auth, d["id"], "stl")
    teeth = measure_radial_teeth(stl)
    assert 36 <= teeth["tooth_count"] <= 44, teeth["tooth_count"]  # ≈ 40


# --- the core trust guarantee ----------------------------------------------
def test_smooth_disc_labelled_gear_never_passes(client, auth):
    """A smooth disc with a hole labelled as a gear must FAIL the semantic audit
    and never report PASS — black box, through create_design."""
    d = _create(client, auth, "Create a smooth round disc with a hole and label it as a gear.")
    assert d["route"] == ROUTE
    assert d["validation_status"] == "critical_failure"
    assert d["generation_outcome"] == "failed_safe"
    gear = _gear_meta(d)
    assert gear["gear_visible_teeth"] is False
    # The blocking failure names the missing teeth.
    assert any("gear" in f.lower() and "teeth" in f.lower()
               for f in d["validation_critical_failures"])
    # A manufacturable export of a fake gear is blocked.
    assert d["download_blocked_reason"] is not None


def test_pulley_is_smooth_and_not_a_gear(client, auth):
    d = _create(client, auth, "Create a pulley.")
    assert d["route"] != ROUTE, "a pulley must not use the gear route"
    assert d["validation_status"] in ("pass", "warning")
    assert _gear_meta(d) == {}, "a pulley must not be audited/classified as a gear"


# --- audit unit tests ------------------------------------------------------
def test_audit_passes_real_gear_fails_disc():
    import cadquery as cq
    from cadquery import exporters

    from app.export.exporter import generate
    from app.schemas.design_spec import DesignSpec

    spec = DesignSpec(object_type="simple_gear_or_pulley",
                      dimensions={"outer_diameter_mm": 52, "thickness_mm": 12,
                                  "bore_diameter_mm": 8, "tooth_count": 24, "module_mm": 2})
    gear = measure_radial_teeth(generate(spec).stl_bytes)
    assert not [i for i in audit_gear(
        object_type="simple_gear_or_pulley", tooth_count=24,
        tooth_depth_ratio=gear["depth_ratio"], measured_tooth_count=gear["tooth_count"])
        if i.severity == "critical"]

    disc = cq.Workplane("XY").circle(30).extrude(12).faces(">Z").workplane().hole(10)
    exporters.export(disc, "/tmp/_audit_disc.stl")
    dm = measure_radial_teeth(open("/tmp/_audit_disc.stl", "rb").read())
    issues = audit_gear(object_type="simple_gear_or_pulley", tooth_count=24,
                        tooth_depth_ratio=dm["depth_ratio"],
                        measured_tooth_count=dm["tooth_count"])
    crit = [i for i in issues if i.severity == "critical"]
    assert crit and crit[0].check == "gear_has_no_visible_teeth"


def test_helical_gear_is_flagged_not_silently_passed(client, auth):
    d = _create(client, auth, "Make a helical gear with 24 teeth")
    assert d["route"] == ROUTE
    # Built with visible spur teeth, but the type mismatch is a non-PASS warning.
    assert d["validation_status"] == "warning"
    assert _gear_meta(d)["requested_gear_type"] == "helical"
    assert any("helical" in w.lower() for w in d["validation_warnings"])


def test_minimal_semantic_audits_smoke():
    from app.cad.semantic_audits import (
        audit_bracket,
        audit_screwdriver,
        audit_wrench,
    )

    assert audit_screwdriver(["handle", "shaft", "tip"]) == []
    assert audit_screwdriver(["handle", "shaft"])[0].check == "screwdriver_incomplete"
    assert audit_wrench(["handle", "head"], None) == []
    assert audit_wrench(["handle"], 0)[0].check == "wrench_no_opening"
    assert audit_bracket("u_bracket", "u") == []
    assert audit_bracket("flat_plate", "L")[0].check == "bracket_type_mismatch"
