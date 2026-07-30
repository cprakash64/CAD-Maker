"""Direct proof of Phase 7's Drawing -> CAD beta acceptance criteria
(docs/drawing-to-cad-beta.md), one test per criterion:

1. Important unresolved dimensions block automatic final generation.
2. Supported cases pass their declared benchmark (offline eval suite).
3. Unsupported drawings are rejected or downgraded honestly.
4. No silent geometry invention occurs for critical dimensions.

Other tests already exercise pieces of this behaviour incidentally
(tests/test_drawing_to_cad.py, tests/test_drawing_consistency.py); this file
is the single place that states each criterion explicitly and proves it.
"""
from __future__ import annotations

import io
from pathlib import Path

DATA = Path(__file__).parent / "data"


def _post(client, auth, filename: str, **form):
    data = (DATA / filename).read_bytes()
    return client.post(
        "/api/drawings/to-cad",
        files={"file": (filename, io.BytesIO(data), "image/svg+xml")},
        data={"sync": "true", **{k: str(v) for k, v in form.items() if v is not None}},
        headers=auth["headers"],
    )


# --- 1. Important unresolved dimensions block automatic final generation ---

def test_missing_depth_blocks_export_but_design_still_builds(client, auth):
    """simple_adapter_plate.svg has no depth/thickness annotation anywhere in
    its source. The design must still build (inspectable), but the
    manufacturable export must be blocked -- never a silent default."""
    r = _post(client, auth, "simple_adapter_plate.svg")
    assert r.status_code == 200, r.text
    d = r.json()["design"]
    assert d["id"]  # the design itself was built, not refused outright
    assert d["download_blocked_reason"] is not None
    assert d["drawing_fidelity"]["drawing_fidelity_status"] == "failed"
    assert "depth" in d["drawing_fidelity"]["critical_unresolved"]
    assert d["drawing_review_required"] is True

    # The raw download endpoint itself must also refuse, not just the flag.
    for fmt in ("stl", "step"):
        dl = client.get(f"/api/designs/{d['id']}/files/{fmt}", headers=auth["headers"])
        assert dl.status_code == 409


# --- 2. Supported cases pass their declared benchmark -----------------------

def test_offline_drawing_benchmark_passes(tmp_path):
    """The offline (mock-provider) drawing_to_cad eval suite -- the declared
    ground-truth benchmark for the supported envelope -- passes every
    non-requires_live case. A regression in extraction, the unresolved-
    dimension gate, or export integrity must fail this test, not just some
    downstream product surface.

    Runs as a real subprocess (not in-process): eval/runner.py documents a
    hard ordering constraint -- eval.bootstrap.bootstrap_environment() must
    run before ANY app.*/eval.* import, or a .env with a live OpenAI key can
    silently win over the offline default. This process already imported
    `app` for its own client/auth fixtures, so an in-process call would
    violate that invariant; a subprocess keeps the harness's own contract
    intact instead of relying on import order getting lucky."""
    import json
    import subprocess
    import sys

    backend_dir = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        [sys.executable, "-m", "eval.cli", "run", "--suite", "drawing_to_cad",
         "--out", str(tmp_path)],
        cwd=backend_dir, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    reports = sorted(tmp_path.glob("eval_mock_*.json"))
    assert reports, f"no eval report written; stdout:\n{result.stdout}"
    report = json.loads(reports[-1].read_text())
    overall = report["summary"]["overall"]
    assert overall["total"] > 0, "no offline drawing_to_cad cases were executed"
    assert overall["failed"] == 0, report.get("failures") or overall


# --- 3. Unsupported drawings are rejected or downgraded honestly ------------

def test_unreadable_input_is_rejected_not_guessed(client, auth):
    r = client.post(
        "/api/drawings/to-cad",
        files={"file": ("broken.svg", io.BytesIO(b"<svg>not a real drawing</svg>"),
                        "image/svg+xml")},
        data={"sync": "true"},
        headers=auth["headers"],
    )
    assert r.status_code in (400, 422), r.text


def test_forced_provider_failure_never_reports_clean_pass(client, auth, monkeypatch):
    """A drawing read under a forced vision-provider timeout must downgrade
    honestly (review/failed) -- never a silent 'ok' fidelity, regardless of
    which downstream family/route ends up handling it."""
    import app.drawing.interpret as interp_mod

    class _Timeout:
        name = "openai"

        def interpret_drawing(self, *a, **k):
            raise TimeoutError("APITimeoutError: request timed out")

    monkeypatch.setattr(interp_mod, "get_provider", lambda: _Timeout())
    data = (DATA / "flanged_pipe_branch_sheet.png").read_bytes()
    r = client.post(
        "/api/drawings/to-cad",
        files={"file": ("flanged_pipe_branch_sheet.png", io.BytesIO(data), "image/png")},
        data={"sync": "true"},
        headers=auth["headers"],
    )
    assert r.status_code == 200, r.text
    d = r.json()["design"]
    assert d["drawing_fidelity"]["drawing_fidelity_status"] in ("review", "failed")
    assert d["validation_status"] != "pass"
    assert d["drawing_beta"] is True


# --- 4. No silent geometry invention occurs for critical dimensions --------

def test_resolution_requires_explicit_override_not_a_default(client, auth):
    """The ONLY way to unblock a drawing with a genuinely missing critical
    dimension is an explicit, user-supplied value -- re-posting the exact
    same file with nothing added must stay blocked every time (no code path
    quietly resolves it after the fact)."""
    r1 = _post(client, auth, "simple_adapter_plate.svg")
    r2 = _post(client, auth, "simple_adapter_plate.svg")
    assert r1.json()["design"]["download_blocked_reason"] is not None
    assert r2.json()["design"]["download_blocked_reason"] is not None

    resolved = _post(client, auth, "simple_adapter_plate.svg", thickness_mm=6)
    d = resolved.json()["design"]
    assert d["download_blocked_reason"] is None
    assert d["drawing_fidelity"]["critical_unresolved"] == []
    # The resolved value actually came from the override, not a made-up one.
    assert d["bounding_box_mm"]["z"] == 6 or abs(d["bounding_box_mm"]["z"] - 6) < 0.5
