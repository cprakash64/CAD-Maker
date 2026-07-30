"""/ready and /metrics (docs/ops/observability.md)."""
from app.config import settings


def test_ready_reports_ok_when_dependencies_are_healthy(client):
    r = client.get("/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["ready"] is True
    assert body["checks"]["database"]["ok"] is True
    assert body["checks"]["storage"]["ok"] is True


def test_ready_returns_503_when_db_check_fails(client, monkeypatch):
    import app.routers.ops as ops

    monkeypatch.setattr(ops, "_check_db", lambda: (False, "OperationalError"))
    r = client.get("/ready")
    assert r.status_code == 503
    body = r.json()
    assert body["ready"] is False
    assert body["checks"]["database"]["ok"] is False


def test_metrics_open_when_no_ops_token_configured(client, monkeypatch):
    monkeypatch.setattr(settings, "ops_api_token", None)
    r = client.get("/metrics")
    assert r.status_code == 200
    assert b"http_requests_total" in r.content


def test_metrics_requires_token_when_configured(client, monkeypatch):
    monkeypatch.setattr(settings, "ops_api_token", "a-real-secret-token-value")
    r = client.get("/metrics")
    assert r.status_code == 401

    r_ok = client.get("/metrics", headers={"Authorization": "Bearer a-real-secret-token-value"})
    assert r_ok.status_code == 200


def test_metrics_reflects_a_real_request(client, auth):
    client.post("/api/designs/create", json={"prompt": "a bracket 80x40x6mm"}, headers=auth["headers"])
    r = client.get("/metrics")
    text = r.content.decode()
    assert 'design_requests_total{outcome="success"}' in text
    assert "job_active_status_count" in text
    assert "storage_bytes_used" in text


def test_health_liveness_is_always_trivially_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
