"""View segmentation for mixed-layout raster drawings.

Real drawing sheets mix dimensioned orthographic/section views with a RENDERED
isometric preview. The render is the worst possible dimension source (foreshortened,
shaded, no annotations) yet it visually dominates — vision models anchor on it.

``prioritize_technical_views`` splits the sheet into regions along white
gutters (projection profiles), classifies each region as LINE ART (thin dark
strokes on white — the dimensioned views) or RENDER (large shaded/midtone or
saturated areas), and recomposes a sheet containing only the technical views.
Deliberately conservative: if segmentation finds nothing to drop, produces one
region, or would drop too much ink, the ORIGINAL image is returned unchanged —
this stage can only remove a misleading render, never lose a dimensioned view.
"""
from __future__ import annotations

import io
from dataclasses import dataclass

from app.observability import log_event

# A region is a RENDER when more than this fraction of its ink is midtone
# shading (line art is dark strokes + white paper, almost no midtones).
_RENDER_MIDTONE_FRACTION = 0.55
# Only drop renders when the surviving technical views carry real content: a
# filled render always dominates raw ink counts (area vs thin strokes), so the
# safety guard is on what REMAINS, not on what is dropped.
_MIN_KEEPER_INK_PX = 1200
_MIN_KEEPER_INK_FRACTION = 0.02
# Gutter detection: a row/column is "blank" when fewer than this fraction of
# its pixels are inked.
_BLANK_ROW_FRACTION = 0.004
_MIN_GUTTER_PX = 12
_MIN_REGION_PX = 48


@dataclass
class SheetRegion:
    x0: int
    y0: int
    x1: int
    y1: int
    ink: float           # inked-pixel count
    midtone_fraction: float
    is_render: bool


def prioritize_technical_views(image_bytes: bytes) -> tuple[bytes, list[str]]:
    """Return (possibly recomposed) image bytes + human-readable notes.

    On ANY failure (missing PIL/numpy, undecodable image, degenerate layout)
    returns the input unchanged — segmentation is an optimization, never a
    gate."""
    try:
        return _segment(image_bytes)
    except Exception as exc:  # noqa: BLE001 - never block interpretation
        log_event("drawing_segment_skipped", reason=type(exc).__name__,
                  detail=str(exc)[:200])
        return image_bytes, []


def _segment(image_bytes: bytes) -> tuple[bytes, list[str]]:
    import numpy as np
    from PIL import Image

    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    rgb = np.asarray(img, dtype=np.int16)
    gray = rgb.mean(axis=2)
    # Saturation-ish signal: colored renders differ across channels.
    chroma = rgb.max(axis=2) - rgb.min(axis=2)
    ink = (gray < 235) | (chroma > 24)  # anything that isn't near-white paper

    regions = _split_regions(np, ink)
    if len(regions) < 2:
        return image_bytes, []

    total_ink = float(ink.sum()) or 1.0
    out: list[SheetRegion] = []
    for x0, y0, x1, y1 in regions:
        sub_ink = ink[y0:y1, x0:x1]
        n = float(sub_ink.sum())
        if n < _MIN_REGION_PX:
            continue
        sub_gray = gray[y0:y1, x0:x1]
        sub_chroma = chroma[y0:y1, x0:x1]
        midtone = ((sub_gray > 90) & (sub_gray < 215) & sub_ink) | \
                  ((sub_chroma > 24) & sub_ink)
        frac = float(midtone.sum()) / n
        out.append(SheetRegion(x0, y0, x1, y1, ink=n, midtone_fraction=frac,
                               is_render=frac >= _RENDER_MIDTONE_FRACTION))

    renders = [r for r in out if r.is_render]
    keepers = [r for r in out if not r.is_render]
    if not renders or not keepers:
        return image_bytes, []
    keeper_ink = sum(r.ink for r in keepers)
    if keeper_ink < _MIN_KEEPER_INK_PX or keeper_ink / total_ink < _MIN_KEEPER_INK_FRACTION:
        return image_bytes, []  # too little would survive — classification unsafe

    # Recompose: white sheet with only the technical views, in place.
    canvas = Image.new("RGB", img.size, (255, 255, 255))
    for r in keepers:
        canvas.paste(img.crop((r.x0, r.y0, r.x1, r.y1)), (r.x0, r.y0))
    buf = io.BytesIO()
    canvas.save(buf, format="PNG")
    notes = [f"Ignored {len(renders)} rendered preview region(s) on the sheet — "
             "dimensions are read from the technical views only"]
    log_event("drawing_segmented", regions=len(out), dropped=len(renders))
    return buf.getvalue(), notes


def _split_regions(np, ink) -> list[tuple[int, int, int, int]]:
    """Recursive projection-profile split along blank gutters (columns first,
    then rows, one more column pass), returning (x0, y0, x1, y1) regions."""

    def spans(mask_1d, min_gap: int, limit: float) -> list[tuple[int, int]]:
        """Inked spans along one axis, separated by >= min_gap blank cells."""
        blank = mask_1d <= limit
        spans_out: list[tuple[int, int]] = []
        start = None
        gap = 0
        for i, b in enumerate(blank):
            if b:
                gap += 1
                if start is not None and gap >= min_gap:
                    spans_out.append((start, i - gap + 1))
                    start = None
            else:
                if start is None:
                    start = i
                gap = 0
        if start is not None:
            spans_out.append((start, len(blank)))
        return spans_out

    def split(x0, y0, x1, y1, axis, depth) -> list[tuple[int, int, int, int]]:
        if depth <= 0:
            return [(x0, y0, x1, y1)]
        sub = ink[y0:y1, x0:x1]
        if axis == "x":
            profile = sub.sum(axis=0)
            limit = _BLANK_ROW_FRACTION * (y1 - y0)
        else:
            profile = sub.sum(axis=1)
            limit = _BLANK_ROW_FRACTION * (x1 - x0)
        parts = spans(profile, _MIN_GUTTER_PX, limit)
        if len(parts) <= 1:
            parts = [(0, len(profile))]
        regions = []
        for a, b in parts:
            if axis == "x":
                regions.extend(split(x0 + a, y0, x0 + b, y1, "y", depth - 1))
            else:
                regions.extend(split(x0, y0 + a, x1, y0 + b, "x", depth - 1))
        return regions

    h, w = ink.shape
    return split(0, 0, w, h, "x", 3)
