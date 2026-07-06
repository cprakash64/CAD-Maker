"""View segmentation: rendered isometric previews are dropped from mixed-layout
sheets so dimensions come from the technical views; pure line-art sheets pass
through untouched."""
from __future__ import annotations

import io

import numpy as np
import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image, ImageDraw  # noqa: E402

from app.drawing.segment import prioritize_technical_views  # noqa: E402


def _ink(img_bytes: bytes, x0: int, x1: int) -> int:
    arr = np.asarray(Image.open(io.BytesIO(img_bytes)).convert("L"))
    return int((arr[:, x0:x1] < 235).sum())


def _line_art_view(d: ImageDraw.ImageDraw, x: int) -> None:
    d.rectangle([x, 80, x + 240, 320], outline="black", width=2)
    d.line([x, 200, x + 240, 200], fill="black", width=1)
    d.text((x + 40, 40), "14.8", fill="black")


def _render_blob(d: ImageDraw.ImageDraw, x: int) -> None:
    for i in range(200):
        g = 100 + int(i * 0.5)
        d.ellipse([x + i * 0.2, 100 + i * 0.2, x + 290 - i * 0.2, 340 - i * 0.2],
                  fill=(g, g - 20, g - 40))


def test_render_region_dropped_technical_view_kept():
    img = Image.new("RGB", (800, 400), "white")
    d = ImageDraw.Draw(img)
    _line_art_view(d, 60)
    _render_blob(d, 500)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    out, notes = prioritize_technical_views(buf.getvalue())
    assert notes and "rendered preview" in notes[0].lower() or "Ignored" in notes[0]
    assert _ink(out, 0, 400) > 1000, "technical view must survive"
    assert _ink(out, 400, 800) == 0, "render must be removed"


def test_multiple_technical_views_all_survive():
    img = Image.new("RGB", (1200, 400), "white")
    d = ImageDraw.Draw(img)
    _line_art_view(d, 60)
    _line_art_view(d, 420)
    _render_blob(d, 850)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    out, notes = prioritize_technical_views(buf.getvalue())
    assert notes
    assert _ink(out, 0, 350) > 1000
    assert _ink(out, 350, 700) > 1000
    assert _ink(out, 800, 1200) == 0


def test_pure_line_art_untouched():
    img = Image.new("RGB", (400, 400), "white")
    d = ImageDraw.Draw(img)
    d.rectangle([50, 50, 350, 350], outline="black", width=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    out, notes = prioritize_technical_views(buf.getvalue())
    assert out == buf.getvalue() and notes == []


def test_garbage_bytes_pass_through():
    out, notes = prioritize_technical_views(b"not an image at all")
    assert out == b"not an image at all" and notes == []
