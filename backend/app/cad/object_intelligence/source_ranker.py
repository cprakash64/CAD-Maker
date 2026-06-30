"""Rank candidate sources by trust (official manufacturer first, GPT last)."""
from __future__ import annotations

from app.cad.object_intelligence.mechanical_spec import (
    SOURCE_OFFICIAL,
    SOURCE_WEB,
)
from app.cad.object_intelligence.source_search import SourceHit

# Trusted-source priority (higher = preferred).
_KIND_PRIORITY = {
    "mechanical_drawing": 5,
    "step": 5,
    "datasheet": 4,
    "github": 3,
    "distributor": 2,
}


def rank(hits: list[SourceHit]) -> list[SourceHit]:
    """Order hits best-first: official before non-official, then by document kind."""
    return sorted(
        hits,
        key=lambda h: (h.official, _KIND_PRIORITY.get(h.kind, 0)),
        reverse=True,
    )


def source_type_for(hit: SourceHit) -> str:
    """The dimension trust level a hit yields once extracted."""
    return SOURCE_OFFICIAL if hit.official else SOURCE_WEB
