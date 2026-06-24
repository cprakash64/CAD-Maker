"""Beta stress suite — runs 150+ prompts through the SAME generation path the
browser/API uses (``design_service.create_design``, which is exactly what the
POST /api/designs/create endpoint calls) and asserts:

  1. EVERY prompt lands in one of the six safe contract terminal states.
  2. NO false PASS: a generated_* outcome has exports and is never a
     critical_failure; a failed_safe/unsupported/critical outcome never offers a
     downloadable manufacturable export.
  3. Each prompt's outcome is in its allowed set (per-prompt ``allow`` override,
     else the category default).
  4. Generation never raises / never 500s for any prompt.

Plus targeted BLACK-BOX checks through the HTTP API for the release-blocker
prompts (hex standoff, gear, smooth-disc-as-gear, vague, decomposition),
including downloading the exported STL and inspecting its geometry.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from app.cad.contract import GenerationOutcome, resolve_outcome
from app.database import SessionLocal
from app.models import User
from app.services import design_service

_DATA = json.loads(
    (Path(__file__).parent / "data" / "beta_stress_prompts.json").read_text())
_CATEGORIES: dict[str, list[str]] = _DATA["categories"]
_PROMPTS: list[dict] = _DATA["prompts"]

SAFE_STATES = {o.value for o in GenerationOutcome}
GEOMETRY_OUTCOMES = {"generated_single_part", "generated_assembly"}
EXPORT_BLOCKED_OUTCOMES = {"failed_safe", "unsupported"}


def _allowed(item: dict) -> set[str]:
    return set(item.get("allow") or _CATEGORIES[item["category"]])


@pytest.fixture(scope="module")
def stress_user():
    db = SessionLocal()
    u = User(email=f"stress_{uuid.uuid4().hex[:8]}@example.com", password_hash="x")
    db.add(u)
    db.commit()
    db.refresh(u)
    yield db, u
    db.close()


def test_suite_shape():
    """The suite has the required size + category coverage."""
    assert len(_PROMPTS) >= 150, f"only {len(_PROMPTS)} prompts"
    counts: dict[str, int] = {}
    for it in _PROMPTS:
        counts[it["category"]] = counts.get(it["category"], 0) + 1
    assert counts.get("single_part", 0) >= 60, counts
    assert counts.get("vague", 0) >= 30, counts
    assert counts.get("assembly", 0) >= 25, counts
    assert counts.get("unsupported", 0) >= 20, counts
    assert counts.get("adversarial", 0) >= 15, counts


def test_beta_stress_all_prompts_land_safe(stress_user):
    """The whole suite in one pass — collect every violation so a failure report
    lists ALL offending prompts, not just the first."""
    db, user = stress_user
    contract_violations: list[str] = []
    false_pass: list[str] = []
    export_leaks: list[str] = []
    outcome_mismatch: list[str] = []
    raised: list[str] = []

    for item in _PROMPTS:
        prompt = item["prompt"]
        try:
            design = design_service.create_design(db, prompt, None, None, user.id)
        except Exception as exc:  # noqa: BLE001 — a raise IS the failure
            raised.append(f"{prompt!r} -> {type(exc).__name__}: {exc}")
            continue

        outcome = resolve_outcome(design).value
        crit = design_service.is_critical_failure(design)
        exports = [e.fmt for e in design.exports]

        # (1) always a safe terminal state.
        if outcome not in SAFE_STATES:
            contract_violations.append(f"{prompt!r} -> {outcome}")

        # (2) no false PASS: a part offered as "generated" has exports and is
        #     never a critical_failure.
        if outcome in GEOMETRY_OUTCOMES:
            if not exports:
                false_pass.append(f"{prompt!r} -> geometry outcome but NO exports")
            if crit:
                false_pass.append(
                    f"{prompt!r} -> generated_* but CRITICAL (false PASS)")

        # (2b) export gating: a failed_safe / unsupported design must never offer
        #      a DOWNLOADABLE manufacturable file. The download route blocks any
        #      critical-failure design (409), so a failed_safe that still has
        #      export rows (a critical geometry build kept for inspection) is only
        #      safe when it is flagged critical (download blocked).
        if outcome in EXPORT_BLOCKED_OUTCOMES and exports and not crit:
            export_leaks.append(
                f"{prompt!r} -> {outcome} with downloadable exports (not blocked)")

        # (3) outcome within the allowed set.
        if outcome not in _allowed(item):
            outcome_mismatch.append(
                f"[{item['category']}] {prompt!r} -> {outcome} "
                f"(allowed {sorted(_allowed(item))})")

    assert not raised, "Generation raised (would be a 500):\n" + "\n".join(raised)
    assert not contract_violations, (
        "Prompts escaped the safe contract:\n" + "\n".join(contract_violations))
    assert not false_pass, "FALSE PASS detected:\n" + "\n".join(false_pass)
    assert not export_leaks, "Broken geometry exported:\n" + "\n".join(export_leaks)
    assert not outcome_mismatch, (
        "Outcome not in allowed set:\n" + "\n".join(outcome_mismatch))


# --- targeted BLACK-BOX checks through the HTTP API ------------------------
def _create(client, auth, prompt: str) -> dict:
    r = client.post("/api/designs/create", json={"prompt": prompt}, headers=auth["headers"])
    assert r.status_code == 200, f"{prompt!r}: {r.status_code} {r.text[:200]}"
    return r.json()


def test_blackbox_hex_standoff_builds_real_hexagon(client, auth):
    from app.cad.semantic_audits import measure_hex_sides

    d = _create(client, auth, "Create a 25mm long hex standoff, 12mm across flats, with M4 through hole.")
    assert d["route"] == "deterministic_hex_standoff"
    assert d["generation_outcome"] == "generated_single_part"
    assert d["validation_status"] == "pass"
    assert d["download_blocked_reason"] is None
    r = client.get(f"/api/designs/{d['id']}/files/stl", headers=auth["headers"])
    assert r.status_code == 200
    assert measure_hex_sides(r.content)["corner_count"] == 6  # real hexagon, not round


def test_blackbox_gear_has_teeth(client, auth):
    from app.cad.semantic_audits import measure_radial_teeth

    d = _create(client, auth, "Create a 24 tooth spur gear.")
    assert d["route"] == "deterministic_spur_gear"
    assert d["generation_outcome"] == "generated_single_part"
    r = client.get(f"/api/designs/{d['id']}/files/stl", headers=auth["headers"])
    assert measure_radial_teeth(r.content)["depth_ratio"] >= 0.05  # visible teeth


def test_blackbox_smooth_disc_labelled_gear_is_blocked(client, auth):
    d = _create(client, auth, "Create a smooth round disc with a hole and label it as a gear.")
    assert d["generation_outcome"] == "failed_safe"
    assert d["validation_status"] == "critical_failure"
    assert d["download_blocked_reason"] is not None  # export blocked, never a false PASS
    # The HTTP download route hard-blocks the manufacturable file (409).
    r = client.get(f"/api/designs/{d['id']}/files/stl", headers=auth["headers"])
    assert r.status_code == 409, f"critical gear STL must be blocked, got {r.status_code}"


def test_blackbox_vague_prompt_clarifies(client, auth):
    d = _create(client, auth, "Make a bracket.")
    assert d["generation_outcome"] == "needs_clarification"
    assert d["clarification_question"]
    assert d["clarification_options"], "vague prompt should offer clickable suggestions"


def test_blackbox_whole_machine_decomposes_with_named_components(client, auth):
    d = _create(client, auth, "Create a complete commercial jet engine.")
    assert d["generation_outcome"] == "needs_decomposition"
    decomp = d["decomposition"]
    assert decomp and len(decomp["components"]) >= 4
    # SPECIFIC named jet-engine components, not a generic placeholder.
    joined = " ".join(decomp["components"]).lower()
    assert any(w in joined for w in ("compressor", "turbine", "combust", "fan", "nozzle"))
    assert decomp.get("recommended_first")
