"""Phase 1F: this phase's work must not reopen a previously-closed security path.

Cheap, fast guards that would fail loudly if the removed code-execution surface,
provider hook, or ownership checks regressed while adding the drill-jig route,
the flange fix, or the upload guard.
"""
from __future__ import annotations

import importlib

import pytest

from tests.conftest import TINY_PNG


# --- the F-1 execution surface stays gone ---------------------------------
@pytest.mark.parametrize("module", [
    "app.generation.code_sandbox",
    "app.generation.cad_programs",
    "app.generation.compiler",
    "app.generation.scad_runner",
])
def test_code_execution_modules_still_absent(module):
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module)


def test_no_provider_has_cad_program():
    from app.llm.base import LLMProvider
    from app.llm.mock_provider import MockLLMProvider
    from app.llm.openai_provider import OpenAIProvider

    for cls in (LLMProvider, MockLLMProvider, OpenAIProvider):
        assert not hasattr(cls, "cad_program"), cls.__name__


def test_anthropic_still_removed():
    with pytest.raises(ModuleNotFoundError):
        import app.llm.anthropic_provider  # noqa: F401


# --- ownership / isolation still enforced ---------------------------------
def test_cross_user_design_access_returns_404(client, auth, auth2):
    d = client.post("/api/designs/create",
                    json={"prompt": "a drill jig 120x80x6mm with 6mm holes at 25mm"},
                    headers=auth["headers"]).json()
    r = client.get(f"/api/designs/{d['id']}", headers=auth2["headers"])
    assert r.status_code == 404


def test_cross_user_drawing_job_polling_returns_404(client, auth2):
    r = client.get("/api/drawings/jobs/not-your-job", headers=auth2["headers"])
    assert r.status_code == 404


# --- invalid uploads never reach the model / parsers ----------------------
def test_malicious_upload_is_rejected_before_processing(client, auth):
    """A script-bearing SVG is rejected at the gate, not parsed or interpreted."""
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>x</script><rect width="5" height="5"/></svg>'
    r = client.post("/api/drawings/to-cad",
                    files={"file": ("x.svg", svg, "image/svg+xml")},
                    data={"sync": "true"}, headers=auth["headers"])
    assert r.status_code in (413, 415, 422)


def test_upload_errors_do_not_leak_paths_or_tracebacks(client, auth):
    r = client.post("/api/drawings/interpret",
                    files={"file": ("x.png", b"NOTIMAGE" * 40, "image/png")},
                    headers=auth["headers"])
    assert r.status_code in (413, 415, 422)
    body = r.text.lower()
    for leak in ("traceback", "/users/", "/private/", "site-packages", 'file "'):
        assert leak not in body, f"response leaked {leak!r}"


# --- critical validation still gates export -------------------------------
def test_valid_drawing_still_processes(client, auth):
    """A real (blank) image with a hint still flows through the pipeline — the
    guard must not block legitimate uploads."""
    r = client.post("/api/drawings/interpret",
                    files={"file": ("x.png", TINY_PNG, "image/png")},
                    data={"hint": "rectangular bracket 80mm wide 40mm deep 5mm thick"},
                    headers=auth["headers"])
    assert r.status_code == 200, r.text
