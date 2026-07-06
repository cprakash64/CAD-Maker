"""Raster preprocessing for Drawing → CAD.

Runs ONCE per uploaded raster, before the provider call and before any CV
analysis (profile tracing, flange detection, segmentation):

1. normalize orientation (apply the EXIF rotation, then drop it),
2. flatten transparency onto white paper (alpha/palette transparency reads as
   BLACK in naive grayscale conversion — the whole sheet would become "ink"),
3. downscale very large scans to ``DRAWING_MAX_IMAGE_SIDE`` (provider tokens +
   CV time; drawings carry no information at 6000px that 1600px loses),
4. crop to the linework bounding box (+ a small margin) so the provider reads
   the drawing, not the whitespace around it.

Also provides ``compress_for_retry`` — the smaller/cheaper re-send used when
the provider times out and one retry is allowed.

Best-effort by contract: any failure returns the input bytes unchanged and the
pipeline continues (preprocessing is an optimization, never a gate).
"""
from __future__ import annotations

import io

from app.observability import log_event

# Keep a little paper around the linework so strokes never touch the border
# (the profile tracer flood-fills the background from the border).
_CROP_MARGIN_PX = 12
# Don't crop when the linework already fills most of the sheet.
_CROP_MIN_SAVING = 0.10
# "Ink" for the crop box: anything meaningfully darker than paper.
_INK_GRAY = 235


def preprocess_drawing_image(image_bytes: bytes,
                             max_side: int | None = None) -> tuple[bytes, list[str]]:
    """Return (cleaned PNG bytes, human-readable notes). Never raises."""
    try:
        return _preprocess(image_bytes, max_side)
    except Exception as exc:  # noqa: BLE001 - preprocessing is best-effort
        log_event("drawing_preprocess_skipped", reason=type(exc).__name__,
                  detail=str(exc)[:200])
        return image_bytes, []


def _preprocess(image_bytes: bytes, max_side: int | None) -> tuple[bytes, list[str]]:
    import numpy as np
    from PIL import Image, ImageOps

    from app.config import settings

    max_side = max_side or settings.drawing_max_image_side
    img = Image.open(io.BytesIO(image_bytes))
    notes: list[str] = []
    changed = False

    oriented = ImageOps.exif_transpose(img)
    if oriented is not img:
        img, changed = oriented, True

    # Transparency → white paper.
    if img.mode in ("RGBA", "LA", "PA") or (
            img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        flat = Image.new("RGB", rgba.size, (255, 255, 255))
        flat.paste(rgba, mask=rgba.split()[-1])
        img, changed = flat, True
        notes.append("Transparent background flattened to white")
    elif img.mode != "RGB":
        img = img.convert("RGB")
        changed = True

    if max(img.size) > max_side:
        f = max_side / max(img.size)
        img = img.resize((max(1, round(img.width * f)),
                          max(1, round(img.height * f))))
        changed = True
        notes.append(f"Large scan downscaled to {max(img.size)}px")

    # Crop to the linework bounding box (+ margin).
    gray = np.asarray(img.convert("L"), dtype=np.uint8)
    ink_rows = (gray < _INK_GRAY).any(axis=1)
    ink_cols = (gray < _INK_GRAY).any(axis=0)
    if ink_rows.any() and ink_cols.any():
        y_idx = np.nonzero(ink_rows)[0]
        x_idx = np.nonzero(ink_cols)[0]
        x0 = max(0, int(x_idx[0]) - _CROP_MARGIN_PX)
        x1 = min(img.width, int(x_idx[-1]) + 1 + _CROP_MARGIN_PX)
        y0 = max(0, int(y_idx[0]) - _CROP_MARGIN_PX)
        y1 = min(img.height, int(y_idx[-1]) + 1 + _CROP_MARGIN_PX)
        if (x1 - x0) * (y1 - y0) < (1 - _CROP_MIN_SAVING) * img.width * img.height:
            img = img.crop((x0, y0, x1, y1))
            changed = True

    if not changed:
        return image_bytes, notes
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    log_event("drawing_preprocessed", width=img.width, height=img.height,
              bytes=buf.tell())
    return buf.getvalue(), notes


def compress_for_retry(image_bytes: bytes, max_side: int = 1000) -> bytes:
    """Smaller re-send for a provider retry after a timeout. Never raises."""
    try:
        out, _ = _preprocess(image_bytes, max_side)
        return out
    except Exception:  # noqa: BLE001
        return image_bytes
