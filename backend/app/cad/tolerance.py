"""Central dimensional tolerance + printability policy (all millimetres).

Single source of truth for "is the generated dimension close enough to what was
requested?" Used by validation, the dimension report, and the golden benchmark so
the same numbers govern everywhere. Values are configurable via settings/env
(CAD_LENGTH_TOLERANCE_MM, CAD_LENGTH_TOLERANCE_FRAC, CAD_DIAMETER_TOLERANCE_MM,
PRINTER_MIN_FEATURE_MM, PRINTER_MIN_HOLE_MM).

Policy: millimetres are the canonical unit. We NEVER silently change a requested
dimension; printer XY compensation defaults to 0.0 and any non-zero value is
surfaced in the dimension report.
"""
from __future__ import annotations

from app.config import settings

DEFAULT_UNIT = "mm"


def length_tolerance(expected_mm: float) -> float:
    """Absolute tolerance (mm) allowed for a length/extent of `expected_mm`."""
    return max(settings.cad_length_tolerance_mm,
               abs(float(expected_mm)) * settings.cad_length_tolerance_frac)


def diameter_tolerance(expected_mm: float) -> float:
    """Absolute tolerance (mm) allowed for a hole/bore diameter."""
    return max(settings.cad_diameter_tolerance_mm,
               abs(float(expected_mm)) * settings.cad_length_tolerance_frac)


def within(expected: float, actual: float, tol: float) -> bool:
    return abs(float(expected) - float(actual)) <= tol + 1e-9


def policy_dict() -> dict:
    """The active tolerance policy, embedded into every dimension report."""
    return {
        "unit": DEFAULT_UNIT,
        "length_tolerance_mm": settings.cad_length_tolerance_mm,
        "length_tolerance_frac": settings.cad_length_tolerance_frac,
        "diameter_tolerance_mm": settings.cad_diameter_tolerance_mm,
        "printer_min_feature_mm": settings.printer_min_feature_mm,
        "printer_min_hole_mm": settings.printer_min_hole_mm,
        "printer_xy_compensation_mm": settings.printer_xy_compensation_mm,
    }
