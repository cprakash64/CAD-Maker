"""Missing-Information Policy — the heart of generate-first behavior.

We generate CAD whenever the prompt has enough to build a reasonable model, and
ask clarification only when generation would be unsafe, impossible, or truly
ambiguous. Missing *non-critical* parameters use documented template defaults and
are surfaced as assumptions.
"""
from __future__ import annotations

# Only these block generation. Everything else has a safe template default.
CRITICAL_FIELDS = {"object_type", "valid_design_spec", "prompt", "unsupported"}

# Documented non-critical fields (defaults exist). Used for nicer assumption text.
NON_CRITICAL_FIELDS = {
    "material", "hole_count", "holes", "hole_pattern", "hole_pattern_start",
    "hole_spacing", "spacing", "lip", "lip_size", "lip_height", "registration_lip",
    "fillet", "fillet_radius", "chamfer", "chamfer_size", "screw_clearance",
    "tolerance", "wall_thickness", "lid_thickness", "corner_radius",
    "counterbore", "counterbore_diameter", "counterbore_depth", "countersink",
    "countersink_diameter", "boss_diameter", "vent_count", "gusset", "gusset_height",
    "center_bore", "bolt_count", "bolt_circle_diameter", "bolt_circle", "bolt_pcd",
    "bolt_hole_diameter", "bolt_hole_diameter_mm", "keyway", "keyway_width_mm",
    "keyway_depth_mm", "tooth_count", "manufacturing_method",
}


def is_critical(field: str) -> bool:
    """A missing field blocks generation only if it's genuinely critical."""
    return field.strip().lower() in CRITICAL_FIELDS


def split_missing(missing: list[str]) -> tuple[list[str], list[str]]:
    """Partition reported-missing fields into (critical, non_critical)."""
    critical, non_critical = [], []
    for m in missing:
        (critical if is_critical(m) else non_critical).append(m)
    return critical, non_critical


def default_assumption_notes(non_critical: list[str]) -> list[str]:
    """Human-readable assumptions for non-critical fields we defaulted."""
    if not non_critical:
        return []
    pretty = ", ".join(sorted({m.replace("_", " ") for m in non_critical}))
    return [f"Used sensible defaults for unspecified details: {pretty}"]
