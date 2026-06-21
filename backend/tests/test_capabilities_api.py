"""API tests for the capability endpoint and classification storage.

These exercise the HTTP surface so the frontend contract is pinned: the
capability catalog is honest and complete, and a generated design carries its
structured classification metadata.
"""
from __future__ import annotations

from fastapi.testclient import TestClient


def test_capabilities_endpoint_lists_families(client: TestClient):
    r = client.get("/api/capabilities")
    assert r.status_code == 200, r.text
    body = r.json()
    families = body["families"]
    assert len(families) >= 10
    ids = {f["family_id"] for f in families}
    # Core families must be advertised.
    assert {"mounting_plate", "spacer", "l_bracket", "tube_chassis"} <= ids

    for f in families:
        # Honesty: every advertised family explains its limits + maturity.
        assert f["known_limitations"], f["family_id"]
        assert f["maturity"] in {
            "production_ready", "beta", "concept", "unsupported"
        }
        assert f["maturity_meaning"]
        # Unsupported families must not advertise an export.
        if f["maturity"] == "unsupported":
            assert f["exportable"] is False


def test_capabilities_counts_match(client: TestClient):
    body = client.get("/api/capabilities").json()
    assert body["counts"]["total"] == len(body["families"])
    assert sum(body["counts"]["by_maturity"].values()) == len(body["families"])


def test_capabilities_makes_no_fake_claims(client: TestClient):
    """Concept assemblies must be labelled concept and flagged not-certified."""
    body = client.get("/api/capabilities").json()
    chassis = next(f for f in body["families"] if f["family_id"] == "tube_chassis")
    assert chassis["maturity"] == "concept"
    assert any("not" in lim.lower() and "certif" in lim.lower()
               for lim in chassis["known_limitations"])


def test_generated_design_carries_classification(client: TestClient, auth: dict):
    r = client.post(
        "/api/designs/create",
        json={"prompt": "A rectangular mounting plate 80x40x5mm with two 6mm holes"},
        headers=auth["headers"],
    )
    assert r.status_code in (200, 201), r.text
    dto = r.json()
    cls = dto["classification"]
    assert cls is not None
    assert cls["family_id"] == "mounting_plate"
    assert cls["design_mode"] == "single_part"
    assert cls["can_generate_now"] is True
    assert cls["limitations"]


def test_huge_prompt_classification_marks_decomposition(client: TestClient, auth: dict):
    r = client.post(
        "/api/designs/create",
        json={"prompt": (
            "Design a complete car with engine, suspension, transmission, "
            "drivetrain, body panels and a full interior dashboard"
        )},
        headers=auth["headers"],
    )
    assert r.status_code in (200, 201), r.text
    dto = r.json()
    assert dto["needs_decomposition"] is True
    cls = dto["classification"]
    assert cls["generation_strategy"] == "needs_decomposition"
    assert cls["can_generate_now"] is False
