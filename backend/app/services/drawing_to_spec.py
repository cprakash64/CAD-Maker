"""Drawing → CAD mapping: DrawingToCADAnalysis → the NORMAL generation pipeline.

Two entry paths produce an analysis (deterministic vector parsing and vision
interpretation); this module turns either into CAD through existing machinery:

* ``plan_from_analysis`` — a strict, parametric ``CadPlan`` (plate / flange /
  spacer / generic extruded part with exact hole positions) compiled by the
  trusted deterministic compiler. Used whenever the analysis carries reliable
  geometry (always for DXF/SVG).
* ``to_interpretation`` — bridges to ``DrawingInterpretationSpec`` so the
  existing assumption-first drawing pipeline (scale inference, feature-graph
  planner, audits, fallbacks) handles everything else.

Missing depth never blocks: it falls back to a per-family default and is
recorded as a visible assumption.
"""
from __future__ import annotations

import math
import re
from collections import Counter

from app.cad.plan.schema import CadPlan, Expected, Feature
from app.observability import log_event
from app.schemas.drawing_analysis import (
    DimensionAnnotation,
    DrawingProfile,
    DrawingToCADAnalysis,
    HoleFeature,
    PatternFeature,
    default_depth_mm,
    normalize_family,
)
from app.schemas.drawing_spec import (
    PIPE_BRANCH_DETERMINISTIC_FAMILIES,
    DrawingAssumption,
    DrawingHoleCalloutSpec,
    DrawingInterpretationSpec,
)
from app.services.drawing_ingest import VectorAnalysisRaw

_INCH_MM = 25.4
_NUM_RE = re.compile(r"[-+]?\d+\.?\d*")


def _first_number(text: str) -> float | None:
    m = _NUM_RE.search(text)
    return float(m.group()) if m else None


# ------------------------------------------------------------------ vector path

def analysis_from_vector(
    raw: VectorAnalysisRaw,
    *,
    filename: str | None = None,
    units: str | None = None,
    thickness_mm: float | None = None,
    family: str | None = None,
    notes: str | None = None,
) -> DrawingToCADAnalysis:
    """Deterministic vector geometry → analysis. Exact positions, no vision."""
    scale = _INCH_MM if units == "inch" else 1.0
    analysis = DrawingToCADAnalysis(
        source=raw.source,  # type: ignore[arg-type]
        units="mm",
        drawing_type="mechanical_part",
        title=(filename or "").rsplit(".", 1)[0].replace("_", " ").strip() or None,
        scale_confidence=0.5 if raw.units_assumed else 0.95,
        confidence_score=0.9,
        dimension_annotations=[
            DimensionAnnotation(text=t, value=_first_number(t), confidence=0.9)
            for t in raw.dimension_texts[:64]
        ],
    )
    if units == "inch":
        analysis.assume("Uploaded vector coordinates interpreted as inches and converted to mm")
    elif raw.units_note:
        analysis.assume(raw.units_note)
    analysis.ambiguities.extend(raw.warnings)

    span_w = (raw.max_x - raw.min_x) * scale
    span_h = (raw.max_y - raw.min_y) * scale
    cx = (raw.min_x + raw.max_x) / 2
    cy = (raw.min_y + raw.max_y) / 2

    if raw.outer_circle is not None:
        d = raw.outer_circle.diameter * scale
        analysis.outer_profile = DrawingProfile(kind="circle", diameter_mm=d)
        analysis.overall_dimensions.width_mm = d
        analysis.overall_dimensions.height_mm = d
        cx, cy = raw.outer_circle.x, raw.outer_circle.y
    elif raw.outer_rect is not None:
        _, _, w, h = raw.outer_rect
        analysis.outer_profile = DrawingProfile(
            kind="rectangle", width_mm=w * scale, height_mm=h * scale,
            corner_radius_mm=(raw.corner_radius or 0) * scale or None)
        analysis.overall_dimensions.width_mm = w * scale
        analysis.overall_dimensions.height_mm = h * scale
        rx, ry, _, _ = raw.outer_rect
        cx, cy = rx + w / 2, ry + h / 2
    elif raw.polygon:
        analysis.outer_profile = DrawingProfile(
            kind="polygon", width_mm=span_w, height_mm=span_h,
            points=[{"x": (x - cx) * scale, "y": (y - cy) * scale} for x, y in raw.polygon],
        )
        analysis.overall_dimensions.width_mm = span_w
        analysis.overall_dimensions.height_mm = span_h
        analysis.assume("Outer profile taken from the largest closed polyline")
    else:
        analysis.outer_profile = DrawingProfile(kind="rectangle",
                                                width_mm=span_w, height_mm=span_h)
        analysis.overall_dimensions.width_mm = span_w
        analysis.overall_dimensions.height_mm = span_h
        analysis.assume(
            "No single closed outer profile found; the part outline was taken "
            "from the drawing's overall bounding box")

    # Holes: exact centers relative to the part center. A circle concentric
    # with a circular outer profile is the center bore (inner profile).
    for c in raw.circles:
        x, y, d = (c.x - cx) * scale, (c.y - cy) * scale, c.diameter * scale
        if (analysis.outer_profile.kind == "circle"
                and math.hypot(x, y) < 0.05 * (analysis.outer_profile.diameter_mm or 1)):
            analysis.inner_profiles.append(DrawingProfile(kind="circle", diameter_mm=d))
        else:
            analysis.features.through_holes.append(
                HoleFeature(diameter_mm=d, x_mm=round(x, 3), y_mm=round(y, 3),
                            confidence=0.95))

    _detect_circular_pattern(analysis)
    _apply_family_and_depth(analysis, family=family, thickness_mm=thickness_mm,
                            notes=notes)
    return analysis


def _detect_circular_pattern(analysis: DrawingToCADAnalysis) -> None:
    """3+ same-diameter holes equidistant from center → one circular pattern
    (kept as a pattern so symmetry survives into the plan and the audit).

    Only applied on CIRCULAR outer profiles: on rectangular plates the corner
    holes are also equidistant from center, but their exact rectangular
    placement must be preserved — a bolt-circle would move them."""
    if analysis.outer_profile.kind != "circle":
        return
    holes = analysis.features.through_holes
    if len(holes) < 3:
        return
    by_dia = Counter(round(h.diameter_mm, 1) for h in holes)
    dia, n = by_dia.most_common(1)[0]
    if n < 3:
        return
    group = [h for h in holes if round(h.diameter_mm, 1) == dia]
    radii = [math.hypot(h.x_mm, h.y_mm) for h in group]
    mean_r = sum(radii) / len(radii)
    if mean_r < 1e-6 or any(abs(r - mean_r) > max(0.02 * mean_r, 0.5) for r in radii):
        return
    analysis.features.patterns.append(PatternFeature(
        kind="circular", count=len(group), hole_diameter_mm=dia,
        pitch_circle_diameter_mm=round(2 * mean_r, 2)))
    analysis.features.through_holes = [h for h in holes if h not in group]


# ------------------------------------------------------------------ vision path

def analysis_from_interpretation(
    interp: DrawingInterpretationSpec,
    *,
    units: str | None = None,
    thickness_mm: float | None = None,
    family: str | None = None,
    notes: str | None = None,
) -> DrawingToCADAnalysis:
    """Bridge a vision interpretation into the unified analysis shape."""
    dims = dict(interp.overall_dimensions)

    def pick(*needles: str) -> float | None:
        for k, v in dims.items():
            key = k.lower()
            if v and v > 0 and any(n in key for n in needles):
                return float(v)
        return None

    analysis = DrawingToCADAnalysis(
        source="vision",
        units="mm",
        drawing_type=("mechanical_part" if interp.is_mechanical()
                      else "unknown"),
        title=interp.title,
        detected_views=[{"view": _VIEW_NAMES.get(str(v.view_type), "unknown")}
                        for v in interp.views],
        scale_confidence=interp.drawing_units_confidence,
        confidence_score=interp.overall_confidence,
        recommended_family=family or interp.suggested_object_type,
        assumptions=[f"{a.field}: {a.assumption}" for a in interp.assumptions],
        ambiguities=[q.question for q in interp.clarification_questions],
        clarification_questions=[q.question for q in interp.clarification_questions],
    )
    analysis.overall_dimensions.width_mm = pick("width", "length", "outer_diameter", "od")
    analysis.overall_dimensions.height_mm = pick("height", "depth") or \
        analysis.overall_dimensions.width_mm
    analysis.overall_dimensions.depth_mm = pick("thickness", "thick")
    for h in interp.holes:
        if not h.diameter or h.diameter <= 0:
            continue
        if h.count >= 3:  # repeated callouts stay patterns, preserving symmetry
            analysis.features.patterns.append(PatternFeature(
                kind="circular", count=h.count, hole_diameter_mm=h.diameter))
        else:
            analysis.features.through_holes.extend(
                HoleFeature(diameter_mm=h.diameter, confidence=h.confidence)
                for _ in range(h.count))
    analysis.needs_clarification = not interp.generatable_with_assumptions()
    _apply_family_and_depth(analysis, family=family, thickness_mm=thickness_mm,
                            notes=notes)
    return analysis


_VIEW_NAMES = {"front": "front", "top": "top", "right": "side", "left": "side",
               "isometric": "isometric", "section": "section"}


# ------------------------------------------------------- family / depth defaults

def _apply_family_and_depth(
    analysis: DrawingToCADAnalysis,
    *,
    family: str | None,
    thickness_mm: float | None,
    notes: str | None,
) -> None:
    fam = normalize_family(family) if family else None
    if fam is None:
        fam = _infer_family(analysis, notes)
        if fam != "generic_extruded_part":
            analysis.assume(f"Part family inferred from the drawing: {fam.replace('_', ' ')}")
    analysis.recommended_family = fam

    depth = thickness_mm or analysis.overall_dimensions.depth_mm
    if thickness_mm:
        analysis.assume(f"Thickness/depth {thickness_mm:g}mm taken from your input")
    if not depth:
        depth = _depth_from_annotations(analysis)
        if depth:
            analysis.assume(f"Depth {depth:g}mm read from a drawing annotation")
    if not depth:
        depth = default_depth_mm(fam)
        # Pipe/flange/branch families are a pre-existing, deliberate exception
        # (see the "PART G" comment in app.routers.drawings): this whole
        # family is built from proportional estimates (wall/PCD/flange
        # thickness/branch length) as a documented, accepted characteristic,
        # capped at "review" rather than "failed" -- unlike a flat part's
        # primary Z-depth, which genuinely defines the whole solid and has no
        # such family-level allowance.
        if fam in PIPE_BRANCH_DETERMINISTIC_FAMILIES:
            analysis.assume(
                f"The drawing does not show a depth/thickness — assumed "
                f"{depth:g}mm (typical for a {fam.replace('_', ' ')}); pipe/"
                f"flange dimensions are routinely estimated from drawing "
                f"proportions for this family (review, not a hard block).")
            analysis.ambiguities.append("missing depth")
        else:
            analysis.assume(
                f"The drawing does not show a depth/thickness — assumed "
                f"{depth:g}mm (typical for a {fam.replace('_', ' ')}). This is "
                f"a CRITICAL unresolved dimension: the final export is "
                f"blocked until a real thickness is confirmed (upload a "
                f"clearer drawing, or resubmit with an explicit thickness "
                f"override).")
            analysis.mark_critical_ambiguity("depth", "missing depth")
    analysis.inferred_depth_mm = depth
    analysis.overall_dimensions.depth_mm = analysis.overall_dimensions.depth_mm or depth


def _infer_family(analysis: DrawingToCADAnalysis, notes: str | None) -> str:
    text = (notes or "").lower()
    for fam in ("flange", "bracket", "enclosure", "clamp", "spacer", "standoff",
                "gear", "jig", "wheel", "tire", "plate"):
        if fam in text:
            return normalize_family(fam) or "generic_extruded_part"
    p = analysis.outer_profile
    if p.kind == "circle":
        # Circular outer + bolt-circle pattern → flange; with a big center bore
        # and no pattern → spacer/washer-like part.
        if analysis.features.patterns:
            return "flange"
        if analysis.inner_profiles:
            return "spacer"
        return "flange"
    if p.kind in ("rectangle", "polygon", "unknown"):
        if analysis.features.patterns or analysis.features.through_holes:
            return "adapter_plate"
    return "generic_extruded_part"


def _depth_from_annotations(analysis: DrawingToCADAnalysis) -> float | None:
    for ann in analysis.dimension_annotations:
        t = ann.text.lower()
        if ann.value and ann.value > 0 and ("thk" in t or "thick" in t or "depth" in t):
            return float(ann.value)
    return None


# ------------------------------------------------------------- plan construction

def plan_from_analysis(analysis: DrawingToCADAnalysis) -> CadPlan | None:
    """Build a strict parametric CadPlan when the analysis carries reliable
    geometry. Returns None when the drawing needs the LLM feature-graph path
    (vision-only analyses without exact positions)."""
    if analysis.source == "vision":
        return None  # vision output goes through the assumption-first LLM path
    p = analysis.outer_profile
    depth = analysis.inferred_depth_mm or default_depth_mm(analysis.recommended_family)
    if p.kind == "circle" and p.diameter_mm:
        return _plan_round_part(analysis, p.diameter_mm, depth)
    if p.width_mm and p.height_mm:
        return _plan_plate_part(analysis, p.width_mm, p.height_mm, depth)
    return None


def _hole_features(analysis: DrawingToCADAnalysis, depth: float,
                   skip_patterns: int = 0) -> tuple[list[Feature], int]:
    """Exact-position hole/pattern/slot cut features. ``skip_patterns`` lets a
    builder omit patterns it already consumed (e.g. a flange's bolt circle)."""
    feats: list[Feature] = []
    n = 0
    for i, h in enumerate(analysis.features.through_holes):
        feats.append(Feature(
            id=f"hole_{i + 1}", kind="hole", op="cut",
            description=f"Ø{h.diameter_mm:g}mm through hole",
            params={"diameter": h.diameter_mm},
            at=[h.x_mm, h.y_mm, 0.0], through=True))
        n += 1
    for j, pat in enumerate(analysis.features.patterns[skip_patterns:]):
        if pat.kind == "circular" and pat.pitch_circle_diameter_mm:
            feats.append(Feature(
                id=f"bolt_circle_{j + 1}", kind="hole_pattern_circle", op="cut",
                description=(f"{pat.count}× Ø{pat.hole_diameter_mm:g}mm holes on a "
                             f"Ø{pat.pitch_circle_diameter_mm:g}mm bolt circle"),
                params={"count": pat.count, "diameter": pat.hole_diameter_mm,
                        "pcd": pat.pitch_circle_diameter_mm}))
            n += pat.count
    for k, s in enumerate(analysis.features.slots):
        feats.append(Feature(
            id=f"slot_{k + 1}", kind="slot", op="cut",
            description=f"{s.length_mm:g}×{s.width_mm:g}mm slot",
            params={"length": s.length_mm, "width": s.width_mm, "angle": s.angle_deg},
            at=[s.x_mm, s.y_mm, 0.0]))
    return feats, n


def _plan_plate_part(analysis: DrawingToCADAnalysis, width: float, height: float,
                     depth: float) -> CadPlan:
    fam = analysis.recommended_family or "adapter_plate"
    features = [Feature(
        id="base_plate", kind="plate",
        description=f"{width:g}×{height:g}×{depth:g}mm base plate from the drawing's outer profile",
        params={"width": width, "depth": height, "thickness": depth})]
    if analysis.outer_profile.corner_radius_mm:
        features.append(Feature(
            id="corner_fillet", kind="fillet",
            description="rounded corners from the drawing",
            params={"radius": analysis.outer_profile.corner_radius_mm}))
    holes, n = _hole_features(analysis, depth)
    features.extend(holes)
    return CadPlan(
        object_type=fam if fam != "generic_extruded_part" else "generic_mechanical_part",
        name=analysis.title or fam.replace("_", " "),
        assumptions=list(analysis.assumptions),
        features=features,
        expected=Expected(
            bbox_mm={"x": width, "y": height, "z": depth},
            hole_count=n, through_hole_count=n,
        ),
    )


def _plan_round_part(analysis: DrawingToCADAnalysis, od: float, depth: float) -> CadPlan:
    fam = analysis.recommended_family or "flange"
    bore = max((ip.diameter_mm or 0) for ip in analysis.inner_profiles) \
        if analysis.inner_profiles else 0.0
    n = 0
    consumed_patterns = 0
    if analysis.features.patterns:
        pat = analysis.features.patterns[0]
        pcd = pat.pitch_circle_diameter_mm or round(od - 2.5 * pat.hole_diameter_mm, 1)
        body = Feature(
            id="flange_body", kind="circular_flange",
            description=f"Ø{od:g}×{depth:g}mm circular flange from the drawing",
            params={"od": od, "thickness": depth, "pcd": pcd,
                    "bolt_count": pat.count, "bolt_diameter": pat.hole_diameter_mm,
                    "bore": bore})
        n += pat.count + (1 if bore else 0)
        consumed_patterns = 1  # the flange body already cuts this bolt circle
        features = [body]
    else:
        features = [Feature(
            id="body", kind="cylinder",
            description=f"Ø{od:g}×{depth:g}mm cylindrical body from the drawing",
            params={"diameter": od, "height": depth})]
        if bore:
            features.append(Feature(
                id="center_bore", kind="hole", op="cut",
                description=f"Ø{bore:g}mm center bore",
                params={"diameter": bore}, at=[0, 0, 0]))
            n += 1
    holes, extra = _hole_features(analysis, depth, skip_patterns=consumed_patterns)
    features.extend(holes)
    n += extra
    return CadPlan(
        object_type="blind_flange" if (fam == "flange" and not bore) else fam,
        name=analysis.title or fam.replace("_", " "),
        assumptions=list(analysis.assumptions),
        features=features,
        expected=Expected(
            bbox_mm={"x": od, "y": od, "z": depth},
            hole_count=n or None, through_hole_count=n or None,
        ),
    )


# --------------------------------------------------------- raster profile path

def plan_from_raster_profile(
    profile,
    interp=None,
    thickness_mm: float | None = None,
    dimension_texts: list[str] | None = None,
) -> tuple[CadPlan, bool]:
    """Deterministically traced raster outline → profile_extrusion CadPlan.

    Returns (plan, used_default_scale). Scale priority: dimensions read by the
    vision pass > dimension labels parsed from sheet text (PDF text layer /
    notes) > default envelope, flagged (the design lands in REVIEW, never a
    clean PASS). The traced geometry is authoritative: detected enclosed holes
    are cut exactly where drawn; nothing else is invented."""
    assumptions = list(profile.notes)
    warnings: list[str] = []
    used_default_scale = True
    scale = None
    labels = None
    if interp is not None and interp.overall_dimensions:
        from app.drawing.scale import infer_scale

        scaled = infer_scale(interp)
        assumptions.extend(scaled.assumptions)
        dims = [v for k, v in scaled.dimensions.items()
                if v > 0 and not any(h in k.lower() for h in ("count", "thick", "depth"))]
        if dims:
            target = max(dims)
            scale = target / max(profile.width_px, profile.height_px)
            used_default_scale = False
            assumptions.append(
                f"Profile scaled so its largest side is {target:g}mm "
                "(largest dimension read from the drawing)")
    if scale is None and dimension_texts:
        from app.drawing.dim_labels import parse_dimension_labels

        labels = parse_dimension_labels(dimension_texts)
        if labels.envelope_mm:
            scale = labels.envelope_mm / max(profile.width_px, profile.height_px)
            assumptions.append(
                f"Profile scaled so its largest side is {labels.envelope_mm:g}mm "
                "(largest dimension label found on the sheet)")
            warnings.append("Dimensions estimated from drawing image; "
                            "verify before manufacturing.")
    if scale is None:
        scale = 100.0 / max(profile.width_px, profile.height_px)
        assumptions.append(
            "No legible overall dimension — profile scaled to a 100mm envelope "
            "(adjust dimensions in the studio)")
        warnings.append("Dimensions estimated from drawing image; "
                        "verify before manufacturing.")

    thickness = thickness_mm
    if thickness:
        assumptions.append(f"Extruded {thickness:g}mm deep (your input)")
    else:
        thickness = _thickness_from_interp(interp) or \
            (labels.thickness if labels is not None else None)
        if thickness:
            assumptions.append(f"Extruded {thickness:g}mm deep (read from the drawing)")
    if not thickness:
        thickness = 6.0
        assumptions.append(
            "The drawing does not show a depth — extruded 6mm (typical plate stock)")

    w_mm = profile.width_px * scale
    h_mm = profile.height_px * scale
    pts = [[round(x - w_mm / 2, 3), round(y - h_mm / 2, 3)]
           for x, y in profile.scaled_points(scale)]
    features = [Feature(
        id="outer_profile", kind="extruded_profile",
        description="dimensioned 2D outline from the drawing, extruded",
        params={"thickness": thickness}, profile=pts)]
    features.extend(_raster_hole_features(profile, scale, w_mm, h_mm, assumptions))
    n_holes = len(features) - 1
    assumptions.extend(warnings)  # surfaced on the design like any assumption
    plan = CadPlan(
        object_type="profile_extrusion",
        name="extruded profile (from drawing)",
        assumptions=assumptions,
        features=features,
        expected=Expected(
            bbox_mm={"x": round(w_mm, 2), "y": round(h_mm, 2), "z": thickness},
            hole_count=n_holes, through_hole_count=n_holes,
        ),
    )
    return plan, used_default_scale


# Holes smaller than this after scaling are line-weight noise, not features.
_MIN_HOLE_MM = 0.8


def _raster_hole_features(profile, scale: float, w_mm: float, h_mm: float,
                          assumptions: list[str]) -> list[Feature]:
    """Cut features for the profile's enclosed hole regions, at their exact
    traced positions (centered plan coordinates), PRESERVING SHAPE.

    Circle/ellipse regions cut as a round hole. Every other shape (hexagon,
    rectangle, slot, arbitrary polygon) is cut with its EXACT traced polygon —
    a hexagonal hole stays hexagonal, never an equivalent-area circle. A polygon
    is only downgraded to a circle if its vertices are missing/degenerate, and
    only then is an approximation assumption recorded."""
    feats: list[Feature] = []
    for i, hole in enumerate(getattr(profile, "holes", []) or []):
        dia = (hole.diameter_px if hole.is_round
               else hole.equivalent_diameter_px) * scale
        if dia < _MIN_HOLE_MM:
            continue
        x = round(hole.x_px * scale - w_mm / 2, 3)
        y = round(hole.y_px * scale - h_mm / 2, 3)
        polygon = getattr(hole, "polygon", None) or []
        kind = getattr(hole, "kind", "circle")
        if not hole.is_round and len(polygon) >= 3:
            # Exact polygon cut: vertices scaled + centered to plan coordinates.
            pts = [[round(px * scale - w_mm / 2, 3),
                    round(py * scale - h_mm / 2, 3)] for px, py in polygon]
            feats.append(Feature(
                id=f"drawn_hole_{i + 1}", kind="polygon_cut", op="cut",
                description=f"{kind.replace('_', ' ')} cutout traced from the drawing",
                profile=pts, through=True))
            log_event("drawing_polygon_hole_cut", kind=kind, verts=len(pts))
            continue
        if not hole.is_round:
            # Polygon unavailable — only NOW fall back to an equivalent circle.
            assumptions.append(
                f"Non-circular cutout approximated as a Ø{dia:.1f}mm hole "
                "(polygon trace unavailable) — refine in the studio")
            log_event("drawing_polygon_hole_fallback_to_circle", kind=kind)
        feats.append(Feature(
            id=f"drawn_hole_{i + 1}", kind="hole", op="cut",
            description=(f"Ø{dia:.1f}mm hole traced from the drawing"
                         if hole.is_round else
                         f"cutout from the drawing (≈Ø{dia:.1f}mm)"),
            params={"diameter": round(dia, 3)},
            at=[x, y, 0.0], through=True))
    return feats


def _thickness_from_interp(interp) -> float | None:
    if interp is None:
        return None
    for k, v in (interp.overall_dimensions or {}).items():
        if v and v > 0 and ("thick" in k.lower() or "depth" in k.lower()):
            return float(v)
    return None


def profile_route_applies(profile, interp) -> bool:
    """Use the deterministic profile path when the traced outline is trustworthy.

    DETERMINISTIC BY DESIGN: the decision is driven by the CV geometry (the
    traced contour and its enclosed holes), NOT by whether the vision call
    happened to succeed or time out — the same image must take the same route
    every run. The traced contour and its holes are extruded here (holes
    preserved); only a recognized SPECIFIC family from vision (a pipe, a named
    template — never a bare 'generic mechanical part') defers to its dedicated
    builder. Concentric/slotted drawings are taken earlier by the richer sketch
    reconstruction, so this route only ever sees plain hole-bearing profiles."""
    if profile is None:
        return False
    if profile.interior_fraction < 0.02:
        # The traced loop covers almost none of the sheet: it's one box on a
        # multi-view/annotated sheet (title block, section frame), not the part.
        return False
    # A SPECIFIC recognized family (not a bare generic reading) keeps its
    # dedicated builder; everything else — generic/unknown/timed-out — takes the
    # deterministic contour route so the route can't flip on a vision timeout.
    if interp is not None and interp.generatable_with_assumptions() \
            and interp.suggested_object_type not in (
                None, "generic_mechanical_part", "generic_extruded_part",
                "profile_extrusion"):
        return False
    return True


_POLY_SHAPES = ("hexagon", "regular polygon", "rectangle", "rounded slot",
                "arbitrary polygon", "ellipse")


def _shape_from_description(desc: str) -> str:
    d = (desc or "").lower()
    for s in _POLY_SHAPES:
        if s in d:
            return s.replace(" ", "_")
    return "arbitrary_polygon"


def analysis_from_sketch_ir(ir) -> DrawingToCADAnalysis:
    """UI/annotation analysis for a reconstructed mechanical sketch."""
    outer = ir.outer
    bb = outer.bbox_mm if outer else {}
    analysis = DrawingToCADAnalysis(
        source="sketch_ir", units="mm", drawing_type="mechanical_part",
        title="Reconstructed 2D sketch (from drawing)",
        recommended_family="generic_extruded_part",
        confidence_score=ir.confidence,
        assumptions=list(ir.assumptions),
        ambiguities=list(ir.warnings),
    )
    kind = "rectangle" if (outer and "rect" in outer.kind) else "polygon"
    analysis.outer_profile = DrawingProfile(
        kind=kind, width_mm=bb.get("w"), height_mm=bb.get("h"),
        corner_radius_mm=outer.corner_radius_mm if outer else None,
        points=[{"x": v[0], "y": v[1]} for v in (outer.vertices if outer else [])][:256],
    )
    analysis.overall_dimensions.width_mm = bb.get("w")
    analysis.overall_dimensions.height_mm = bb.get("h")
    analysis.overall_dimensions.depth_mm = ir.default_thickness_mm
    analysis.inferred_depth_mm = ir.default_thickness_mm
    for cf in ir.cut_features:
        if cf.kind == "circular_hole" and cf.diameter_mm:
            analysis.features.through_holes.append(HoleFeature(
                diameter_mm=cf.diameter_mm, x_mm=cf.center[0], y_mm=cf.center[1],
                confidence=cf.confidence))
        else:
            d = cf.width_mm or 1.0
            analysis.features.through_holes.append(HoleFeature(
                diameter_mm=max(d, 1.0), x_mm=cf.center[0], y_mm=cf.center[1],
                shape=_IR_SHAPE.get(cf.kind, "arbitrary_polygon"),
                confidence=cf.confidence))
    for nf in ir.nested_features:
        analysis.features.through_holes.append(HoleFeature(
            diameter_mm=max(nf.inner_diameter_mm, 1.0),
            x_mm=nf.center[0], y_mm=nf.center[1], kind="counterbore",
            confidence=nf.confidence))
    return analysis


_IR_SHAPE = {"rectangular_slot": "rectangle", "rounded_slot": "rounded_slot",
             "arc_slot": "rounded_slot", "polygon_hole": "hexagon",
             "arbitrary_cutout": "arbitrary_polygon"}


def analysis_from_profile(profile, plan: CadPlan,
                          confidence: float) -> DrawingToCADAnalysis:
    """UI-facing analysis for a deterministically traced raster profile."""
    exp = plan.expected.bbox_mm or {}
    feature = plan.features[0]
    analysis = DrawingToCADAnalysis(
        source="raster_profile", units="mm", drawing_type="mechanical_part",
        title=plan.name,
        recommended_family="generic_extruded_part",
        confidence_score=confidence,
        assumptions=list(plan.assumptions),
    )
    analysis.outer_profile = DrawingProfile(
        kind="polygon", width_mm=exp.get("x"), height_mm=exp.get("y"),
        points=[{"x": p[0], "y": p[1]} for p in (feature.profile or [])][:256],
    )
    for f in plan.features[1:]:
        if f.kind.value == "hole":
            analysis.features.through_holes.append(HoleFeature(
                diameter_mm=f.p("diameter"), x_mm=f.at[0], y_mm=f.at[1],
                confidence=0.8))
        elif f.kind.value == "polygon_cut" and f.profile:
            # Polygon cut (hexagon / rectangle / slot): report its shape + a
            # bounding diameter and centroid so metadata shows the true shape.
            shape = _shape_from_description(f.description)
            xs = [p[0] for p in f.profile]
            ys = [p[1] for p in f.profile]
            analysis.features.through_holes.append(HoleFeature(
                diameter_mm=max(max(xs) - min(xs), max(ys) - min(ys)) or 1.0,
                x_mm=round(sum(xs) / len(xs), 3), y_mm=round(sum(ys) / len(ys), 3),
                shape=shape, confidence=0.8))
    analysis.overall_dimensions.width_mm = exp.get("x")
    analysis.overall_dimensions.height_mm = exp.get("y")
    analysis.overall_dimensions.depth_mm = exp.get("z")
    analysis.inferred_depth_mm = exp.get("z")
    return analysis


# ------------------------------------------------------------------- interp bridge

def to_interpretation(analysis: DrawingToCADAnalysis) -> DrawingInterpretationSpec:
    """Analysis → DrawingInterpretationSpec so the existing assumption-first
    generation path (`_generate_from_interpretation`) can build it."""
    dims: dict[str, float] = {}
    d = analysis.overall_dimensions
    if analysis.outer_profile.kind == "circle" and analysis.outer_profile.diameter_mm:
        dims["outer_diameter_mm"] = analysis.outer_profile.diameter_mm
    else:
        if d.width_mm:
            dims["width"] = d.width_mm
        if d.height_mm:
            dims["depth"] = d.height_mm
    if analysis.inferred_depth_mm:
        dims["thickness"] = analysis.inferred_depth_mm
    holes: list[DrawingHoleCalloutSpec] = [
        DrawingHoleCalloutSpec(diameter=h.diameter_mm, count=h.count,
                               confidence=h.confidence)
        for h in analysis.features.through_holes
    ] + [
        DrawingHoleCalloutSpec(diameter=p.hole_diameter_mm, count=p.count,
                               pattern=f"circular PCD {p.pitch_circle_diameter_mm or '?'}")
        for p in analysis.features.patterns
    ]
    fam = analysis.recommended_family or "generic_extruded_part"
    from app.schemas.drawing_spec import UnresolvedDimension

    unresolved = [
        UnresolvedDimension(field=cat, category=cat, reason="missing",
                            detail=f"'{cat}' was not shown on the source drawing",
                            critical=True)
        for cat in analysis.critical_ambiguities
    ]
    return DrawingInterpretationSpec(
        title=analysis.title,
        units="mm",
        suggested_object_type=_FAMILY_TO_DRAWING_TYPE.get(fam, "generic_mechanical_part"),
        detected_object_type=fam,
        overall_dimensions=dims,
        holes=holes,
        assumptions=[DrawingAssumption(field="drawing", assumption=a)
                     for a in analysis.assumptions],
        unresolved_dimensions=unresolved,
        overall_confidence=max(analysis.confidence_score, 0.5),
        drawing_units_confidence=analysis.scale_confidence,
    )


_FAMILY_TO_DRAWING_TYPE = {
    "adapter_plate": "adapter_plate", "mounting_plate": "mounting_plate",
    "plate": "mounting_plate", "bracket": "bracket", "l_bracket": "l_bracket",
    "u_bracket": "u_bracket", "enclosure": "electronics_enclosure",
    "flange": "blind_flange", "blind_flange": "blind_flange",
    "clamp": "pipe_clamp", "spacer": "spacer", "standoff": "spacer",
    "gear": "simple_gear_or_pulley", "jig": "drill_jig",
    "generic_extruded_part": "generic_mechanical_part",
}
