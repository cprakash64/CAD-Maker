"""Per-workflow executors: drive the real product surface (the same FastAPI
routes the frontend calls) and return an EvalContext for the assertions
module to check. Nothing here re-implements product logic — every executor
is a thin, faithful client of the HTTP API.
"""
from __future__ import annotations

import io
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from eval.context import EvalContext
from eval.schema import EvalCase

DATA_DIR = Path(__file__).resolve().parent.parent / "tests" / "data"


def _download_exports(client, headers, design_id: str, response: dict) -> tuple[
        Optional[bytes], Optional[bytes]]:
    stl_bytes = step_bytes = None
    fmts = {e["fmt"] for e in (response.get("exports") or [])}
    if "stl" in fmts:
        r = client.get(f"/api/designs/{design_id}/files/stl", headers=headers)
        if r.status_code == 200:
            stl_bytes = r.content
    if "step" in fmts:
        r = client.get(f"/api/designs/{design_id}/files/step", headers=headers)
        if r.status_code == 200:
            step_bytes = r.content
    return stl_bytes, step_bytes


def run_text_to_cad(client, headers, case: EvalCase, provider_name: str) -> EvalContext:
    start = time.perf_counter()
    try:
        r = client.post("/api/designs/create", json={"prompt": case.prompt}, headers=headers)
    except Exception as exc:  # noqa: BLE001 - a raised exception IS a crash finding
        return EvalContext(case=case, ok=False, error=f"{type(exc).__name__}: {exc}",
                            latency_ms=_ms(start), provider=provider_name)
    latency = _ms(start)
    if r.status_code >= 500:
        return EvalContext(case=case, ok=False, http_status=r.status_code,
                            error=r.text[:500], latency_ms=latency, provider=provider_name)
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    ctx = EvalContext(case=case, ok=True, http_status=r.status_code, response=body,
                       latency_ms=latency, provider=provider_name)
    if r.status_code < 300 and body.get("id"):
        ctx.stl_bytes, ctx.step_bytes = _download_exports(client, headers, body["id"], body)
    return ctx


def run_drawing_to_cad(client, headers, case: EvalCase, provider_name: str) -> EvalContext:
    fixture_path = DATA_DIR / case.fixture
    if not fixture_path.exists():
        return EvalContext(case=case, ok=False,
                            error=f"fixture not found: {fixture_path}", provider=provider_name)
    data = fixture_path.read_bytes()
    media_type = case.fixture_media_type or "application/octet-stream"
    start = time.perf_counter()
    try:
        r = client.post(
            "/api/drawings/to-cad",
            files={"file": (fixture_path.name, io.BytesIO(data), media_type)},
            data={"sync": "true",
                 **{k: str(v) for k, v in case.fixture_form.items()}},
            headers=headers,
        )
    except Exception as exc:  # noqa: BLE001
        return EvalContext(case=case, ok=False, error=f"{type(exc).__name__}: {exc}",
                            latency_ms=_ms(start), provider=provider_name)
    latency = _ms(start)
    if r.status_code >= 500:
        return EvalContext(case=case, ok=False, http_status=r.status_code,
                            error=r.text[:500], latency_ms=latency, provider=provider_name)
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    design = body.get("design") or {}
    ctx = EvalContext(case=case, ok=True, http_status=r.status_code, response=design,
                       latency_ms=latency, provider=provider_name,
                       extra={"analysis": body.get("analysis")})
    if design.get("id"):
        ctx.stl_bytes, ctx.step_bytes = _download_exports(client, headers, design["id"], design)
    return ctx


_EDIT_ROUTES = {
    "modify": "/api/designs/{id}/modify",
    "circle_edit": "/api/designs/{id}/circle-edit",
    "localized_edit": "/api/designs/{id}/localized-edit",
}


def run_modification(client, headers, case: EvalCase, provider_name: str) -> EvalContext:
    start = time.perf_counter()
    setup = client.post("/api/designs/create", json={"prompt": case.setup_prompt},
                        headers=headers)
    if setup.status_code >= 400:
        return EvalContext(case=case, ok=False,
                            error=f"setup failed: {setup.status_code} {setup.text[:300]}",
                            provider=provider_name)
    before = setup.json()
    design_id = before["id"]
    edit_kind = case.edit_kind or "modify"
    route = _EDIT_ROUTES[edit_kind].format(id=design_id)

    if edit_kind == "modify":
        payload = {"prompt": case.edit_instruction}
    else:
        sel = case.edit_selection or {}
        payload = {}
        if edit_kind == "circle_edit":
            payload = {
                "selected": {"entity_type": sel["entity_type"], "entity_id": sel["entity_id"]},
                "instruction": case.edit_instruction,
                "validated_parameters": sel.get("validated_parameters", {}),
            }
        elif edit_kind == "localized_edit":
            payload = {
                "selected_entity_type": sel["entity_type"],
                "selected_entity_id": sel["entity_id"],
                "allowed_operation": sel["allowed_operation"],
                "natural_language_instruction": case.edit_instruction,
                "validated_parameters": sel.get("validated_parameters", {}),
            }

    try:
        r = client.post(route, json=payload, headers=headers)
    except Exception as exc:  # noqa: BLE001
        return EvalContext(case=case, ok=False, error=f"{type(exc).__name__}: {exc}",
                            before_response=before, latency_ms=_ms(start),
                            provider=provider_name)
    latency = _ms(start)
    if r.status_code >= 500:
        return EvalContext(case=case, ok=False, http_status=r.status_code,
                            error=r.text[:500], before_response=before,
                            latency_ms=latency, provider=provider_name)
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    # /modify, /circle-edit, and /localized-edit all return the updated
    # DesignDTO directly (confirmed by reading their router handlers).
    ctx = EvalContext(case=case, ok=True, http_status=r.status_code, response=body,
                       before_response=before, latency_ms=latency, provider=provider_name)
    return ctx


def run_export_integrity(client, headers, case: EvalCase, provider_name: str) -> EvalContext:
    return run_text_to_cad(client, headers, case, provider_name)


def run_security(case: EvalCase) -> EvalContext:
    """Run one authoritative pytest node and record pass/fail. This suite
    deliberately does NOT re-implement security assertions -- the hardened
    pytest suites (phases 1-3 of this project) are the ground truth; the eval
    harness's job is to make their pass/fail visible in the same baseline
    report as everything else, not to duplicate them."""
    start = time.perf_counter()
    backend_root = Path(__file__).resolve().parent.parent
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", case.pytest_node_id],
        cwd=str(backend_root), capture_output=True, text=True, timeout=120,
    )
    latency = _ms(start)
    ok = proc.returncode == 0
    return EvalContext(
        case=case, ok=ok, http_status=0 if ok else 1,
        response={"exports": []} if ok else None,
        error=None if ok else (proc.stdout[-2000:] + proc.stderr[-1000:]),
        latency_ms=latency, provider="pytest",
        extra={"returncode": proc.returncode},
    )


def _ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 2)


EXECUTORS = {
    "text_to_cad": run_text_to_cad,
    "drawing_to_cad": run_drawing_to_cad,
    "modification": run_modification,
    "export_integrity": run_export_integrity,
    # "security" is dispatched specially by the runner (no client/headers).
}
