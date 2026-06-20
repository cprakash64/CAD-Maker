"""Evaluation harness for prompt quality, latency, cost, and failure modes.

    python -m scripts.run_eval --provider mock --limit 200
    python -m scripts.run_eval --provider openai --limit 50

Runs each prompt through the SAME safety pipeline used in production
(parse -> validate -> generate), scoring per the beta metrics, and writes JSON
and CSV reports to the eval report directory. Never executes model output.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.cad.base import CadGenerationError  # noqa: E402
from app.config import settings  # noqa: E402
from app.export.exporter import generate  # noqa: E402
from app.parsing.prompt_parser import parse_prompt  # noqa: E402

DATA = Path(__file__).resolve().parent.parent / "tests" / "data" / "eval_prompts.json"

# Rough output-side cost estimate per 1K tokens (USD). Mock/anthropic-less runs
# have no usage and score 0. Tune per model as needed.
_COST_PER_1K = {"gpt-4o-mini": 0.0006, "gpt-4o": 0.01}


def _estimate_cost(provider_name: str) -> float:
    # Without real token usage we report 0 (mock/offline). Real OpenAI usage
    # would be summed from response.usage; left as 0 here to stay deterministic.
    return 0.0


def score_prompt(entry: dict, provider) -> dict:
    prompt = entry["prompt"]
    expect_type = entry.get("expect_type")
    dangerous = entry.get("dangerous", False)

    row = {
        "prompt": prompt,
        "category": entry.get("category", ""),
        "valid_json": False,
        "routed_type": None,
        "correct_template": None,
        "model_or_clarification": False,
        "export_success": None,
        "dangerous_prompt_blocked_or_clarified": None,
        "latency_ms": 0.0,
        "estimated_cost": 0.0,
        "outcome": "crash",
    }

    start = time.perf_counter()
    try:
        result = parse_prompt(prompt, provider=provider)
        row["valid_json"] = True  # parser returned a structured ParseResult
    except Exception as exc:  # noqa: BLE001
        row["error"] = f"{type(exc).__name__}: {exc}"
        row["latency_ms"] = round((time.perf_counter() - start) * 1000, 2)
        return row

    routed = (
        result.spec.object_type
        if result.spec
        else (result.raw_llm_output or {}).get("object_type")
    )
    row["routed_type"] = routed
    if expect_type:
        row["correct_template"] = routed == expect_type

    if result.spec is None:
        row["outcome"] = "clarification"
        row["model_or_clarification"] = True
    else:
        try:
            gen = generate(result.spec)
            ok = len(gen.stl_bytes) > 0 and gen.step_bytes[:5] == b"ISO-1"
            row["export_success"] = ok
            row["model_or_clarification"] = True
            row["outcome"] = "model"
        except CadGenerationError:
            # Contradictory geometry -> the service surfaces a clarification.
            row["outcome"] = "clarification"
            row["model_or_clarification"] = True
            row["export_success"] = False

    if dangerous:
        # A dangerous prompt is handled well if it did NOT silently build a model.
        row["dangerous_prompt_blocked_or_clarified"] = row["outcome"] == "clarification"

    row["latency_ms"] = round((time.perf_counter() - start) * 1000, 2)
    row["estimated_cost"] = _estimate_cost(getattr(provider, "name", "?"))
    return row


def summarize(rows: list[dict]) -> dict:
    n = len(rows)
    routed = [r for r in rows if r["correct_template"] is not None]
    dangerous = [r for r in rows if r["dangerous_prompt_blocked_or_clarified"] is not None]
    models = [r for r in rows if r["outcome"] == "model"]
    exports = [r for r in rows if r["export_success"] is not None]

    def pct(num, den):
        return round(100 * num / den, 1) if den else None

    return {
        "total": n,
        "valid_json_pct": pct(sum(r["valid_json"] for r in rows), n),
        "model_or_clarification_pct": pct(sum(r["model_or_clarification"] for r in rows), n),
        "model_pct": pct(len(models), n),
        "clarification_pct": pct(sum(r["outcome"] == "clarification" for r in rows), n),
        "crash_pct": pct(sum(r["outcome"] == "crash" for r in rows), n),
        "correct_template_pct": pct(sum(r["correct_template"] for r in routed), len(routed)),
        "export_success_pct": pct(sum(bool(r["export_success"]) for r in exports), len(exports)),
        "dangerous_handled_pct": pct(
            sum(r["dangerous_prompt_blocked_or_clarified"] for r in dangerous), len(dangerous)
        ),
        "avg_latency_ms": round(sum(r["latency_ms"] for r in rows) / n, 2) if n else 0,
        "total_estimated_cost": round(sum(r["estimated_cost"] for r in rows), 6),
    }


def _provider(name: str):
    from app.llm.factory import get_provider

    settings.llm_provider = name
    return get_provider()


def main() -> int:
    ap = argparse.ArgumentParser(description="SourceCAD prompt eval harness")
    ap.add_argument("--provider", default="mock", choices=["mock", "anthropic", "openai"])
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--out", default=settings.eval_report_dir)
    args = ap.parse_args()

    data = json.loads(DATA.read_text())
    prompts = data["creation"][: args.limit]
    provider = _provider(args.provider)

    rows = [score_prompt(e, provider) for e in prompts]
    summary = summarize(rows)
    summary["provider"] = args.provider
    summary["timestamp"] = datetime.now(timezone.utc).isoformat()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    json_path = out_dir / f"eval_{args.provider}_{stamp}.json"
    csv_path = out_dir / f"eval_{args.provider}_{stamp}.csv"

    json_path.write_text(json.dumps({"summary": summary, "rows": rows}, indent=2))
    with csv_path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    print(json.dumps(summary, indent=2))
    print(f"\nWrote {json_path}\nWrote {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
