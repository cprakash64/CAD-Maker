#!/usr/bin/env python3
"""Deployment smoke test (docs/ops/deployment-runbook.md).

Exercises a deployed LunaiCAD instance end to end against its real HTTP API:
signup -> login -> create a design -> poll to completion -> download STL ->
check /ready and /metrics. Exits non-zero with a clear message on the first
failing step.

Usage:
    python deploy/smoke_test.py --base-url https://your-domain.example
    python deploy/smoke_test.py --base-url http://127.0.0.1:8000 --ops-token $OPS_API_TOKEN

Uses only the standard library (urllib) so it never needs a virtualenv set
up on whatever machine you're running the check from.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
import uuid


class SmokeTestFailure(Exception):
    pass


def _request(method: str, url: str, *, headers: dict | None = None,
            body: dict | None = None, timeout: float = 30.0) -> tuple[int, dict | bytes]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            status = resp.status
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        status = exc.code
    try:
        return status, json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return status, raw


def _step(name: str):
    print(f"-> {name} ...", end=" ", flush=True)
    return time.perf_counter()


def _ok(start: float):
    print(f"ok ({(time.perf_counter() - start) * 1000:.0f}ms)")


def run(base_url: str, ops_token: str | None, poll_timeout: float) -> None:
    base_url = base_url.rstrip("/")
    email = f"smoketest-{uuid.uuid4().hex[:12]}@example.com"
    password = "smoke-test-password-123"

    t = _step("GET /health (liveness)")
    status, body = _request("GET", f"{base_url}/health")
    if status != 200:
        raise SmokeTestFailure(f"/health returned {status}: {body}")
    _ok(t)

    t = _step("GET /ready (readiness)")
    status, body = _request("GET", f"{base_url}/ready")
    if status != 200:
        raise SmokeTestFailure(f"/ready returned {status}: {body}")
    if not body.get("ready"):
        raise SmokeTestFailure(f"/ready reports not-ready: {body}")
    _ok(t)

    t = _step("POST /api/auth/signup")
    status, body = _request("POST", f"{base_url}/api/auth/signup",
                            body={"email": email, "password": password})
    if status != 201:
        raise SmokeTestFailure(f"signup returned {status}: {body}")
    token = body["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    _ok(t)

    t = _step("POST /api/auth/login")
    status, body = _request("POST", f"{base_url}/api/auth/login",
                            body={"email": email, "password": password})
    if status != 200:
        raise SmokeTestFailure(f"login returned {status}: {body}")
    _ok(t)

    t = _step("POST /api/designs/create")
    status, body = _request(
        "POST", f"{base_url}/api/designs/create",
        headers=headers,
        body={"prompt": "a rectangular mounting plate 80x40x6mm with two M6 holes"},
        timeout=poll_timeout,
    )
    if status not in (200, 202):
        raise SmokeTestFailure(f"design create returned {status}: {body}")
    _ok(t)

    if status == 202:
        job_id = body["job_id"]
        t = _step(f"poll /api/jobs/{job_id} to completion")
        deadline = time.perf_counter() + poll_timeout
        design = None
        while time.perf_counter() < deadline:
            jstatus, jbody = _request("GET", f"{base_url}/api/jobs/{job_id}", headers=headers)
            if jstatus != 200:
                raise SmokeTestFailure(f"job poll returned {jstatus}: {jbody}")
            if jbody["status"] == "succeeded":
                design = jbody["result"]["design"]
                break
            if jbody["status"] in ("failed", "timed_out", "cancelled"):
                raise SmokeTestFailure(f"job ended in {jbody['status']}: {jbody.get('error')}")
            time.sleep(1.0)
        else:
            raise SmokeTestFailure(f"job did not finish within {poll_timeout}s")
        _ok(t)
    else:
        design = body

    design_id = design["id"]
    if not design.get("exports"):
        raise SmokeTestFailure(f"design {design_id} has no exports")

    t = _step("GET design STL export")
    status, content = _request("GET", f"{base_url}/api/designs/{design_id}/files/stl",
                               headers=headers, timeout=30.0)
    if status != 200:
        raise SmokeTestFailure(f"STL download returned {status}")
    if not isinstance(content, bytes) or len(content) < 100:
        raise SmokeTestFailure("STL download returned suspiciously little data")
    _ok(t)

    if ops_token:
        t = _step("GET /metrics (authenticated)")
        status, content = _request(
            "GET", f"{base_url}/metrics",
            headers={"Authorization": f"Bearer {ops_token}"})
        if status != 200:
            raise SmokeTestFailure(f"/metrics returned {status}")
        _ok(t)

    print(f"\nAll smoke tests passed against {base_url}.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, help="e.g. https://your-domain.example")
    parser.add_argument("--ops-token", default=None,
                        help="OPS_API_TOKEN, to also check /metrics. Optional.")
    parser.add_argument("--poll-timeout", type=float, default=120.0,
                        help="Seconds to wait for design generation to finish.")
    args = parser.parse_args()

    try:
        run(args.base_url, args.ops_token, args.poll_timeout)
    except SmokeTestFailure as exc:
        print(f"\nSMOKE TEST FAILED: {exc}")
        sys.exit(1)
    except Exception as exc:  # noqa: BLE001 - report, don't traceback-dump
        print(f"\nSMOKE TEST ERRORED: {type(exc).__name__}: {exc}")
        sys.exit(2)


if __name__ == "__main__":
    main()
