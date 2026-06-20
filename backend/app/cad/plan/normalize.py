"""Normalize a CadPlan before compiling — assumption-first.

Fills missing SECONDARY dimensions with engineering defaults (recording an
assumption for each), and reconciles the ``expected.*`` intent counts with the
plan's actual features so validation is self-consistent (a plan that describes N
holes is expected to build N holes — no spurious "expected 17 got 16" warnings
from the LLM mis-estimating its own metadata).

Intent counts are tracked SEPARATELY from mesh topology (a coaxial pin through
two ears is one ``pin_hole`` intent, two physical openings).

(Python mirror of the requested ``src/lib/cad/normalizePlan.ts``.)
"""
from __future__ import annotations

from app.cad.plan.defaults import CAD_DEFAULTS
from app.cad.plan.schema import CadPlan, Feature, FeatureKind


def _holes_for(f: Feature) -> tuple[int, int]:
    """(holes, through_holes) a feature contributes — mirrors the compiler."""
    k = f.kind
    if k == FeatureKind.hole:
        return 1, (1 if f.through else 0)
    if k in (FeatureKind.countersink, FeatureKind.counterbore):
        return 1, 1
    if k == FeatureKind.hole_pattern_rect:
        n = int(f.p("nx", 2, "cols")) * int(f.p("ny", 2, "rows"))
        return n, n
    if k == FeatureKind.hole_pattern_circle:
        n = int(f.p("count", 0, "bolt_count", "holes"))
        return n, n
    if k == FeatureKind.circular_flange:
        bolts = int(f.p("bolt_count", 0, "holes", "count", "bolt_holes"))
        bore = 1 if (f.p("bore", 0, "center_bore", "id", "inner_diameter") > 0) else 0
        return bolts, bolts + bore
    if k == FeatureKind.pipe_spool:
        bolts = int(f.p("bolt_count", 8, "holes", "bolt_holes"))
        return bolts * 2, bolts * 2
    return 0, 0


def _is_pin_hole(f: Feature) -> bool:
    if f.kind != FeatureKind.hole:
        return False
    d = (f.description or "").lower()
    return "pin" in d or "pivot" in d


def _assume(plan: CadPlan, seen: set, text: str) -> None:
    if text not in seen:
        plan.assumptions.append(text)
        seen.add(text)


def normalize_cad_plan(plan: CadPlan, prompt: str = "") -> CadPlan:
    seen = set(plan.assumptions)

    # --- 1) Fill missing SECONDARY dimensions per recognized family --------
    ot = (plan.object_type or "").lower()
    for f in plan.features:
        if f.kind == FeatureKind.fillet and f.p("radius", 0, "size", "r") <= 0:
            f.params["radius"] = CAD_DEFAULTS["plate"]["fillet_radius"]
            _assume(plan, seen, f"Assumed {f.params['radius']}mm fillet radius")
        if f.kind == FeatureKind.boss:
            d = CAD_DEFAULTS["bearing_block"]
            if f.p("height", 0, "length", "thickness") <= 0:
                f.params["height"] = d["min_boss_height"]
                _assume(plan, seen, f"Assumed {d['min_boss_height']}mm boss height")
            if f.p("od", 0, "diameter", "outer_diameter") <= 0:
                f.params["od"] = 45
                _assume(plan, seen, "Assumed 45mm boss OD")
        if f.kind == FeatureKind.shell and f.p("thickness", 0, "wall", "wall_thickness") <= 0:
            f.params["thickness"] = CAD_DEFAULTS["enclosure"]["wall_thickness"]
            _assume(plan, seen, f"Assumed {f.params['thickness']}mm wall thickness")

    # --- 2) Reconcile intent counts with the actual features ---------------
    holes = through = 0
    pin = mounting = 0
    flange = boss = 0
    for f in plan.features:
        h, t = _holes_for(f)
        holes += h
        through += t
        if _is_pin_hole(f):
            pin += 1
        elif f.kind in (FeatureKind.hole, FeatureKind.hole_pattern_rect,
                        FeatureKind.hole_pattern_circle) and not _is_center_bore(f):
            mounting += h
        if f.kind == FeatureKind.circular_flange:
            flange += 1
        if f.kind == FeatureKind.pipe_spool:
            flange += 2
        if f.kind == FeatureKind.boss:
            boss += 1

    exp = plan.expected
    # Counts derived from the plan are the intent — they make validation
    # self-consistent (advisory warnings only, never blocking).
    exp.hole_count = holes
    exp.through_hole_count = through
    if pin:
        exp.pin_hole_count = pin
    if mounting:
        exp.mounting_hole_count = mounting
    if flange:
        exp.flange_count = flange
    if boss:
        exp.boss_count = boss
    if "step" not in exp.export_formats or "stl" not in exp.export_formats:
        exp.export_formats = ["step", "stl"]

    return plan


def _is_center_bore(f: Feature) -> bool:
    if f.kind != FeatureKind.hole:
        return False
    x, y, _ = f.at
    return abs(x) < 1e-6 and abs(y) < 1e-6
