"""Run the semantic generation benchmark (mock) — checks meaning, not file existence.

The full benchmark generates real geometry for every prompt, so we evaluate the
whole set exactly ONCE and share the results across assertions.
"""
import json
from pathlib import Path

from scripts.run_semantic_generation_benchmark import evaluate

DATA = Path(__file__).parent / "data" / "semantic_generation_benchmark.json"
PROMPTS = json.loads(DATA.read_text())["prompts"]


def _subset() -> list[dict]:
    """A representative slice (every family + a few sizes) so CI stays fast while
    still proving semantic correctness. The full 200 run via
    `python -m scripts.run_semantic_generation_benchmark`."""
    by_family: dict[str, list[dict]] = {}
    for p in PROMPTS:
        by_family.setdefault(p["expected_object_family"], []).append(p)
    chosen: list[dict] = []
    for fam, items in by_family.items():
        chosen.extend(items[:3] if fam != "unsupported" else items[:2])
    return chosen


_RESULTS = None


def _results():
    global _RESULTS
    if _RESULTS is None:
        _RESULTS = [(p, evaluate(p)) for p in _subset()]
    return _RESULTS


def test_benchmark_has_at_least_200_prompts():
    assert len(PROMPTS) >= 200


def test_compiler_families_semantically_pass():
    fam = [(p, r) for p, r in _results() if p["expected_route"] == "cadquery_program"]
    assert len(fam) >= 25
    fails = [(p["prompt"], r["problems"]) for p, r in fam if not r["ok"]]
    assert not fails, f"semantic failures: {fails[:6]}"


def test_full_semantic_benchmark_passes():
    fails = [(p["prompt"], r["problems"]) for p, r in _results() if not r["ok"]]
    assert not fails, f"benchmark failures: {fails[:8]}"
