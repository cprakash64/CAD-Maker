#!/usr/bin/env python3
"""Grep backend + frontend source for banned-claim phrasing (app.safety.
language.BANNED_CLAIMS) so overstated language ("production-ready",
"guaranteed fit", "manufacturing certified", ...) can't silently creep back
into user-facing strings.

Report-only by default (prints findings, exits 0) -- pass --strict to exit 1
on any finding, for a future CI gate once the codebase is clean. Scoped to
source files most likely to contain user-facing copy; skips tests, deps,
generated/build output, and this script's own module (which legitimately
CONTAINS the banned phrases as data).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.safety.language import BANNED_CLAIMS  # noqa: E402

_SEARCH_DIRS = [
    ROOT / "backend" / "app",
    ROOT / "frontend" / "src",
]
_SEARCH_EXTS = {".py", ".ts", ".tsx", ".js", ".jsx"}
_SKIP_PARTS = {"node_modules", "__pycache__", ".next", "dist", "build", "tests",
               "test", ".venv"}
_SKIP_FILES = {"language.py", "audit_banned_language.py"}


def _iter_source_files():
    for base in _SEARCH_DIRS:
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix not in _SEARCH_EXTS:
                continue
            if path.name in _SKIP_FILES:
                continue
            if _SKIP_PARTS & set(path.parts):
                continue
            yield path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strict", action="store_true",
                         help="exit 1 on any finding (default: report only)")
    args = parser.parse_args()

    import re

    compiled = [(re.compile(p, re.IGNORECASE), why) for p, why in BANNED_CLAIMS]
    # A phrase preceded by a negation ("not structurally certified", "non-
    # production-ready") is the CORRECT disclaimer, not a violation -- skip it.
    negation = re.compile(r"\b(?:not|non|never|isn.t|aren.t|no)[-\s]*$", re.IGNORECASE)
    findings: list[tuple[Path, int, str, str]] = []
    for path in _iter_source_files():
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            for regex, why in compiled:
                m = regex.search(line)
                if m and not negation.search(line[:m.start()]):
                    findings.append((path, lineno, m.group(0), why))

    if not findings:
        print("No banned-claim language found.")
        return 0

    print(f"Found {len(findings)} banned-claim phrase(s):\n")
    for path, lineno, phrase, why in findings:
        rel = path.relative_to(ROOT)
        print(f"  {rel}:{lineno}: {phrase!r} -- {why}")

    return 1 if args.strict else 0


if __name__ == "__main__":
    raise SystemExit(main())
