"""Internal CAD defaults — conservative, documented fallback values.

IMPORTANT: these are SourceCAD's own internal engineering defaults, chosen to be
safe and printable. They are NOT a claim of ASME, ISO, or Machinery's Handbook
compliance — do not present them to users as standards-certified. They exist so
that when a user omits a secondary dimension we fill it with one sensible,
*documented* number (and record an assumption) instead of guessing per-request.

Everything here is plain data + tiny pure helpers; no geometry, no I/O.
"""
from __future__ import annotations

# Whether these values are certified against an external standard. Kept False on
# purpose — surfaced via the capability endpoint so the UI never over-promises.
STANDARDS_CERTIFIED = False
DEFAULTS_SOURCE = "SourceCAD internal defaults (not ASME/ISO/Machinery's Handbook certified)"


# --- metric clearance holes ------------------------------------------------
# Clearance-hole diameter (mm) for a metric screw passing freely through a part.
# "normal" is the everyday default; "close"/"loose" bracket it. Internal values
# in the spirit of common medium-fit clearance drilling, not a certified table.
METRIC_CLEARANCE_HOLES: dict[str, dict[str, float]] = {
    "M3": {"close": 3.2, "normal": 3.4, "loose": 3.8},
    "M4": {"close": 4.3, "normal": 4.5, "loose": 4.8},
    "M5": {"close": 5.3, "normal": 5.5, "loose": 5.8},
    "M6": {"close": 6.4, "normal": 6.6, "loose": 7.0},
    "M8": {"close": 8.4, "normal": 9.0, "loose": 10.0},
    "M10": {"close": 10.5, "normal": 11.0, "loose": 12.0},
}


def clearance_hole(metric: str, fit: str = "normal") -> float:
    """Clearance-hole diameter (mm) for a metric size like 'M6'. Falls back to a
    conservative (size + 0.6mm) for sizes outside the table."""
    key = metric.upper().strip()
    row = METRIC_CLEARANCE_HOLES.get(key)
    if row:
        return row.get(fit, row["normal"])
    digits = "".join(c for c in key if c.isdigit() or c == ".")
    try:
        return float(digits) + 0.6
    except ValueError:
        return 6.6  # M6 normal — a safe everyday default


# --- plate / sheet thicknesses --------------------------------------------
# Common stock plate thicknesses (mm) we snap to when a thickness is omitted.
COMMON_PLATE_THICKNESSES_MM: tuple[float, ...] = (2.0, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0, 12.0)
DEFAULT_PLATE_THICKNESS_MM = 5.0


def nearest_plate_thickness(value_mm: float) -> float:
    """Snap a requested thickness to the nearest common stock plate thickness."""
    if value_mm <= 0:
        return DEFAULT_PLATE_THICKNESS_MM
    return min(COMMON_PLATE_THICKNESSES_MM, key=lambda t: abs(t - value_mm))


# --- printability floors ---------------------------------------------------
# Smallest hole we will model without warning; below this most FDM/SLA processes
# struggle. Used to flag (not block) tiny holes.
MIN_PRINTABLE_HOLE_MM = 2.0
MIN_PRINTABLE_WALL_MM = 1.2


# --- fillet / chamfer ------------------------------------------------------
DEFAULT_FILLET_MM = 2.0
DEFAULT_CHAMFER_MM = 1.0


# --- tube sizes ------------------------------------------------------------
# Common round-tube OD × wall (mm) for clamps, frames, handles, hooks.
COMMON_TUBE_SIZES_MM: tuple[dict[str, float], ...] = (
    {"od": 12.0, "wall": 1.5},
    {"od": 16.0, "wall": 1.5},
    {"od": 20.0, "wall": 2.0},
    {"od": 25.0, "wall": 2.0},
    {"od": 32.0, "wall": 2.5},
    {"od": 40.0, "wall": 3.0},
)
DEFAULT_TUBE_OD_MM = 25.0
DEFAULT_TUBE_WALL_MM = 2.0


# --- wall thickness defaults ----------------------------------------------
# Default wall thickness (mm) by enclosure / casing context.
WALL_THICKNESS_DEFAULTS_MM: dict[str, float] = {
    "enclosure": 2.0,
    "casing": 3.0,
    "structural": 4.0,
}
DEFAULT_WALL_THICKNESS_MM = 2.0


def as_dict() -> dict:
    """Machine-readable snapshot for the capability endpoint / docs."""
    return {
        "standards_certified": STANDARDS_CERTIFIED,
        "source": DEFAULTS_SOURCE,
        "metric_clearance_holes_mm": METRIC_CLEARANCE_HOLES,
        "common_plate_thicknesses_mm": list(COMMON_PLATE_THICKNESSES_MM),
        "default_plate_thickness_mm": DEFAULT_PLATE_THICKNESS_MM,
        "min_printable_hole_mm": MIN_PRINTABLE_HOLE_MM,
        "min_printable_wall_mm": MIN_PRINTABLE_WALL_MM,
        "default_fillet_mm": DEFAULT_FILLET_MM,
        "default_chamfer_mm": DEFAULT_CHAMFER_MM,
        "common_tube_sizes_mm": [dict(t) for t in COMMON_TUBE_SIZES_MM],
        "default_tube_od_mm": DEFAULT_TUBE_OD_MM,
        "default_tube_wall_mm": DEFAULT_TUBE_WALL_MM,
        "wall_thickness_defaults_mm": WALL_THICKNESS_DEFAULTS_MM,
    }


# --- concept-object default envelopes --------------------------------------
# Documented default dimensions (mm) for the everyday concept-fallback families.
# Each entry is the simplified concept envelope used when the user gives no
# dimensions; builders record an assumption listing the ones they applied.
CONCEPT_DEFAULTS: dict[str, dict[str, float]] = {
    "hammer": {"handle_diameter": 28.0, "handle_length": 280.0,
               "head_length": 110.0, "head_width": 32.0, "head_height": 36.0},
    "wrench": {"handle_length": 200.0, "handle_width": 22.0, "thickness": 8.0,
               "head_diameter": 44.0, "jaw_opening": 18.0},
    "pliers": {"pivot_diameter": 18.0, "thickness": 10.0,
               "jaw_length": 56.0, "handle_length": 120.0, "arm_width": 9.0,
               "pivot_hole": 6.0},
    "wheel": {"diameter": 200.0, "thickness": 40.0, "hub_diameter": 60.0,
              "hub_height": 52.0, "bore": 25.0, "spoke_holes": 5.0,
              "spoke_hole_diameter": 26.0},
    "fan_blade": {"hub_diameter": 44.0, "hub_height": 14.0, "blade_count": 4.0,
                  "blade_length": 90.0, "blade_width": 26.0, "blade_thickness": 6.0,
                  "bore": 12.0},
    "hook": {"plate_width": 50.0, "plate_thickness": 10.0, "plate_height": 70.0,
             "arm_length": 60.0, "arm_size": 16.0, "tip_height": 30.0,
             "mount_hole": 6.0},
    "handle": {"grip_length": 160.0, "grip_width": 24.0, "grip_height": 18.0,
               "leg_diameter": 16.0, "standoff": 40.0, "mount_hole": 6.0},
    "tool_holder": {"width": 200.0, "depth": 60.0, "thickness": 20.0,
                    "back_height": 80.0, "back_thickness": 12.0,
                    "tool_holes": 4.0, "tool_hole_diameter": 25.0},
    "stand": {"top_width": 160.0, "top_depth": 120.0, "top_thickness": 10.0,
              "height": 120.0, "leg_diameter": 20.0},
    "casing": {"width": 120.0, "depth": 80.0, "height": 50.0, "wall": 3.0},
}
