"""Run the generation regression dataset through the mock planner (non-live)."""
import json
from pathlib import Path

from scripts.run_generation_regression import evaluate

DATA = Path(__file__).parent / "data" / "generation_regression_prompts.json"
PROMPTS = json.loads(DATA.read_text())["prompts"]


def test_dataset_has_at_least_150_prompts():
    assert len(PROMPTS) >= 150


def test_feature_graph_handles_at_least_10_non_template_prompts():
    fg = [p for p in PROMPTS if p.get("route") == "feature_graph"]
    assert len(fg) >= 10
    built = 0
    for entry in fg:
        if evaluate(entry)["ok"]:
            built += 1
    assert built >= 10, f"only {built} feature-graph prompts passed"


def test_full_regression_passes_with_mock():
    rows = [evaluate(e) for e in PROMPTS]
    failures = [(r["prompt"], r["problems"]) for r in rows if not r["ok"]]
    assert not failures, f"regression failures: {failures[:8]}"
