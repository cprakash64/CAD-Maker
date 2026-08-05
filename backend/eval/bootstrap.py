"""Environment bootstrap -- MUST be imported and called before ANY other
`eval.*` or `app.*` module is imported.

This module deliberately imports nothing from `app.*` or the rest of `eval.*`
(stdlib only), because `app.config.Settings.load()` reads `os.environ` (and a
`.env` file, via `os.environ.setdefault`) exactly once at import time. If any
other module transitively imports `app.config` before this function has set
LLM_PROVIDER, `.env`'s value (which may be `openai` with a real API key)
silently wins -- turning a default "offline" run into an unintended, costly
live OpenAI call. `eval/cli.py` imports ONLY this module before calling it;
every other `eval.*` import happens strictly after.
"""
from __future__ import annotations

import os
import tempfile


def bootstrap_environment(live: bool, isolated_data_dir: str | None = None) -> None:
    tmp = isolated_data_dir or tempfile.mkdtemp(prefix="lunaicad-eval-")
    os.environ.setdefault("DATABASE_URL", f"sqlite:///{tmp}/eval.db")
    os.environ.setdefault("STORAGE_DIR", f"{tmp}/storage")
    os.environ.setdefault("TESTING", "true")
    os.environ.setdefault("APP_ENV", "development")
    os.environ.setdefault("DEV_ALLOW_MOCK_DRAWING", "true")
    if live:
        os.environ["LLM_PROVIDER"] = "openai"
        if not os.environ.get("OPENAI_API_KEY"):
            raise RuntimeError(
                "--live requires OPENAI_API_KEY to be set in the environment.")
    else:
        # Force (not setdefault): a `.env` file with LLM_PROVIDER=openai must
        # NEVER win over the harness's offline default.
        os.environ["LLM_PROVIDER"] = "mock"
