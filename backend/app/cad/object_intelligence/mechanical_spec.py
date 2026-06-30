"""Core structured model for the Object Intelligence layer.

A :class:`MechanicalObjectSpec` is the universal, source-tracked description of a
real-world object (a board, motor, bearing, display, …) that LunaiCAD resolves from
a prompt. It records WHERE the dimensions came from (``source_type``) and HOW MUCH
we trust them (``confidence_score``) so the validation layer can decide whether a
generated part may PASS, must be REVIEW, or is only a CONCEPT — the product rule is
that GPT-estimated critical dimensions can never PASS.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# --- dimension trust levels (source_type) ----------------------------------
# Ordered best→worst; the resolver always prefers the highest-trust source found.
SOURCE_LOCAL_VERIFIED = "local_verified"            # curated local preset / standard
SOURCE_OFFICIAL = "official_source_extracted"        # official datasheet / drawing / STEP
SOURCE_WEB = "web_source_extracted"                  # credible but non-official
SOURCE_USER = "user_provided"                        # dimensions given by the user
SOURCE_GPT = "gpt_estimated"                          # GPT inferred — never PASS
SOURCE_UNKNOWN = "unknown"                            # nothing trustworthy found

SOURCE_RANK = {
    SOURCE_LOCAL_VERIFIED: 5,
    SOURCE_OFFICIAL: 4,
    SOURCE_WEB: 3,
    SOURCE_USER: 5,            # user-provided is as trusted as a local preset
    SOURCE_GPT: 1,
    SOURCE_UNKNOWN: 0,
}

# --- object categories ------------------------------------------------------
CAT_SBC = "single_board_computer"
CAT_MCU = "microcontroller_board"
CAT_SENSOR = "sensor_module"
CAT_DISPLAY = "display_module"
CAT_BATTERY = "battery_pack"
CAT_MOTOR = "motor"
CAT_BEARING = "bearing"
CAT_FASTENER = "fastener"
CAT_PULLEY = "pulley"
CAT_GEAR = "gear"
CAT_BRACKET = "bracket"
CAT_ENCLOSURE = "enclosure"
CAT_HOLDER = "device_holder"
CAT_GENERIC = "generic_object"

# --- generated families (the CAD route a resolution maps to) ----------------
FAM_DEVICE_ENCLOSURE = "device_enclosure"
FAM_BOARD_ENCLOSURE = "board_enclosure"
FAM_MOTOR_MOUNT = "motor_mount"
FAM_BEARING_HOLDER = "bearing_holder"
FAM_DISPLAY_BEZEL = "display_bezel"
FAM_MODULE_MOUNT = "module_mount"
FAM_PHONE_HOLDER = "phone_holder"
FAM_GENERIC_FITTED_BOX = "generic_fitted_box"


@dataclass
class MechanicalObjectSpec:
    """Source-tracked structured spec for a resolved object."""

    object_name: str
    normalized_name: str = ""
    category: str = CAT_GENERIC
    manufacturer: str | None = None
    model: str | None = None
    source_urls: list[str] = field(default_factory=list)
    source_type: str = SOURCE_UNKNOWN
    confidence_score: float = 0.0           # 0..1
    dimensions: dict[str, float] = field(default_factory=dict)
    mounting_holes: list[dict] = field(default_factory=list)
    connector_cutouts: list[dict] = field(default_factory=list)
    keepout_zones: list[dict] = field(default_factory=list)
    board_outline: dict | None = None
    port_locations: list[dict] = field(default_factory=list)
    hole_pattern: dict | None = None
    standards: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    unsupported_features: list[str] = field(default_factory=list)
    generated_family: str = FAM_GENERIC_FITTED_BOX
    validation_requirements: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "object_name": self.object_name,
            "normalized_name": self.normalized_name or self.object_name,
            "category": self.category,
            "manufacturer": self.manufacturer,
            "model": self.model,
            "source_urls": self.source_urls,
            "source_type": self.source_type,
            "confidence_score": round(self.confidence_score, 3),
            "dimensions": self.dimensions,
            "mounting_holes": self.mounting_holes,
            "connector_cutouts": self.connector_cutouts,
            "keepout_zones": self.keepout_zones,
            "board_outline": self.board_outline,
            "port_locations": self.port_locations,
            "hole_pattern": self.hole_pattern,
            "standards": self.standards,
            "assumptions": self.assumptions,
            "unsupported_features": self.unsupported_features,
            "generated_family": self.generated_family,
            "validation_requirements": self.validation_requirements,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "MechanicalObjectSpec":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in known})
