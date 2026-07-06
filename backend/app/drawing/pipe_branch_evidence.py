"""Deterministic side-branch topology detection for pipe/flange drawings.

A flanged pipe BRANCH / TEE (main pipe + a perpendicular side outlet + a third
flange) must never be built as a straight two-flange pipe SPOOL. Vision models
frequently label a branch sheet "flanged_pipe_spool" (they see circular
flanges); this module reads the SHEET LAYOUT for the topology cues that separate
a branch from a straight spool, before/independent of any LLM:

* a flange FACE view (a ring of ≥6 bolt circles) — reuses ``detect_flanged_branch``;
* multiple PIPE-ELEVATION views (tall/wide thin rectangles = pipe side runs) —
  a branch draws the main run AND the branch as separate elevations;
* an ISOMETRIC preview whose silhouette is COMPACT (extent in two directions,
  low solidity) rather than one long straight tube — the strongest single
  "this is an L/T assembly" signal (the drawing shows the branch to make the
  3D topology unambiguous);
* ≥2 distinct circular flange faces across views (multi-outlet fitting).

Text/vision cues ("branch", "tee", "nozzle", "outlet", "section A-A") corroborate
but are not required. Everything here is pixel analysis; it never raises.
"""
from __future__ import annotations

import io
import math
import re
from dataclasses import dataclass, field

from app.observability import log_event

_MIN_REGION_INK = 120
_MIN_SIDE = 20
# A technical view is a "pipe elevation" when it's a clearly elongated thin
# rectangle (a pipe run drawn side-on).
_ELEVATION_ASPECT = 2.2
# Render silhouette is "compact" (branch-like) below this elongation and above
# this off-axis extent ratio; a straight tube is far more elongated.
_ISO_COMPACT_ELONG = 1.9
_ISO_COMPACT_MINOR = 0.42

_BRANCH_TEXT_RE = re.compile(
    r"\bbranch\b|\btee\b|\bt[- ]?fitting\b|\bnozzle\b|\bside\s+outlet\b|"
    r"\bside\s+branch\b|\bside\s+flange\b|\bsection\s+a[- ]?a\b|\boutlet\b", re.I)
_SPOOL_TEXT_RE = re.compile(
    r"\bstraight\s+(?:pipe|spool)\b|\bspool\s+piece\b|\bno\s+branch\b", re.I)


@dataclass
class BranchTopology:
    side_branch: bool
    flange_faces: int
    pipe_elevations: int
    iso_present: bool
    iso_compact: bool
    reason: str
    notes: list[str] = field(default_factory=list)


def has_branch_text_cue(*texts: str | None) -> bool:
    return any(t and _BRANCH_TEXT_RE.search(t) for t in texts)


def has_straight_spool_text_cue(*texts: str | None) -> bool:
    joined = " ".join(t for t in texts if t)
    return bool(_SPOOL_TEXT_RE.search(joined)) and not _BRANCH_TEXT_RE.search(joined)


def detect_side_branch(image_bytes: bytes,
                       text_cues: str | None = None) -> BranchTopology | None:
    """Classify the sheet's pipe topology. Returns a ``BranchTopology`` (with
    ``side_branch`` set) or None when nothing pipe-like is found. Never raises."""
    try:
        return _detect(image_bytes, text_cues)
    except Exception as exc:  # noqa: BLE001 - topology detection is best-effort
        log_event("drawing_pipe_branch_evidence_failed",
                  reason=type(exc).__name__, detail=str(exc)[:200])
        return None


def _detect(image_bytes: bytes, text_cues: str | None) -> BranchTopology | None:
    import numpy as np
    from PIL import Image

    from app.drawing.raster_profile import _connected_regions, _flood_from_border
    from app.drawing.segment import _split_regions
    from app.services.raster_flanged_pipe_branch import _flange_face_in_view

    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    rgb = np.asarray(img, dtype=np.int16)
    gray = rgb.mean(axis=2)
    chroma = rgb.max(axis=2) - rgb.min(axis=2)
    ink = gray < 100
    render = ((gray > 90) & (gray < 215)) | (chroma > 24)

    boxes = _split_regions(np, ink | render)
    flange_faces = 0
    pipe_elevations = 0
    iso_present = False
    iso_compact = False
    for x0, y0, x1, y1 in boxes:
        w, h = x1 - x0, y1 - y0
        if w < _MIN_SIDE or h < _MIN_SIDE:
            continue
        sub_ink = ink[y0:y1, x0:x1]
        n_ink = int(sub_ink.sum())
        if n_ink < _MIN_REGION_INK:
            continue
        sub_render = render[y0:y1, x0:x1]
        is_render = int(sub_render.sum()) > 1.2 * n_ink
        if is_render:
            iso_present = True
            if _silhouette_is_compact(np, ink[y0:y1, x0:x1] | sub_render):
                iso_compact = True
            continue
        aspect = w / h if h else 1.0
        if max(aspect, 1 / aspect) >= _ELEVATION_ASPECT:
            pipe_elevations += 1
        elif _flange_face_in_view(np, sub_ink, _flood_from_border,
                                  _connected_regions) is not None:
            flange_faces += 1

    if flange_faces == 0 and pipe_elevations == 0 and not iso_present:
        return None  # nothing pipe-like on the sheet

    text_branch = has_branch_text_cue(text_cues)
    text_spool = has_straight_spool_text_cue(text_cues)

    # Decision. Text branch cue is decisive; otherwise pixel topology:
    #  * ≥2 flange faces = a multi-outlet fitting (branch/tee);
    #  * a COMPACT isometric preview alongside any pipe/flange view = an L/T
    #    assembly (a straight spool's isometric is one long tube);
    #  * ≥2 separate pipe elevations = a main run drawn with a branch run.
    reason = "no_branch_detected"
    side_branch = False
    if text_branch:
        side_branch, reason = True, "branch_text_cue"
    elif text_spool:
        side_branch, reason = False, "straight_spool_text_cue"
    elif flange_faces >= 2:
        side_branch, reason = True, "multiple_flange_faces"
    elif iso_compact and (flange_faces >= 1 or pipe_elevations >= 1):
        side_branch, reason = True, "compact_isometric_side_branch"
    elif pipe_elevations >= 3:
        side_branch, reason = True, "multiple_pipe_elevations"

    topo = BranchTopology(
        side_branch=side_branch, flange_faces=flange_faces,
        pipe_elevations=pipe_elevations, iso_present=iso_present,
        iso_compact=iso_compact, reason=reason)
    log_event("drawing_pipe_branch_evidence",
              side_branch=side_branch, reason=reason,
              flange_faces=flange_faces, pipe_elevations=pipe_elevations,
              iso_present=iso_present, iso_compact=iso_compact)
    return topo


def _silhouette_is_compact(np, mask) -> bool:
    """A render silhouette is 'compact' (a branched L/T assembly) when its
    foreground has real extent in BOTH principal directions — a straight pipe
    isometric is one long, thin tube (highly elongated, tiny minor extent)."""
    ys, xs = np.nonzero(mask)
    if len(xs) < 50:
        return False
    pts = np.stack([xs.astype(float), ys.astype(float)])
    pts -= pts.mean(axis=1, keepdims=True)
    cov = np.cov(pts)
    evals, evecs = np.linalg.eigh(cov)
    lo, hi = float(evals.min()), float(evals.max())
    elong = math.sqrt(hi / max(lo, 1e-6))
    proj_major = pts.T @ evecs[:, int(np.argmax(evals))]
    proj_minor = pts.T @ evecs[:, int(np.argmin(evals))]
    major_extent = proj_major.max() - proj_major.min()
    minor_extent = proj_minor.max() - proj_minor.min()
    ratio = minor_extent / major_extent if major_extent else 0.0
    return elong <= _ISO_COMPACT_ELONG and ratio >= _ISO_COMPACT_MINOR
