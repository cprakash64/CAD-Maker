"""Gear trust: "Make a gear." must render a VISIBLE spur gear (alternating
tooth/root geometry), and a smooth disc/cylinder may NEVER pass as a gear.

The release-blocking bug was a gear that exported a smooth cylinder with a bore
and reported PASS because only bbox/hole/watertight checks ran. These tests pin
the geometry fix (module-based teeth) and the new semantic tooth audit.
"""
from __future__ import annotations

import io

import pytest

from app.cad.semantic_audits import (
    audit_gear,
    measure_radial_teeth,
)


def _create(client, auth, prompt: str) -> dict:
    r = client.post("/api/designs/create", json={"prompt": prompt}, headers=auth["headers"])
    assert r.status_code == 200, r.text
    return r.json()


def _gear_audit(d: dict) -> dict:
    return ((d.get("dimension_report") or {}).get("semantic_audit") or {}).get("gear") or {}


# --- end-to-end gear prompts ----------------------------------------------
def test_make_a_gear_is_a_visible_spur_gear(client, auth):
    d = _create(client, auth, "Make a gear.")
    assert d["generation_outcome"] == "generated_single_part"
    assert d["object_type"] == "simple_gear_or_pulley"
    assert d["classification"]["family_id"] == "gear_blank"
    assert d["validation_status"] == "pass"
    assert d["download_blocked_reason"] is None

    # Default gear: 24 teeth, and the rim genuinely has teeth (not a disc).
    assert d["spec"]["dimensions"]["tooth_count"] == 24
    gear = _gear_audit(d)
    assert gear["teeth_visible"] is True
    assert gear["tooth_depth_ratio"] >= 0.05, "rim is effectively circular — no teeth"

    # STEP + STL both exported and non-empty.
    assert {e["fmt"] for e in d["exports"]} == {"stl", "step"}
    assert all(e["size_bytes"] > 0 for e in d["exports"])


def test_60_tooth_gear_with_explicit_dimensions(client, auth):
    d = _create(
        client, auth,
        "Make a 60 tooth gear, 120mm outside diameter, 10mm thick, with a 12mm center bore.")
    assert d["validation_status"] == "pass"
    dims = d["spec"]["dimensions"]
    assert dims["tooth_count"] == 60
    assert dims["bore_diameter_mm"] == 12
    assert d["bounding_box_mm"]["x"] == pytest.approx(120, abs=1.0)
    assert d["bounding_box_mm"]["z"] == pytest.approx(10, abs=0.5)
    assert _gear_audit(d)["teeth_visible"] is True


def test_40_tooth_spur_gear_module_2(client, auth):
    d = _create(client, auth, "Create a 40 tooth spur gear, module 2, 8mm bore, 10mm thick.")
    assert d["validation_status"] == "pass"
    dims = d["spec"]["dimensions"]
    assert dims["tooth_count"] == 40
    # module 2 * (40 + 2) = 84mm tip diameter.
    assert d["bounding_box_mm"]["x"] == pytest.approx(84, abs=1.0)
    assert _gear_audit(d)["teeth_visible"] is True


def test_gear_with_pitch_diameter(client, auth):
    d = _create(client, auth, "Make a gear with 32 teeth, 64mm pitch diameter, 12mm thick.")
    assert d["validation_status"] == "pass"
    # pitch 64 / 32 teeth -> module 2 -> tip 2*(32+2) = 68mm.
    assert d["bounding_box_mm"]["x"] == pytest.approx(68, abs=1.5)
    assert _gear_audit(d)["teeth_visible"] is True


@pytest.mark.parametrize("prompt", [
    "Make a gear.",
    "Make a 24 tooth gear.",
    "Create a 40 tooth spur gear, module 2, 8mm bore, 10mm thick.",
])
def test_gear_exports_are_nonempty(client, auth, prompt):
    d = _create(client, auth, prompt)
    exports = {e["fmt"]: e["size_bytes"] for e in d["exports"]}
    assert exports.get("stl", 0) > 0
    assert exports.get("step", 0) > 0


# --- the core trust guarantee: a smooth disc can NEVER pass as a gear -------
def test_smooth_disc_labelled_gear_fails_semantic_audit():
    """A plain disc/cylinder with gear metadata must FAIL the semantic gear audit
    (critical), so it can never be reported as a PASS gear."""
    import cadquery as cq
    from cadquery import exporters

    disc = cq.Workplane("XY").circle(30).extrude(12).faces(">Z").workplane().hole(10)
    with io.BytesIO() as _:
        exporters.export(disc, "/tmp/_trust_disc.stl")
    stl = open("/tmp/_trust_disc.stl", "rb").read()

    teeth = measure_radial_teeth(stl)
    assert teeth["depth_ratio"] < 0.015, "disc should have ~no radial variation"

    issues = audit_gear(object_type="simple_gear_or_pulley", tooth_count=24,
                        tooth_depth_ratio=teeth["depth_ratio"],
                        measured_peaks=teeth["peaks"])
    criticals = [i for i in issues if i.severity == "critical"]
    assert criticals, "a smooth disc claiming to be a gear must fail critically"
    assert criticals[0].check == "gear_has_no_teeth"


def test_real_gear_passes_semantic_audit():
    """A real module-based gear has visible teeth and passes the audit."""
    import os
    import tempfile

    os.environ.setdefault("STORAGE_DIR", tempfile.mkdtemp())
    from app.export.exporter import generate
    from app.schemas.design_spec import DesignSpec

    spec = DesignSpec(object_type="simple_gear_or_pulley",
                      dimensions={"outer_diameter_mm": 52, "thickness_mm": 12,
                                  "bore_diameter_mm": 8, "tooth_count": 24, "module_mm": 2})
    res = generate(spec)
    teeth = measure_radial_teeth(res.stl_bytes)
    assert teeth["depth_ratio"] >= 0.05
    issues = audit_gear(object_type="simple_gear_or_pulley", tooth_count=24,
                        tooth_depth_ratio=teeth["depth_ratio"], measured_peaks=teeth["peaks"])
    assert not [i for i in issues if i.severity == "critical"]


# --- unsupported gear subtypes are flagged, never a silent PASS ------------
def test_helical_gear_is_flagged_not_passed_silently(client, auth):
    d = _create(client, auth, "Make a helical gear with 24 teeth")
    # Built (visible spur teeth) but the type mismatch is surfaced as a non-PASS
    # warning so it is never reported as a true helical gear.
    assert d["validation_status"] in ("warning", "pass")
    gear = _gear_audit(d)
    assert gear.get("requested_gear_type") == "helical"
    assert d["validation_status"] == "warning"
    assert any("helical" in w.lower() for w in d["validation_warnings"])


# --- other minimal semantic audits ----------------------------------------
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
