"""Manufacturability checks.

These run on a validated DesignSpec (dimensions already positive and within
gross bounds) and surface practical warnings: thin walls, undersized or
poorly placed holes, too-small fillets, and method-specific 3D-printing risks.
Each check returns structured results so the UI can render them clearly.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel

import math

from app.cad.registry import get_template
from app.schemas.design_spec import DesignSpec, HoleType

# Practical floors, in millimeters.
MIN_WALL_FDM = 1.2
MIN_WALL_CNC = 1.0
MIN_HOLE_DIAMETER = 1.0
MIN_FILLET = 0.4
MIN_EDGE_DISTANCE_RATIO = 1.0  # hole edge must be >= 1.0 x diameter from a part edge
MIN_HOLE_GAP_RATIO = 0.5  # web between two holes >= 0.5 x larger diameter
FDM_MAX_UNSUPPORTED_OVERHANG_NOTE = 45  # degrees


class Severity(str, Enum):
    info = "info"
    warning = "warning"
    error = "error"


class CheckResult(BaseModel):
    check: str
    severity: Severity
    passed: bool
    message: str


def _primary_thickness_mm(spec: DesignSpec) -> float | None:
    for key in ("thickness", "wall_thickness"):
        if key in spec.dimensions:
            return spec.to_mm(spec.dimensions[key])
    return None


def _plate_extent_mm(spec: DesignSpec) -> tuple[float, float] | None:
    """Return (x_extent, y_extent) in mm for plate-like parts, else None."""
    keys_by_type = {
        "rectangular_bracket": ("width", "depth"),
        "drill_jig": ("length", "width"),
        "adapter_plate": ("width", "depth"),
    }
    pair = keys_by_type.get(spec.object_type)
    if not pair:
        return None
    try:
        return spec.to_mm(spec.dimensions[pair[0]]), spec.to_mm(spec.dimensions[pair[1]])
    except KeyError:
        return None


def run_checks(spec: DesignSpec) -> list[CheckResult]:
    results: list[CheckResult] = []
    method = spec.manufacturing_method

    # Feature-graph designs have no template; run only generic material/info checks.
    if spec.object_type == "feature_graph":
        results.append(
            CheckResult(
                check="feature_graph",
                severity=Severity.info,
                passed=True,
                message="Built from a validated flexible CAD feature graph "
                "(no template-specific checks).",
            )
        )
        results.append(
            CheckResult(
                check="material_assumption",
                severity=Severity.info,
                passed=True,
                message=f"Assuming {spec.material} via {method.replace('_', ' ')}.",
            )
        )
        return results

    # --- Wall / thickness ---
    min_wall = MIN_WALL_CNC if method == "cnc_milling" else MIN_WALL_FDM
    for key in ("thickness", "wall_thickness", "lid_thickness"):
        if key in spec.dimensions:
            val = spec.to_mm(spec.dimensions[key])
            results.append(
                CheckResult(
                    check=f"min_{key}",
                    severity=Severity.warning,
                    passed=val >= min_wall,
                    message=(
                        f"{key.replace('_', ' ')} is {val:.2f}mm; "
                        f"recommended minimum for {method} is {min_wall}mm"
                        if val < min_wall
                        else f"{key.replace('_', ' ')} {val:.2f}mm is adequate"
                    ),
                )
            )

    # --- Holes: diameter, edge distance ---
    extent = _plate_extent_mm(spec)
    for i, hole in enumerate(spec.holes):
        dia = spec.to_mm(hole.diameter)
        results.append(
            CheckResult(
                check=f"hole_{i}_diameter",
                severity=Severity.error,
                passed=dia >= MIN_HOLE_DIAMETER,
                message=(
                    f"Hole {i + 1} diameter {dia:.2f}mm is below the {MIN_HOLE_DIAMETER}mm "
                    "minimum and likely will not form cleanly"
                    if dia < MIN_HOLE_DIAMETER
                    else f"Hole {i + 1} diameter {dia:.2f}mm is valid"
                ),
            )
        )
        if extent:
            hx, hy = spec.to_mm(hole.x), spec.to_mm(hole.y)
            half_x, half_y = extent[0] / 2.0, extent[1] / 2.0
            # Use the largest feature footprint (counterbore widens the hole).
            footprint = dia
            if hole.counterbore_diameter:
                footprint = max(footprint, spec.to_mm(hole.counterbore_diameter))
            if hole.countersink_diameter:
                footprint = max(footprint, spec.to_mm(hole.countersink_diameter))
            edge_gap = min(half_x - abs(hx), half_y - abs(hy)) - footprint / 2.0
            need = MIN_EDGE_DISTANCE_RATIO * dia
            results.append(
                CheckResult(
                    check=f"hole_{i}_edge_distance",
                    severity=Severity.warning,
                    passed=edge_gap >= need,
                    message=(
                        f"Hole {i + 1} sits {edge_gap:.2f}mm from the nearest edge; "
                        f"recommend >= {need:.2f}mm (1x diameter) to avoid blow-out"
                        if edge_gap < need
                        else f"Hole {i + 1} edge distance {edge_gap:.2f}mm is adequate"
                    ),
                )
            )

        # Counterbore / countersink validity.
        if hole.hole_type == HoleType.counterbore and hole.counterbore_diameter:
            thickness = _primary_thickness_mm(spec)
            cb_depth = spec.to_mm(hole.counterbore_depth or 0)
            ok = hole.counterbore_diameter > hole.diameter and (
                thickness is None or cb_depth < thickness
            )
            results.append(
                CheckResult(
                    check=f"hole_{i}_counterbore",
                    severity=Severity.warning,
                    passed=ok,
                    message=(
                        f"Hole {i + 1} counterbore depth {cb_depth:.2f}mm is too deep "
                        f"for the {thickness:.2f}mm material"
                        if not ok and thickness
                        else f"Hole {i + 1} counterbore is valid"
                    ),
                )
            )

    # --- Hole-to-hole spacing (web between adjacent holes) ---
    holes_mm = [
        (spec.to_mm(h.x), spec.to_mm(h.y), spec.to_mm(h.diameter)) for h in spec.holes
    ]
    for a in range(len(holes_mm)):
        for b in range(a + 1, len(holes_mm)):
            xa, ya, da = holes_mm[a]
            xb, yb, db = holes_mm[b]
            center_dist = math.hypot(xa - xb, ya - yb)
            web = center_dist - (da + db) / 2.0
            need = MIN_HOLE_GAP_RATIO * max(da, db)
            if web < need:
                results.append(
                    CheckResult(
                        check=f"hole_spacing_{a}_{b}",
                        severity=Severity.warning,
                        passed=False,
                        message=(
                            f"Holes {a + 1} and {b + 1} are only {web:.2f}mm apart "
                            f"(web); recommend >= {need:.2f}mm to avoid a weak/torn wall"
                        ),
                    )
                )

    # --- Fillet radius ---
    if spec.fillet_radius is not None and spec.fillet_radius > 0:
        fr = spec.to_mm(spec.fillet_radius)
        results.append(
            CheckResult(
                check="min_fillet",
                severity=Severity.info,
                passed=fr >= MIN_FILLET,
                message=(
                    f"Fillet radius {fr:.2f}mm is very small and may not be resolvable"
                    if fr < MIN_FILLET
                    else f"Fillet radius {fr:.2f}mm is fine"
                ),
            )
        )

    # --- 3D-printing specific risk: tall thin standoff / overhang note ---
    if method in ("fdm_3d_print", "sla_3d_print"):
        if spec.object_type == "spacer":
            od = spec.to_mm(spec.dimensions.get("outer_diameter", 0))
            length = spec.to_mm(spec.dimensions.get("length", 0))
            if od and length and length > 5 * od:
                results.append(
                    CheckResult(
                        check="print_aspect_ratio",
                        severity=Severity.warning,
                        passed=False,
                        message=(
                            f"Standoff is tall and slender ({length:.1f}mm tall, "
                            f"{od:.1f}mm wide); may topple or wobble while printing. "
                            "Consider a brim or printing horizontally."
                        ),
                    )
                )
        if spec.object_type == "enclosure":
            results.append(
                CheckResult(
                    check="print_orientation",
                    severity=Severity.info,
                    passed=True,
                    message=(
                        "Print the enclosure body open-side-up and the lid flat to "
                        "avoid supports on internal walls."
                    ),
                )
            )

        # Thin-feature / first-layer resolvability for printing.
        thinnest = min(
            (
                spec.to_mm(spec.dimensions[k])
                for k in ("thickness", "wall_thickness", "lid_thickness")
                if k in spec.dimensions
            ),
            default=None,
        )
        if thinnest is not None and thinnest < 0.8:
            results.append(
                CheckResult(
                    check="print_min_feature",
                    severity=Severity.error,
                    passed=False,
                    message=(
                        f"Thinnest wall {thinnest:.2f}mm is below ~0.8mm; most FDM "
                        "nozzles (0.4mm) cannot print fewer than two perimeters reliably."
                    ),
                )
            )

        # Counterbores printed face-up leave a bridged ceiling over the hole.
        if any(h.hole_type == HoleType.counterbore for h in spec.holes):
            results.append(
                CheckResult(
                    check="print_counterbore_bridging",
                    severity=Severity.info,
                    passed=True,
                    message=(
                        "Counterbores bridge over the pilot hole when printed face-up; "
                        "print that face down or expect minor sag in the recess."
                    ),
                )
            )

    # --- Material / manufacturing assumption surfaced to the user ---
    results.append(
        CheckResult(
            check="material_assumption",
            severity=Severity.info,
            passed=True,
            message=(
                f"Assuming {spec.material} via {method.replace('_', ' ')}. "
                "Change the material/method if that's not what you intended."
            ),
        )
    )

    # --- Template can actually build it (defaults + ranges) ---
    try:
        get_template(spec.object_type).resolve(spec)
        results.append(
            CheckResult(
                check="geometry_resolvable",
                severity=Severity.info,
                passed=True,
                message="All required dimensions present and within range.",
            )
        )
    except Exception as exc:  # noqa: BLE001
        results.append(
            CheckResult(
                check="geometry_resolvable",
                severity=Severity.error,
                passed=False,
                message=str(exc),
            )
        )

    return results
