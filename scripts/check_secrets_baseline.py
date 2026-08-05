#!/usr/bin/env python3
"""CI gate for detect-secrets (docs/ops/security-scanning.md).

Re-scans the tracked source paths and fails if it finds a potential secret
NOT already present (and reviewed) in the committed `.secrets.baseline`.
Existing baseline entries (including ones already marked `is_secret: false`
during audit) never fail the build — only genuinely NEW findings do, which is
what should prompt a human to run detect-secrets, review the new finding, and
either fix a real leak or re-commit an updated, audited baseline.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / ".secrets.baseline"
SCAN_PATHS = [
    "backend/app", "backend/tests", "backend/alembic", "backend/deploy",
    "backend/requirements.txt", "backend/alembic.ini", "backend/pytest.ini",
    "frontend/src", "docs", "deploy", ".github",
]


def _keys(results: dict) -> set[tuple]:
    return {
        (file, finding["hashed_secret"], finding["line_number"])
        for file, findings in results.items()
        for finding in findings
    }


def main() -> None:
    if not BASELINE.exists():
        print(f"No baseline at {BASELINE} -- run detect-secrets and commit one first.")
        sys.exit(1)

    old = json.loads(BASELINE.read_text())

    # `detect-secrets scan --baseline FILE` rewrites FILE in place (no stdout
    # output) -- rescan into a throwaway copy so this check never mutates the
    # committed baseline itself.
    with tempfile.TemporaryDirectory() as tmp:
        scratch = Path(tmp) / "baseline.json"
        shutil.copy(BASELINE, scratch)
        proc = subprocess.run(
            ["detect-secrets", "scan", *SCAN_PATHS, "--baseline", str(scratch)],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        if proc.returncode != 0:
            print("detect-secrets scan failed to run:")
            print(proc.stderr)
            sys.exit(2)
        new = json.loads(scratch.read_text())

    added = _keys(new["results"]) - _keys(old["results"])
    if added:
        print("New potential secrets found that are NOT in the audited baseline:")
        for file, hashed, line in sorted(added):
            print(f"  {file}:{line} ({hashed})")
        print(
            "\nReview each: if it's a real secret, remove it from the code/history "
            "and rotate it. If it's a false positive, run:\n"
            "  detect-secrets scan <paths> --baseline .secrets.baseline > .secrets.baseline\n"
            "  detect-secrets audit .secrets.baseline\n"
            "and commit the updated baseline."
        )
        sys.exit(1)

    print("No new potential secrets outside the audited baseline.")


if __name__ == "__main__":
    main()
