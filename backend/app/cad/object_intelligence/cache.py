"""Local on-disk cache for source-extracted object specs.

Once an object's dimensions are extracted from a (slow) web/datasheet lookup, the
structured spec is cached here so the SAME object never triggers a repeat web/LLM
call — repeated generations are fast and offline. Local curated presets bypass the
cache entirely (they are already instant). The cache stores only EXTRACTED
DIMENSIONS we are permitted to keep (never proprietary CAD files).
"""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path

from app.cad.object_intelligence.mechanical_spec import MechanicalObjectSpec

_CACHE_DIR = Path(__file__).resolve().parent / "_cache"
_LOCK = threading.Lock()


def _key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_") or "object"


def cache_path(name: str) -> Path:
    return _CACHE_DIR / f"{_key(name)}.json"


def get(name: str) -> MechanicalObjectSpec | None:
    p = cache_path(name)
    try:
        if not p.exists():
            return None
        return MechanicalObjectSpec.from_dict(json.loads(p.read_text()))
    except Exception:  # noqa: BLE001 — a corrupt cache entry is simply ignored
        return None


def put(name: str, spec: MechanicalObjectSpec) -> None:
    try:
        with _LOCK:
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache_path(name).write_text(json.dumps(spec.to_dict(), indent=2))
    except Exception:  # noqa: BLE001 — caching is best-effort, never fatal
        pass


def has(name: str) -> bool:
    return cache_path(name).exists()
