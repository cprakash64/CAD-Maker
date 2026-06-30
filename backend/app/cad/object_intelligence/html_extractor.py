"""Extract mechanical-dimension text from a fetched HTML document.

Strips tags to readable text and pulls dimension-bearing snippets (length/width/
height, mounting hole pitch/diameter, connector mentions). The numeric extraction
into a structured spec is done by :mod:`extraction_pipeline` (optionally with an
LLM acting ONLY on this retrieved text — never inventing numbers).
"""
from __future__ import annotations

import re

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
_DIM_HINT = re.compile(
    r"(length|width|height|depth|thickness|mounting|hole|pitch|diameter|"
    r"\bmm\b|\bØ\b|connector|usb|hdmi|ethernet|pcb|board)", re.I)


def html_to_text(raw: bytes) -> str:
    try:
        s = raw.decode("utf-8", errors="ignore")
    except Exception:  # noqa: BLE001
        return ""
    s = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", s, flags=re.I | re.S)
    s = _TAG.sub(" ", s)
    return _WS.sub(" ", s).strip()


def dimension_snippets(text: str, *, max_snippets: int = 40) -> list[str]:
    """Sentence-ish fragments that mention a dimension — the input GPT/extractor
    reads (so it extracts from sources rather than inventing numbers)."""
    out: list[str] = []
    for frag in re.split(r"(?<=[.;])\s+|\n", text):
        if _DIM_HINT.search(frag) and re.search(r"\d", frag):
            f = frag.strip()[:240]
            if f:
                out.append(f)
            if len(out) >= max_snippets:
                break
    return out
