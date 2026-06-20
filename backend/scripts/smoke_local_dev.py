"""Local-dev smoke test: proves the browser path works end to end.

    backend/.venv/bin/python -m scripts.smoke_local_dev          # uses a running
                                                                 # server on :8000,
                                                                 # or boots one
    SOURCECAD_API=http://127.0.0.1:8123 ... -m scripts.smoke_local_dev

Checks exactly what the New Design page needs:
  1. GET  /health                -> 200
  2. GET  /api/auth/me           -> 401 unauthenticated (expected dev-auth
                                    response), then 200 with a fresh signup token
  3. POST /api/designs/create    -> a real design for the bearing-block prompt
                                    (no fatal clarification, feature-graph route)
  4. GET  /api/designs/{id}/files/{step,stl} -> 200, non-empty downloads

Exits non-zero on the first failure. Stdlib only (urllib), no extra deps.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

BASE = os.environ.get("SOURCECAD_API", "http://127.0.0.1:8000").rstrip("/")
PROMPT = ("Create a compact bearing block for a 20mm shaft with a "
          "90mm by 45mm by 12mm base.")


def _req(method: str, path: str, body: dict | None = None, token: str | None = None):
    """(status, parsed-or-raw-body). Network errors abort with a clear message."""
    req = urllib.request.Request(BASE + path, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(req, data=data, timeout=180) as resp:
            raw = resp.read()
            try:
                return resp.status, json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError):
                return resp.status, raw  # binary download (STL/STEP)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return e.code, raw
    except urllib.error.URLError as e:
        sys.exit(f"FAIL: cannot reach the backend at {BASE} ({method} {path}): {e.reason}\n"
                 f"      Start it with: bash scripts/dev.sh backend")


def _server_up() -> bool:
    try:
        with urllib.request.urlopen(BASE + "/health", timeout=2) as resp:
            return resp.status == 200
    except OSError:
        return False


def _ok(label: str, detail: str = "") -> None:
    print(f"   OK  {label}" + (f" — {detail}" if detail else ""))


def main() -> None:
    proc = None
    if not _server_up():
        print(f"==> No server at {BASE}; starting one (mock provider, temp DB)")
        env = dict(os.environ,
                   LLM_PROVIDER=os.environ.get("LLM_PROVIDER", "mock"),
                   APP_ENV="development", TESTING="true",
                   DATABASE_URL=f"sqlite:////tmp/smoke_local_{os.getpid()}.db",
                   STORAGE_DIR=f"/tmp/smoke_local_storage_{os.getpid()}")
        port = BASE.rsplit(":", 1)[-1]
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app",
             "--host", "127.0.0.1", "--port", port],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(60):
            if _server_up():
                break
            time.sleep(1)
        else:
            sys.exit("FAIL: backend did not come up in 60s")

    try:
        # 1) health
        status, body = _req("GET", "/health")
        assert status == 200 and body.get("status") == "ok", (status, body)
        _ok("GET /health -> 200", f"provider={body.get('llm_provider', '?')}")

        # 2) auth: unauthenticated /me must be a clean 401, then 200 with a token
        status, _ = _req("GET", "/api/auth/me")
        assert status == 401, f"unauthenticated /me should be 401, got {status}"
        _ok("GET /api/auth/me (no token) -> 401 (expected dev-auth response)")

        email = f"smoke-{uuid.uuid4().hex[:10]}@example.com"
        status, body = _req("POST", "/api/auth/signup",
                            {"email": email, "password": "password123"})
        assert status == 201 and body.get("access_token"), (status, body)
        token = body["access_token"]
        status, body = _req("GET", "/api/auth/me", token=token)
        assert status == 200 and body.get("email") == email, (status, body)
        _ok("signup + GET /api/auth/me (token) -> 200")

        # 3) plain-English generation
        status, d = _req("POST", "/api/designs/create", {"prompt": PROMPT}, token=token)
        assert status == 200, (status, d)
        assert not d["needs_clarification"], f"fatal clarification: {d['clarification_question']}"
        assert d["route"] == "cad_plan", f"expected feature-graph route, got {d['route']}"
        fmts = {e["fmt"] for e in d["exports"]}
        assert {"step", "stl"} <= fmts, f"missing exports: {fmts}"
        _ok("POST /api/designs/create (bearing block) -> design",
            f"type={d['object_type']} route={d['route']} "
            f"audit_passed={d.get('feature_audit_passed')}")

        # 4) STEP + STL actually download
        for fmt in ("step", "stl"):
            status, raw = _req("GET", f"/api/designs/{d['id']}/files/{fmt}", token=token)
            assert status == 200 and isinstance(raw, (bytes, bytearray)) and len(raw) > 0, \
                f"{fmt} download failed ({status})"
            _ok(f"GET /api/designs/.../files/{fmt} -> 200", f"{len(raw)} bytes")

        print("All local-dev smoke checks passed.")
    finally:
        if proc is not None:
            proc.terminate()
            proc.wait(timeout=15)


if __name__ == "__main__":
    main()
