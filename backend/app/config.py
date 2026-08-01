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
    # Short-lived by default: a presigned URL carries no further access check
    # of its own, so it is only as private as its lifetime. 15 minutes is
    # generous for a browser download/redirect while bounding how long a
    # leaked/logged URL stays valid. Override via S3_SIGNED_URL_TTL if needed.
    s3_signed_url_ttl: int = 900

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
    # Honour X-Forwarded-For when keying anonymous limits. Enable ONLY when the
    # app sits behind a reverse proxy that OVERWRITES the header (nginx
    # `proxy_set_header X-Forwarded-For $remote_addr`, an ALB, or Cloudflare).
    # Left off, the direct socket address is used. If it were on by default a
    # client talking to the app directly could forge a new address per request
    # and get a fresh rate-limit bucket every time.
    trust_proxy_headers: bool = False
    rate_limit_auth: str = "10/60"          # login + signup (per IP)
    rate_limit_create: str = "30/60"        # design creation (expensive: CAD gen)
    rate_limit_regenerate: str = "60/60"    # deterministic param rebuilds
    rate_limit_modify: str = "30/60"        # plain-English / localized / circle edits
    rate_limit_drawing: str = "12/60"       # drawing interpretation (vision, costly)
    rate_limit_package: str = "60/60"       # exports, packages, drawing views
    # Job polling: clients poll roughly once a second while a drawing job runs,
    # so this is deliberately generous — it exists to stop an unbounded polling
    # loop, not to pace the normal UI.
    rate_limit_poll: str = "600/60"
    # Cheap authenticated reads (design fetch/list/checks/feedback). High enough
    # to be invisible in normal use, low enough to bound scripted scraping.
    rate_limit_read: str = "300/60"
    rate_limit_default: str = "120/60"      # fallback for any other category

    # LLM provider: "openai" in production, or "mock" (offline, dev/test only).
    llm_provider: str = "mock"
    openai_api_key: str | None = None
    openai_base_url: str | None = None
    openai_model: str = "gpt-4o-mini"
    openai_reasoning_effort: str | None = None
    # Reliability: per-request timeout and SDK-level automatic retries (the
    # OpenAI client retries transient 429/5xx/connection errors with backoff).
    # Planning timeout is intentionally short (<=20s): known templates are routed
    # deterministically (no LLM), and a slow/stuck planner must fail fast and fall
    # back rather than block the UI for minutes (the production 503-after-183s bug).
    openai_timeout_seconds: int = 20
    openai_max_retries: int = 1
    # Phase 5/6 selectable-geometry metadata is advisory and MUST never block or
    # slow a basic CAD generation. It can be disabled entirely, and each build's
    # extraction is run under a hard wall-clock timeout (returns empty metadata
    # and continues if BRep inspection is ever slow/hangs).
    selectable_metadata_enabled: bool = True
    selectable_metadata_timeout_seconds: float = 6.0
    # Total wall-clock budget for ONE generation (all LLM fallbacks + repair
    # passes combined). Bounds the model-fallback chain so a request can never
    # hang for minutes; exceeding it returns a clean 503. Alias accepted at load
    # time: TOTAL_LLM_TIMEOUT_SECONDS.
    cad_generation_timeout_seconds: int = 120
    # Drawing → CAD: budget for the VISION interpretation call chain. Shorter
    # than the CAD budget — a drawing that can't be read in this window should
    # fail fast (deterministic contour extraction / a clean retry takes over),
    # never stack model-fallback timeouts.
    drawing_vision_timeout_seconds: int = 30
    # Drawing → CAD async job pipeline. The provider timeout bounds ONLY the
    # vision interpretation call chain (the job itself keeps running into the
    # deterministic fallback afterwards). A provider retry re-sends a
    # COMPRESSED image, never the full original. Images are downscaled to
    # drawing_max_image_side before the provider call and CV analysis.
    # drawing_job_timeout_seconds is superseded by job_hard_timeout_seconds
    # (docs/adr/0001-job-queue-database-backed.md) -- the worker supervisor
    # now enforces a REAL, process-killing timeout for every job, drawings
    # included, rather than this value's old role as a poller-only watchdog
    # that couldn't actually stop the underlying (in-process) work. Kept as a
    # settings field (not removed) so an existing .env override is harmless.
    drawing_job_timeout_seconds: int = 180
    drawing_provider_timeout_seconds: int = 75
    drawing_provider_retries: int = 1
    drawing_max_image_side: int = 1600
    # Deterministic best-effort fallback when the provider fails/times out:
    # profile tracing, segmented main-contour extraction, and flange-family
    # detection generate a REVIEW-flagged model instead of failing the job.
    drawing_enable_deterministic_fallback: bool = True

    # CAD feature-graph engine (plain-English → CadPlan → CadQuery).
    # cad_engine="feature_graph" makes the CadPlan compiler the primary route;
    # "legacy" falls back to the old template-first pipeline.
    cad_engine: str = "feature_graph"
    # CAD planner provider/model. cad_llm_provider defaults to llm_provider; the
    # OpenAI planner uses cad_llm_model with a gpt-5.1 → gpt-4.1 fallback chain.
    cad_llm_provider: str | None = None
    cad_llm_model: str = "gpt-5.5"

    # Object Intelligence: allow bounded trusted-source web lookup for objects with
    # no local preset. OFF by default (and in tests) — local presets/standards and
    # user-provided dimensions never need it, and an unknown object asks for
    # dimensions rather than hanging on a slow lookup.
    object_intelligence_web_search: bool = False

    # Thread modeling detail for fasteners / tapped holes:
    #   "modeled"         — real helical thread geometry (default for std fasteners)
    #   "cosmetic"        — fast smooth-bore preview (no thread geometry)
    #   "high_resolution" — modeled thread at finer tessellation (final / 3D print)
    thread_detail: str = "modeled"

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

    # --- Job queue / worker (docs/adr/0001-job-queue-database-backed.md) -------
    # A pathological CAD job (drawing parsing / CadPlan compile / OCCT ops /
    # meshing / export) must never run inline in the FastAPI process — it runs
    # in an isolated worker subprocess so a hang or segfault can only ever take
    # down that one job, never the API. The queue itself is the existing SQL
    # database (SQLite in dev, Postgres in production) — see the ADR for why
    # that's the right choice over Redis/Celery/RQ on a single small VPS.
    job_worker_concurrency: int = 2          # max jobs running at once, this node
    job_max_queue_depth: int = 50            # queued+running across ALL users
    job_per_user_concurrent_limit: int = 2   # queued+running for ONE user
    # Hard wall-clock ceiling per job; the supervisor SIGTERMs then SIGKILLs the
    # child if it's still alive past this. Kept >= cad_generation_timeout_seconds
    # (the pipeline's own cooperative budget) so a well-behaved job is never cut
    # off before its own timeout would have fired.
    job_hard_timeout_seconds: int = 150
    job_sigkill_grace_seconds: int = 5
    # RLIMIT_AS (address space) in MB for the child process; 0 disables the
    # limit. Linux-only (see runner.py) -- "memory limit where supported".
    job_memory_limit_mb: int = 1536
    # RLIMIT_CPU (seconds of actual CPU time, not wall clock) for the child.
    job_cpu_time_limit_seconds: int = 120
    # A "running" job whose heartbeat is older than this is presumed orphaned
    # (its worker process died without updating the DB) and is reaped: requeued
    # or failed per its retry policy. Must exceed job_heartbeat_interval_seconds
    # by a wide margin to avoid reaping a job that's merely between heartbeats.
    job_stale_heartbeat_seconds: int = 90
    job_heartbeat_interval_seconds: int = 10
    # The supervisor exits cleanly (for a process manager to restart it fresh)
    # after processing this many jobs, bounding slow memory growth in long-lived
    # OCCT/cadquery C-extension state. 0 disables recycling.
    job_worker_max_jobs_before_recycle: int = 200
    # How long a submitting request will poll-wait (cheap DB reads only, no CAD
    # work in this thread) for a fast job to finish before degrading to a plain
    # 202 + job_id response. Keeps today's synchronous response shape for the
    # common (fast) case without ever blocking on the actual CAD compute.
    # KNOWN GAP (see docs/adr/0001-job-queue-database-backed.md): the current
    # frontend does not yet handle a 202 from POST /api/designs/create (it
    # only does so for the drawing endpoints, which already returned 202
    # before this phase) -- a job slower than this window degrades to a
    # response shape the frontend can't parse today, rather than the clean
    # "job did not finish, poll it" contract this API now supports. This
    # value is set high enough that ordinary generations essentially never
    # cross it, buying time for a follow-up frontend polling integration
    # (mirroring frontend/src/lib/drawingJob.ts) without leaving the common
    # case worse off in the meantime. Keep BELOW the reverse proxy's
    # read timeout (docs/deployment.md's nginx example uses 60s).
    job_sync_wait_seconds: float = 45.0
    job_sync_poll_interval_seconds: float = 0.3
    # Root directory for per-job scratch space; each job gets its own
    # subdirectory, removed on success, failure, timeout, or crash.
    job_tmp_root: str = str(_BACKEND_ROOT / "job_tmp")
    # Poll interval for an idle supervisor with no queued jobs.
    job_poll_interval_seconds: float = 1.0

    # Observability
    log_level: str = "INFO"
    eval_report_dir: str = str(_BACKEND_ROOT / "eval_reports")
    # HMAC secret for pseudonymizing user/tenant ids in telemetry (see
    # app.observability.pseudonymize). Falls back to jwt_secret when unset --
    # set a DEDICATED value in production so rotating one doesn't rotate the
    # other's correlation history.
    telemetry_hash_secret: str | None = None
    # Bearer token required for internal ops endpoints (/metrics, the detailed
    # /ready body) -- separate from user JWTs so monitoring doesn't need a
    # logged-in account. Required in production (see production_problems).
    ops_api_token: str | None = None

    # --- Per-account quotas (volume, distinct from rate_limit_* which bounds
    # burst request RATE) -- 0 disables the check. -------------------------
    quota_designs_per_day: int = 200
    quota_designs_per_month: int = 2000
    storage_quota_mb_per_user: int = 4096

    # --- Cost-control policy (docs/operations/cost-control-architecture.md)
    # -- versioned so an active BudgetReservation always records which
    # policy generation it was checked against, for audit. Bump
    # COST_CONTROL_POLICY_VERSION whenever any limit below changes meaning
    # (not merely its numeric value) in a way that matters for that audit
    # trail. All costs are integer US-cent fixed-point, never floats. 0
    # disables the corresponding check. Per-account overrides
    # (AccountLimitOverride) take precedence over these when set. -----------
    cost_control_policy_version: str = "1"
    cost_control_enabled: bool = True
    # Per-account daily/monthly GENERATION limit (design_create + drawing
    # jobs combined) -- distinct from quota_designs_per_day/month above,
    # which count ALL Job rows; these are enforced through the atomic
    # reservation counters instead of a live COUNT query, closing the
    # concurrent-request race the live-COUNT approach had.
    cost_daily_generation_limit: int = 100
    cost_monthly_generation_limit: int = 1500
    # Per-account daily DRAWING-conversion limit (the priciest call class).
    cost_daily_drawing_limit: int = 30
    # Per-request caps -- reject BEFORE reserving if the conservative
    # estimate alone already exceeds these (docs: "request too expensive").
    cost_max_estimated_tokens_per_request: int = 20_000
    cost_max_estimated_cost_cents_per_request: int = 50  # $0.50
    # Per-account cumulative daily $ cap (reserved, not just actual-spend).
    cost_max_daily_account_cost_cents: int = 300  # $3.00/account/day
    # Global budgets -- the authoritative, restart-safe, multi-worker-safe
    # counterpart to app.llm.circuit_breaker's per-process daily-spend trip
    # (that breaker is NOT weakened or replaced by this; it still trips fast
    # in-process as a first line of defense -- this is the durable ledger
    # behind it, see docs/operations/cost-control-architecture.md).
    cost_global_daily_budget_cents: int = 5000  # $50.00/day, system-wide
    cost_global_hourly_emergency_budget_cents: int = 1000  # $10.00/hour
    # Max retries counted PER RESERVATION (a job's automatic retries, via
    # job_service.RETRY_POLICY, are included in the ORIGINAL reservation --
    # this bounds how many attempts a single reservation may cover before
    # a retry storm is treated as its own failure).
    cost_max_retries_per_request: int = 3
    # Design-version retention: the Nth-oldest version is pruned (its
    # snapshot only; the design itself is untouched) once a design exceeds
    # this many retained versions. 0 disables.
    cost_max_retained_design_versions: int = 50
    # Per-account upload frequency (distinct from rate_limit_drawing's burst
    # window and upload_guard's per-file size caps) -- total uploads/hour.
    cost_max_upload_frequency_per_hour: int = 60
    # A reservation still "reserved" past this many seconds, with no
    # terminal job/request outcome, is presumed orphaned (crashed process /
    # restart) and released by the stale-reservation reaper.
    cost_reservation_ttl_seconds: int = 600

    # --- Retention (docs/ops/data-retention.md) -----------------------------
    # How long a design's prompt/spec/exports are kept after last update
    # before an artifact-retention sweep may reclaim storage. The DB row
    # itself (and the prompt text within it) is the access-controlled
    # retention store the product-contract's "prompt retention" policy
    # requires -- ordinary logs never carry the raw prompt (see
    # app.observability.content_fingerprint). 0 disables the sweep (keep
    # forever).
    artifact_retention_days: int = 180

    # --- LLM circuit breaker (failure-rate AND daily-spend tripped) --------
    # docs/ops/observability.md. Consecutive-failure trip gives graceful
    # degraded mode when OpenAI is down: once open, calls fail FAST with a
    # clean LLMUnavailableError instead of waiting out a timeout each time.
    llm_circuit_breaker_enabled: bool = True
    llm_circuit_breaker_failure_threshold: int = 5
    llm_circuit_breaker_cooldown_seconds: int = 60
    # Daily model-spend cap (estimated, from token usage -- see
    # app.llm.pricing). 0 disables the cost-based trip. Resets at UTC
    # midnight. This is a BLUNT safety backstop against a runaway loop or
    # billing surprise, not a precise budget -- see docs/ops/observability.md.
    llm_cost_daily_cap_usd: float = 25.0

    # --- DB connection-pool protection --------------------------------------
    # Explicit (rather than SQLAlchemy's silent defaults) so the pool's real
    # capacity is documented and tunable per deployment size. No-ops for
    # SQLite (NullPool-like single-file access; see app/database.py).
    db_pool_size: int = 10
    db_max_overflow: int = 10
    db_pool_timeout_seconds: int = 30

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
        # Alias: TOTAL_LLM_TIMEOUT_SECONDS -> cad_generation_timeout_seconds when
        # the canonical var isn't set.
        if not _env_is_set("CAD_GENERATION_TIMEOUT_SECONDS") and _env_is_set(
            "TOTAL_LLM_TIMEOUT_SECONDS"
        ):
            kwargs["cad_generation_timeout_seconds"] = _get_int(
                "TOTAL_LLM_TIMEOUT_SECONDS", 120
            )
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
        # Production supports OpenAI only. "mock" is a deterministic offline
        # provider for dev/test and is refused outside development.
        if self.llm_provider == "mock":
            problems.append(
                f"LLM_PROVIDER=mock is not allowed when APP_ENV={self.app_env}; "
                "set LLM_PROVIDER=openai with an API key."
            )
        elif self.llm_provider == "openai" and not self.openai_api_key:
            problems.append("LLM_PROVIDER=openai requires OPENAI_API_KEY.")
        elif self.llm_provider != "openai":
            problems.append(
                f"Unknown LLM_PROVIDER={self.llm_provider!r}; the only supported "
                "production provider is 'openai'."
            )

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
        # A wildcard origin combined with allow_credentials=True (main.py) would
        # let any site issue authenticated cross-origin calls.
        if "*" in self.cors_origins:
            problems.append(
                "CORS_ORIGINS must not contain '*' — the API sends credentials, "
                "so every allowed origin has to be named explicitly."
            )
        if "localhost" in self.public_base_url or "127.0.0.1" in self.public_base_url:
            problems.append(
                "PUBLIC_BASE_URL must be your public backend URL, not localhost."
            )

        # --- Dev-only surface must be off ---
        if self.dev_mode:
            problems.append("DEV_MODE must be false in production (it exposes provider status).")

        # --- Ops endpoints (/metrics, detailed /ready) must be locked down ---
        if not self.ops_api_token or len(self.ops_api_token) < 16:
            problems.append(
                "OPS_API_TOKEN must be set (>=16 chars) in production — it gates "
                "/metrics and the detailed /ready body, which report cost/queue/"
                "business data that must not be publicly readable."
            )

        # --- DB connection pool must be sane ---
        if self.db_pool_size < 1 or self.db_max_overflow < 0 or self.db_pool_timeout_seconds < 1:
            problems.append(
                "DB_POOL_SIZE must be >=1, DB_MAX_OVERFLOW >=0, and "
                "DB_POOL_TIMEOUT_SECONDS >=1."
            )

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
