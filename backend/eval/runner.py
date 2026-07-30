"""Orchestrates one evaluation run: bootstrap -> load cases -> execute ->
score -> report.

CRITICAL ordering constraint: `eval.bootstrap.bootstrap_environment()` MUST
run before this module (or anything it imports, including `eval.assertions`
-> `app.cad.tolerance` -> `app.config`) is imported. `eval/cli.py` is the only
sanctioned entry point and enforces this; do not import `eval.runner` at
module level from anywhere else. See `eval/bootstrap.py` for why this matters
(a `.env` with a live OpenAI key can otherwise silently win over the
harness's offline default)."""
from __future__ import annotations

import itertools
import os
from pathlib import Path

from eval import versioning
from eval.assertions import FAIL, PASS, AssertionResult, run_all
from eval.report import CaseResult, build_report, score_case, write_json, write_markdown
from eval.schema import EvalCase, load_all_cases

_email_counter = itertools.count()


def _signup(client) -> dict:
    email = f"eval_{next(_email_counter)}_{os.getpid()}@example.com"
    r = client.post("/api/auth/signup", json={"email": email, "password": "password123456"})
    if r.status_code != 201:
        raise RuntimeError(f"eval harness could not sign up a user: {r.status_code} {r.text}")
    body = r.json()
    return {"Authorization": f"Bearer {body['access_token']}"}


def run(
    suites: list[str] | None = None,
    live: bool = False,
    repeats: int = 1,
    out_dir: Path | None = None,
    data_splits: list[str] | None = None,
    fixtures_dir: Path | None = None,
) -> dict:
    from app.database import init_db
    from app.main import app
    from fastapi.testclient import TestClient

    init_db()
    client = TestClient(app)
    headers = _signup(client)

    from eval.schema import FIXTURES_DIR

    all_cases = load_all_cases(fixtures_dir or FIXTURES_DIR)
    cases = all_cases
    if suites:
        cases = [c for c in cases if c.suite in suites]
    if data_splits:
        cases = [c for c in cases if c.data_split in data_splits]
    skipped_live_only = [c.case_id for c in cases if c.requires_live and not live]
    if not live:
        cases = [c for c in cases if not c.requires_live]
    if not cases:
        raise RuntimeError("no cases matched the requested filters")

    effective_repeats = repeats if live else 1
    if repeats > 1 and not live:
        print("NOTE: --repeats > 1 has no effect offline (the mock provider is "
              "deterministic); running each case once.")

    provider_name = "openai" if live else "mock"
    model = None
    if live:
        from app.config import settings
        model = settings.cad_llm_model or settings.openai_model

    results: list[CaseResult] = []
    for case in cases:
        for rep in range(effective_repeats):
            ctx = _execute_one(client, headers, case, provider_name, live)
            if case.workflow == "security":
                assertion_results = [
                    AssertionResult("pytest_security_check", PASS if ctx.ok else FAIL,
                                    "" if ctx.ok else (ctx.error or "")[:500])
                ]
            else:
                assertion_results = run_all(ctx)
            results.append(score_case(case, ctx, assertion_results, repeat_index=rep))

    meta = {
        "timestamp": _now_iso(),
        "live": live,
        "provider": provider_name,
        "model": model,
        "prompt_version": versioning.prompt_version(),
        "git_commit": versioning.git_commit(),
        "repeats": effective_repeats,
        "total_cases_loaded": len(cases),
        "total_cases_in_fixtures": len(all_cases),
        "suites_run": sorted({c.suite for c in cases}),
        "skipped_requires_live": skipped_live_only,
    }
    report = build_report(results, meta)

    out_dir = out_dir or _default_out_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = _stamp()
    json_path = out_dir / f"eval_{provider_name}_{stamp}.json"
    md_path = out_dir / f"eval_{provider_name}_{stamp}.md"
    write_json(report, json_path)
    write_markdown(report, md_path)
    report["_paths"] = {"json": str(json_path), "markdown": str(md_path)}
    return report


def _execute_one(client, headers, case: EvalCase, provider_name: str, live: bool):
    from eval import live_instrumentation
    from eval.executors import EXECUTORS, run_security

    if case.workflow == "security":
        return run_security(case)

    usage = None
    if live:
        from app.llm.factory import get_cad_provider, get_provider

        for getter in (get_provider, get_cad_provider):
            try:
                live_instrumentation.install(getter())
            except Exception:  # noqa: BLE001 - instrumentation must never break a run
                pass
        from app.config import settings

        usage = live_instrumentation.begin(settings.cad_llm_model or settings.openai_model)

    ctx = EXECUTORS[case.workflow](client, headers, case, provider_name)
    if usage is not None:
        ctx.retries = usage.retries
        ctx.prompt_tokens = usage.prompt_tokens
        ctx.completion_tokens = usage.completion_tokens
        ctx.estimated_cost_usd = usage.estimated_cost_usd()
        ctx.model = usage.model
    return ctx


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _stamp() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")


def _default_out_dir() -> Path:
    from app.config import settings

    return Path(settings.eval_report_dir)
