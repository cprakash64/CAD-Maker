"""The one documented command for the evaluation harness.

    python -m eval.cli run                              # offline, deterministic (default, CI-safe)
    python -m eval.cli run --suite known_families        # one suite only
    python -m eval.cli run --live --repeats 3            # OpenAI-backed, 3x per case
    python -m eval.cli run --split development           # skip held-out cases
    python -m eval.cli validate                          # just validate fixtures, run nothing

`run` always writes both a JSON and a Markdown report under
`--out` (default: `settings.eval_report_dir`), and prints the Markdown
summary to stdout. Exit code is non-zero if any case FAILED (not merely
no_signal), so this doubles as a CI gate.

Offline (no `--live`) uses the deterministic mock LLM provider and never
makes a network call -- this is the subset CI runs on every PR. `--live`
requires OPENAI_API_KEY and is never run automatically.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _cmd_validate(args: argparse.Namespace) -> int:
    from eval.schema import FIXTURES_DIR, load_all_cases

    try:
        cases = load_all_cases(Path(args.fixtures) if args.fixtures else FIXTURES_DIR)
    except ValueError as exc:
        print(f"FIXTURE VALIDATION FAILED: {exc}", file=sys.stderr)
        return 1
    by_suite: dict[str, int] = {}
    for c in cases:
        by_suite[c.suite] = by_suite.get(c.suite, 0) + 1
    print(f"OK: {len(cases)} cases validated across {len(by_suite)} suites")
    for suite, n in sorted(by_suite.items()):
        print(f"  {suite:24s} {n}")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    # eval.bootstrap imports NOTHING from app.* or the rest of eval.* (see its
    # docstring) -- it is the only thing allowed to be imported before it
    # runs. Importing eval.runner/eval.report before this line would
    # transitively import app.config too early and let a `.env` file with a
    # live OpenAI key silently win over the offline default.
    from eval.bootstrap import bootstrap_environment

    bootstrap_environment(live=args.live)
    from eval.report import render_markdown
    from eval.runner import run

    report = run(
        suites=args.suite or None,
        live=args.live,
        repeats=args.repeats,
        out_dir=Path(args.out) if args.out else None,
        data_splits=args.split or None,
    )
    print(render_markdown(report))
    print(f"\nWrote {report['_paths']['json']}\nWrote {report['_paths']['markdown']}")
    any_fail = any(c["status"] == "fail" for c in report["cases"])
    return 1 if any_fail else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m eval.cli", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="execute the evaluation harness")
    run_p.add_argument("--suite", action="append",
                       help="limit to one suite (repeatable). Default: all suites.")
    run_p.add_argument("--split", action="append",
                       choices=["development", "regression", "held_out"],
                       help="limit to one data split (repeatable). Default: all splits.")
    run_p.add_argument("--live", action="store_true",
                       help="use the real OpenAI provider instead of the deterministic "
                            "mock (requires OPENAI_API_KEY). Never enabled by default.")
    run_p.add_argument("--repeats", type=int, default=1,
                       help="repeat each case this many times (only meaningful with "
                            "--live, since the mock provider is deterministic)")
    run_p.add_argument("--out", default=None, help="report output directory")
    run_p.set_defaults(func=_cmd_run)

    val_p = sub.add_parser("validate", help="validate fixtures against the schema, run nothing")
    val_p.add_argument("--fixtures", default=None)
    val_p.set_defaults(func=_cmd_validate)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
