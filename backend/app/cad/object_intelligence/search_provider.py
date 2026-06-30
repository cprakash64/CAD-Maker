"""Pluggable web-search provider abstraction for source-backed dimension lookup.

Behind ``OBJECT_INTELLIGENCE_WEB_SEARCH=true`` + a provider key. Supports a small
set of real providers (SerpApi / Bing / Brave / Tavily) via a common interface;
without a configured provider it is a no-op (returns ``[]``), so generation stays
fully offline and deterministic by default. Every call is BOUNDED (max results +
timeout) so it can never block CAD generation.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

MAX_RESULTS = 5
DEFAULT_TIMEOUT_S = 8.0


@dataclass
class SearchResult:
    url: str
    title: str
    snippet: str = ""


class SearchProvider:
    name = "none"

    def search(self, query: str, *, max_results: int = MAX_RESULTS,
               timeout_s: float = DEFAULT_TIMEOUT_S) -> list[SearchResult]:
        raise NotImplementedError


class _HttpJsonProvider(SearchProvider):
    """Shared base for HTTP/JSON search APIs (SerpApi, Bing, Brave, Tavily). The
    concrete subclass maps the provider's response JSON to ``SearchResult``s."""

    endpoint = ""
    env_key = ""

    def _params(self, query: str, max_results: int) -> dict:
        raise NotImplementedError

    def _parse(self, data: dict) -> list[SearchResult]:
        raise NotImplementedError

    def search(self, query, *, max_results=MAX_RESULTS, timeout_s=DEFAULT_TIMEOUT_S):
        key = os.environ.get(self.env_key)
        if not key:
            return []
        try:
            import httpx

            r = httpx.get(self.endpoint, params=self._params(query, max_results),
                          timeout=timeout_s)
            r.raise_for_status()
            return self._parse(r.json())[:max_results]
        except Exception:  # noqa: BLE001 — any provider/network error ⇒ no results
            return []


class TavilyProvider(_HttpJsonProvider):
    name = "tavily"
    endpoint = "https://api.tavily.com/search"
    env_key = "TAVILY_API_KEY"

    def _params(self, query, max_results):
        return {"api_key": os.environ.get(self.env_key), "query": query,
                "max_results": max_results}

    def _parse(self, data):
        return [SearchResult(r.get("url", ""), r.get("title", ""), r.get("content", ""))
                for r in (data.get("results") or [])]


class BraveProvider(_HttpJsonProvider):
    name = "brave"
    endpoint = "https://api.search.brave.com/res/v1/web/search"
    env_key = "BRAVE_API_KEY"

    def _params(self, query, max_results):
        return {"q": query, "count": max_results}

    def _parse(self, data):
        web = (data.get("web") or {}).get("results") or []
        return [SearchResult(r.get("url", ""), r.get("title", ""),
                             r.get("description", "")) for r in web]


_PROVIDERS = {p.name: p for p in (TavilyProvider(), BraveProvider())}


def get_search_provider() -> SearchProvider | None:
    """The configured provider, or None when web search is disabled/unconfigured."""
    from app.config import settings

    if not getattr(settings, "object_intelligence_web_search", False):
        return None
    name = os.environ.get("OBJECT_INTELLIGENCE_SEARCH_PROVIDER", "tavily").lower()
    return _PROVIDERS.get(name)
