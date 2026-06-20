"""Deterministic engineering defaults for assumption-first CAD generation.

When a prompt omits a SECONDARY dimension (boss height, hole offset, wall
thickness, flange thickness, …) we infer it from these defaults rather than
asking the user. Every inferred value is recorded as an assumption.

(Python mirror of the requested ``src/lib/cad/defaults.ts``.)
"""
from __future__ import annotations

# Screw label -> clearance hole diameter (mm).
CLEARANCE_HOLES_MM = {
    "M2": 2.4, "M2.5": 2.9, "M2_5": 2.9, "M3": 3.4, "M4": 4.5, "M5": 5.5,
    "M6": 6.6, "M8": 9.0, "M10": 11.0, "M12": 13.5,
}

CAD_DEFAULTS = {
    "clearance_holes_mm": CLEARANCE_HOLES_MM,
    "plate": {
        "default_thickness": 6,
        "edge_hole_offset_min": 8,
        "edge_hole_offset_factor": 0.15,
        "fillet_radius": 2,
    },
    "bearing_block": {
        "boss_od_factor": 2.25,
        "boss_height_factor": 1.5,
        "min_boss_height": 18,
        "base_hole_offset_factor_x": 0.18,
        "base_hole_offset_factor_y": 0.25,
        "base_hole_diameter": 6.6,
        "boss_fillet": 2,
    },
    "hinge_bracket": {
        "ear_thickness": 6,
        "ear_height": 30,
        "ear_gap_extra_over_pin": 4,
        "pin_hole_diameter": 8,
        "base_hole_diameter": 5.5,
        "base_hole_offset_factor_x": 0.25,
    },
    "enclosure": {
        "width": 100,
        "height": 60,
        "depth": 40,
        "wall_thickness": 2.5,
        "back_plate_thickness": 3,
        "corner_radius": 3,
        "mounting_hole_diameter": 4.5,
        "sensor_hole_diameter": 18,
        "screw_boss_od": 8,
        "screw_boss_id": 3.4,
    },
    "flange": {
        "thickness_factor": 0.12,
        "min_thickness": 10,
        "pcd_factor": 0.8,
        "bolt_hole_diameter": 9,
        "bolt_count": 8,
    },
    "pipe": {
        "wall_thickness": 5,
        "flange_od_factor": 1.5,
        "flange_thickness_factor": 0.15,
    },
}


def clearance_for(label: str | None, fallback: float = 6.6) -> float:
    """Clearance-hole diameter for an M-label like 'M6' (case-insensitive)."""
    if not label:
        return fallback
    key = label.strip().upper().replace(" ", "")
    return CLEARANCE_HOLES_MM.get(key, fallback)
