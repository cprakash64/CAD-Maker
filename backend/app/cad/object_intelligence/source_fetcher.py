"""Bounded document fetcher for source-backed extraction.

Fetches at most a couple of candidate documents (HTML or PDF) with a hard size +
time bound so extraction can never block CAD generation. Offline-safe: it only runs
when web search is enabled and ``httpx`` is available.

LEGAL: only the document's TEXT/dimensions are read for extraction; proprietary CAD
files (STEP/STL) are never downloaded, cached, or rehosted as LunaiCAD output.
"""
from __future__ import annotations

from dataclasses import dataclass

MAX_DOCUMENTS = 2
MAX_BYTES = 8 * 1024 * 1024     # 8 MB cap per document
DEFAULT_TIMEOUT_S = 8.0
# CAD files we deliberately do NOT fetch (use datasheet dimensions, not their CAD).
_BLOCKED_SUFFIXES = (".step", ".stp", ".stl", ".sldprt", ".igs", ".iges", ".x_t")


@dataclass
class FetchedDocument:
    url: str
    content_type: str           # "pdf" | "html"
    text: str = ""              # extracted text (filled by the extractors)
    raw: bytes = b""


def _is_blocked(url: str) -> bool:
    u = url.lower().split("?")[0]
    return u.endswith(_BLOCKED_SUFFIXES)


def fetch_document(url: str, *, timeout_s: float = DEFAULT_TIMEOUT_S
                   ) -> FetchedDocument | None:
    """Fetch one document, bounded by time + size, skipping proprietary CAD files.
    Returns None on any error / disallowed type."""
    if _is_blocked(url):
        return None
    try:
        import httpx

        with httpx.stream("GET", url, timeout=timeout_s, follow_redirects=True) as r:
            r.raise_for_status()
            ctype = r.headers.get("content-type", "").lower()
            kind = "pdf" if ("pdf" in ctype or url.lower().endswith(".pdf")) else "html"
            buf = bytearray()
            for chunk in r.iter_bytes():
                buf.extend(chunk)
                if len(buf) > MAX_BYTES:
                    break
            return FetchedDocument(url=url, content_type=kind, raw=bytes(buf))
    except Exception:  # noqa: BLE001 — network/parse error ⇒ no document
        return None
