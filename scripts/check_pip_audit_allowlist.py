#!/usr/bin/env python3
"""CI gate for pip-audit (docs/security/dependency-risk-assessment.md).

Runs pip-audit against backend/requirements.txt and fails on any finding
whose advisory id is not in backend/.pip-audit-allowlist.json. Every
allowlist entry carries a written reachability justification, an owner, and
a review-by date -- this is a hard gate (no `|| true`), not a silenced scan.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQUIREMENTS = ROOT / "backend" / "requirements.txt"
ALLOWLIST = ROOT / "backend" / ".pip-audit-allowlist.json"


def main() -> None:
    if not ALLOWLIST.exists():
        print(f"No allowlist at {ALLOWLIST}.")
        sys.exit(1)
    allowed = {e["id"] for e in json.loads(ALLOWLIST.read_text())["entries"]}

    proc = subprocess.run(
        ["pip-audit", "-r", str(REQUIREMENTS), "--format", "json"],
        capture_output=True, text=True,
    )
    # pip-audit exits 1 when it finds vulnerabilities -- that's expected;
    # only a missing/unparseable report is a hard error here.
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        print("pip-audit did not produce parseable JSON:")
        print(proc.stdout)
        print(proc.stderr)
        sys.exit(1)

    deps = data["dependencies"] if isinstance(data, dict) else data
    unallowed = []
    for dep in deps:
        for vuln in dep.get("vulns", []):
            if vuln["id"] not in allowed:
                unallowed.append((dep["name"], dep["version"], vuln["id"]))

    if unallowed:
        print("New pip-audit findings not in backend/.pip-audit-allowlist.json:")
        for name, version, vid in unallowed:
            print(f"  {name} {version}: {vid}")
        print(
            "\nEither fix (upgrade/remove the package) or add a reviewed "
            "entry to backend/.pip-audit-allowlist.json with a written "
            "reachability justification -- see docs/security/"
            "dependency-risk-assessment.md."
        )
        sys.exit(1)

    print(f"pip-audit: no findings outside the {len(allowed)}-entry reviewed allowlist.")


if __name__ == "__main__":
    main()
