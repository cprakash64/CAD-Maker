"""Vectorize a raster mechanical drawing into a MechanicalSketchIR.

Builds on the pure-numpy enclosure machinery in ``raster_profile`` (flood-fill
the background from the border → light areas sealed inside ink loops are the
part interior and its holes). On top of that it:

* groups CONCENTRIC loops so a counterbore (outer recess ring + inner through
  bore) is ONE nested feature, never two unrelated holes — the "8 random holes"
  bug for a 4-bolt plate;
* classifies each single loop as a circular hole, a rectangular/rounded/arc
  slot, or an arbitrary polygon cutout, keeping the EXACT traced outline for
  every non-circular shape (arc slots survive as their real curve);
* classifies the outer profile (rounded rectangle / symmetric plate / polygon);
* sets the mm scale from a parsed overall dimension when one is legible, else a
  flagged default envelope (REVIEW).

Never raises: any failure returns None and the caller keeps its existing path.
"""
from __future__ import annotations

import io
import math

from app.drawing.sketch_ir import (
    ConcentricHoleGroup,
    CutFeature,
    Dimension,
    MechanicalSketchIR,
    OuterProfile,
    Scale,
)
from app.observability import log_event

_MAX_WORK_DIM = 900
_INK_THRESHOLD = 100
_RDP_EPS = 1.4
_MIN_INTERIOR_FRACTION = 0.02
_MIN_HOLE_PX = 40
# Two loops are concentric when their centres are within this fraction of the
# larger loop's radius.
_CONCENTRIC_TOL = 0.22
# A loop is "slot-like" (elongated) beyond this aspect ratio.
_SLOT_ASPECT = 2.0
# Centroid offset / slot length above which an elongated slot is a curved ARC.
# A straight slot's centroid sits on its axis (ratio ≈ 0); a genuine arc sector
# is well clear of this threshold.
_ARC_OFFSET_RATIO = 0.06
# An enclosed region spanning at least this fraction of the part width OR height
# is interior material / a split half / a full-width base strip — never a hole.
_BODY_SPAN_FRACTION = 0.72


def build_sketch_ir(image_bytes: bytes, dimension_texts: list[str] | None = None,
                    thickness_mm: float | None = None,
                    source: str = "drawing_fallback") -> MechanicalSketchIR | None:
    """Reconstruct the 2D mechanical sketch. Returns None if no part-sized closed
    outer profile is present. Never raises."""
    try:
        return _build(image_bytes, dimension_texts, thickness_mm, source)
    except Exception as exc:  # noqa: BLE001 - vectorization is best-effort
        log_event("drawing_vectorization_failed", reason=type(exc).__name__,
                  detail=str(exc)[:200])
        return None


def _build(image_bytes, dimension_texts, thickness_mm, source):
    import numpy as np
    from PIL import Image

    from app.drawing.raster_profile import (
        _classify_hole,
        _connected_regions,
        _flood_from_border,
        _rdp,
        _trace_boundary,
    )

    log_event("drawing_vectorization_started")
    img = Image.open(io.BytesIO(image_bytes)).convert("L")
    if max(img.size) > _MAX_WORK_DIM:
        f = _MAX_WORK_DIM / max(img.size)
        img = img.resize((max(1, int(img.width * f)), max(1, int(img.height * f))))
    gray = np.asarray(img, dtype=np.uint8)
    h, w = gray.shape
    ink = gray < _INK_THRESHOLD
    light = ~ink
    background = _flood_from_border(np, light)
    enclosed = light & ~background
    if not enclosed.any():
        return None
    log_event("drawing_recess_detector_started", detector_version=DETECTOR_VERSION,
              gray_shape=[int(h), int(w)], outer_profile_available=bool(enclosed.any()))

    # THE PART = the largest connected component of the solid footprint
    # (ink outline + everything it encloses). Taking a CONNECTED COMPONENT — not
    # the whole ink mask — drops floating annotations / dimension text / title
    # blocks (separate components), while any internal line that splits the light
    # interior (a base strip, a step, a crossing dimension line) stays PART OF the
    # component because it touches the outline. So the outer silhouette is
    # annotation-free AND split-robust in one step.
    solid = ~background
    part_candidates = _connected_regions(np, solid)
    if not part_candidates:
        return None
    part = max(part_candidates, key=lambda r: r["area"])
    if part["area"] < _MIN_INTERIOR_FRACTION * w * h:
        return None
    ix0, iy0, ix1, iy1 = part["bbox"]
    iw, ih = ix1 - ix0 + 1, iy1 - iy0 + 1

    # INTERNAL DARK FILLS → RECESSES (key_recess_v2). A solid dark/shaded fill
    # inside the part is a recessed FACE, not a wall and not empty space. Found by
    # erosion BEFORE any hole/slot classification. Each accepted fill is then
    # NEUTRALIZED in the ink/light masks (treated as material) and the interior is
    # re-flooded — so the white strips a dark bar would otherwise seal off stop
    # being read as through slots (the "shaft cut into two walls" failure).
    recess_regions = _detect_dark_fill_regions(
        np, gray, part, background, ix0, iy0, ix1, iy1)
    if recess_regions:
        for rr in recess_regions:
            ink[rr["mask"]] = False
            light[rr["mask"]] = True
        background = _flood_from_border(np, light)
        enclosed = light & ~background
        log_event("drawing_dark_regions_neutralized", count=len(recess_regions),
                  kinds=[rr["kind"] for rr in recess_regions])

    # HOLES vs INTERIOR MATERIAL (the field failure this fixes): a base strip /
    # step / crossing line splits the light interior into several regions.
    # Interior MATERIAL — including every split half — reaches the outer wall, so
    # its bbox touches >= 2 silhouette edges; a real hole / slot / cutout is fully
    # surrounded and touches < 2. Collecting holes from ALL floating regions
    # inside the part (not just the single largest interior) stops the smaller
    # half's features (the top concentric group, the central rectangular cutout)
    # from being dropped.
    tol_x, tol_y = max(2, int(0.03 * iw)), max(2, int(0.03 * ih))
    all_regions = [r for r in _connected_regions(np, enclosed)
                   if r["bbox"][0] >= ix0 - tol_x and r["bbox"][1] >= iy0 - tol_y
                   and r["bbox"][2] <= ix1 + tol_x and r["bbox"][3] <= iy1 + tol_y]

    def _contains_nested(r) -> bool:
        """True when another region's BBOX sits fully inside r's bbox — then r is
        the OUTER material RING (e.g. the solid head around its bore), not a hole.
        Uses bbox containment, not area: a thin annulus has a smaller area than
        the large bore it encloses but a strictly larger bounding box."""
        x0, y0, x1, y1 = r["bbox"]
        r_bbox_area = (x1 - x0 + 1) * (y1 - y0 + 1)
        for o in all_regions:
            if o is r:
                continue
            ox0, oy0, ox1, oy1 = o["bbox"]
            if (ox0 >= x0 and oy0 >= y0 and ox1 <= x1 and oy1 <= y1
                    and (ox1 - ox0 + 1) * (oy1 - oy0 + 1) < 0.95 * r_bbox_area):
                return True
        return False

    hole_regions = []
    for r in all_regions:
        x0, y0, x1, y1 = r["bbox"]
        rw, rh = x1 - x0 + 1, y1 - y0 + 1
        fill = r["area"] / (rw * rh)
        # A round enclosed loop (square-ish bbox filled to ~π/4) that contains no
        # nested region is a true circular THROUGH-HOLE — a wide head bore in a
        # narrow key must not be mistaken for interior material by the edge/span
        # filters. A round-ish region that CONTAINS a nested loop is the solid
        # head material around its bore, so it stays subject to those filters.
        is_round = (max(rw / rh, rh / rw) <= 1.35 and 0.60 <= fill <= 0.90
                    and not _contains_nested(r))
        edges = (int(x0 <= ix0 + tol_x) + int(x1 >= ix1 - tol_x)
                 + int(y0 <= iy0 + tol_y) + int(y1 >= iy1 - tol_y))
        spans = (x1 - x0) >= _BODY_SPAN_FRACTION * iw \
            or (y1 - y0) >= _BODY_SPAN_FRACTION * ih
        if not is_round and (edges >= 2 or spans):
            continue  # interior material, a split half, or a full-width base strip
        if r["area"] < _MIN_HOLE_PX:
            continue
        # A CIRCULAR hole is always a real through-hole and is NEVER suppressed as
        # solid tooth/body material — even a thin-ringed head bore whose dilated
        # ring reaches outside the head must stay a hole (is_round from above).
        # TOOTH-TAB SOLIDITY: a real hole/slot is surrounded by MATERIAL; a tooth
        # tab / protruding step is a closed OUTLINE whose interior opens to the
        # exterior through only a thin wall. Dilating the enclosed region past that
        # wall reaches the background → it is SOLID silhouette material, not a cut.
        # Only NON-circular tabs are suppressed; circular holes are preserved.
        ring = _dilate(np, r["mask"], _TOOTH_WALL_PX) & ~r["mask"]
        ring_total = int(ring.sum()) or 1
        tab_ext_frac = float((ring & background).sum()) / ring_total
        if tab_ext_frac > _TOOTH_EXT_FRAC and not is_round:
            log_event("drawing_key_tooth_cutout_suppressed",
                      candidate_kind="enclosed_loop", bbox=[x0, y0, x1, y1],
                      exterior_frac=round(tab_ext_frac, 2),
                      reason="tooth_region_is_solid_material")
            continue
        if tab_ext_frac > _TOOTH_EXT_FRAC and is_round:
            log_event("drawing_key_head_hole_preserved", kind="circular_hole",
                      bbox=[x0, y0, x1, y1], exterior_frac=round(tab_ext_frac, 2),
                      reason="circular_head_feature_is_true_through_hole")
        hole_regions.append(r)

    # ---- scale (mm per pixel) ------------------------------------------------
    scale_mm_per_px, scale_obj, dims_ir = _resolve_scale(
        dimension_texts, max(iw, ih))

    def to_mm_xy(cx_px, cy_px):
        """Image-pixel centre → centred mm (x right, y up)."""
        x = (cx_px - ix0 - iw / 2.0) * scale_mm_per_px
        y = (iy1 - cy_px - ih / 2.0) * scale_mm_per_px
        return [round(x, 3), round(y, 3)]

    # ---- outer profile (traced from the full part silhouette) ----------------
    contour = _trace_boundary(np, part["mask"])
    poly = _rdp(contour, _RDP_EPS) if len(contour) >= 8 else []
    outer = _outer_profile(poly, ix0, iy1, iw, ih, scale_mm_per_px)
    if outer is None:
        return None

    # ---- recesses first (from the dark fills detected + neutralized above) ---
    cut_features: list[CutFeature] = []
    counts = {"circular_hole": 0, "polygon_hole": 0, "rectangular_slot": 0,
              "rounded_slot": 0, "arc_slot": 0, "arbitrary_cutout": 0,
              "counterbore": 0, "recessed_channel": 0, "blind_recess": 0}
    for ri, rr in enumerate(recess_regions):
        rx0, ry0, rx1, ry1 = rr["bbox"]
        cf = _recess_feature_from_mask(
            np, rr["mask"], rr["kind"], rx1 - rx0 + 1, ry1 - ry0 + 1,
            scale_mm_per_px, to_mm_xy, f"recess_{ri + 1}")
        if cf is None:
            continue
        cut_features.append(cf)
        counts[cf.kind] = counts.get(cf.kind, 0) + 1
        log_event("drawing_dark_region_detected", kind=cf.kind, through=False,
                  bbox=[rx0, ry0, rx1, ry1], area_px=rr["area"],
                  aspect_ratio=round(rr["aspect"], 2))
        log_event("drawing_slot_suppressed_by_dark_recess",
                  original_kind="rounded_slot_or_arc_slot",
                  replacement_kind=cf.kind, mean_gray=round(rr["mean_gray"], 1))

    # ---- concentric grouping + classification (EMPTY enclosed regions) -------
    entries = []
    for r in hole_regions:
        x0, y0, x1, y1 = r["bbox"]
        rw, rh = x1 - x0 + 1, y1 - y0 + 1
        entries.append({
            "region": r, "cx": (x0 + x1) / 2, "cy": (y0 + y1) / 2,
            "rw": rw, "rh": rh, "rad": (rw + rh) / 4.0, "fill": r["area"] / (rw * rh),
        })
    groups = _group_concentric(entries)

    concentric_groups: list[ConcentricHoleGroup] = []
    for gi, group in enumerate(groups):
        group.sort(key=lambda e: e["rad"], reverse=True)
        if len(group) >= 2:
            chg = _build_concentric_group(group, gi, scale_mm_per_px, to_mm_xy)
            concentric_groups.append(chg)
            counts["counterbore"] += 1
            log_event("drawing_concentric_group", id=chg.id,
                      circles=chg.circle_count, kind=chg.group_kind,
                      outer=chg.outer_diameter_mm,
                      ring=chg.counterbore_or_boss_diameter_mm,
                      through=chg.through_hole_diameter_mm)
            continue
        e = group[0]
        cf = _classify_cut(np, e, _classify_hole, ix0, iy1, iw, ih,
                           scale_mm_per_px, to_mm_xy, f"cut_{gi + 1}")
        if cf is None:
            continue
        cut_features.append(cf)
        counts[cf.kind] = counts.get(cf.kind, 0) + 1
        log_event("drawing_loop_classified", kind=cf.kind)
        if cf.kind in ("rectangular_slot", "rounded_slot", "arc_slot"):
            log_event("drawing_slot_detected", kind=cf.kind)

    scale_estimated = scale_obj.estimated
    confidence = 0.72 if not scale_estimated else 0.55
    # nested_features is the derived 1:1 back-compat view of concentric_groups.
    nested_features = [g.to_nested() for g in concentric_groups]
    ir = MechanicalSketchIR(
        source=source, confidence=confidence, scale=scale_obj,
        outer_profiles=[outer], cut_features=cut_features,
        concentric_groups=concentric_groups,
        nested_features=nested_features, dimensions=dims_ir,
        default_thickness_mm=thickness_mm or 6.0,
    )
    if scale_estimated:
        ir.warnings.append("Dimensions estimated from drawing image; "
                           "verify before manufacturing.")
        ir.assumptions.append(
            f"No legible overall dimension — scaled to a "
            f"{round(max(iw, ih) * scale_mm_per_px):g}mm envelope")
    else:
        ir.assumptions.append(
            f"Scaled from the drawing dimension "
            f"'{scale_obj.source_dimension_label}'")
    if thickness_mm:
        ir.assumptions.append(f"Extruded {thickness_mm:g}mm deep (your input)")

    # Snap pixel-measured circles onto the sheet's exact Ø/R callouts (printed
    # dimensions are the source of truth, not the pixel scale). Best-effort.
    if dimension_texts:
        from app.drawing.dim_labels import parse_dimension_labels
        from app.drawing.feature_normalizer import snap_ir_to_callouts

        labels = parse_dimension_labels(dimension_texts)
        callout_dias = list(labels.diameters) + [d for _, d in labels.hole_callouts]
        ir = snap_ir_to_callouts(ir, diameter_callouts=callout_dias,
                                 radii_callouts=labels.radii)

    log_event("drawing_entities_detected",
              circles=counts["circular_hole"],
              slots=counts["rectangular_slot"] + counts["rounded_slot"] + counts["arc_slot"],
              polygons=counts["polygon_hole"] + counts["arbitrary_cutout"],
              counterbores=counts["counterbore"])
    log_event("drawing_sketch_ir_built",
              outer_profiles=1, cut_features=len(cut_features),
              nested_features=len(nested_features), warnings=len(ir.warnings))
    return ir


# ------------------------------------------------------------------ outer profile

def _outer_profile(poly, ix0, iy1, iw, ih, s) -> OuterProfile | None:
    if len(poly) < 3:
        return None
    verts = [[round((x - ix0 - iw / 2.0) * s, 3), round((iy1 - y - ih / 2.0) * s, 3)]
             for x, y in poly]
    w_mm, h_mm = iw * s, ih * s
    area = _poly_area(verts)
    bbox_area = w_mm * h_mm or 1.0
    fill = area / bbox_area
    corner_r = _corner_radius(poly, iw, ih) * s
    n = len(verts)
    # Rounded rectangle: fills most of its bbox but not entirely (rounded
    # corners shave the corners), with detectable corner arcs.
    if 0.82 <= fill < 0.995 and corner_r > 0.02 * max(w_mm, h_mm):
        kind = "rounded_rectangle"
    elif fill >= 0.995 and n <= 8:
        kind = "polygon"  # a plain rectangle is a 4-gon polygon
    elif _is_symmetric(verts):
        kind = "symmetric_plate"
    else:
        kind = "arbitrary_closed_contour"
    return OuterProfile(
        id="outer_1", kind=kind, vertices=verts,
        bbox_mm={"w": round(w_mm, 3), "h": round(h_mm, 3)},
        corner_radius_mm=round(corner_r, 3) if corner_r > 0 else None,
        confidence=0.7)


def _corner_radius(poly, iw, ih) -> float:
    """Estimate a uniform corner radius: how far the outline's corner points sit
    from the bbox corners (0 for a sharp rectangle)."""
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    insets = []
    for cxp, cyp in corners:
        d = min(math.hypot(px - cxp, py - cyp) for px, py in poly)
        insets.append(d)
    insets.sort()
    med = insets[len(insets) // 2]
    # Only a radius if it's a meaningful, roughly uniform inset.
    return med if med > 3 and med < 0.4 * min(iw, ih) else 0.0


def _is_symmetric(verts) -> bool:
    xs = [v[0] for v in verts]
    return abs(sum(xs)) < 0.06 * (max(xs) - min(xs) + 1e-6)


# ------------------------------------------------------------------ cut features

def _classify_cut(np, e, classify_hole, ix0, iy1, iw, ih, s, to_mm, fid):
    from app.drawing.raster_profile import _rdp, _trace_boundary

    r = e["region"]
    center = to_mm(e["cx"], e["cy"])
    aspect = max(e["rw"] / e["rh"], e["rh"] / e["rw"])
    hole = classify_hole(np, r, ix0, iy1)  # RasterHole (kind, polygon, is_round)

    # The exact traced outline (mm, centred) — kept for EVERY non-circular cut so
    # arc slots / rounded slots / polygons survive as their real shape.
    def traced_vertices():
        contour = _trace_boundary(np, r["mask"])
        poly = _rdp(contour, 1.2) if len(contour) >= 6 else []
        if len(poly) >= 2 and poly[0] == poly[1]:
            poly = poly[:-1]
        return [to_mm(px, py) for px, py in poly]

    # SLOT-LIKE loops are decided BEFORE roundness (an arc sector traces to many
    # vertices and would otherwise read as a circle). A loop is slot-like when
    # it is elongated OR a thin curved band (a low-fill arc sector: its bbox
    # holds the arc's bulge but the region is a slim crescent).
    curved = e["fill"] < 0.6 and _is_arc(np, r)
    if aspect >= _SLOT_ASPECT or curved:
        verts = traced_vertices()
        if curved or _is_arc(np, r):
            kind = "arc_slot"
        elif hole.kind == "rounded_slot" or not _is_rectangular(verts):
            kind = "rounded_slot"
        else:
            kind = "rectangular_slot"
        return CutFeature(id=fid, kind=kind, center=center, vertices=verts,
                          width_mm=round(min(e["rw"], e["rh"]) * s, 3),
                          height_mm=round(max(e["rw"], e["rh"]) * s, 3),
                          confidence=0.6)
    if hole.is_round:
        return CutFeature(id=fid, kind="circular_hole", center=center,
                          diameter_mm=round(e["rad"] * 2 * s, 3), confidence=0.7)
    verts = traced_vertices()
    if hole.kind == "rectangle":
        return CutFeature(id=fid, kind="rectangular_slot", center=center,
                          vertices=verts,
                          width_mm=round(e["rw"] * s, 3),
                          height_mm=round(e["rh"] * s, 3), confidence=0.6)
    # An arched/curved-top window (flat bottom + vertical sides + arc top) keeps
    # its EXACT traced profile and is cut as that profile — never a circle.
    if hole.kind == "arched_rectangular_cutout":
        return CutFeature(id=fid, kind="arched_rectangular_cutout", center=center,
                          vertices=verts,
                          width_mm=round(e["rw"] * s, 3),
                          height_mm=round(e["rh"] * s, 3), confidence=0.65)
    return CutFeature(id=fid, kind="polygon_hole" if hole.kind in
                      ("hexagon", "regular_polygon") else "arbitrary_cutout",
                      center=center, vertices=verts, confidence=0.6)


def _is_rectangular(verts) -> bool:
    """A 4-vertex axis-alignedish loop reads as a rectangle."""
    return len(verts) == 4


def _is_arc(np, region) -> bool:
    """True when an elongated region is CURVED (an arc slot): its pixel centroid
    is offset from the straight chord joining its two extreme points."""
    ys, xs = np.nonzero(region["mask"])
    if len(xs) < 30:
        return False
    pts = np.stack([xs.astype(float), ys.astype(float)])
    c = pts.mean(axis=1, keepdims=True)
    p = pts - c
    cov = np.cov(p)
    evals, evecs = np.linalg.eigh(cov)
    major = evecs[:, int(np.argmax(evals))]
    proj = p.T @ major
    i0, i1 = int(np.argmin(proj)), int(np.argmax(proj))
    a = np.array([xs[i0], ys[i0]], float)
    b = np.array([xs[i1], ys[i1]], float)
    length = np.linalg.norm(b - a)
    if length < 8:
        return False
    ab = (b - a) / length
    normal = np.array([-ab[1], ab[0]])
    centroid = c[:, 0]
    offset = abs((centroid - a) @ normal)
    return offset / length > _ARC_OFFSET_RATIO


# ------------------------------------------------------- internal dark recesses

_RECESS_MIN_FILL = 0.34
_RECESS_MIN_THICK_PX = 6
_RECESS_MIN_AREA_FRAC = 0.006
_RECESS_CHANNEL_ASPECT = 2.5
_RECESS_KINDS = ("recessed_channel", "blind_recess", "through_channel_cut")

# Dark-FILL recess detector (key_recess_v2). A recessed/grooved face is drawn as
# a solid dark/shaded FILL; a through opening is empty paper. A morphological
# erosion separates thick FILLS (survive) from thin outline/dimension STROKES
# (vanish), so a dark bar is found even when it is drawn in ink and touches the
# outline. Everything darker than this counts as fill candidate ink.
DETECTOR_VERSION = "key_recess_v2"
_DARK_FILL_THRESHOLD = 165      # gray < this = ink OR shaded fill (not pale paper)
_ERODE_ITERS = 3               # removes strokes up to ~2*iters px wide
_CORE_MIN_THICK_PX = 5         # min eroded-core thickness of a genuine FILL
_DARK_FILL_MIN_AREA_FRAC = 0.004
_DARK_FILL_MAX_SPAN = 0.93     # a fill spanning ~the whole part is the silhouette
# Tooth-tab suppression: an enclosed loop whose interior opens to the exterior
# through a wall this thin (px) with more than this ring fraction is a solid
# protruding tab/step (key teeth), not a real hole.
_TOOTH_WALL_PX = 7
_TOOTH_EXT_FRAC = 0.15


def _erode(np, m, iters):
    for _ in range(iters):
        o = m.copy()
        o[1:, :] &= m[:-1, :]
        o[:-1, :] &= m[1:, :]
        o[:, 1:] &= m[:, :-1]
        o[:, :-1] &= m[:, 1:]
        m = o
    return m


def _dilate(np, m, iters):
    for _ in range(iters):
        o = m.copy()
        o[1:, :] |= m[:-1, :]
        o[:-1, :] |= m[1:, :]
        o[:, 1:] |= m[:, :-1]
        o[:, :-1] |= m[:, 1:]
        m = o
    return m


def _detect_dark_fill_regions(np, gray, part, background, ix0, iy0, ix1, iy1):
    """Interior dark FILLS (recesses/channels) via erosion. Returns a list of
    {mask, bbox, kind, aspect, area, mean_gray}. Logs every candidate with its
    accept/reject reason so the real runtime path is observable."""
    from app.drawing.raster_profile import _connected_regions

    iw, ih = ix1 - ix0 + 1, iy1 - iy0 + 1
    part_area = max(1, part["area"])
    dark = (gray < _DARK_FILL_THRESHOLD) & part["mask"]
    core = _erode(np, dark, _ERODE_ITERS)     # thin strokes vanish; fills remain
    if not core.any():
        return []
    out = []
    for i, r in enumerate(_connected_regions(np, core)):
        # Recover the fill body around this thick core (clamped to the dark mask
        # so it never grows into the surrounding paper).
        comp = _dilate(np, r["mask"], _ERODE_ITERS + 1) & dark
        ys, xs = np.nonzero(comp)
        if len(xs) == 0:
            continue
        x0, y0, x1, y1 = int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())
        w, h = x1 - x0 + 1, y1 - y0 + 1
        area = int(comp.sum())
        aspect = max(w / h, h / w)
        mean_gray = float(gray[comp].mean())
        # The erosion CORE thickness separates a solid FILL (thick core) from a
        # thin outline STROKE that only survived at a junction/corner (thin core).
        # A tooth-tab outline stroke has a sliver core; the shaft recess bar does
        # not — this stops tooth linework from reading as a recess.
        cx0, cy0, cx1, cy1 = r["bbox"]
        core_min = min(cx1 - cx0 + 1, cy1 - cy0 + 1)
        # A recess is surrounded by MATERIAL; a tooth / step / outline blob sits
        # on the silhouette, so much of its perimeter faces the EXTERIOR
        # background. Measure the fraction of the fill's 1px ring that touches
        # background — high ⇒ it's boundary linework, not an interior recess.
        ring = _dilate(np, comp, 2) & ~comp
        ring_total = int(ring.sum()) or 1
        ext_frac = float((ring & background).sum()) / ring_total
        reason = None
        if area < _DARK_FILL_MIN_AREA_FRAC * part_area:
            reason = "too_small"
        elif w >= _DARK_FILL_MAX_SPAN * iw and h >= _DARK_FILL_MAX_SPAN * ih:
            reason = "is_outline_silhouette"
        elif core_min < _CORE_MIN_THICK_PX:
            reason = "stroke_not_fill"
        elif min(w, h) < _RECESS_MIN_THICK_PX:
            reason = "too_thin"
        elif ext_frac > 0.25:
            reason = "on_part_boundary"
        accepted = reason is None
        kind = ("recessed_channel" if aspect >= _RECESS_CHANNEL_ASPECT
                else "blind_recess")
        log_event("drawing_dark_region_candidate", bbox=[x0, y0, x1, y1],
                  area_px=area, aspect_ratio=round(aspect, 2),
                  mean_gray=round(mean_gray, 1), exterior_ring_frac=round(ext_frac, 2),
                  accepted=accepted, reject_reason=reason,
                  kind_candidate=kind if accepted else None)
        if accepted:
            out.append({"mask": comp, "bbox": (x0, y0, x1, y1), "kind": kind,
                        "aspect": aspect, "area": area, "mean_gray": mean_gray})
    return out


def _recess_feature_from_mask(np, mask, kind, w_px, h_px, s, to_mm, fid):
    from app.drawing.raster_profile import _rdp, _trace_boundary

    contour = _trace_boundary(np, mask)
    poly = _rdp(contour, 1.6) if len(contour) >= 6 else []
    if len(poly) >= 2 and poly[0] == poly[-1]:
        poly = poly[:-1]
    if len(poly) < 3:
        return None
    verts = [to_mm(px, py) for px, py in poly]
    ys, xs = np.nonzero(mask)
    center = to_mm(float((xs.min() + xs.max()) / 2), float((ys.min() + ys.max()) / 2))
    return CutFeature(id=fid, kind=kind, center=center, vertices=verts,
                      through=False, width_mm=round(min(w_px, h_px) * s, 3),
                      height_mm=round(max(w_px, h_px) * s, 3), confidence=0.7)


# ------------------------------------------------------------ concentric groups

def _build_concentric_group(group, gi, s, to_mm) -> ConcentricHoleGroup:
    """Normalize N nearly-co-centred circles into ONE concentric feature group.

    All circles are kept (a 3-ring group survives), their centres are SNAPPED to
    the common mean, the smallest is the through-hole, and the larger circles are
    the counterbore/ring steps. Never emits independent through-holes per
    contour."""
    group = sorted(group, key=lambda e: e["rad"], reverse=True)  # outer → inner
    diameters = [round(e["rad"] * 2 * s, 3) for e in group]      # DESC
    # Snap every circle to one common centre (mean of the group's centres).
    scx = sum(e["cx"] for e in group) / len(group)
    scy = sum(e["cy"] for e in group) / len(group)
    center = to_mm(scx, scy)

    through = diameters[-1]
    outer = diameters[0]
    # The counterbore/ring is the circle just OUTSIDE the through-hole; for a
    # 2-circle group that is the outer circle itself.
    ring = diameters[-2] if len(diameters) >= 2 else outer

    # group_kind: a plain 2-ring pocket is a counterbore; a 3+-ring stack has an
    # extra outer boss/lobe boundary around the counterbore.
    if len(diameters) >= 3:
        kind = "counterbore_with_through_hole"
    elif len(diameters) == 2:
        kind = "counterbore_with_through_hole"
    else:  # pragma: no cover - single circles never reach here
        kind = "through_hole_only"

    return ConcentricHoleGroup(
        id=f"group_{gi + 1}", center=center, circles_mm=diameters,
        outer_diameter_mm=outer, counterbore_or_boss_diameter_mm=ring,
        through_hole_diameter_mm=through, group_kind=kind,
        source_entity_ids=[f"circle_{gi + 1}_{j}" for j in range(len(group))],
        confidence=0.6, depth_mm=None, depth_policy="counterbore_default",
        generated_metadata={"circle_count": len(diameters),
                            "snapped_center": True})


# ------------------------------------------------------------------ helpers

def _group_concentric(entries):
    groups: list[list] = []
    for e in sorted(entries, key=lambda e: e["rad"], reverse=True):
        placed = False
        for g in groups:
            gx, gy = g[0]["cx"], g[0]["cy"]
            tol = _CONCENTRIC_TOL * max(g[0]["rad"], e["rad"])
            if math.hypot(e["cx"] - gx, e["cy"] - gy) <= tol:
                g.append(e)
                placed = True
                break
        if not placed:
            groups.append([e])
    return groups


def _poly_area(verts) -> float:
    n = len(verts)
    return abs(sum(verts[i][0] * verts[(i + 1) % n][1] - verts[(i + 1) % n][0] * verts[i][1]
                   for i in range(n))) / 2.0


# ------------------------------------------------------------------ scale

def _resolve_scale(dimension_texts, max_side_px):
    from app.drawing.dim_labels import parse_dimension_labels

    labels = parse_dimension_labels(dimension_texts) if dimension_texts else None
    dims_ir: list[Dimension] = []
    if labels:
        for v in labels.linear:
            dims_ir.append(Dimension(value_mm=v, kind="horizontal", text=str(v),
                                     confidence=0.5))
        for d in labels.diameters:
            dims_ir.append(Dimension(value_mm=d, kind="diameter", text=f"Ø{d}",
                                     confidence=0.5))
        for r in labels.radii:
            dims_ir.append(Dimension(value_mm=r, kind="radius", text=f"R{r}",
                                     confidence=0.5))
    envelope = labels.envelope_mm if labels else None
    if envelope:
        px_per_mm = max_side_px / envelope
        return (envelope / max_side_px,
                Scale(px_per_mm=round(px_per_mm, 4),
                      source_dimension_label=str(envelope), estimated=False),
                dims_ir)
    # Default: scale the largest side to a 100mm envelope (flagged).
    return (100.0 / max_side_px,
            Scale(px_per_mm=round(max_side_px / 100.0, 4), estimated=True),
            dims_ir)
