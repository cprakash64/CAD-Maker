"""Detect internal DARK (filled) regions inside a mechanical drawing.

Not every black area is material. In a line drawing a solid-filled interior
region inside the part outline usually marks a RECESS / groove / hollow channel
/ depressed face — not solid metal. The "key looks like one solid silhouette"
field failure is exactly this: the long black channel down the key's shaft was
treated as material instead of a cut.

This finds ink connected components that are FILLED BLOBS (not thin outline
strokes) sitting inside the traced outer profile, and classifies each:

* long + narrow + interior            → ``recessed_channel``
* opens onto / touches the outline    → ``through_channel_cut``
* otherwise a compact filled pocket   → ``blind_recess``

It reuses the pure-numpy enclosure machinery in ``raster_profile`` and never
raises (returns [] on any failure).
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field

from app.observability import log_event

_MAX_WORK_DIM = 900
_INK_THRESHOLD = 100
# A filled region is a BLOB, not a stroke: it fills a good part of its bbox and
# is thicker than an outline stroke.
_MIN_FILL_FRACTION = 0.34
_MIN_THICKNESS_PX = 6
_MIN_AREA_FRACTION = 0.006      # of the part area
_CHANNEL_ASPECT = 2.5


@dataclass
class DarkRegion:
    kind: str                              # recessed_channel / blind_recess / through_channel_cut
    center_px: tuple[float, float]
    bbox: tuple[int, int, int, int]
    area_px: int
    aspect: float
    polygon_img: list[tuple[float, float]] = field(default_factory=list)  # (x,y) img
    touches_edge: bool = False


def detect_internal_dark_regions(image_bytes: bytes) -> list[DarkRegion]:
    """Filled interior dark regions (recesses/channels). Never raises."""
    try:
        return _detect(image_bytes)
    except Exception as exc:  # noqa: BLE001 - best-effort
        log_event("drawing_dark_region_detect_failed", reason=type(exc).__name__)
        return []


def _detect(image_bytes: bytes) -> list[DarkRegion]:
    import numpy as np
    from PIL import Image

    from app.drawing.raster_profile import (
        _connected_regions,
        _flood_from_border,
        _rdp,
        _trace_boundary,
    )

    img = Image.open(io.BytesIO(image_bytes)).convert("L")
    if max(img.size) > _MAX_WORK_DIM:
        f = _MAX_WORK_DIM / max(img.size)
        img = img.resize((max(1, int(img.width * f)), max(1, int(img.height * f))))
    gray = np.asarray(img, dtype=np.uint8)
    ink = gray < _INK_THRESHOLD
    light = ~ink
    background = _flood_from_border(np, light)
    solid = ~background                       # the part's filled footprint
    parts = _connected_regions(np, solid)
    if not parts:
        return []
    part = max(parts, key=lambda r: r["area"])
    px0, py0, px1, py1 = part["bbox"]
    part_area = part["area"]
    pw, ph = px1 - px0 + 1, py1 - py0 + 1

    # Candidate filled regions: ink components that are BLOBS (thick + well
    # filled), inside the part, and clearly smaller than the whole silhouette.
    out: list[DarkRegion] = []
    for r in _connected_regions(np, ink):
        x0, y0, x1, y1 = r["bbox"]
        w, h = x1 - x0 + 1, y1 - y0 + 1
        if not (x0 >= px0 - 2 and y0 >= py0 - 2 and x1 <= px1 + 2 and y1 <= py1 + 2):
            continue                          # outside the part
        if w >= 0.92 * pw and h >= 0.92 * ph:
            continue                          # this IS the outer silhouette/outline
        fill = r["area"] / (w * h)
        if fill < _MIN_FILL_FRACTION or min(w, h) < _MIN_THICKNESS_PX:
            continue                          # a thin outline stroke, not a fill
        if r["area"] < _MIN_AREA_FRACTION * part_area:
            continue
        aspect = max(w / h, h / w)
        edge = int(x0 <= px0 + 3) + int(x1 >= px1 - 3) + int(y0 <= py0 + 3) \
            + int(y1 >= py1 - 3)
        touches = edge >= 1
        contour = _trace_boundary(np, r["mask"])
        poly = _rdp(contour, 1.4) if len(contour) >= 6 else []
        # DEFAULT = a BLIND recess (a depressed surface), NEVER a through cut.
        # A filled dark region inside a part is a recessed/grooved FACE by
        # default; it becomes a through cut ONLY with explicit evidence (a
        # dimensioned "through"/"slot"/"open" callout), which raster pixels don't
        # carry. A long/narrow blind groove is a recessed_channel; a compact one
        # is a blind_recess. Touching the outline is NOT sufficient for "through".
        kind = "recessed_channel" if aspect >= _CHANNEL_ASPECT else "blind_recess"
        log_event("drawing_dark_region_classified", kind=kind,
                  reason="internal_dark_region_default", aspect=round(aspect, 2),
                  touches_edge=touches)
        out.append(DarkRegion(
            kind=kind, center_px=((x0 + x1) / 2, (y0 + y1) / 2),
            bbox=(x0, y0, x1, y1), area_px=int(r["area"]), aspect=round(aspect, 2),
            polygon_img=[(float(px), float(py)) for px, py in poly],
            touches_edge=touches))
    if out:
        log_event("drawing_dark_regions_detected", count=len(out),
                  kinds=[d.kind for d in out])
    return out


__all__ = ["DarkRegion", "detect_internal_dark_regions"]
