"""Published dimensional data for standard mechanical (catalog) parts.

Plain data + tiny pure helpers — no geometry, no I/O. Values are nominal
dimensions from public metric fastener standards (ISO / DIN). They describe the
NOMINAL part so we can generate parametric geometry; they are NOT a claim of
certified per-batch tolerance compliance.

LEGAL / SOURCING NOTE:
McMaster CAD files must not be scraped, cached, redistributed, or used as source
geometry unless LunaiCAD has explicit commercial permission. Everything in this
module is public dimensional data keyed by thread size — not vendor geometry.
"""
from __future__ import annotations

import math

_COS30 = math.cos(math.pi / 6.0)  # 0.8660254… — across-flats / across-corners ratio


def across_corners(across_flats_mm: float) -> float:
    """Across-corners (circumscribed-circle) distance of a regular hexagon from
    its across-flats (inscribed-circle) distance: e = s / cos(30°)."""
    return across_flats_mm / _COS30


# --- ISO metric coarse thread pitch (mm) -----------------------------------
# Nominal coarse-thread pitch by metric diameter. Used when the user names a
# thread but no pitch (the common case for "M12 nut").
METRIC_COARSE_PITCH_MM: dict[float, float] = {
    2.0: 0.4, 2.5: 0.45, 3.0: 0.5, 4.0: 0.7, 5.0: 0.8, 6.0: 1.0,
    8.0: 1.25, 10.0: 1.5, 12.0: 1.75, 14.0: 2.0, 16.0: 2.0, 18.0: 2.5,
    20.0: 2.5, 22.0: 2.5, 24.0: 3.0, 27.0: 3.0, 30.0: 3.5,
}


def coarse_pitch_mm(nominal_dia_mm: float) -> float | None:
    """Coarse-thread pitch (mm) for a metric nominal diameter, or None."""
    return METRIC_COARSE_PITCH_MM.get(round(float(nominal_dia_mm), 3))


def thread_minor_diameter_mm(nominal_dia_mm: float, pitch_mm: float) -> float:
    """Internal-thread minor (root) diameter D1 ≈ D − 1.0825·P (ISO 68 profile).

    This is the diameter of a nut's tapped through-bore — a reasonable COSMETIC
    bore when the thread form is not modelled."""
    return round(float(nominal_dia_mm) - 1.0825 * float(pitch_mm), 3)


# --- ISO 4032 / DIN 934 regular (style 1) hex nut --------------------------
# Nominal dimensions per metric thread size:
#   s  = width across flats (mm)         — nominal (= max)
#   m  = nut height / thickness (mm)      — nominal max
# Across-corners (e) is derived from s via `across_corners`. ISO 4032 and
# DIN 934 share these nominal values for the common sizes below.
HEX_NUT_ISO4032: dict[str, dict[str, float]] = {
    "M2":   {"across_flats": 4.0,  "height": 1.6},
    "M2.5": {"across_flats": 5.0,  "height": 2.0},
    "M3":   {"across_flats": 5.5,  "height": 2.4},
    "M4":   {"across_flats": 7.0,  "height": 3.2},
    "M5":   {"across_flats": 8.0,  "height": 4.7},
    "M6":   {"across_flats": 10.0, "height": 5.2},
    "M8":   {"across_flats": 13.0, "height": 6.8},
    "M10":  {"across_flats": 16.0, "height": 8.4},
    "M12":  {"across_flats": 18.0, "height": 10.8},
    "M14":  {"across_flats": 21.0, "height": 12.8},
    "M16":  {"across_flats": 24.0, "height": 14.8},
    "M18":  {"across_flats": 27.0, "height": 15.8},
    "M20":  {"across_flats": 30.0, "height": 18.0},
    "M22":  {"across_flats": 34.0, "height": 19.4},
    "M24":  {"across_flats": 36.0, "height": 21.5},
}

# DIN 934 differs from ISO 4032 only in the across-flats of a couple of legacy
# sizes (M10 = 17mm, M12 = 19mm on the older DIN width series). The standard's
# CURRENT issue is harmonized with ISO, so we model both from one table and note
# the standard label the user asked for. Override map kept explicit + documented.
HEX_NUT_DIN934_AF_OVERRIDES: dict[str, float] = {
    # (empty by default — current DIN 934 is aligned with ISO 4032 widths)
}

# Default standard when the user names a plain hex nut with no standard.
DEFAULT_HEX_NUT_STANDARD = "ISO 4032"
KNOWN_HEX_NUT_STANDARDS = ("ISO 4032", "DIN 934")


def hex_nut_dimensions(thread: str, standard: str = DEFAULT_HEX_NUT_STANDARD) -> dict | None:
    """Nominal hex-nut dimensions for a thread like ``"M12"``.

    Returns ``{across_flats, across_corners, height}`` in mm, or None for an
    unknown size. ``standard`` selects ISO 4032 vs DIN 934 (only the across-flats
    of a few legacy DIN sizes differ; see ``HEX_NUT_DIN934_AF_OVERRIDES``)."""
    key = thread.upper().strip()
    row = HEX_NUT_ISO4032.get(key)
    if row is None:
        return None
    af = row["across_flats"]
    if standard.upper().replace(" ", "") == "DIN934":
        af = HEX_NUT_DIN934_AF_OVERRIDES.get(key, af)
    return {
        "across_flats": af,
        "across_corners": round(across_corners(af), 3),
        "height": row["height"],
    }
