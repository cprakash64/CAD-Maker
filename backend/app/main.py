"""FastAPI application entry point."""
from __future__ import annotations

import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.config import settings
from app.database import init_db
from app.llm.base import LLMUnavailableError
from app.observability import log_event, logger
from app.rate_limit import rate_limit
from app.routers import auth, capabilities, designs, drawings, templates

# Fail fast on unsafe production config (mock provider in prod, default JWT
# secret, missing DATABASE_URL / CORS / storage, dev_mode on, etc.).
settings.validate_startup()

app = FastAPI(title="SourceCAD AI Part Studio API", version="0.7.4")


@app.exception_handler(LLMUnavailableError)
async def _llm_unavailable(request: Request, exc: LLMUnavailableError) -> JSONResponse:
    """Surface AI-provider outages as a clean 503 (never a raw stack trace)."""
    log_event("llm_unavailable", path=request.url.path)
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.exception_handler(SQLAlchemyError)
async def _database_error(request: Request, exc: SQLAlchemyError) -> JSONResponse:
    """Database faults become a structured 503, never a driver message.

    A raw SQLAlchemy error text carries the failing SQL, table and column names,
    and often the connection URL — schema and infrastructure disclosure. The
    full exception is still logged server-side with a traceback; only the
    generic message crosses the API boundary.
    """
    logger.exception("database error on %s", request.url.path)
    log_event("database_error", path=request.url.path, error_type=type(exc).__name__)
    return JSONResponse(
        status_code=503,
        content={"detail": "A database error occurred. Please try again."},
    )


@app.exception_handler(Exception)
async def _unhandled_error(request: Request, exc: Exception) -> JSONResponse:
    """Last-resort boundary: log the full traceback, return a generic 500.

    This does not swallow anything — the failure is still a 500 and the complete
    traceback goes to the application log. It only stops the traceback, file
    paths, and local variables from being rendered into the HTTP response.
    """
    logger.exception("unhandled error on %s", request.url.path)
    log_event("unhandled_error", path=request.url.path, error_type=type(exc).__name__)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error."},
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    # In development also accept localhost/127.0.0.1 on ANY port, so the Next
    # dev server hopping to :3001 (or being opened via 127.0.0.1) never turns
    # into an opaque "TypeError: Failed to fetch" in the browser.
    allow_origin_regex=(
        r"https?://(localhost|127\.0\.0\.1)(:\d+)?" if settings.dev_mode else None
    ),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(designs.router)
app.include_router(drawings.router)
app.include_router(drawings.alias_router)  # POST /api/drawing-to-cad
app.include_router(templates.router)
app.include_router(capabilities.router)


@app.middleware("http")
async def _timing(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Response-Time-ms"] = str(
        round((time.perf_counter() - start) * 1000, 1)
    )
    # Log non-trivial API calls (skip health/docs noise). No secrets logged.
    if request.url.path.startswith("/api/"):
        log_event(
            "http_request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            latency_ms=round((time.perf_counter() - start) * 1000, 2),
        )
    return response


@app.on_event("startup")
def _startup() -> None:
    init_db()
    if settings.storage_backend == "local":
        Path(settings.storage_dir).mkdir(parents=True, exist_ok=True)


@app.get("/health")
def health() -> dict:
    """Health + (dev-only) provider/storage status. Never exposes secrets."""
    body: dict = {"status": "ok"}
    if settings.dev_mode:
        body["llm_provider"] = settings.llm_provider
        body["storage_backend"] = settings.storage_backend
        body["dev_mode"] = True
    return body


@app.get("/api/provider-status", dependencies=[rate_limit("read")])
def provider_status() -> dict:
    """Capability status the frontend uses to enable/block AI flows.

    Never returns secrets — only whether a capable provider is configured.
    """
    import importlib.util

    provider = settings.llm_provider
    image_understanding = settings.can_understand_images()

    provider_error: str | None = None
    text_available = True
    structured_available = False
    model = "mock"

    if provider == "openai":
        model = settings.openai_model
        structured_available = bool(settings.openai_api_key)
        if importlib.util.find_spec("openai") is None:
            provider_error = "The 'openai' package is not installed (pip install -r requirements.txt)."
            text_available = image_understanding = structured_available = False
        elif not settings.openai_api_key:
            provider_error = "OPENAI_API_KEY is missing — set it in .env."
            text_available = image_understanding = structured_available = False
        label = "OpenAI vision active" if image_understanding else (provider_error or "OpenAI unavailable")
    else:
        label = "Mock mode — image understanding blocked"

    # This route is unauthenticated (the UI calls it before/without a session),
    # so the payload is split: capability booleans the frontend needs to
    # enable/block AI flows are always public, while the deployment's internals
    # — environment, model id, timeouts, retry counts and the operator-facing
    # remediation text — are only returned in dev_mode. In production those
    # would tell an anonymous caller which model and configuration to target,
    # and provider_error names the exact env vars that are unset.
    body = {
        "provider": provider,
        "image_understanding": image_understanding,
        "image_understanding_available": image_understanding,
        "text_generation_available": text_available,
        "structured_outputs_available": structured_available,
        "drawing_to_cad_enabled": settings.drawing_to_cad_enabled(),
        "model_verified": False,
    }
    if settings.dev_mode:
        body.update({
            "app_env": settings.app_env,
            "model": model,
            "mock_allowed": settings.mock_allowed,
            "provider_error": provider_error,
            "status_label": label,
            # Reliability surface. The model id is NOT verified against the
            # provider at startup; an invalid id degrades through the fallback
            # chain and, if exhausted, returns a 503 rather than crashing.
            "request_timeout_seconds": settings.openai_timeout_seconds,
            "max_retries": settings.openai_max_retries,
        })
    else:
        # Enough for the UI to explain itself, with no configuration detail.
        body["provider_error"] = (
            None if not provider_error else "AI features are unavailable."
        )
        body["status_label"] = (
            "AI features active" if image_understanding else "AI features unavailable"
        )
    return body
