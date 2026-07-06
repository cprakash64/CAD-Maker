"""Deterministic best-effort CAD for drawings the provider couldn't interpret.

The contract (the fix for the field failure where an OpenAI timeout turned a
perfectly good multi-view drawing into ``generated=false``): every valid
mechanical drawing image with USABLE LINEWORK gets a best-effort model —
flagged REVIEW when dimensions were estimated — and only images with no
detectable geometry (or a CAD-kernel failure after all fallbacks) may fail.

Routes, tried in evidence-strength order:

* ``raster_profile``          — the caller already traced a dominant closed
                                outline (single-view sheets); extrude it,
                                holes preserved.
* ``segmented_flange_family`` — a sheet region carries the flange-face + bolt
                                ring pixel signature; build the flanged pipe
                                branch family deterministically.
* ``segmented_main_contour``  — multi-view sheet: split along white gutters,
                                drop renders/annotation strips/title blocks,
                                trace the most substantial closed contour among
                                the technical views, extrude it with its holes.

Everything here is pixel analysis + the existing deterministic builders — no
LLM anywhere. Never raises: any internal failure returns None and the caller
falls back to the clean generated=false path.
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field

from app.cad.plan.schema import CadPlan
from app.observability import log_event
from app.schemas.drawing_spec import DrawingInterpretationSpec

# A region must carry at least this many inked pixels to be a drawing view.
_MIN_REGION_INK_PX = 150
_MIN_REGION_SIDE_PX = 24
# Wider/flatter than this and it's a title block / dimension strip, not a view.
_MAX_VIEW_ASPECT = 6.0
# Midtone-ink fraction above which a region is a shaded render (see segment.py).
_RENDER_MIDTONE_FRACTION = 0.55
# Per-region flange detection must be at least this confident to take the
# family route over a plain contour extrusion.
_MIN_FLANGE_CONFIDENCE = 0.6
# White margin pasted around crops so border-touching linework still floods.
_CROP_MARGIN_PX = 10
_MAX_CANDIDATE_REGIONS = 8


@dataclass
class RegionInfo:
    box: tuple[int, int, int, int]
    ink_px: int
    kind: str  # "technical" | "render" | "annotation"


@dataclass
class BestEffortOutcome:
    route: str  # raster_profile | segmented_main_contour | segmented_flange_family
    plan: CadPlan | None = None
    interp: DrawingInterpretationSpec | None = None
    profile: object | None = None  # RasterProfile behind a profile plan
    confidence: float = 0.55
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def generate_best_effort_from_drawing(
    image_bytes: bytes,
    raster_profile_result=None,
    provider_result: DrawingInterpretationSpec | None = None,
    thickness_mm: float | None = None,
    dimension_texts: list[str] | None = None,
) -> BestEffortOutcome | None:
    """Best-effort deterministic interpretation of the drawing. Never raises."""
    try:
        return _best_effort(image_bytes, raster_profile_result, provider_result,
                            thickness_mm, dimension_texts)
    except Exception as exc:  # noqa: BLE001 - the fallback must never crash a job
        log_event("drawing_fallback_error", error_type=type(exc).__name__,
                  detail=str(exc)[:200])
        return None


def _best_effort(image_bytes, profile, provider_result, thickness_mm,
                 dimension_texts) -> BestEffortOutcome | None:
    from app.services.drawing_to_spec import plan_from_raster_profile

    # A. Caller-supplied dominant outline (single-view sheet) — strongest
    #    deterministic evidence; extrude it, holes preserved.
    if profile is not None:
        plan, _ = plan_from_raster_profile(
            profile, _usable(provider_result), thickness_mm=thickness_mm,
            dimension_texts=dimension_texts)
        return BestEffortOutcome(route="raster_profile", plan=plan,
                                 profile=profile,
                                 warnings=_estimate_warnings(plan))

    # B/C. Multi-view sheet: segment, classify, and analyse per region.
    segmented = _segment_regions(image_bytes)
    if segmented is None:
        return None
    img, regions = segmented
    technical = [r for r in regions if r.kind == "technical"]
    technical.sort(key=lambda r: r.ink_px, reverse=True)
    technical = technical[:_MAX_CANDIDATE_REGIONS]

    crops = [(r, _crop_png(img, r.box)) for r in technical]
    # Whole sheet with a white margin as a last candidate: rescues outlines
    # that touch the image border (border-flood extraction needs paper there).
    crops.append((None, _crop_png(img, (0, 0, img.width, img.height))))

    flange = _detect_flange(crops)
    if flange is not None:
        interp = _flange_interp(flange, provider_result)
        if interp is not None:
            return BestEffortOutcome(
                route="segmented_flange_family", interp=interp,
                confidence=interp.overall_confidence,
                notes=["Flange face + bolt ring detected in a sheet region "
                       "(pixel analysis, no AI)"])

    best = _best_contour(crops)
    if best is None:
        return None
    region, contour = best
    plan, _ = plan_from_raster_profile(
        contour, _usable(provider_result), thickness_mm=thickness_mm,
        dimension_texts=dimension_texts)
    notes = ["Main view selected from the segmented sheet; other regions "
             "(annotations/renders/side views) were used for context only"]
    if region is None:
        notes = ["Outline traced from the full sheet (no separable views)"]
    return BestEffortOutcome(route="segmented_main_contour", plan=plan,
                             profile=contour, notes=notes,
                             warnings=_estimate_warnings(plan))


def _usable(interp: DrawingInterpretationSpec | None):
    """Only pass the provider result through when it actually read something —
    a timeout/outage interpretation must not inject fabricated dimensions."""
    if interp is None or not interp.overall_dimensions:
        return None
    return interp


def _estimate_warnings(plan: CadPlan) -> list[str]:
    return [a for a in plan.assumptions if "estimated" in a.lower()]


# ------------------------------------------------------------- region analysis

def _segment_regions(image_bytes: bytes):
    import numpy as np
    from PIL import Image

    from app.drawing.segment import _split_regions

    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    except Exception:  # noqa: BLE001 - not an image
        return None
    rgb = np.asarray(img, dtype=np.int16)
    gray = rgb.mean(axis=2)
    chroma = rgb.max(axis=2) - rgb.min(axis=2)
    ink = (gray < 235) | (chroma > 24)
    if not ink.any():
        return None

    out: list[RegionInfo] = []
    for x0, y0, x1, y1 in _split_regions(np, ink):
        w, h = x1 - x0, y1 - y0
        if w < _MIN_REGION_SIDE_PX or h < _MIN_REGION_SIDE_PX:
            continue
        sub_ink = ink[y0:y1, x0:x1]
        n_ink = int(sub_ink.sum())
        if n_ink < _MIN_REGION_INK_PX:
            continue
        sub_gray = gray[y0:y1, x0:x1]
        sub_chroma = chroma[y0:y1, x0:x1]
        midtone = (((sub_gray > 90) & (sub_gray < 215)) | (sub_chroma > 24)) & sub_ink
        frac = float(midtone.sum()) / n_ink
        aspect = w / h
        if frac >= _RENDER_MIDTONE_FRACTION:
            kind = "render"          # shaded isometric preview: context only
        elif aspect > _MAX_VIEW_ASPECT or aspect < 1 / _MAX_VIEW_ASPECT:
            kind = "annotation"      # title block / dimension strip / label band
        else:
            kind = "technical"
        out.append(RegionInfo(box=(x0, y0, x1, y1), ink_px=n_ink, kind=kind))
    log_event("drawing_fallback_regions",
              total=len(out),
              technical=sum(1 for r in out if r.kind == "technical"),
              renders=sum(1 for r in out if r.kind == "render"),
              annotations=sum(1 for r in out if r.kind == "annotation"))
    return img, out


def _crop_png(img, box, margin: int = _CROP_MARGIN_PX) -> bytes:
    from PIL import Image

    crop = img.crop(box)
    canvas = Image.new("RGB", (crop.width + 2 * margin, crop.height + 2 * margin),
                       (255, 255, 255))
    canvas.paste(crop, (margin, margin))
    buf = io.BytesIO()
    canvas.save(buf, format="PNG")
    return buf.getvalue()


def _detect_flange(crops):
    """Best flange-face detection across the candidate regions."""
    from app.services.raster_flanged_pipe_branch import detect_flanged_branch

    best = None
    for _, crop_bytes in crops:
        det = detect_flanged_branch(crop_bytes)
        if det is not None and det.confidence >= _MIN_FLANGE_CONFIDENCE \
                and (best is None or det.confidence > best.confidence):
            best = det
    return best


def _flange_interp(det, provider_result):
    from app.services.raster_flanged_pipe_branch import interp_from_evidence

    return interp_from_evidence(det, vision=_usable(provider_result))


def _best_contour(crops):
    """Trace each candidate crop; keep the most useful closed contour — the
    main drawing view. Scored by enclosed size, with a penalty for plain
    rectangles (section frames / borders / title boxes look like big closed
    rectangles) and a bonus for drawn holes (real part views carry features)."""
    from app.drawing.raster_profile import extract_profile

    best = None
    best_score = 0.0
    for region, crop_bytes in crops:
        contour = extract_profile(crop_bytes)
        if contour is None:
            continue
        # interior_fraction is relative to ITS crop; weight by the crop's own
        # traced bbox so a big main view beats a small detail view.
        score = contour.width_px * contour.height_px * max(
            contour.interior_fraction, 0.01)
        if contour.is_rectangleish() and contour.hole_count == 0:
            score *= 0.3
        score *= 1.0 + 0.2 * min(contour.hole_count, 5)
        if score > best_score:
            best, best_score = (region, contour), score
    return best
