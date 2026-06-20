"""Semantic generation benchmark runner.

    python -m scripts.run_semantic_generation_benchmark --provider mock --limit 200
    python -m scripts.run_semantic_generation_benchmark --provider openai --limit 200  # opt-in

For each prompt: generate (compiler / planner), then check semantic expectations —
not just that a file exists. Prints a summary + per-failure detail, writes a report.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402

DATA = Path(__file__).resolve().parent.parent / "tests" / "data" / "semantic_generation_benchmark.json"


def evaluate(entry: dict) -> dict:
    from app.export.exporter import generate
    from app.generation.cad_programs import generate_program
    from app.generation.compiler import compile_prompt
    from app.llm.factory import get_provider
    from app.parsing.complex_plan import looks_complex, plan_prompt
    from app.services.design_service import _plan_long_prompt

    prompt = entry["prompt"]
    row = {"prompt": prompt, "ok": True, "problems": []}
    provider = get_provider()

    # Compiler families.
    if generate_program(prompt) is not None:
        out = compile_prompt(prompt, provider)
        row["route"] = "cadquery_program"
        if out is None or not out.ok:
            row["ok"] = False
            row["problems"].append("compiler failed: " + (out.report.summary() if out and out.report else "none"))
            return row
        if not out.report.passed:
            row["ok"] = False
            row["problems"].append("semantic: " + out.report.summary())
        fam = entry.get("expected_object_family")
        if fam and fam not in out.brief.object_family:
            row["ok"] = False
            row["problems"].append(f"family {out.brief.object_family} != {fam}")
        return row

    # Planner (templates / feature-graph / general / clarification).
    result = _plan_long_prompt(prompt) if looks_complex(prompt) else plan_prompt(prompt)
    row["route"] = result.route or ("precision_template" if result.spec else "clarification")
    if entry.get("should_generate", True):
        if result.spec is None:
            row["ok"] = False
            row["problems"].append("expected generation, got clarification")
        else:
            try:
                gen = generate(result.spec)
                if not (len(gen.stl_bytes) > 0 and gen.step_bytes[:5] == b"ISO-1"):
                    row["ok"] = False
                    row["problems"].append("export not valid")
            except Exception as exc:  # noqa: BLE001
                row["ok"] = False
                row["problems"].append(f"generate failed: {exc}")
    else:
        if result.spec is not None:
            row["ok"] = False
            row["problems"].append("expected clarification, but it generated")
    return row


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default="mock", choices=["mock", "anthropic", "openai"])
    ap.add_argument("--limit", type=int, default=300)
    args = ap.parse_args()
    settings.llm_provider = args.provider

    prompts = json.loads(DATA.read_text())["prompts"][: args.limit]
    rows = [evaluate(e) for e in prompts]
    passed = sum(r["ok"] for r in rows)
    print(f"provider={args.provider}  {passed}/{len(rows)} semantically OK "
          f"({100*passed/len(rows):.0f}%)")
    for r in rows:
        if not r["ok"]:
            print(f"  FAIL {r['prompt'][:60]!r}: {'; '.join(r['problems'])}")

    out = Path(settings.eval_report_dir); out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    (out / f"semantic_benchmark_{args.provider}_{stamp}.json").write_text(
        json.dumps({"passed": passed, "total": len(rows), "rows": rows}, indent=2))
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
