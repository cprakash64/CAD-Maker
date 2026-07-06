"""Async job flow for Drawing → CAD (202 + polling).

POST /api/drawings/generate and /to-cad return 202 + job_id immediately; the
pipeline runs on a worker thread and GET /api/drawings/jobs/{id} reports
stage/progress until done/failed with the full result payload attached.
"""
from __future__ import annotations

import io
import time
from pathlib import Path

DATA = Path(__file__).parent / "data"
_TIMEOUT_S = 180


def _poll(client, auth, job_id: str) -> dict:
    deadline = time.time() + _TIMEOUT_S
    seen_stages: list[str] = []
    while time.time() < deadline:
        r = client.get(f"/api/drawings/jobs/{job_id}", headers=auth["headers"])
        assert r.status_code == 200, r.text
        job = r.json()
        if not seen_stages or seen_stages[-1] != job["stage"]:
            seen_stages.append(job["stage"])
        if job["status"] in ("done", "failed"):
            job["_stages"] = seen_stages
            return job
        time.sleep(0.2)
    raise AssertionError(f"job {job_id} did not finish in {_TIMEOUT_S}s")


def test_generate_returns_202_and_job_completes(client, auth):
    files = {"file": ("drawing.png", io.BytesIO(b"\x89PNG fake image bytes"), "image/png")}
    r = client.post("/api/drawings/generate", files=files,
                    data={"hint": "flanged pipe branch, 12 holes per flange, "
                                  "90mm main pipe"},
                    headers=auth["headers"])
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["job_id"] and body["status"] == "queued"
    assert body["poll"].endswith(body["job_id"])

    job = _poll(client, auth, body["job_id"])
    assert job["status"] == "done", job
    assert job["progress"] == 100
    result = job["result"]
    assert result["generated"] is True
    d = result["design"]
    assert job["design_id"] == d["id"]
    assert {e["fmt"] for e in d["exports"]} >= {"step", "stl"}


def test_to_cad_job_reports_progress_stages(client, auth):
    svg = (DATA / "simple_adapter_plate.svg").read_bytes()
    r = client.post("/api/drawings/to-cad",
                    files={"file": ("plate.svg", io.BytesIO(svg), "image/svg+xml")},
                    headers=auth["headers"])
    assert r.status_code == 202, r.text
    job = _poll(client, auth, r.json()["job_id"])
    assert job["status"] == "done", job
    assert job["result"]["generated"] is True
    # Stages progress monotonically toward done (polling may skip fast stages).
    from app.services.drawing_jobs import STAGE_PROGRESS

    order = list(STAGE_PROGRESS)
    idx = [order.index(s) for s in job["_stages"]]
    assert idx == sorted(idx), f"stages went backwards: {job['_stages']}"
    assert job["_stages"][-1] == "done"


def test_failed_job_lands_in_clean_failed_state(client, auth):
    """An unreadable image (mock provider, no hint) must produce a DONE job with
    generated=false + message — the client exits the generating state cleanly."""
    files = {"file": ("drawing.png", io.BytesIO(b"\x89PNG fake image bytes"), "image/png")}
    r = client.post("/api/drawings/generate", files=files, headers=auth["headers"])
    assert r.status_code == 202
    job = _poll(client, auth, r.json()["job_id"])
    assert job["status"] == "done"  # pipeline ran; the drawing was the problem
    assert job["result"]["generated"] is False
    assert job["result"]["design"] is None


def test_pipeline_exception_fails_job_with_message(client, auth, monkeypatch):
    import app.routers.drawings as dr

    def boom(*a, **k):
        raise RuntimeError("kernel exploded")

    monkeypatch.setattr(dr, "interpret_image", boom)
    files = {"file": ("drawing.png", io.BytesIO(b"\x89PNG fake image bytes"), "image/png")}
    r = client.post("/api/drawings/generate", files=files, headers=auth["headers"])
    assert r.status_code == 202
    job = _poll(client, auth, r.json()["job_id"])
    assert job["status"] == "failed"
    assert job["error"]


def test_job_is_owner_scoped(client, auth, auth2):
    files = {"file": ("drawing.png", io.BytesIO(b"\x89PNG fake image bytes"), "image/png")}
    r = client.post("/api/drawings/generate", files=files, headers=auth["headers"])
    job_id = r.json()["job_id"]
    r2 = client.get(f"/api/drawings/jobs/{job_id}", headers=auth2["headers"])
    assert r2.status_code == 404
    _poll(client, auth, job_id)  # let the worker finish before teardown


def test_unknown_job_404(client, auth):
    r = client.get("/api/drawings/jobs/nope", headers=auth["headers"])
    assert r.status_code == 404
    assert "expired" in r.json()["detail"] or "not found" in r.json()["detail"].lower()
