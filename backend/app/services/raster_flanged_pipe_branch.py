"""Deterministic / hybrid raster interpretation for flanged pipe branch drawings.

A flanged-fitting drawing sheet has an unmistakable PIXEL signature that needs
no LLM: a plan view whose closed ink loops enclose a ring of ≥6 similar small
circles (the bolt pattern) around a central circular region (the bore) inside a
round flange face. This module

* **detects** that signature (``detect_flanged_branch``) by running the same
  border-flood enclosure analysis the profile tracer uses, per sheet view —
  BEFORE any vision call, in well under a second. A shaded isometric preview is
  ignored (render regions are visual context, never dimension sources);
* **extracts** the family parameters in pixel RATIOS (bolt count, bolt Ø/PCD/
  bore/flange-OD relative sizes) from the plan view and the main-run aspect
  from the tallest orthographic view;
* **assembles** a ``DrawingInterpretationSpec`` for the existing deterministic
  pipe-branch route, resolving absolute scale by priority

      user notes  >  vision dimensions (decimal-normalized)  >  assumed OD,

  so an OpenAI timeout degrades to a correctly-SHAPED model flagged REVIEW —
  never a failure, and never a fabricated generic part;
* **parses user notes** ("flanged pipe branch, 12 bolt holes, flange OD 14.8,
  bore 10, height 15, wall 0.5") into the same spec — the notes fast path that
  bypasses vision entirely.
"""
from __future__ import annotations

import io
import math
import re
from dataclasses import dataclass, field

from app.observability import log_event
from app.schemas.drawing_spec import (
    DrawingHoleCalloutSpec,
    DrawingInterpretationSpec,
)

# Detection thresholds.
_MIN_BOLT_CIRCLES = 6
_MAX_BOLT_CIRCLES = 64
_CIRCLE_FILL_MIN = 0.55      # region area / bbox area (perfect circle = π/4 ≈ 0.785)
_CIRCLE_ASPECT_TOL = 0.45    # |1 - w/h| tolerance for "round"
_RING_RADIUS_TOL = 0.18      # bolt centers' distance-from-center spread
_BOLT_AREA_TOL = 0.5         # bolt regions' area spread around the median
_MAX_WORK_DIM = 900

# Assumed flange OD (mm) when neither notes nor vision provide any absolute
# dimension — structurally correct model, clearly flagged, REVIEW status.
DEFAULT_FLANGE_OD_MM = 120.0


@dataclass
class BranchDetection:
    """Pixel evidence of a flanged pipe branch on the sheet (ratios only)."""

    confidence: float
    bolt_count: int
    flange_od_px: float
    bolt_ring_d_px: float
    bolt_d_px: float
    bore_d_px: float | None
    # height/width aspect of the tallest other line-art view (main run proxy).
    run_aspect: float | None
    views: int
    notes: list[str] = field(default_factory=list)

    @property
    def pcd_ratio(self) -> float:
        return self.bolt_ring_d_px / self.flange_od_px

    @property
    def bolt_ratio(self) -> float:
        return self.bolt_d_px / self.flange_od_px

    @property
    def bore_ratio(self) -> float | None:
        return (self.bore_d_px / self.flange_od_px) if self.bore_d_px else None


def detect_flanged_branch(image_bytes: bytes) -> BranchDetection | None:
    """Classify the sheet from pixels alone. Never raises."""
    try:
        return _detect(image_bytes)
    except Exception as exc:  # noqa: BLE001 - detection is best-effort
        log_event("flange_detect_failed", reason=type(exc).__name__,
                  detail=str(exc)[:200])
        return None


def _detect(image_bytes: bytes) -> BranchDetection | None:
    import numpy as np
    from PIL import Image

    from app.drawing.raster_profile import _connected_regions, _flood_from_border

    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    if max(img.size) > _MAX_WORK_DIM:
        f = _MAX_WORK_DIM / max(img.size)
        img = img.resize((max(1, int(img.width * f)), max(1, int(img.height * f))))
    rgb = np.asarray(img, dtype=np.int16)
    gray = rgb.mean(axis=2)
    chroma = rgb.max(axis=2) - rgb.min(axis=2)
    ink = (gray < 100)                       # geometry strokes
    render = ((gray > 90) & (gray < 215)) | (chroma > 24)  # shading/graphics

    # Split the sheet into views along blank gutters (reuse the segmenter).
    from app.drawing.segment import _split_regions

    boxes = _split_regions(np, ink | render)
    best: BranchDetection | None = None
    aspects: list[float] = []
    line_art_views = 0
    for x0, y0, x1, y1 in boxes:
        sub_ink = ink[y0:y1, x0:x1]
        n_ink = int(sub_ink.sum())
        if n_ink < 80:
            continue
        sub_render = render[y0:y1, x0:x1]
        if int(sub_render.sum()) > 1.2 * n_ink:
            continue  # shaded isometric preview: context only, never dimensions
        line_art_views += 1
        aspects.append((y1 - y0) / max(1, (x1 - x0)))
        found = _flange_face_in_view(np, sub_ink, _flood_from_border,
                                     _connected_regions)
        if found and (best is None or found.confidence > best.confidence):
            best = found

    if best is None:
        return None
    best.views = line_art_views
    if line_art_views >= 2:
        best.confidence = min(0.9, best.confidence + 0.1)
        # The tallest OTHER orthographic view approximates the main run aspect.
        tall = [a for a in aspects if a > 1.05]
        if tall:
            best.run_aspect = max(tall)
    log_event("flange_detected", confidence=best.confidence,
              bolt_count=best.bolt_count, views=line_art_views,
              run_aspect=best.run_aspect)
    return best


def _flange_face_in_view(np, ink, flood, regions_fn) -> BranchDetection | None:
    """Look for the plan-view signature inside ONE view: a ring of similar
    small circular enclosed regions around a common center, with an optional
    central circular bore region."""
    light = ~ink
    background = flood(np, light)
    enclosed = light & ~background
    if not enclosed.any():
        return None
    regions = regions_fn(np, enclosed)
    circles = []
    for r in regions:
        x0, y0, x1, y1 = r["bbox"]
        w, h = x1 - x0 + 1, y1 - y0 + 1
        if w < 3 or h < 3:
            continue
        aspect = w / h
        fill = r["area"] / (w * h)
        if abs(1 - aspect) <= _CIRCLE_ASPECT_TOL and fill >= _CIRCLE_FILL_MIN:
            circles.append({"cx": (x0 + x1) / 2, "cy": (y0 + y1) / 2,
                            "d": (w + h) / 2, "area": r["area"]})
    if len(circles) < _MIN_BOLT_CIRCLES:
        return None

    # Bolt candidates: the largest group of similar-diameter small circles.
    circles.sort(key=lambda c: c["area"])
    best_group: list[dict] = []
    for i, seed in enumerate(circles):
        group = [c for c in circles
                 if abs(c["area"] - seed["area"]) <= _BOLT_AREA_TOL * max(seed["area"], 1)]
        if len(group) > len(best_group):
            best_group = group
    if not (_MIN_BOLT_CIRCLES <= len(best_group) <= _MAX_BOLT_CIRCLES):
        return None
    cx = sum(c["cx"] for c in best_group) / len(best_group)
    cy = sum(c["cy"] for c in best_group) / len(best_group)
    radii = [math.hypot(c["cx"] - cx, c["cy"] - cy) for c in best_group]
    ring_r = sum(radii) / len(radii)
    if ring_r < 8 or any(abs(r - ring_r) > _RING_RADIUS_TOL * ring_r for r in radii):
        return None  # not an equidistant ring

    bolt_d = sum(c["d"] for c in best_group) / len(best_group)
    # Central bore: a circular region near the ring center, clearly bigger than
    # a bolt hole and inside the ring.
    bore = None
    for c in circles:
        if c in best_group:
            continue
        if math.hypot(c["cx"] - cx, c["cy"] - cy) < 0.25 * ring_r \
                and bolt_d * 1.5 < c["d"] < 2 * ring_r:
            if bore is None or c["d"] > bore:
                bore = c["d"]
    # Flange OD: the annulus region (largest enclosed region containing the
    # ring) extends to the outer circle — measure its extent from the center.
    flange_od = _outer_extent(np, enclosed, cx, cy)
    if flange_od <= 2 * ring_r * 0.9:
        flange_od = 2 * ring_r * 1.25  # ring near the rim; infer the OD

    confidence = 0.6 + min(0.15, 0.01 * len(best_group))
    if bore:
        confidence += 0.05
    det = BranchDetection(
        confidence=round(confidence, 2), bolt_count=len(best_group),
        flange_od_px=flange_od, bolt_ring_d_px=2 * ring_r, bolt_d_px=bolt_d,
        bore_d_px=bore, run_aspect=None, views=1,
        notes=[f"Detected a circular flange face with {len(best_group)} bolt "
               "holes on a common bolt circle (pixel analysis, no AI)"])
    return det


def _outer_extent(np, enclosed, cx: float, cy: float) -> float:
    """Diameter of the enclosed material around (cx, cy) — the flange face."""
    ys, xs = np.nonzero(enclosed)
    d = np.hypot(xs - cx, ys - cy)
    if len(d) == 0:
        return 0.0
    return float(np.percentile(d, 99.5) * 2)


# ------------------------------------------------------------- notes fast path

_NOTES_FAMILY_RE = re.compile(
    r"flanged?\s+(pipe\s+)?(branch|tee|fitting)|pipe\s+branch|\btee\b", re.I)
_NUM = r"(\d+(?:\.\d+)?)"
_NOTES_PATTERNS: tuple[tuple[str, str], ...] = (
    ("flange_outer_diameter_mm", rf"flange\s*(?:od|outer\s*diam\w*)\s*:?\s*{_NUM}"),
    ("bore_diameter_mm", rf"(?:bore|inner\s*diam\w*|id)\s*:?\s*{_NUM}"),
    ("main_pipe_length_mm", rf"(?:height|length|main\s*(?:pipe\s*)?length)\s*:?\s*{_NUM}"),
    ("branch_length_mm", rf"branch\s*(?:length|projection)\s*:?\s*{_NUM}"),
    ("wall_thickness_mm", rf"wall\s*(?:thickness)?\s*:?\s*{_NUM}"),
    ("flange_thickness_mm", rf"flange\s*thick\w*\s*:?\s*{_NUM}"),
    ("main_pipe_outer_diameter_mm", rf"(?:main\s*)?pipe\s*od\s*:?\s*{_NUM}"),
    ("bolt_circle_diameter_mm", rf"(?:pcd|bolt\s*circle)\s*:?\s*{_NUM}"),
    ("fillet_radius_mm", rf"fillet\s*(?:radius)?\s*:?\s*r?{_NUM}"),
)
_NOTES_BOLTS_RE = re.compile(
    rf"(\d+)\s*(?:x\s*)?(?:bolt\s*holes?|bolts|holes)(?:.*?ø?\s*{_NUM})?", re.I)
_NOTES_BOLT_DIA_RE = re.compile(rf"(?:bolt|hole)s?\s*(?:ø|dia\w*)\s*:?\s*{_NUM}", re.I)


def params_from_notes(notes: str | None) -> dict | None:
    """Parse explicit flanged-pipe-branch parameters out of the user's notes.
    Returns {dims, bolt_count, bolt_dia} when the notes NAME the family and give
    at least one dimension — the fast path that bypasses vision entirely."""
    if not notes or not _NOTES_FAMILY_RE.search(notes):
        return None
    t = notes.lower()
    dims: dict[str, float] = {}
    for key, pattern in _NOTES_PATTERNS:
        m = re.search(pattern, t)
        if m:
            dims[key] = float(m.group(1))
    bolt_count = None
    bolt_dia = None
    m = _NOTES_BOLTS_RE.search(t)
    if m:
        bolt_count = int(m.group(1))
        if m.group(2):
            bolt_dia = float(m.group(2))
    m = _NOTES_BOLT_DIA_RE.search(t)
    if m:
        bolt_dia = float(m.group(1))
    if not dims and bolt_count is None:
        return None
    return {"dims": dims, "bolt_count": bolt_count, "bolt_dia": bolt_dia}


# ------------------------------------------------------ interpretation assembly

def interp_from_evidence(
    detection: BranchDetection | None,
    vision: DrawingInterpretationSpec | None = None,
    notes_params: dict | None = None,
) -> DrawingInterpretationSpec | None:
    """Assemble the flanged-pipe-branch interpretation from all evidence.

    Absolute scale priority: user notes > vision dimensions > assumed default
    OD (flagged; the build lands in REVIEW). Structure (bolt count, ratios)
    priority: notes > detection > vision. Returns None when there is no
    structural evidence at all."""
    if detection is None and notes_params is None:
        return None

    dims: dict[str, float] = {}
    holes: list[DrawingHoleCalloutSpec] = []
    assumptions: list[str] = []
    source = "vision"
    confidence = 0.55

    vision_ok = vision is not None and vision.generatable_with_assumptions() \
        and bool(vision.overall_dimensions)
    if vision_ok:
        dims.update({k: float(v) for k, v in vision.overall_dimensions.items()
                     if v and float(v) > 0})
        holes = list(vision.holes)
        confidence = max(confidence, vision.overall_confidence)

    # Pixel detection fills structure and, given ANY absolute anchor, sizes.
    if detection is not None:
        assumptions.extend(detection.notes)
        anchor = dims.get("flange_outer_diameter_mm")
        if anchor is None and notes_params:
            anchor = notes_params["dims"].get("flange_outer_diameter_mm")
        assumed = False
        if anchor is None:
            anchor = DEFAULT_FLANGE_OD_MM
            assumed = True
            assumptions.append(
                f"No legible flange diameter — assumed Ø{anchor:g}mm and scaled "
                "the detected proportions to it (adjust in the studio)")
        px_scale = anchor / detection.flange_od_px
        dims.setdefault("flange_outer_diameter_mm", anchor)
        dims.setdefault("bolt_circle_diameter_mm",
                        round(detection.bolt_ring_d_px * px_scale, 2))
        if detection.bore_d_px:
            dims.setdefault("main_pipe_outer_diameter_mm",
                            round(detection.bore_d_px * px_scale / 0.86, 2))
        # Main-run height from the tallest orthographic view — a crude proxy,
        # only trusted in the plausible range (a skinny section view can win
        # the aspect contest; the proportional default is safer then).
        if detection.run_aspect and 0.8 <= detection.run_aspect <= 2.5:
            dims.setdefault("main_pipe_length_mm",
                            round(anchor * detection.run_aspect, 2))
        if not holes:
            holes = [DrawingHoleCalloutSpec(
                diameter=round(detection.bolt_d_px * px_scale, 2),
                count=detection.bolt_count,
                callout=f"{detection.bolt_count}x (detected)")]
        if assumed:
            source = "raster_assumed"
            confidence = max(confidence, 0.55)
        else:
            confidence = max(confidence, detection.confidence)
        # A detection-assembled branch has its STRUCTURE (bolt circle, wall,
        # PCD, branch proportions) inferred from pixel ratios — only an overall
        # anchor is measured. That is never a fully-dimensioned read, so cap the
        # confidence below the PASS threshold: the build lands in REVIEW, not a
        # clean PASS with assumed dimensions.
        assumptions.append("Bolt circle, wall and branch proportions inferred "
                           "from the drawing — verify dimensions before manufacturing")
        confidence = min(confidence, 0.72)

    # User notes are ground truth — they override everything.
    if notes_params:
        for k, v in notes_params["dims"].items():
            if k == "bore_diameter_mm":
                # bore → main pipe ID; approximate OD via wall when present.
                wall = notes_params["dims"].get("wall_thickness_mm")
                dims["main_pipe_outer_diameter_mm"] = v + 2 * wall if wall else \
                    dims.get("main_pipe_outer_diameter_mm", v * 1.2)
                dims["main_pipe_bore_mm"] = v
            else:
                dims[k] = v
        if notes_params["bolt_count"]:
            cnt = notes_params["bolt_count"]
            dia = notes_params["bolt_dia"]
            holes = [DrawingHoleCalloutSpec(
                diameter=dia or (holes[0].diameter if holes else None),
                count=cnt, callout=f"{cnt}x (from notes)")]
        assumptions.append("Dimensions taken from your notes (they override the drawing)")
        source = "notes" if source != "raster_assumed" else source
        confidence = max(confidence, 0.8)

    if not dims and not holes:
        return None
    # User-stated numbers are literal millimetres — pin the units so the scale
    # inference NEVER rescales them (14.8 stays 14.8, 0.5 stays 0.5).
    units_confidence = 0.95 if notes_params else (
        vision.drawing_units_confidence if vision_ok else 0.4)
    return DrawingInterpretationSpec(
        title="Flanged pipe branch (deterministic raster path)",
        units="mm",
        suggested_object_type="flanged_pipe_branch",
        detected_object_type="flanged pipe branch (pixel/notes evidence)",
        overall_dimensions=dims,
        holes=holes,
        assumptions=[{"field": "detection", "assumption": a} for a in assumptions],
        overall_confidence=confidence,
        drawing_units_confidence=units_confidence,
        interp_source=source,
    )
