"""Reproducible version fingerprints for the baseline report.

Nothing in the codebase versions the LLM system prompts explicitly, so
"prompt version" here is a content hash of the actual prompt constants the
running process will use — it changes exactly when a prompt changes, which is
the property a baseline comparison needs, without requiring anyone to
remember to bump a number.
"""
from __future__ import annotations

import hashlib
import subprocess


def prompt_version() -> str:
    from app.llm import base as llm_base

    constants = [
        getattr(llm_base, name, "")
        for name in (
            "SYSTEM_PROMPT", "MODIFICATION_SYSTEM_PROMPT", "CAD_PLAN_SYSTEM_PROMPT",
            "FEATURE_GRAPH_SYSTEM_PROMPT", "GENERAL_CAD_PLAN_SYSTEM_PROMPT",
            "DRAWING_SYSTEM_PROMPT",
        )
    ]
    blob = "\x00".join(constants).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:12]


def git_commit() -> str:
    """Best-effort short commit sha. This environment has a known iCloud/
    fileproviderd fault that can hang `git`, so this is time-boxed and
    NEVER allowed to block or fail the eval run."""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=3,
        )
        if proc.returncode == 0:
            return proc.stdout.strip()
    except Exception:  # noqa: BLE001 - version metadata is best-effort only
        pass
    return "unknown"
