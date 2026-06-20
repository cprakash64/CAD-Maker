"""Structured "requested vs generated" dimension report + print-readiness.

This is the user-facing trust artifact: for a generated part it states what was
requested, what the geometry actually measures (BRep + mesh ground truth), and
whether the two agree within the configured tolerance — plus a print-readiness
summary. It NEVER mutates geometry; it only reports.
"""
from __future__ import annotations

from app.cad.measure import measure_solid, mesh_facts
from app.cad.plan.compiler import CadPlanResult
from app.cad.plan.schema import HOLE_KINDS, CadPlan
from app.cad.tolerance import diameter_tolerance, length_tolerance, policy_dict, within
from app.config import settings


def _smallest_requested_hole(plan: CadPlan) -> float | None:
    dias: list[float] = []
    for f in plan.features:
        if f.kind in HOLE_KINDS:
            d = f.p("diameter", 0.0, "dia", "d", "hole_diameter", "bolt_diameter")
            if d > 0:
                dias.append(d)
    return min(dias) if dias else None


def _compare_bbox(requested: dict, measured: dict) -> list[dict]:
    out: list[dict] = []
    for axis in ("x", "y", "z"):
        if axis not in requested:
            continue
        exp = float(requested[axis])
        act = float(measured.get(axis, 0.0))
        tol = length_tolerance(exp)
        out.append({
            "name": f"bbox_{axis}",
            "requested_mm": round(exp, 3),
            "measured_mm": round(act, 3),
            "tolerance_mm": round(tol, 3),
            "delta_mm": round(act - exp, 3),
            "within": within(exp, act, tol),
        })
    return out


def _print_readiness(measured: dict, smallest_hole: float | None) -> dict:
    issues: list[str] = []
    watertight = bool(measured.get("watertight"))
    manifold = bool(measured.get("manifold"))
    single_body = measured.get("components", 1) == 1
    volume_ok = float(measured.get("volume_mm3", 0.0)) > 0.0

    if not volume_ok:
        issues.append("Generated solid has zero or invalid volume — nothing to print.")
    if not watertight:
        issues.append("Mesh is not watertight (open faces); most slicers prefer a closed solid.")
    if not manifold:
        issues.append("Mesh is non-manifold (edges shared by !=2 faces); may confuse slicers.")
    if not single_body:
        issues.append(f"{measured.get('components')} disconnected bodies (expected 1 fused part).")

    min_hole_ok = True
    if smallest_hole is not None and smallest_hole < settings.printer_min_hole_mm:
        min_hole_ok = False
        issues.append(
            f"Smallest hole Ø{smallest_hole:g}mm is below the printable minimum "
            f"{settings.printer_min_hole_mm:g}mm and may not form."
        )

    tiny_ok = True
    bbox = measured.get("bbox_mm", {})
    positives = [v for v in (bbox.get("x"), bbox.get("y"), bbox.get("z")) if v and v > 0]
    if positives and min(positives) < settings.printer_min_feature_mm:
        tiny_ok = False
        issues.append(
            f"Smallest overall extent {min(positives):g}mm is below the printable "
            f"feature floor {settings.printer_min_feature_mm:g}mm."
        )

    return {
        "printable": bool(volume_ok and min_hole_ok and tiny_ok),
        "watertight": watertight,
        "manifold": manifold,
        "single_body": single_body,
        "positive_volume": volume_ok,
        "min_hole_diameter_mm": round(smallest_hole, 3) if smallest_hole is not None else None,
        "min_printable_hole_mm": settings.printer_min_hole_mm,
        # We do not run a full geometric minimum-wall solver; surfaced honestly.
        "min_wall_checked": False,
        "issues": issues,
    }


def _compensation_notes() -> list[str]:
    comp = settings.printer_xy_compensation_mm
    if comp:
        return [f"Printer XY compensation of {comp:g}mm is applied to the geometry."]
    return ["No printer compensation applied — requested dimensions preserved exactly."]


def _validation_summary(measured: dict, requested: dict,
                        within_tolerance: bool | None, pr: dict) -> dict:
    """Classify the build into pass / warning / critical_failure.

    CRITICAL (production-blocking trust failures): disconnected bodies, out-of-
    tolerance dimensions, hole-count / through-hole mismatch, non-watertight or
    non-manifold mesh, zero/negative volume. WARNINGS: non-blocking printability
    advisories (e.g. a hole below the printable minimum). Everything else passes.
    """
    crit: list[str] = []
    if measured.get("components", 1) != 1:
        crit.append(
            f"Disconnected geometry: {measured.get('components')} separate bodies "
            "(expected one fused solid)."
        )
    if within_tolerance is False:
        crit.append("Generated dimensions are outside the requested tolerance.")
    if measured.get("watertight") is False:
        crit.append("Mesh is not watertight (open / leaking surface).")
    if measured.get("manifold") is False:
        crit.append("Mesh is non-manifold (edges shared by more than two faces).")
    if (measured.get("volume_mm3") or 0) <= 0:
        crit.append("Generated solid has zero or negative volume.")
    rc = requested.get("hole_count")
    if rc is not None and measured.get("hole_count") != rc:
        crit.append(
            f"Missing/extra holes: requested {rc}, generated {measured.get('hole_count')}."
        )
    rt = requested.get("through_hole_count")
    if rt is not None and measured.get("through_hole_count") != rt:
        crit.append(
            f"Through-hole count mismatch: requested {rt}, "
            f"generated {measured.get('through_hole_count')}."
        )

    # Non-critical printability advisories (geometry-health issues are already
    # criticals above, so only keep the "printable" floor warnings here).
    warn = [i for i in (pr.get("issues") or []) if "printable" in i]

    status = "critical_failure" if crit else ("warning" if warn else "pass")
    return {"status": status, "critical_failures": crit, "warnings": warn}


def build_spec_report(
    *, requested_dimensions_mm: dict, bbox_mm: dict, volume_mm3: float,
    surface_area_mm2: float, hole_count: int, smallest_hole_mm: float | None,
    stl_bytes: bytes,
) -> dict:
    """Dimension report for a template / DesignSpec-built part.

    Template parameters don't map 1:1 to bounding-box axes, so this echoes the
    requested dimensions (in mm) alongside measured BRep + mesh facts and the
    print-readiness summary, without a bbox tolerance verdict (None)."""
    mesh = mesh_facts(stl_bytes)
    measured = {
        "bbox_mm": bbox_mm, "volume_mm3": volume_mm3,
        "surface_area_mm2": surface_area_mm2, "hole_count": hole_count, **mesh,
    }
    pr = _print_readiness(measured, smallest_hole_mm)
    # No requested hole/bbox targets in the template path, so only geometry-health
    # criticals (disconnected / non-watertight / non-manifold / zero volume) apply.
    requested: dict = {}
    return {
        "unit": "mm",
        "tolerance": policy_dict(),
        "requested": {"dimensions_mm": requested_dimensions_mm},
        "measured": measured,
        "comparisons": [],
        "within_tolerance": None,
        "print_readiness": pr,
        "validation": _validation_summary(measured, requested, None, pr),
        "notes": _compensation_notes(),
    }


def build_dimension_report(plan: CadPlan, result: CadPlanResult, stl_bytes: bytes) -> dict:
    """Compare a compiled CadPlan against its measured geometry."""
    brep = measure_solid(result.solid)
    mesh = mesh_facts(stl_bytes)
    measured = {
        "bbox_mm": brep["bbox_mm"],
        "volume_mm3": brep["volume_mm3"],
        "surface_area_mm2": brep["surface_area_mm2"],
        "hole_count": result.hole_count,
        "through_hole_count": result.through_hole_count,
        **mesh,
    }

    exp = plan.expected
    requested = {
        "bbox_mm": exp.bbox_mm,
        "hole_count": exp.hole_count,
        "through_hole_count": exp.through_hole_count,
    }

    comparisons: list[dict] = []
    if exp.bbox_mm:
        comparisons.extend(_compare_bbox(exp.bbox_mm, brep["bbox_mm"]))
    if exp.hole_count is not None:
        comparisons.append({
            "name": "hole_count", "requested_mm": exp.hole_count,
            "measured_mm": result.hole_count, "tolerance_mm": 0,
            "delta_mm": result.hole_count - exp.hole_count,
            "within": result.hole_count == exp.hole_count,
        })
    if exp.through_hole_count is not None:
        comparisons.append({
            "name": "through_hole_count", "requested_mm": exp.through_hole_count,
            "measured_mm": result.through_hole_count, "tolerance_mm": 0,
            "delta_mm": result.through_hole_count - exp.through_hole_count,
            "within": result.through_hole_count == exp.through_hole_count,
        })

    within_tol = all(c["within"] for c in comparisons) if comparisons else None
    pr = _print_readiness(measured, _smallest_requested_hole(plan))

    return {
        "unit": "mm",
        "tolerance": policy_dict(),
        "requested": requested,
        "measured": measured,
        "comparisons": comparisons,
        "within_tolerance": within_tol,
        "print_readiness": pr,
        "validation": _validation_summary(measured, requested, within_tol, pr),
        "notes": _compensation_notes(),
    }
