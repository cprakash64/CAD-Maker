"""Eval harness: dataset size, scoring fields, and report generation."""
import csv
import json
from pathlib import Path

from app.llm.mock_provider import MockLLMProvider
from scripts.run_eval import score_prompt, summarize

DATA = Path(__file__).parent / "data" / "eval_prompts.json"


def test_dataset_has_at_least_200_prompts():
    data = json.loads(DATA.read_text())
    assert len(data["creation"]) >= 200
    assert len(data["modification"]) >= 5


def test_score_prompt_has_all_required_fields():
    provider = MockLLMProvider()
    row = score_prompt(
        {"prompt": "bracket 80x40x5mm with two M6 holes", "expect_type": "rectangular_bracket"},
        provider,
    )
    for field in (
        "valid_json",
        "correct_template",
        "model_or_clarification",
        "export_success",
        "dangerous_prompt_blocked_or_clarified",
        "latency_ms",
        "estimated_cost",
    ):
        assert field in row
    assert row["valid_json"] is True
    assert row["correct_template"] is True
    assert row["export_success"] is True
    assert row["latency_ms"] >= 0


def test_dangerous_prompt_is_blocked_or_clarified():
    provider = MockLLMProvider()
    row = score_prompt(
        {"prompt": "bracket 80mm wide, -5mm thick", "dangerous": True}, provider
    )
    assert row["dangerous_prompt_blocked_or_clarified"] is True
    assert row["outcome"] == "clarification"


def test_eval_on_subset_meets_thresholds():
    data = json.loads(DATA.read_text())
    provider = MockLLMProvider()
    rows = [score_prompt(e, provider) for e in data["creation"][:40]]
    summary = summarize(rows)
    assert summary["crash_pct"] == 0.0
    assert summary["model_or_clarification_pct"] >= 80.0
    # Routing accuracy should be high on the deterministic-routing entries.
    if summary["correct_template_pct"] is not None:
        assert summary["correct_template_pct"] >= 80.0


def test_run_eval_writes_json_and_csv(tmp_path):
    import sys

    from scripts import run_eval

    argv = sys.argv
    sys.argv = ["run_eval", "--provider", "mock", "--limit", "12", "--out", str(tmp_path)]
    try:
        assert run_eval.main() == 0
    finally:
        sys.argv = argv

    jsons = list(tmp_path.glob("eval_mock_*.json"))
    csvs = list(tmp_path.glob("eval_mock_*.csv"))
    assert jsons and csvs
    report = json.loads(jsons[0].read_text())
    assert report["summary"]["total"] == 12
    assert "model_or_clarification_pct" in report["summary"]
    with csvs[0].open() as fh:
        reader = csv.DictReader(fh)
        assert len(list(reader)) == 12
