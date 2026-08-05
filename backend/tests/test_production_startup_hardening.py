"""Comprehensive coverage for Settings.production_problems() / validate_startup().

Prior coverage (test_phase2_upload_limits_and_data.py) exercised exactly one
case (wildcard CORS). This file asserts every individual unsafe-production
condition the settings module claims to reject, plus that a fully-valid
production configuration boots clean. None of this touches the CAD kernel —
it is pure dataclass construction, so it runs fast and without cadquery.
"""
from __future__ import annotations

import pytest

from app.config import Settings

# A deliberately valid, complete production configuration. Each test below
# takes a copy and breaks exactly one field, then asserts production_problems()
# names the break -- proving the check is real, not just "something is wrong".
_VALID_PROD_KWARGS = dict(
    app_env="production",
    testing=False,
    llm_provider="openai",
    openai_api_key="sk-live-not-a-real-key-but-present",
    jwt_secret="a" * 40,
    database_url_was_set=True,  # sentinel consumed below, not a real field
    storage_backend="local",
    cors_origins="https://app.example.com",
    public_base_url="https://api.example.com",
    dev_mode=False,
    ops_api_token="a-fake-ops-token-1234567890",
)


def _settings(monkeypatch, **overrides) -> Settings:
    """Build a Settings instance for the "valid production" baseline with the
    given fields overridden, honoring the DATABASE_URL env-presence check the
    same way Settings.load() does (_env_is_set reads os.environ directly)."""
    kwargs = dict(_VALID_PROD_KWARGS)
    database_url_was_set = kwargs.pop("database_url_was_set")
    kwargs.update(overrides)
    env_was_set = kwargs.pop("database_url_was_set", database_url_was_set)
    if env_was_set:
        monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@host/db")
    else:
        monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    if "cors_origins" in overrides or True:
        monkeypatch.setenv("CORS_ORIGINS", kwargs.get("cors_origins", ""))
    return Settings(**kwargs)


def test_valid_production_configuration_has_no_problems(monkeypatch):
    s = _settings(monkeypatch)
    assert s.production_problems() == []
    s.validate_startup()  # must not raise


def test_mock_provider_rejected_in_production(monkeypatch):
    s = _settings(monkeypatch, llm_provider="mock")
    problems = s.production_problems()
    assert any("mock" in p.lower() for p in problems)
    with pytest.raises(RuntimeError):
        s.validate_startup()


def test_unknown_provider_rejected_in_production(monkeypatch):
    s = _settings(monkeypatch, llm_provider="anthropic")
    problems = s.production_problems()
    assert any("unknown" in p.lower() or "openai" in p.lower() for p in problems)


def test_openai_without_api_key_rejected(monkeypatch):
    s = _settings(monkeypatch, openai_api_key=None)
    problems = s.production_problems()
    assert any("api_key" in p.lower() or "openai_api_key" in p.lower() for p in problems)


def test_default_jwt_secret_rejected(monkeypatch):
    s = _settings(monkeypatch, jwt_secret="dev-insecure-secret-change-me")
    problems = s.production_problems()
    assert any("jwt_secret" in p.lower() for p in problems)


def test_missing_jwt_secret_rejected(monkeypatch):
    s = _settings(monkeypatch, jwt_secret="")
    problems = s.production_problems()
    assert any("jwt_secret" in p.lower() for p in problems)


def test_short_jwt_secret_rejected(monkeypatch):
    s = _settings(monkeypatch, jwt_secret="short")
    problems = s.production_problems()
    assert any("jwt_secret" in p.lower() for p in problems)


def test_missing_database_url_rejected(monkeypatch):
    s = _settings(monkeypatch, database_url_was_set=False)
    problems = s.production_problems()
    assert any("database_url" in p.lower() for p in problems)


def test_s3_backend_without_bucket_rejected(monkeypatch):
    s = _settings(monkeypatch, storage_backend="s3", s3_bucket=None)
    problems = s.production_problems()
    assert any("s3_bucket" in p.lower() for p in problems)


def test_unknown_storage_backend_rejected(monkeypatch):
    s = _settings(monkeypatch, storage_backend="ftp")
    problems = s.production_problems()
    assert any("storage_backend" in p.lower() for p in problems)


def test_wildcard_cors_rejected(monkeypatch):
    s = _settings(monkeypatch, cors_origins="*")
    assert any("*" in p for p in s.production_problems())


def test_localhost_cors_rejected(monkeypatch):
    s = _settings(monkeypatch, cors_origins="http://localhost:3000")
    problems = s.production_problems()
    assert any("cors_origins" in p.lower() for p in problems)


def test_missing_cors_origins_env_rejected(monkeypatch):
    # CORS_ORIGINS is read from the env presence, not just the field value --
    # an unset env var means the field is still carrying the dataclass default.
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    s = Settings(
        app_env="production", testing=False, llm_provider="openai",
        openai_api_key="sk-live", jwt_secret="a" * 40, storage_backend="local",
        public_base_url="https://api.example.com", dev_mode=False,
    )
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@host/db")
    problems = s.production_problems()
    assert any("cors_origins" in p.lower() for p in problems)


def test_localhost_public_base_url_rejected(monkeypatch):
    s = _settings(monkeypatch, public_base_url="http://localhost:8000")
    problems = s.production_problems()
    assert any("public_base_url" in p.lower() for p in problems)


def test_dev_mode_true_rejected_in_production(monkeypatch):
    s = _settings(monkeypatch, dev_mode=True)
    problems = s.production_problems()
    assert any("dev_mode" in p.lower() for p in problems)


def test_missing_ops_api_token_rejected(monkeypatch):
    s = _settings(monkeypatch, ops_api_token=None)
    problems = s.production_problems()
    assert any("ops_api_token" in p.lower() for p in problems)


def test_short_ops_api_token_rejected(monkeypatch):
    s = _settings(monkeypatch, ops_api_token="short")
    problems = s.production_problems()
    assert any("ops_api_token" in p.lower() for p in problems)


def test_invalid_db_pool_settings_rejected(monkeypatch):
    s = _settings(monkeypatch, db_pool_size=0)
    problems = s.production_problems()
    assert any("db_pool_size" in p.lower() for p in problems)

    s = _settings(monkeypatch, db_max_overflow=-1)
    assert any("db_pool_size" in p.lower() or "db_max_overflow" in p.lower()
              for p in s.production_problems())

    s = _settings(monkeypatch, db_pool_timeout_seconds=0)
    assert any("db_pool_timeout" in p.lower() for p in s.production_problems())


def test_development_environment_bypasses_all_checks(monkeypatch):
    """The same unsafe combination that fails in production must be a no-op
    in development -- these checks must never block local iteration."""
    s = Settings(
        app_env="development", testing=False, llm_provider="mock",
        jwt_secret="dev-insecure-secret-change-me", dev_mode=True,
        cors_origins="*",
    )
    assert s.production_problems() == []
    s.validate_startup()  # must not raise


def test_testing_flag_bypasses_all_checks_even_in_production_app_env(monkeypatch):
    """The test harness itself runs with APP_ENV=development (conftest.py), but
    this guards the documented escape hatch: TESTING=true must also short-
    circuit production_problems() regardless of app_env, so CI can exercise a
    production-shaped config without needing real secrets."""
    s = Settings(
        app_env="production", testing=True, llm_provider="mock",
        jwt_secret="dev-insecure-secret-change-me", dev_mode=True,
        cors_origins="*",
    )
    assert s.production_problems() == []
