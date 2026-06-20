"""Prompt benchmark: every prompt must yield a valid model or a useful
clarification (never a crash), with >=80% reaching that bar and correct
template routing for deterministic prompts."""
import json
from pathlib import Path

import pytest

from app.cad.base import CadGenerationError
from app.export.exporter import generate
from app.parsing.modification_parser import parse_and_apply
from app.parsing.prompt_parser import parse_prompt

DATA = json.loads(
    (Path(__file__).parent / "data" / "benchmark_prompts.json").read_text()
)
CREATION = DATA["creation"]
MODIFICATION = DATA["modification"]


def _outcome(prompt: str):
    """Return ('model', design) | ('clarification', question) | ('crash', err)."""
    try:
        result = parse_prompt(prompt)
    except Exception as exc:  # noqa: BLE001
        return "crash", repr(exc)
    if result.spec is None:
        return "clarification", result.clarification_question
    try:
        gen = generate(result.spec)
    except CadGenerationError as exc:
        # Spec validated but geometry is contradictory — the service surfaces
        # this as a clarification, so count it the same way here.
        return "clarification", str(exc)
    except Exception as exc:  # noqa: BLE001
        return "crash", repr(exc)
    assert len(gen.stl_bytes) > 0 and gen.step_bytes[:5] == b"ISO-1"
    return "model", result.spec


def test_benchmark_has_enough_prompts():
    assert len(CREATION) + len(MODIFICATION) >= 50


def test_no_prompt_crashes_and_80pct_succeed():
    handled = 0
    crashes = []
    for entry in CREATION:
        kind, payload = _outcome(entry["prompt"])
        if kind in ("model", "clarification"):
            handled += 1
        else:
            crashes.append((entry["prompt"], payload))
    assert not crashes, f"Prompts crashed: {crashes}"
    rate = handled / len(CREATION)
    assert rate >= 0.80, f"Only {rate:.0%} of prompts produced model-or-clarification"


@pytest.mark.parametrize("entry", [e for e in CREATION if "expect_type" in e])
def test_routing(entry):
    kind, payload = _outcome(entry["prompt"])
    # Routing is asserted via the (possibly clarification) object_type.
    result = parse_prompt(entry["prompt"])
    routed = (
        result.spec.object_type
        if result.spec
        else (result.raw_llm_output or {}).get("object_type")
    )
    assert routed == entry["expect_type"], (
        f"{entry['prompt']!r} routed to {routed}, expected {entry['expect_type']}"
    )


@pytest.mark.parametrize("entry", [e for e in CREATION if e["expect"] == "clarification"])
def test_clarification_prompts(entry):
    kind, _ = _outcome(entry["prompt"])
    assert kind == "clarification", f"{entry['prompt']!r} did not ask for clarification"


@pytest.mark.parametrize("entry", [e for e in CREATION if e["expect"] == "model"])
def test_model_prompts(entry):
    kind, _ = _outcome(entry["prompt"])
    assert kind == "model", f"{entry['prompt']!r} did not produce a model"


def test_modification_benchmark():
    handled = 0
    for entry in MODIFICATION:
        base = parse_prompt(entry["base"])
        assert base.spec is not None, f"base failed: {entry['base']}"
        result = parse_and_apply(entry["prompt"], base.spec)
        if entry["expect"] == "model":
            assert result.spec is not None, f"{entry['prompt']!r} produced no model"
            gen = generate(result.spec)
            assert len(gen.stl_bytes) > 0 and gen.step_bytes[:5] == b"ISO-1"
            handled += 1
        else:
            assert result.clarification_question, (
                f"{entry['prompt']!r} should have asked for clarification"
            )
            handled += 1
    assert handled == len(MODIFICATION)
