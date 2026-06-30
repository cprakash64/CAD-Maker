"""Source-backed mechanical-spec extraction pipeline (bounded, cached, offline-safe).

Orchestrates: search (≤5 results) → rank official-first → fetch (≤2 docs) → extract
dimension snippets → structured :class:`MechanicalObjectSpec`. The whole pass is
bounded by a wall-clock budget so a slow/unreachable network yields a fast
REVIEW/clarification, never a multi-minute block. Results are cached by normalized
name so a repeated prompt never re-hits the web.

GPT is given the RETRIEVED source snippets to extract from — it must not invent
manufacturing dimensions. When no provider/extractor is wired (the default), the
pipeline returns ``None`` and the resolver clarifies honestly.

LEGAL: stores citation metadata + extracted dimensions only; never proprietary CAD.
"""
from __future__ import annotations

import time

from app.cad.object_intelligence import cache
from app.cad.object_intelligence.mechanical_spec import (
    SOURCE_OFFICIAL,
    SOURCE_WEB,
    MechanicalObjectSpec,
)
from app.cad.object_intelligence.search_provider import (
    SearchResult,
    get_search_provider,
)

# Hard ceiling for the whole extraction pass (search + fetch + extract).
TOTAL_BUDGET_S = 15.0
MAX_DOCS = 2

_OFFICIAL_HINTS = ("raspberrypi.com", "arduino.cc", "nvidia.com", "espressif.com",
                   "developer.nvidia.com", "datasheet", "/mechanical", "github.com")


def _rank(results: list[SearchResult]) -> list[SearchResult]:
    def score(r: SearchResult) -> int:
        u = (r.url or "").lower()
        return sum(2 if h in u else 0 for h in _OFFICIAL_HINTS)
    return sorted(results, key=score, reverse=True)


def _is_official(url: str) -> bool:
    u = (url or "").lower()
    return any(h in u for h in _OFFICIAL_HINTS[:6])


def extract_object_spec(object_name: str, *, budget_s: float = TOTAL_BUDGET_S
                        ) -> MechanicalObjectSpec | None:
    """Resolve a structured spec for an unknown object from trusted sources, or None.

    Cache-first; then (only if web search is enabled + a provider is configured)
    a bounded search→fetch→extract pass. Always returns within ``budget_s``."""
    cached = cache.get(object_name)
    if cached is not None:
        return cached

    provider = get_search_provider()
    if provider is None:
        return None  # web search disabled/unconfigured → resolver clarifies

    deadline = time.monotonic() + budget_s
    try:
        results = provider.search(f"{object_name} mechanical dimensions datasheet",
                                  max_results=5,
                                  timeout_s=max(1.0, deadline - time.monotonic()))
    except Exception:  # noqa: BLE001
        results = []
    if not results:
        return None

    from app.cad.object_intelligence import html_extractor, pdf_extractor
    from app.cad.object_intelligence.source_fetcher import fetch_document

    snippets: list[str] = []
    source_urls: list[str] = []
    official = False
    for r in _rank(results)[:MAX_DOCS]:
        if time.monotonic() >= deadline:
            break
        doc = fetch_document(r.url, timeout_s=max(1.0, deadline - time.monotonic()))
        if doc is None:
            continue
        if doc.content_type == "pdf":
            snippets += pdf_extractor.dimension_snippets(doc.raw)
        else:
            snippets += html_extractor.dimension_snippets(html_extractor.html_to_text(doc.raw))
        source_urls.append(r.url)
        official = official or _is_official(r.url)

    if not snippets:
        return None

    spec = _extract_from_snippets(object_name, snippets, source_urls, official)
    if spec is not None:
        cache.put(object_name, spec)   # cache so repeats never re-hit the web
    return spec


def _extract_from_snippets(object_name: str, snippets: list[str],
                           source_urls: list[str], official: bool
                           ) -> MechanicalObjectSpec | None:
    """Turn retrieved dimension snippets into a structured spec. A real impl hands
    the snippets to an LLM with an EXTRACT-ONLY instruction (no invented numbers) or
    a regex parser; both must populate ``confidence_score`` from how complete the
    extraction was. Unconnected by default → None (honest clarify)."""
    _ = (object_name, snippets)
    if not source_urls:
        return None
    # No extractor wired: do not fabricate dimensions. (A connected extractor would
    # build the spec here with source_type below and a measured confidence.)
    _src = SOURCE_OFFICIAL if official else SOURCE_WEB
    return None
