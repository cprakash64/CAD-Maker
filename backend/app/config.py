"""Application settings (env-driven; no secrets hardcoded).

Reads from process environment (and an optional .env file) with a tiny loader
instead of pydantic-settings, keeping startup dependency-light and fast.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, fields
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent

# The insecure development JWT secret. Production startup refuses to run with it.
_DEFAULT_JWT_SECRET = "dev-insecure-secret-change-me"


def _env_is_set(name: str) -> bool:
    """True if an env var was explicitly provided with a non-empty value."""
    return bool(os.environ.get(name.upper()))


def _load_dotenv(path: Path) -> None:
    """Populate os.environ from a .env file (existing vars win). Best-effort."""
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _get(name: str, default: str) -> str:
    return os.environ.get(name.upper(), default)


def _get_opt(name: str) -> str | None:
    val = os.environ.get(name.upper())
    return val if val else None


def _get_bool(name: str, default: bool) -> bool:
    val = os.environ.get(name.upper())
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


def _get_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name.upper(), default))
    except (TypeError, ValueError):
        return default


def _get_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name.upper(), default))
    except (TypeError, ValueError):
        return default


@dataclass
class Settings:
    # Server
    app_env: str = "development"  # development | staging | production
    testing: bool = False  # set TESTING=true in the test harness
    public_base_url: str = "http://localhost:8000"
    # Common Next dev origins out of the box (localhost + 127.0.0.1, ports
    # 3000/3001 — Next hops to 3001 when 3000 is busy). In dev_mode main.py
    # additionally allows any localhost/127.0.0.1 port via allow_origin_regex.
    cors_origins: str = (
        "http://localhost:3000,http://localhost:3001,"
        "http://127.0.0.1:3000,http://127.0.0.1:3001"
    )
    dev_mode: bool = True  # exposes provider status to the frontend in dev only
    # Allow the offline mock to handle Drawing-to-CAD (dev only; image
    # understanding is NOT reliable in mock mode).
    dev_allow_mock_drawing: bool = False

    # Persistence
    database_url: str = f"sqlite:///{_BACKEND_ROOT / 'cadmaker.db'}"

    # Storage: "local" or "s3"
    storage_backend: str = "local"
    storage_dir: str = str(_BACKEND_ROOT / "storage_data")
    s3_bucket: str | None = None
    s3_region: str | None = None
    s3_endpoint_url: str | None = None  # for S3-compatible (MinIO, R2, etc.)
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = None
    s3_signed_url_ttl: int = 3600

    # Auth
    jwt_secret: str = _DEFAULT_JWT_SECRET
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24 * 7  # one week

    # --- Rate limiting (per-process, in-memory; protects abuse-prone routes) ---
    # Off in dev/test, on by default in staging/production. RATE_LIMIT_ENABLED
    # explicitly overrides (true/false) in any environment. Each limit is
    # "<requests>/<window_seconds>", applied per authenticated user (or per IP
    # when anonymous).
    rate_limit_enabled: bool = False
    rate_limit_auth: str = "10/60"          # login + signup (per IP)
    rate_limit_create: str = "30/60"        # design creation (expensive: CAD gen)
    rate_limit_regenerate: str = "60/60"    # deterministic param rebuilds
    rate_limit_modify: str = "30/60"        # plain-English / localized / circle edits
    rate_limit_drawing: str = "12/60"       # drawing interpretation (vision, costly)
    rate_limit_package: str = "60/60"       # exports, packages, drawing views
    rate_limit_default: str = "120/60"      # fallback for any other category

    # LLM provider: "mock" (default, offline), "anthropic", or "openai"
    llm_provider: str = "mock"
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-4-6"
    openai_api_key: str | None = None
    openai_base_url: str | None = None
    openai_model: str = "gpt-4o-mini"
    openai_reasoning_effort: str | None = None
    # Reliability: per-request timeout and SDK-level automatic retries (the
    # OpenAI client retries transient 429/5xx/connection errors with backoff).
    openai_timeout_seconds: int = 60
    openai_max_retries: int = 2

    # CAD feature-graph engine (plain-English → CadPlan → CadQuery).
    # cad_engine="feature_graph" makes the CadPlan compiler the primary route;
    # "legacy" falls back to the old template-first pipeline.
    cad_engine: str = "feature_graph"
    # CAD planner provider/model. cad_llm_provider defaults to llm_provider; the
    # OpenAI planner uses cad_llm_model with a gpt-5.1 → gpt-4.1 fallback chain.
    cad_llm_provider: str | None = None
    cad_llm_model: str = "gpt-5.5"

    # --- CAD accuracy / 3D-print policy (all millimetres) ---
    # Dimensional tolerance for "requested vs generated" validation. A measured
    # value passes when within max(abs_mm, frac * requested). These are the
    # single source of truth for dimension-drift checks + the golden benchmark.
    cad_length_tolerance_mm: float = 0.5
    cad_length_tolerance_frac: float = 0.02
    cad_diameter_tolerance_mm: float = 0.3
    # Printability floors. Features below these are flagged (never silently grown).
    printer_min_feature_mm: float = 0.8
    printer_min_hole_mm: float = 1.0
    # XY compensation the pipeline applies to counter printer over/under-extrusion.
    # Default 0.0 == we do NOT alter requested dimensions; any non-zero value is
    # surfaced in the dimension report so a size change is never hidden.
    printer_xy_compensation_mm: float = 0.0

    # Observability
    log_level: str = "INFO"
    eval_report_dir: str = str(_BACKEND_ROOT / "eval_reports")

    @classmethod
    def load(cls) -> "Settings":
        _load_dotenv(_BACKEND_ROOT / ".env")
        kwargs: dict = {}
        for f in fields(cls):
            if f.type == "bool":
                kwargs[f.name] = _get_bool(f.name, bool(f.default))
            elif f.type == "int":
                kwargs[f.name] = _get_int(f.name, int(f.default))
            elif f.type == "float":
                kwargs[f.name] = _get_float(f.name, float(f.default))
            elif f.default is None:
                kwargs[f.name] = _get_opt(f.name)
            else:
                kwargs[f.name] = _get(f.name, str(f.default))
        return cls(**kwargs)

    # --- environment / provider gating ---
    @property
    def is_production_like(self) -> bool:
        return self.app_env.lower() in {"staging", "production"}

    def rate_limit_active(self) -> bool:
        """Whether rate limiting is enforced.

        Explicit RATE_LIMIT_ENABLED wins (on or off, any environment). Otherwise
        on by default in staging/production and off in dev/test (so the suite and
        local iteration stay convenient). Tests opt in by setting the field."""
        if _env_is_set("RATE_LIMIT_ENABLED"):
            return self.rate_limit_enabled
        if self.rate_limit_enabled:
            return True
        return self.is_production_like and not self.testing

    @property
    def mock_allowed(self) -> bool:
        """The offline mock is only permitted in development or under tests."""
        return self.testing or self.app_env.lower() == "development"

    def can_understand_images(self) -> bool:
        """True only when a vision-capable provider is configured (real OpenAI)."""
        return self.llm_provider == "openai" and bool(self.openai_api_key)

    def drawing_to_cad_enabled(self) -> bool:
        if self.can_understand_images():
            return True
        # Mock drawing is an explicit dev-only opt-in.
        return (
            self.llm_provider == "mock"
            and self.mock_allowed
            and self.dev_allow_mock_drawing
        )

    @property
    def is_default_jwt_secret(self) -> bool:
        return not self.jwt_secret or self.jwt_secret == _DEFAULT_JWT_SECRET

    def production_problems(self) -> list[str]:
        """Every unsafe/missing production setting (empty list == safe to boot).

        Only meaningful for staging/production; development and tests return [].
        """
        if self.testing or not self.is_production_like:
            return []
        problems: list[str] = []

        # --- LLM provider + credentials ---
        if self.llm_provider == "mock":
            problems.append(
                f"LLM_PROVIDER=mock is not allowed when APP_ENV={self.app_env}; "
                "set LLM_PROVIDER=openai (or anthropic) with an API key."
            )
        elif self.llm_provider == "openai" and not self.openai_api_key:
            problems.append("LLM_PROVIDER=openai requires OPENAI_API_KEY.")
        elif self.llm_provider == "anthropic" and not self.anthropic_api_key:
            problems.append("LLM_PROVIDER=anthropic requires ANTHROPIC_API_KEY.")
        elif self.llm_provider not in {"openai", "anthropic"}:
            problems.append(f"Unknown LLM_PROVIDER={self.llm_provider!r}.")

        # --- JWT signing secret ---
        if self.is_default_jwt_secret:
            problems.append(
                "JWT_SECRET is unset or still the insecure dev default; set a "
                "strong unique value (e.g. `openssl rand -hex 32`)."
            )
        elif len(self.jwt_secret) < 32:
            problems.append("JWT_SECRET is too short; use at least 32 random characters.")

        # --- Database ---
        if not _env_is_set("DATABASE_URL"):
            problems.append(
                "DATABASE_URL must be explicitly set in production (the implicit "
                "dev SQLite file is not a production datastore)."
            )

        # --- Storage ---
        if self.storage_backend == "s3":
            if not self.s3_bucket:
                problems.append("STORAGE_BACKEND=s3 requires S3_BUCKET.")
        elif self.storage_backend != "local":
            problems.append(f"Unknown STORAGE_BACKEND={self.storage_backend!r}.")

        # --- Public/CORS URLs ---
        if not _env_is_set("CORS_ORIGINS") or "localhost" in self.cors_origins or "127.0.0.1" in self.cors_origins:
            problems.append(
                "CORS_ORIGINS must be set to your real frontend origin(s), not localhost."
            )
        if "localhost" in self.public_base_url or "127.0.0.1" in self.public_base_url:
            problems.append(
                "PUBLIC_BASE_URL must be your public backend URL, not localhost."
            )

        # --- Dev-only surface must be off ---
        if self.dev_mode:
            problems.append("DEV_MODE must be false in production (it exposes provider status).")

        return problems

    def validate_startup(self) -> None:
        """Fail fast on unsafe production configuration (no-op in dev/tests)."""
        problems = self.production_problems()
        if problems:
            raise RuntimeError(
                "Refusing to start: unsafe production configuration "
                f"(APP_ENV={self.app_env}):\n  - " + "\n  - ".join(problems)
            )


settings = Settings.load()
