"""Reliability: complex assemblies are decomposed (not attempted), generation is
time-budgeted, and concurrent/duplicate creates don't lock SQLite.
"""
from __future__ import annotations

import json
import threading
import time

import pytest

from app.config import settings
from app.llm.base import LLMUnavailableError

SPORTS_CAR = (
    "Create a detailed 3D CAD model of a rear-wheel-drive sports car chassis frame "
    "using welded steel tubular construction. The frame should include a strong "
    "rectangular main structure, front and rear suspension mounting points, engine "
    "bay, transmission tunnel, floor cross-members, roll cage structure, dashboard "
    "support bar, side-impact bars, and mounting brackets for seats, steering column, "
    "fuel tank, radiator, and body panels. Use round steel tubes with realistic wall "
    "thickness and clean welded joints. Design the chassis for a two-seat coupe with a "
    "front-mounted engine and rear-wheel drive layout. Keep the overall proportions "
    "similar to a compact sports car: approximately 4200 mm long, 1800 mm wide, and "
    "1200 mm high. Include symmetrical left and right geometry, triangulated bracing "
    "for rigidity, and clearly separated front, passenger cabin, and rear sections. "
    "Make the model manufacturable, structurally realistic, and fully parametric, with "
    "organized components for the main frame, roll cage, suspension mounts, engine "
    "mounts, and cross braces."
)

NORMAL_PROMPTS = [
    "Create a rectangular mounting plate 120mm long, 80mm wide, 8mm thick, with four M6 holes",
    "A round standoff spacer 10mm outer diameter, 20mm long, 4mm bore",
    "Make an L bracket with 60mm legs, 5mm thickness, 20mm width, and two 6mm holes on each face.",
]


def _create(client, auth, prompt: str):
    return client.post("/api/designs/create", json={"prompt": prompt}, headers=auth["headers"])


# --- complex assembly -> decomposition, fast, no LLM ----------------------
def test_sports_car_returns_decomposition_quickly(client, auth):
    start = time.perf_counter()
    r = _create(client, auth, SPORTS_CAR)
    elapsed = time.perf_counter() - start
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["needs_decomposition"] is True
    assert d["needs_clarification"] is False
    assert d["preview"] is None
    assert d["exports"] == []
    decomp = d["decomposition"]
    assert decomp and decomp["components"] and decomp["examples"]
    assert decomp["recommended_first"]
    # Cheap string analysis only — must be near-instant, never a minutes-long hang.
    assert elapsed < 5.0


def test_no_llm_call_for_decomposed_assembly(client, auth, monkeypatch):
    """The complexity gate must short-circuit BEFORE any planner/LLM call."""
    from app.llm import factory

    calls = {"n": 0}
    real = factory.get_cad_provider

    def spy():
        calls["n"] += 1
        return real()

    monkeypatch.setattr(factory, "get_cad_provider", spy)
    r = _create(client, auth, SPORTS_CAR)
    assert r.status_code == 200 and r.json()["needs_decomposition"] is True
    assert calls["n"] == 0, "planner/LLM was invoked for a decomposed assembly"


# --- normal prompts are unaffected ----------------------------------------
@pytest.mark.parametrize("prompt", NORMAL_PROMPTS)
def test_normal_prompts_are_not_decomposed(client, auth, prompt):
    r = _create(client, auth, prompt)
    assert r.status_code == 200, r.text
    assert r.json()["needs_decomposition"] is False


def test_l_bracket_still_generates(client, auth):
    r = _create(client, auth, NORMAL_PROMPTS[2])
    d = r.json()
    assert r.status_code == 200
    assert d["needs_decomposition"] is False
    assert {e["fmt"] for e in d["exports"]} == {"stl", "step"}


# --- generation time budget ------------------------------------------------
class _FakeOpenAI:
    def __init__(self, reply):
        self.reply = reply
        self.calls: list[dict] = []

    class _Responses:
        def __init__(self, outer):
            self.outer = outer

        def create(self, **kwargs):
            self.outer.calls.append(kwargs)

            class _R:
                output_text = json.dumps(self.outer.reply)

            return _R()

    @property
    def responses(self):
        return _FakeOpenAI._Responses(self)


def test_expired_budget_aborts_without_calling_model():
    from app.llm.budget import generation_budget
    from app.llm.openai_provider import OpenAIProvider

    provider = OpenAIProvider(client=_FakeOpenAI({"object_type": "spacer", "dimensions": {}}))
    with generation_budget(0.01):
        time.sleep(0.05)  # let the budget expire
        with pytest.raises(LLMUnavailableError) as exc:
            provider.parse_prompt("a spacer")
    assert "too long" in str(exc.value).lower()
    assert provider._client.calls == [], "no model call should be made once budget is spent"


def test_llm_unavailable_surfaces_as_clean_503(client, auth, monkeypatch):
    """A budget/LLM failure during create returns a clean 503, not a stack trace."""
    from app.llm import factory
    from app.llm.mock_provider import MockLLMProvider

    class _Blown(MockLLMProvider):
        name = "mock"

        def plan_cad(self, prompt, feedback=None):
            raise LLMUnavailableError(
                "Generation took too long and was stopped. Please try a simpler part."
            )

    monkeypatch.setattr(factory, "get_cad_provider", lambda: _Blown())
    # A normal (non-decomposed) prompt so we reach the planner.
    r = _create(client, auth, "a rectangular bracket 80x40x5mm with two M6 holes")
    assert r.status_code == 503, r.text
    body = r.json()
    assert "detail" in body and "too long" in body["detail"].lower()
    assert "Traceback" not in body["detail"]


# --- concurrent / duplicate creates must not lock SQLite ------------------
def test_concurrent_creates_do_not_lock_db(auth):
    """Several overlapping creates (the exact failure mode from the bug report)
    must all succeed — no 'database is locked'."""
    from fastapi.testclient import TestClient

    from app.main import app

    results: list = []
    errors: list = []

    def worker():
        try:
            with TestClient(app) as c:
                r = c.post(
                    "/api/designs/create",
                    json={"prompt": SPORTS_CAR},
                    headers=auth["headers"],
                )
                results.append(r.status_code)
        except Exception as exc:  # noqa: BLE001
            errors.append(repr(exc))

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, f"create raised under concurrency: {errors}"
    assert results == [200] * 5, results
    assert not any("database is locked" in e.lower() for e in errors)


def test_total_timeout_setting_is_configurable():
    assert isinstance(settings.cad_generation_timeout_seconds, int)
    assert settings.cad_generation_timeout_seconds > 0
