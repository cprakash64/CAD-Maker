"""Safety-classification taxonomy + category -> policy severity table.

This is a DRAFT product/engineering judgment call, not a legal or safety-
engineering determination. The category -> policy mapping below MUST be
reviewed by qualified legal/safety counsel before it is treated as a final
product policy (see docs/legal/safety-and-engineering-disclaimer.md).

Design principle: this table is the ONE place severity is decided. Detection
(classifier.py) only ever produces a set of `SafetyCategory` values; policy
(policy.py) only ever looks them up here. Nothing downstream hardcodes a
category's behavior -- changing a mapping here is the whole review surface.
"""
from __future__ import annotations

from enum import Enum


class SafetyCategory(str, Enum):
    """The 11 high-consequence application categories named in the product
    safety-policy requirement, plus a floor catch-all."""

    STRUCTURAL_LOAD_BEARING = "structural_load_bearing"
    LIFTING_EQUIPMENT = "lifting_equipment"
    VEHICLE_STEERING_OR_BRAKING = "vehicle_steering_or_braking"
    PRESSURE_CONTAINING = "pressure_containing"
    MEDICAL_DEVICES = "medical_devices"
    CHILD_SAFETY_PRODUCTS = "child_safety_products"
    FIRE_SAFETY_EQUIPMENT = "fire_safety_equipment"
    MAINS_ELECTRICAL_SAFETY = "mains_electrical_safety"
    AEROSPACE_FLIGHT_COMPONENTS = "aerospace_flight_components"
    WEAPON_COMPONENTS = "weapon_components"
    OTHER_HIGH_CONSEQUENCE = "other_high_consequence"


class PolicyAction(str, Enum):
    """The 5 enforcement behaviors named in the product requirement, ordered
    here from MOST to LEAST restrictive -- this order IS the "most
    restrictive wins" precedence used to merge multiple detected categories
    and to make classification sticky across edits (see policy.py)."""

    REFUSE = "refuse"
    CONCEPTUAL_ONLY = "conceptual_only"
    BLOCK_EXPORT = "block_export"
    REQUIRE_ACKNOWLEDGMENT = "require_acknowledgment"
    WARN_REVIEW_MANDATORY = "warn_review_mandatory"
    NONE = "none"


# Precedence for "most restrictive wins" -- lower index = more restrictive.
_SEVERITY_ORDER = [
    PolicyAction.REFUSE,
    PolicyAction.CONCEPTUAL_ONLY,
    PolicyAction.BLOCK_EXPORT,
    PolicyAction.REQUIRE_ACKNOWLEDGMENT,
    PolicyAction.WARN_REVIEW_MANDATORY,
    PolicyAction.NONE,
]


def most_restrictive(actions: list[PolicyAction]) -> PolicyAction:
    if not actions:
        return PolicyAction.NONE
    return min(actions, key=_SEVERITY_ORDER.index)


# --------------------------------------------------------------------------
# CATEGORY -> POLICY. DRAFT judgment calls; each has a one-line rationale.
# Legal/safety review may reweight any of these -- change ONLY this table.
# --------------------------------------------------------------------------
CATEGORY_POLICY: dict[SafetyCategory, PolicyAction] = {
    # Catastrophic-if-wrong, and there is no "conceptual visualization only"
    # value that offsets the risk of a lookalike manufacturable file existing
    # at all -- refuse outright rather than build anything.
    SafetyCategory.WEAPON_COMPONENTS: PolicyAction.REFUSE,
    SafetyCategory.VEHICLE_STEERING_OR_BRAKING: PolicyAction.REFUSE,
    SafetyCategory.AEROSPACE_FLIGHT_COMPONENTS: PolicyAction.REFUSE,
    SafetyCategory.MEDICAL_DEVICES: PolicyAction.REFUSE,
    # Real hobbyist/engineering value in seeing a shape (crane hook geometry,
    # a car-seat bracket layout) even though a manufacturable file from an
    # unreviewed AI generator is not acceptable for these -- build for
    # visualization, never allow a manufacturable export.
    SafetyCategory.LIFTING_EQUIPMENT: PolicyAction.CONCEPTUAL_ONLY,
    SafetyCategory.CHILD_SAFETY_PRODUCTS: PolicyAction.CONCEPTUAL_ONLY,
    # Common in legitimate hobbyist/engineering prompts (brackets, frames,
    # mounts touch this constantly) -- outright refusal or permanent
    # export-blocking would make the product unusable for ordinary mechanical
    # work. Gate behind an explicit, logged acknowledgment instead.
    SafetyCategory.STRUCTURAL_LOAD_BEARING: PolicyAction.REQUIRE_ACKNOWLEDGMENT,
    SafetyCategory.PRESSURE_CONTAINING: PolicyAction.REQUIRE_ACKNOWLEDGMENT,
    SafetyCategory.FIRE_SAFETY_EQUIPMENT: PolicyAction.REQUIRE_ACKNOWLEDGMENT,
    SafetyCategory.MAINS_ELECTRICAL_SAFETY: PolicyAction.REQUIRE_ACKNOWLEDGMENT,
    # Floor: anything that reads as high-consequence but doesn't match a more
    # specific category still gets a mandatory, non-dismissable review notice.
    SafetyCategory.OTHER_HIGH_CONSEQUENCE: PolicyAction.WARN_REVIEW_MANDATORY,
}

CATEGORY_LABELS: dict[SafetyCategory, str] = {
    SafetyCategory.STRUCTURAL_LOAD_BEARING: "Structural / load-bearing component",
    SafetyCategory.LIFTING_EQUIPMENT: "Lifting equipment",
    SafetyCategory.VEHICLE_STEERING_OR_BRAKING: "Vehicle steering or braking component",
    SafetyCategory.PRESSURE_CONTAINING: "Pressure-containing part",
    SafetyCategory.MEDICAL_DEVICES: "Medical device",
    SafetyCategory.CHILD_SAFETY_PRODUCTS: "Child-safety product",
    SafetyCategory.FIRE_SAFETY_EQUIPMENT: "Fire-safety equipment",
    SafetyCategory.MAINS_ELECTRICAL_SAFETY: "Mains-electrical-safety component",
    SafetyCategory.AEROSPACE_FLIGHT_COMPONENTS: "Aerospace flight component",
    SafetyCategory.WEAPON_COMPONENTS: "Weapon component",
    SafetyCategory.OTHER_HIGH_CONSEQUENCE: "Other high-consequence application",
}

# Precise, defensible per-category explanation shown to the user. Deliberately
# avoids the banned-claim vocabulary (see app/safety/language.py): never says
# "safe", "certified", "production-ready", or implies engineering sign-off.
CATEGORY_MESSAGES: dict[SafetyCategory, str] = {
    SafetyCategory.WEAPON_COMPONENTS: (
        "LunaiCAD does not generate weapon components or parts designed to "
        "fire, launch, or weaponize a projectile."
    ),
    SafetyCategory.VEHICLE_STEERING_OR_BRAKING: (
        "LunaiCAD does not generate vehicle steering or braking components. "
        "A failure in either can cause loss of vehicle control."
    ),
    SafetyCategory.AEROSPACE_FLIGHT_COMPONENTS: (
        "LunaiCAD does not generate flight-critical aerospace components."
    ),
    SafetyCategory.MEDICAL_DEVICES: (
        "LunaiCAD does not generate medical devices or components intended "
        "for use in or on the human body."
    ),
    SafetyCategory.LIFTING_EQUIPMENT: (
        "This looks like lifting equipment. LunaiCAD will show a geometric "
        "concept model for reference only -- manufacturable export (STEP/STL) "
        "is not offered for this category. Engineering review by a qualified "
        "professional is required before any lifting part is fabricated."
    ),
    SafetyCategory.CHILD_SAFETY_PRODUCTS: (
        "This looks like a child-safety product. LunaiCAD will show a "
        "geometric concept model for reference only -- manufacturable export "
        "(STEP/STL) is not offered for this category. Products used by or "
        "around children have regulatory requirements (e.g. choking-hazard, "
        "flammability, structural standards) this tool does not check."
    ),
    SafetyCategory.STRUCTURAL_LOAD_BEARING: (
        "This looks like a structural or load-bearing component. LunaiCAD's "
        "checks are geometric only (dimensions, watertightness, printability) "
        "-- not a structural or load analysis. Engineering review is required "
        "before manufacture. Continuing requires acknowledging this."
    ),
    SafetyCategory.PRESSURE_CONTAINING: (
        "This looks like a pressure-containing part. LunaiCAD's checks are "
        "geometric only -- not a pressure-rating or burst analysis. "
        "Engineering review is required before manufacture. Continuing "
        "requires acknowledging this."
    ),
    SafetyCategory.FIRE_SAFETY_EQUIPMENT: (
        "This looks like fire-safety equipment. LunaiCAD's checks are "
        "geometric only -- not a fire-code or life-safety certification. "
        "Engineering review is required before manufacture. Continuing "
        "requires acknowledging this."
    ),
    SafetyCategory.MAINS_ELECTRICAL_SAFETY: (
        "This looks like a mains-electrical-safety component (enclosure, "
        "housing, or fitting rated for line voltage). LunaiCAD's checks are "
        "geometric only -- not an electrical-safety certification. "
        "Engineering review is required before manufacture. Continuing "
        "requires acknowledging this."
    ),
    SafetyCategory.OTHER_HIGH_CONSEQUENCE: (
        "This request describes a high-consequence application. LunaiCAD's "
        "checks are geometric only. Engineering review is required before "
        "manufacture."
    ),
}
