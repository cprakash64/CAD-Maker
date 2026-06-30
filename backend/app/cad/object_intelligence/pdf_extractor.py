"""Extract text from a fetched datasheet / mechanical-drawing PDF (best effort).

Uses ``pypdf`` if available; otherwise returns "" (extraction unavailable → the
pipeline falls back to an honest REVIEW/clarify rather than guessing). Only TEXT is
read — no CAD geometry is copied.
"""
from __future__ import annotations


def pdf_to_text(raw: bytes, *, max_pages: int = 20) -> str:
    if not raw:
        return ""
    try:
        import io

        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(raw))
        parts = []
        for page in reader.pages[:max_pages]:
            try:
                parts.append(page.extract_text() or "")
            except Exception:  # noqa: BLE001
                continue
        return "\n".join(parts)
    except Exception:  # noqa: BLE001 — pypdf missing / unparseable ⇒ no text
        return ""


def dimension_snippets(raw: bytes, *, max_snippets: int = 40) -> list[str]:
    from app.cad.object_intelligence.html_extractor import dimension_snippets as _snip

    return _snip(pdf_to_text(raw), max_snippets=max_snippets)
