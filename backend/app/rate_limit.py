"""Lightweight, dependency-free rate limiting for abuse-prone routes.

Per-process, in-memory sliding-window counters. Identity is the authenticated
user when a valid bearer token is present, else the client IP (honoring
X-Forwarded-For from the reverse proxy). Limits are configured per category via
settings/env and only enforced when ``settings.rate_limit_active()`` is true
(off in dev/test, on by default in production).

Usage on a route:
    @router.post("/create", dependencies=[rate_limit("create")])

NOTE: counters live in this process only. With multiple uvicorn workers each
worker enforces the limit independently (effective limit ≈ N×workers). For a
strict global limit across workers/hosts, back this with Redis later — the
``rate_limit(category)`` interface stays the same.
"""
from __future__ import annotations

import threading
import time
from collections import deque

from fastapi import Depends, HTTPException, Request, status

from app.auth.security import decode_access_token
from app.config import settings
from app.observability import log_event

# category -> settings attribute holding "<count>/<window_seconds>"
_CATEGORY_SETTING = {
    "auth": "rate_limit_auth",
    "create": "rate_limit_create",
    "regenerate": "rate_limit_regenerate",
    "modify": "rate_limit_modify",
    "drawing": "rate_limit_drawing",
    "package": "rate_limit_package",
}


def _parse(spec: str | None, fallback: tuple[int, int]) -> tuple[int, int]:
    """Parse '<count>/<window>' into (count, window_seconds)."""
    try:
        count, window = str(spec).split("/")
        c, w = int(count), int(window)
        if c > 0 and w > 0:
            return c, w
    except (ValueError, AttributeError):
        pass
    return fallback


def _limit_for(category: str) -> tuple[int, int]:
    default = _parse(settings.rate_limit_default, (120, 60))
    attr = _CATEGORY_SETTING.get(category)
    spec = getattr(settings, attr, None) if attr else None
    return _parse(spec, default)


class _SlidingWindow:
    """Thread-safe sliding-window request log keyed by an arbitrary string."""

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def hit(self, key: str, limit: int, window: int) -> tuple[bool, int]:
        """Record a hit. Returns (allowed, retry_after_seconds)."""
        now = time.monotonic()
        cutoff = now - window
        with self._lock:
            dq = self._hits.get(key)
            if dq is None:
                dq = deque()
                self._hits[key] = dq
            while dq and dq[0] <= cutoff:
                dq.popleft()
            if len(dq) >= limit:
                retry = max(1, int(window - (now - dq[0])) + 1)
                return False, retry
            dq.append(now)
            if not dq:  # never, but keeps the map tidy if window==0 edge
                self._hits.pop(key, None)
            return True, 0

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


_store = _SlidingWindow()


def reset_rate_limit() -> None:
    """Clear all counters (used by tests)."""
    _store.reset()


def _client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _identity(request: Request) -> str:
    """Prefer the authenticated user; fall back to client IP."""
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        user_id = decode_access_token(auth[7:].strip())
        if user_id:
            return f"user:{user_id}"
    return f"ip:{_client_ip(request)}"


def _checker(category: str):
    async def dependency(request: Request) -> None:
        if not settings.rate_limit_active():
            return
        limit, window = _limit_for(category)
        identity = _identity(request)
        allowed, retry_after = _store.hit(f"{category}:{identity}", limit, window)
        if not allowed:
            log_event(
                "rate_limited",
                category=category,
                identity_kind=identity.split(":", 1)[0],
                limit=limit,
                window_s=window,
                path=request.url.path,
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    "Rate limit exceeded — too many requests. "
                    f"Please wait {retry_after}s and try again."
                ),
                headers={"Retry-After": str(retry_after)},
            )

    return dependency


def rate_limit(category: str):
    """FastAPI dependency that enforces the limit for ``category``.

    Add to a route via ``dependencies=[rate_limit("create")]``.
    """
    return Depends(_checker(category))
