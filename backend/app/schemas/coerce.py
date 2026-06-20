"""Numeric coercion helpers for tolerant LLM/vision input.

Vision and LLM output often puts numbers inside strings ("14.8", "Ø12",
"approx 90mm", "M6"). These helpers extract a usable float so one messy field
never rejects an otherwise valid spec. Geometry safety is still enforced later
by the strict template/feature-graph validators.
"""
from __future__ import annotations

import re

_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


def to_float(value) -> float | None:
    """Best-effort float from int/float/str; returns None if nothing usable."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        f = float(value)
        return f if _finite(f) else None
    if isinstance(value, str):
        m = _NUM_RE.search(value.replace(",", ""))
        if m:
            try:
                f = float(m.group(0))
                return f if _finite(f) else None
            except ValueError:
                return None
    return None


def coerce_float_map(value) -> dict[str, float]:
    """Coerce a dict's values to floats, dropping any that can't be parsed."""
    if not isinstance(value, dict):
        return {}
    out: dict[str, float] = {}
    for k, v in value.items():
        f = to_float(v)
        if f is not None:
            out[str(k)] = f
    return out


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _finite(f: float) -> bool:
    return f == f and f not in (float("inf"), float("-inf"))
