"""v0.3.7: production gating, provider status, blocked mock drawing, long-prompt routing."""
import pytest

from app.config import _DEFAULT_JWT_SECRET, Settings, settings


def _prod_kwargs(**overrides):
    """A fully-safe production Settings kwargs baseline; override to break one rule."""
    base = dict(
        app_env="production",
        testing=False,
        llm_provider="openai",
        openai_api_key="sk-test",
        jwt_secret="x" * 48,
        dev_mode=False,
        storage_backend="local",
        cors_origins="https://app.example.com",
        public_base_url="https://api.example.com",
    )
    base.update(overrides)
    return base


def _valid_prod_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@db/cad")
    monkeypatch.setenv("CORS_ORIGINS", "https://app.example.com")


# --- Config validation / gating ------------------------------------------
def test_mock_blocked_in_production():
    s = Settings(app_env="production", llm_provider="mock", testing=False)
    with pytest.raises(RuntimeError):
        s.validate_startup()


def test_mock_blocked_in_staging():
    s = Settings(app_env="staging", llm_provider="mock", testing=False)
    with pytest.raises(RuntimeError):
        s.validate_startup()


def test_mock_allowed_in_development():
    Settings(app_env="development", llm_provider="mock").validate_startup()  # no raise


def test_mock_allowed_under_testing_even_if_prod():
    Settings(app_env="production", llm_provider="mock", testing=True).validate_startup()


def test_openai_without_key_blocked_in_production():
    s = Settings(app_env="production", llm_provider="openai", openai_api_key=None, testing=False)
    with pytest.raises(RuntimeError):
        s.validate_startup()


def test_production_rejects_default_jwt_secret(monkeypatch):
    _valid_prod_env(monkeypatch)
    s = Settings(**_prod_kwargs(jwt_secret=_DEFAULT_JWT_SECRET))
    probs = s.production_problems()
    assert any("JWT_SECRET" in p for p in probs)
    with pytest.raises(RuntimeError):
        s.validate_startup()


def test_production_rejects_short_jwt_secret(monkeypatch):
    _valid_prod_env(monkeypatch)
    s = Settings(**_prod_kwargs(jwt_secret="tooshort"))
    assert any("JWT_SECRET" in p for p in s.production_problems())


def test_production_requires_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("CORS_ORIGINS", "https://app.example.com")
    s = Settings(**_prod_kwargs())
    assert any("DATABASE_URL" in p for p in s.production_problems())


def test_production_rejects_localhost_cors_and_dev_mode(monkeypatch):
    _valid_prod_env(monkeypatch)
    s = Settings(**_prod_kwargs(dev_mode=True, cors_origins="http://localhost:3000",
                                public_base_url="http://localhost:8000"))
    probs = " ".join(s.production_problems())
    assert "DEV_MODE" in probs and "CORS_ORIGINS" in probs and "PUBLIC_BASE_URL" in probs


def test_production_s3_requires_bucket(monkeypatch):
    _valid_prod_env(monkeypatch)
    s = Settings(**_prod_kwargs(storage_backend="s3", s3_bucket=None))
    assert any("S3_BUCKET" in p for p in s.production_problems())


def test_valid_production_config_boots(monkeypatch):
    _valid_prod_env(monkeypatch)
    Settings(**_prod_kwargs()).validate_startup()  # no raise


def test_can_understand_images():
    assert Settings(llm_provider="openai", openai_api_key="sk-x").can_understand_images()
    assert not Settings(llm_provider="openai", openai_api_key=None).can_understand_images()
    assert not Settings(llm_provider="mock").can_understand_images()


def test_drawing_to_cad_enabled_rules():
    assert Settings(llm_provider="openai", openai_api_key="sk-x").drawing_to_cad_enabled()
    # mock needs explicit dev opt-in
    assert not Settings(llm_provider="mock", app_env="development",
                        dev_allow_mock_drawing=False).drawing_to_cad_enabled()
    assert Settings(llm_provider="mock", app_env="development",
                    dev_allow_mock_drawing=True, testing=True).drawing_to_cad_enabled()


def test_factory_refuses_mock_when_not_allowed(monkeypatch):
    from app.llm import factory
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "testing", False)
    monkeypatch.setattr(settings, "llm_provider", "mock")
    with pytest.raises(RuntimeError):
        factory.get_provider()


# --- Provider status endpoint --------------------------------------------
def test_provider_status_endpoint(client):
    r = client.get("/api/provider-status")
    assert r.status_code == 200
    body = r.json()
    assert body["provider"] == "mock"
    assert body["image_understanding"] is False
    assert "Mock" in body["status_label"]


# --- Blocked mock drawing flow -------------------------------------------
def test_drawing_blocked_when_mock_drawing_disabled(client, auth, monkeypatch):
    monkeypatch.setattr(settings, "dev_allow_mock_drawing", False)
    r = client.post(
        "/api/drawings/interpret",
        files={"file": ("d.png", b"x" * 200, "image/png")},
        data={"hint": "flanged pipe branch, 90mm main pipe, 8 bolts"},
        headers=auth["headers"],
    )
    assert r.status_code == 409
    assert "unavailable" in r.json()["detail"].lower()


def test_drawing_allowed_when_dev_opt_in(client, auth):
    # conftest sets DEV_ALLOW_MOCK_DRAWING=true, so the hinted flow works.
    r = client.post(
        "/api/drawings/interpret",
        files={"file": ("d.png", b"x" * 200, "image/png")},
        data={"hint": "flanged pipe branch, 90mm main pipe, 8 bolts"},
        headers=auth["headers"],
    )
    assert r.status_code == 200
    assert r.json()["suggested_object_type"] == "flanged_pipe_branch"


# --- Long prompt routes through ComplexCADPlan ---------------------------
LONG_CRANK = (
    "Create a realistic mechanical crankshaft for a 4-cylinder inline engine. "
    "It must have five main journals and four rod journals with inline-four "
    "phasing, eight webs with counterweights, a keyed front snout and a rear "
    "flywheel flange with six bolts. Machined from forged steel, polished. "
) + ("Additional detailed engineering context for the build. " * 40)


def test_long_prompt_routes_through_complex_plan(client, auth, legacy_engine):
    assert len(LONG_CRANK) > 1500
    r = client.post("/api/designs/create", json={"prompt": LONG_CRANK}, headers=auth["headers"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["object_type"] == "inline_4_crankshaft"
    assert len(body["exports"]) == 2  # STL + STEP


def test_long_unsupported_prompt_returns_clarification(client, auth):
    junk = "Build me a " + ("flux nacelle warp manifold " * 300)
    assert len(junk) > 1500
    r = client.post("/api/designs/create", json={"prompt": junk}, headers=auth["headers"])
    assert r.status_code == 200
    assert r.json()["needs_clarification"] is True
