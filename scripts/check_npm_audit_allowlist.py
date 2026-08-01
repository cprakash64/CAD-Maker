#!/usr/bin/env python3
"""CI gate for npm audit (docs/security/dependency-risk-assessment.md).

Runs npm audit in frontend/ and fails on any moderate+ finding for a package
not in frontend/.npm-audit-allowlist.json. Every allowlist entry carries a
written reachability justification, an owner, and a review-by date -- this
is a hard gate (no `|| true`), not a silenced scan.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
ALLOWLIST = FRONTEND / ".npm-audit-allowlist.json"
GATED_SEVERITIES = {"moderate", "high", "critical"}


def main() -> None:
    if not ALLOWLIST.exists():
        print(f"No allowlist at {ALLOWLIST}.")
        sys.exit(1)
    allowed = {e["package"] for e in json.loads(ALLOWLIST.read_text())["entries"]}

    proc = subprocess.run(
        ["npm", "audit", "--json"], capture_output=True, text=True, cwd=FRONTEND,
    )
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        print("npm audit did not produce parseable JSON:")
        print(proc.stdout)
        print(proc.stderr)
        sys.exit(1)

    unallowed = [
        (name, v.get("severity"))
        for name, v in data.get("vulnerabilities", {}).items()
        if v.get("severity") in GATED_SEVERITIES and name not in allowed
    ]

    if unallowed:
        print("New npm audit findings not in frontend/.npm-audit-allowlist.json:")
        for name, severity in unallowed:
            print(f"  {name}: {severity}")
        print(
            "\nEither fix (upgrade/remove the package) or add a reviewed "
            "entry to frontend/.npm-audit-allowlist.json with a written "
            "reachability justification -- see docs/security/"
            "dependency-risk-assessment.md."
        )
        sys.exit(1)

    print(f"npm audit: no moderate+ findings outside the {len(allowed)}-entry reviewed allowlist.")


if __name__ == "__main__":
    main()
