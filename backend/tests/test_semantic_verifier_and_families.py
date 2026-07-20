"""Semantic verifier + deterministic coverage for the ex-`cadquery_program` families.

History: this file used to test the `cadquery_program` compiler, which executed
provider-authored Python in a sandbox. That facility is removed (F-1). What
survives here is the part that was always safe and valuable:

  * the semantic verifier, which is pure data-in/report-out; and
  * coverage that the part families the old compiler handled still generate
    through the deterministic template / feature-graph pipeline.

The old compiler only ever produced geometry under the offline mock provider —
`OpenAIProvider` never implemented `cad_program()`, so in production these
prompts already took the deterministic path this file now asserts.
"""
from __future__ import annotations

import pytest

from app.generation.semantic_verifier import verify
from app.schemas.brief import BriefHole, CADDesignBrief


# --- semantic verifier (pure, no execution) -------------------------------
def test_verifier_flags_wrong_hole_count():
    brief = CADDesignBrief(object_type="flange_plate", object_family="flange_plate",
                           holes=[BriefHole(count=8, pattern="bolt_circle",
                                            bolt_circle_diameter_mm=100)])
    meta = {"object_type": "flange_plate", "solid_count": 1, "holes": 3,
            "feature_counts": {"holes": 3}, "dimensions": {"x": 140, "y": 140, "z": 12}}
    report = verify(brief, meta, {"x": 140, "y": 140, "z": 12})
    assert not report.passed
    assert any(c.name == "hole_count" and not c.passed for c in report.checks)


def test_verifier_flags_disconnected_bodies():
    brief = CADDesignBrief(object_type="block", object_family="block")
    meta = {"object_type": "block", "solid_count": 3, "dimensions": {"x": 50, "y": 50, "z": 20}}
    report = verify(brief, meta, {"x": 50, "y": 50, "z": 20})
    assert not report.passed
    assert any(c.name == "single_connected_body" and not c.passed for c in report.checks)


def test_verifier_passes_a_consistent_part():
    brief = CADDesignBrief(object_type="block", object_family="block")
    meta = {"object_type": "block", "solid_count": 1, "dimensions": {"x": 50, "y": 50, "z": 20}}
    report = verify(brief, meta, {"x": 50, "y": 50, "z": 20})
    assert report.passed, report.summary()


# --- the families the old compiler covered still generate -----------------
# Verified against the post-removal pipeline (see docs/production-readiness.md).
GENERATES = [
    "a simple bearing housing for a 20mm shaft",
    "a rectangular block with a stepped slot and two counterbored holes",
    "a hexagonal spacer with a 6mm through hole",
    "a pulley with a 10mm shaft hole and 60mm outer diameter",
    "a hexagonal gear with a 10mm shaft",
    "a 90 degree pipe elbow with circular flanges",
    "a small vise jaw with two mounting holes and a V groove",
    "a motor mounting plate for a NEMA 17 stepper",
    "spur gear with 32 teeth and 8mm bore",
]


@pytest.mark.parametrize("prompt", GENERATES)
def test_ex_compiler_family_still_generates(client, auth, prompt):
    r = client.post("/api/designs/create", json={"prompt": prompt}, headers=auth["headers"])
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["needs_clarification"] is False, f"{prompt!r} now asks for clarification"
    assert d["exports"], f"{prompt!r} produced no exports"
    assert d["route"] != "cadquery_program", "the removed route must never reappear"


# --- known coverage gaps exposed by removing the compiler -----------------
# These two prompts produced geometry ONLY under the mock provider's program
# path. Production (OpenAI) already fell through to the deterministic pipeline,
# which asks for clarification instead. Recorded as strict xfail so that when
# family defaults land (Phase 2 generation policy) CI flags these to be promoted
# into GENERATES above rather than silently drifting.
KNOWN_GAPS = [
    "a shaft collar with an M6 clamp screw",
    "a flange plate with 8 holes on a 100mm bolt circle",
]


@pytest.mark.parametrize("prompt", KNOWN_GAPS)
@pytest.mark.xfail(strict=True, reason="No deterministic family default yet; see F-1 follow-up")
def test_known_family_gap_generates(client, auth, prompt):
    r = client.post("/api/designs/create", json={"prompt": prompt}, headers=auth["headers"])
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["needs_clarification"] is False
    assert d["exports"]
