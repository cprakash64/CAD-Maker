"""Trusted-source search for object dimensions (bounded, optional, cached).

Resolution order is local-first: the resolver only reaches here when there is NO
local curated preset. This module is the seam where a real implementation would
query trusted sources — official manufacturer datasheets / mechanical drawings /
STEP metadata, official GitHub hardware repos, reputable distributor datasheets —
RANK them (see ``source_ranker``) and extract dimensions (see ``spec_extractor``).

It is intentionally:
  * **bounded** — every call takes a hard ``timeout_s`` (default short) so a slow or
    unreachable network can never hang a generation for minutes (the 180 s bug);
  * **offline-safe** — with no network/key configured (the default, and all tests)
    it returns ``[]`` immediately, so the resolver falls back to an honest
    REVIEW/CONCEPT/clarify rather than guessing;
  * **cache-fronted** — the resolver caches any extracted spec so a repeat lookup
    never hits the network again.

LEGAL: only dimensions are extracted and stored. Proprietary CAD/STEP files are
never scraped, cached, or redistributed unless the licence explicitly allows it.
"""
from __future__ import annotations

from dataclasses import dataclass

# Wall-clock ceiling for a single source lookup. Kept small on purpose; a slow
# lookup yields a fast REVIEW/clarification, never a long block.
DEFAULT_TIMEOUT_S = 6.0


@dataclass
class SourceHit:
    url: str
    title: str
    official: bool
    kind: str            # datasheet | mechanical_drawing | step | github | distributor
    license_ok: bool     # may we use/redistribute? (only matters for files)


def web_search_enabled() -> bool:
    """Whether live source search is configured. Off by default (and in tests) so
    generation is fully deterministic and offline."""
    from app.config import settings

    return bool(getattr(settings, "object_intelligence_web_search", False))


def search_sources(query: str, *, timeout_s: float = DEFAULT_TIMEOUT_S) -> list[SourceHit]:
    """Return candidate trusted sources for an object, ranked best-first, or ``[]``.

    No-op unless web search is explicitly enabled AND a provider is wired in. This
    keeps known/local objects free of any network call and guarantees a fast,
    honest fallback for unknown objects."""
    if not web_search_enabled():
        return []
    # A real provider would populate hits here within ``timeout_s`` and hand them to
    # the ranker. Left unconnected by default (returns nothing → honest fallback).
    return []
