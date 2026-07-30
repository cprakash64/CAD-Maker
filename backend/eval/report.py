"""JSON + Markdown report generation.

Pass rates are computed separately per workflow, per complexity level, and
per suite -- never collapsed into one headline score, per the harness's
founding requirement. A case counts as PASS only when every assertion that
actually ran (status != skip) passed; a case with zero non-skip assertions is
flagged as `no_signal` rather than silently counted as a pass.
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from eval.assertions import FAIL, PASS, SKIP, AssertionResult
from eval.context import EvalContext
from eval.schema import EvalCase


@dataclass
class CaseResult:
    case_id: str
    suite: str
    workflow: str
    complexity: str
    data_split: str
    safety_classification: str
    source: str
    repeat_index: int
    status: str                 # pass | fail | no_signal
    ok: bool
    http_status: Any
    latency_ms: float
    provider: str
    model: str | None
    retries: int
    prompt_tokens: int | None
    completion_tokens: int | None
    estimated_cost_usd: float
    assertions: list[dict]
    error: str | None
    notes: str = ""


def score_case(case: EvalCase, ctx: EvalContext, results: list[AssertionResult],
               repeat_index: int = 0) -> CaseResult:
    non_skip = [r for r in results if r.status != SKIP]
    if not non_skip:
        status = "no_signal"
    else:
        status = PASS if all(r.status == PASS for r in non_skip) else FAIL
    return CaseResult(
        case_id=case.case_id, suite=case.suite, workflow=case.workflow,
        complexity=case.complexity, data_split=case.data_split,
        safety_classification=case.safety_classification, source=case.source,
        repeat_index=repeat_index, status=status, ok=ctx.ok,
        http_status=ctx.http_status, latency_ms=ctx.latency_ms,
        provider=ctx.provider, model=ctx.model, retries=ctx.retries,
        prompt_tokens=ctx.prompt_tokens, completion_tokens=ctx.completion_tokens,
        estimated_cost_usd=ctx.estimated_cost_usd,
        assertions=[asdict(r) for r in results], error=ctx.error,
        notes=case.notes,
    )


def _bucket_stats(results: list[CaseResult]) -> dict:
    n = len(results)
    passed = sum(1 for r in results if r.status == PASS)
    failed = sum(1 for r in results if r.status == FAIL)
    no_signal = sum(1 for r in results if r.status == "no_signal")
    pct = round(100 * passed / n, 1) if n else None
    return {
        "total": n, "passed": passed, "failed": failed, "no_signal": no_signal,
        "pass_rate_pct": pct,
        "avg_latency_ms": round(sum(r.latency_ms for r in results) / n, 2) if n else 0,
        "total_estimated_cost_usd": round(sum(r.estimated_cost_usd for r in results), 6),
    }


def _group(results: list[CaseResult], key) -> dict:
    buckets: dict[str, list[CaseResult]] = defaultdict(list)
    for r in results:
        buckets[key(r)].append(r)
    return {k: _bucket_stats(v) for k, v in sorted(buckets.items())}


def build_summary(results: list[CaseResult]) -> dict:
    return {
        "overall": _bucket_stats(results),
        "by_workflow": _group(results, lambda r: r.workflow),
        "by_complexity": _group(results, lambda r: r.complexity),
        "by_suite": _group(results, lambda r: r.suite),
        "by_data_split": _group(results, lambda r: r.data_split),
        "by_safety_classification": _group(results, lambda r: r.safety_classification),
    }


def build_report(results: list[CaseResult], meta: dict) -> dict:
    return {
        "meta": meta,
        "summary": build_summary(results),
        # Every failing/no_signal case listed explicitly -- a baseline report
        # must never hide a failure inside an aggregate percentage.
        "failures": [asdict(r) for r in results if r.status != PASS],
        "cases": [asdict(r) for r in results],
    }


def write_json(report: dict, out_path: Path) -> None:
    out_path.write_text(json.dumps(report, indent=2, default=str))


def _fmt_pct(v) -> str:
    return f"{v}%" if v is not None else "n/a"


def render_markdown(report: dict) -> str:
    meta = report["meta"]
    summary = report["summary"]
    lines = [
        "# LunaiCAD evaluation baseline",
        "",
        f"- Generated: {meta['timestamp']}",
        f"- Mode: {'LIVE (OpenAI)' if meta['live'] else 'offline (mock provider)'}",
        f"- Provider: {meta['provider']} · Model: {meta.get('model') or 'n/a'}",
        f"- Prompt version: `{meta['prompt_version']}` · Commit: `{meta['git_commit']}`",
        f"- Repeats per nondeterministic case: {meta['repeats']}",
        f"- Total cases executed: {summary['overall']['total']} "
        f"(of {meta['total_cases_in_fixtures']} defined in fixtures)",
        f"- Skipped (require --live/OpenAI, not run offline): "
        f"{len(meta['skipped_requires_live'])}",
        "",
        "## Overall (do not read this row alone -- see breakdowns below)",
        "",
        f"Pass rate: **{_fmt_pct(summary['overall']['pass_rate_pct'])}** "
        f"({summary['overall']['passed']}/{summary['overall']['total']} passed, "
        f"{summary['overall']['failed']} failed, "
        f"{summary['overall']['no_signal']} no-signal)",
        "",
        "## Pass rate by workflow",
        "",
        "| Workflow | Total | Passed | Failed | No-signal | Pass rate | Avg latency (ms) |",
        "|---|---|---|---|---|---|---|",
    ]
    for k, s in summary["by_workflow"].items():
        lines.append(f"| {k} | {s['total']} | {s['passed']} | {s['failed']} | "
                     f"{s['no_signal']} | {_fmt_pct(s['pass_rate_pct'])} | {s['avg_latency_ms']} |")

    lines += ["", "## Pass rate by complexity", "",
             "| Complexity | Total | Passed | Failed | No-signal | Pass rate |",
             "|---|---|---|---|---|---|"]
    for k, s in summary["by_complexity"].items():
        lines.append(f"| {k} | {s['total']} | {s['passed']} | {s['failed']} | "
                     f"{s['no_signal']} | {_fmt_pct(s['pass_rate_pct'])} |")

    lines += ["", "## Pass rate by suite", "",
             "| Suite | Total | Passed | Failed | No-signal | Pass rate |",
             "|---|---|---|---|---|---|"]
    for k, s in summary["by_suite"].items():
        lines.append(f"| {k} | {s['total']} | {s['passed']} | {s['failed']} | "
                     f"{s['no_signal']} | {_fmt_pct(s['pass_rate_pct'])} |")

    lines += ["", "## Pass rate by data split", "",
             "| Split | Total | Passed | Failed | No-signal | Pass rate |",
             "|---|---|---|---|---|---|"]
    for k, s in summary["by_data_split"].items():
        lines.append(f"| {k} | {s['total']} | {s['passed']} | {s['failed']} | "
                     f"{s['no_signal']} | {_fmt_pct(s['pass_rate_pct'])} |")

    failures = report["failures"]
    lines += ["", f"## Failures and no-signal cases ({len(failures)}) -- never hidden", ""]
    if not failures:
        lines.append("None.")
    else:
        for f in failures:
            lines.append(f"### `{f['case_id']}` — {f['suite']} / {f['workflow']} / "
                         f"{f['complexity']} — **{f['status'].upper()}**")
            if f.get("error"):
                lines.append(f"- error: `{f['error'][:300]}`")
            for a in f["assertions"]:
                if a["status"] == "fail":
                    lines.append(f"- FAIL `{a['name']}`: {a['detail']}")
            lines.append("")

    return "\n".join(lines)


def write_markdown(report: dict, out_path: Path) -> None:
    out_path.write_text(render_markdown(report))
