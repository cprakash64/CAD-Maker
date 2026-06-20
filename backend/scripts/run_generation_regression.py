"""Generation reliability regression runner.

    python -m scripts.run_generation_regression --provider mock --limit 100
    python -m scripts.run_generation_regression --provider openai --limit 100  # live, opt-in

For each prompt: route via the unified planner, generate CAD, and check route /
template / should_generate / must_have / must_not_have. Prints a summary and per-
failure detail; writes a JSON report.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402

DATA = Path(__file__).resolve().parent.parent / "tests" / "data" / "generation_regression_prompts.json"


def _route_of(result) -> str:
    """Prefer the planner's declared route; fall back to inferring from the spec."""
    if getattr(result, "route", None):
        return result.route
    spec = result.spec
    if spec is None:
        return "clarification"
    return "feature_graph" if spec.object_type == "feature_graph" else "precision_template"


def _features_present(spec) -> set[str]:
    """Coarse feature tags for must_have/must_not_have checks."""
    tags: set[str] = set()
    if spec is None:
        return tags
    if spec.object_type == "feature_graph" and spec.feature_graph:
        ops = {o.get("op") for o in spec.feature_graph.get("operations", [])}
        tags |= ops
        if "hex_prism" in ops:
            tags.add("hex")
    dims = spec.dimensions
    if dims.get("hex", 0) > 0:
        tags.add("hex")
    if dims.get("tooth_count", 0) > 0:
        tags.add("teeth")
    if spec.object_type == "inline_4_crankshaft":
        tags.add("journals")
    return tags


def evaluate(entry: dict) -> dict:
    from app.export.exporter import generate
    from app.parsing.complex_plan import plan_prompt

    row = {"prompt": entry["prompt"], "ok": True, "problems": []}
    try:
        result = plan_prompt(entry["prompt"])
    except Exception as exc:  # noqa: BLE001
        return {**row, "ok": False, "problems": [f"crash: {exc!r}"]}

    spec = result.spec
    route = _route_of(result)
    row["route"] = route
    row["template"] = spec.object_type if spec else None

    if entry.get("should_generate", True):
        if spec is None:
            row["ok"] = False
            row["problems"].append("expected generation, got clarification")
        else:
            try:
                gen = generate(spec)
                if not (len(gen.stl_bytes) > 0 and gen.step_bytes[:5] == b"ISO-1"):
                    row["ok"] = False
                    row["problems"].append("export not valid")
            except Exception as exc:  # noqa: BLE001
                row["ok"] = False
                row["problems"].append(f"generate failed: {exc}")
    else:
        if spec is not None:
            row["ok"] = False
            row["problems"].append("expected clarification, but it generated")

    # Route / template (only when specified).
    if entry.get("route") and route != entry["route"]:
        row["ok"] = False
        row["problems"].append(f"route {route} != {entry['route']}")
    if entry.get("template") and spec is not None and spec.object_type != entry["template"]:
        row["ok"] = False
        row["problems"].append(f"template {spec.object_type} != {entry['template']}")

    tags = _features_present(spec)
    for f in entry.get("must_have", []):
        if f not in tags:
            row["ok"] = False
            row["problems"].append(f"missing feature '{f}'")
    for f in entry.get("must_not_have", []):
        if f in tags:
            row["ok"] = False
            row["problems"].append(f"unexpected feature '{f}'")
    return row


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default="mock", choices=["mock", "anthropic", "openai"])
    ap.add_argument("--limit", type=int, default=200)
    args = ap.parse_args()
    settings.llm_provider = args.provider

    prompts = json.loads(DATA.read_text())["prompts"][: args.limit]
    rows = [evaluate(e) for e in prompts]
    passed = sum(r["ok"] for r in rows)
    print(f"provider={args.provider}  {passed}/{len(rows)} passed "
          f"({100 * passed / len(rows):.0f}%)")
    for r in rows:
        if not r["ok"]:
            print(f"  FAIL {r['prompt'][:60]!r}: {'; '.join(r['problems'])}")

    out = Path(settings.eval_report_dir); out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    (out / f"generation_regression_{args.provider}_{stamp}.json").write_text(
        json.dumps({"passed": passed, "total": len(rows), "rows": rows}, indent=2))
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
