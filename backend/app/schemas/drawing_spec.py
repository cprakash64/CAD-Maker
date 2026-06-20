"""Strict schemas for Drawing-to-CAD Assist.

A vision model interprets an uploaded 2D mechanical drawing into a
``DrawingInterpretationSpec`` (data only, never code). We validate it, show the
user the extracted views / dimensions / holes / assumptions, and require
confirmation before any geometry is generated. When the drawing lacks enough
information we surface clarification questions instead of guessing.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, computed_field, field_validator

from app.schemas.design_spec import ObjectType, Units

# Detected object types Drawing-to-CAD supports — NOT limited to whole-part
# templates. Anything mechanical is supported; an unrecognized-but-mechanical
# drawing maps to "generic_mechanical_part" and is built by the feature graph.
SUPPORTED_DRAWING_TYPES = {
    "generic_mechanical_part", "adapter_plate", "mounting_plate", "motor_mount_plate",
    "blind_flange", "pipe_spool", "flanged_pipe_branch", "pipe_tee", "pipe_elbow",
    "l_bracket", "u_bracket", "hinge_bracket", "bearing_block", "vise_jaw",
    "electronics_enclosure", "sensor_enclosure", "shaft_support", "bracket",
    "flange", "pipe_fitting",
    # legacy templates still supported
    "rectangular_bracket", "enclosure", "spacer", "pipe_clamp", "drill_jig",
    "handle", "simple_gear_or_pulley", "inline_4_crankshaft",
}

# Common synonyms the vision model emits -> canonical supported type.
_DRAWING_TYPE_SYNONYMS = {
    "tee": "pipe_tee", "tee fitting": "pipe_tee", "t-fitting": "pipe_tee",
    "pipe branch": "flanged_pipe_branch", "branch": "flanged_pipe_branch",
    "flanged pipe branch": "flanged_pipe_branch", "flanged tee": "flanged_pipe_branch",
    "elbow": "pipe_elbow", "pipe elbow": "pipe_elbow",
    "spool": "pipe_spool", "pipe spool": "pipe_spool",
    "flange": "blind_flange", "blind flange": "blind_flange",
    "u bracket": "u_bracket", "u-bracket": "u_bracket", "u-shaped bracket": "u_bracket",
    "l bracket": "l_bracket", "l-bracket": "l_bracket",
    "hinge": "hinge_bracket", "hinge bracket": "hinge_bracket",
    "bearing block": "bearing_block", "bearing housing": "bearing_block",
    "vise jaw": "vise_jaw", "enclosure": "electronics_enclosure",
    "sensor enclosure": "sensor_enclosure", "plate": "mounting_plate",
    "pipe fitting": "pipe_fitting", "fitting": "pipe_fitting",
}

# Mechanical keywords that justify a generic_mechanical_part fallback.
_MECH_KEYWORDS = (
    "pipe", "flange", "bracket", "plate", "block", "boss", "shaft", "mount",
    "bore", "bearing", "hinge", "enclosure", "fitting", "tee", "elbow", "spool",
)


def normalize_drawing_type(value) -> Optional[str]:
    """Map a detected type/string to a supported type, or generic_mechanical_part
    when it's clearly mechanical, else None. Never blocks just for being unlisted."""
    if not value:
        return None
    s = str(value).strip().lower().replace("-", " ")
    s = s.replace(" ", "_") if s.replace(" ", "_") in SUPPORTED_DRAWING_TYPES else s
    if s in SUPPORTED_DRAWING_TYPES:
        return s
    if s in _DRAWING_TYPE_SYNONYMS:
        return _DRAWING_TYPE_SYNONYMS[s]
    if any(k in s for k in _MECH_KEYWORDS):
        return "generic_mechanical_part"
    return None


class DrawingViewType(str, Enum):
    top = "top"
    front = "front"
    right = "right"
    left = "left"
    bottom = "bottom"
    isometric = "isometric"
    section = "section"
    detail = "detail"
    unknown = "unknown"


class DrawingDimensionSpec(BaseModel):
    """A dimension read off the drawing. Descriptive fields are generous so a long
    label never invalidates the whole interpretation."""

    label: str = Field(max_length=256, description="e.g. 'overall width', 'A', 'Ø'")
    value: Optional[float] = Field(default=None, description="numeric value if legible")
    units: Units = Units.mm
    tolerance: Optional[str] = Field(default=None, max_length=128)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    @field_validator("value", mode="before")
    @classmethod
    def _coerce_value(cls, v):
        from app.schemas.coerce import to_float

        return v if v is None else to_float(v)  # "Ø12", "approx 90mm" -> 12 / 90


class DrawingHoleCalloutSpec(BaseModel):
    diameter: Optional[float] = Field(default=None, gt=0, le=1000)
    count: int = Field(default=1, ge=1, le=400)
    callout: Optional[str] = Field(default=None, max_length=256, description="raw text e.g. '4x M6'")
    pattern: Optional[str] = Field(default=None, max_length=256)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class DrawingSectionSpec(BaseModel):
    name: str = Field(max_length=128, description="e.g. 'A-A'")
    description: Optional[str] = Field(default=None, max_length=1024)


class DrawingViewSpec(BaseModel):
    view_type: DrawingViewType
    description: Optional[str] = Field(default=None, max_length=1024)
    dimensions: list[DrawingDimensionSpec] = Field(default_factory=list)
    holes: list[DrawingHoleCalloutSpec] = Field(default_factory=list)


class DrawingAssumption(BaseModel):
    field: str = Field(max_length=128)
    assumption: str = Field(max_length=1024)


class DrawingClarificationQuestion(BaseModel):
    field: str = Field(max_length=128)
    question: str = Field(max_length=1024)


# Below this overall confidence we never offer "Confirm & generate".
CONFIDENCE_THRESHOLD = 0.75
# At/above this confidence a RECOGNIZED mechanical drawing is generatable with
# assumptions — open clarification questions become assumptions + warnings, not
# blockers. Below it (or for a non-mechanical image) we still ask.
GENERATE_WITH_ASSUMPTIONS_CONFIDENCE = 0.45


class DrawingInterpretationSpec(BaseModel):
    """The full, validated interpretation of an uploaded drawing."""

    model_config = {"use_enum_values": True}

    title: Optional[str] = Field(default=None, max_length=256)
    units: Units = Units.mm
    # The CAD object type we believe this maps to — NOT limited to templates.
    # Free string normalized to a supported type (or generic_mechanical_part).
    suggested_object_type: Optional[str] = Field(default=None, max_length=64)
    detected_object_type: Optional[str] = Field(default=None, max_length=128)

    @field_validator("suggested_object_type", mode="before")
    @classmethod
    def _normalize_type(cls, v):
        from app.schemas.drawing_spec import normalize_drawing_type

        # Accept enum instances too (e.g. ObjectType.adapter_plate).
        if hasattr(v, "value"):
            v = v.value
        return normalize_drawing_type(v)
    template_candidate: Optional[str] = Field(default=None, max_length=128)
    views: list[DrawingViewSpec] = Field(default_factory=list)
    sections: list[DrawingSectionSpec] = Field(default_factory=list)
    overall_dimensions: dict[str, float] = Field(default_factory=dict)
    holes: list[DrawingHoleCalloutSpec] = Field(default_factory=list)

    @field_validator("overall_dimensions", mode="before")
    @classmethod
    def _coerce_overall(cls, v):
        """Vision often returns 'overall_vertical_height': 'approx 90mm'. Coerce
        each value to a float, dropping any unparseable key — never reject the
        whole interpretation over one bad dimension."""
        from app.schemas.coerce import coerce_float_map

        return coerce_float_map(v) if isinstance(v, dict) else v

    assumptions: list[DrawingAssumption] = Field(default_factory=list)
    clarification_questions: list[DrawingClarificationQuestion] = Field(default_factory=list)
    missing_critical_dimensions: list[str] = Field(default_factory=list)
    overall_confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    drawing_units_confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    view_detection_confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    dimension_extraction_confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    unsupported_reason: Optional[str] = Field(default=None, max_length=1024)
    interpretation_rationale: Optional[str] = Field(default=None, max_length=2000)
    # Set when the provider itself failed (API/parse error) so the UI shows a
    # real error instead of silently collapsing to "unknown / 0%".
    provider_error: Optional[str] = Field(default=None, max_length=2000)
    # True when this is a best-effort partial interpretation (repair fell back).
    partial: bool = False

    @property
    def confidence(self) -> float:
        return self.overall_confidence

    def is_actionable(self) -> bool:
        """Fully specified and high-confidence: "Confirm & generate" with no
        caveats. Requires a SUPPORTED mechanical type (template OR generic
        feature-graph part), sufficient confidence, and no open clarifications /
        missing critical dimensions. A type being absent from the template list
        is NOT a reason to block — generic_mechanical_part is actionable."""
        return (
            self.is_mechanical()
            and not self.clarification_questions
            and not self.missing_critical_dimensions
            and self.overall_confidence >= CONFIDENCE_THRESHOLD
        )

    def is_mechanical(self) -> bool:
        """A recognized, supported mechanical object with no fatal reason."""
        return (
            self.suggested_object_type in SUPPORTED_DRAWING_TYPES
            and not self.unsupported_reason
        )

    def generatable_with_assumptions(self) -> bool:
        """ASSUMPTION-FIRST gate: a recognized mechanical drawing generates even
        with open clarification questions / missing secondary dimensions — those
        become assumptions + warnings on the design. Only a non-mechanical,
        unrecognizable, or very-low-confidence interpretation still blocks."""
        return (
            self.is_mechanical()
            and self.overall_confidence >= GENERATE_WITH_ASSUMPTIONS_CONFIDENCE
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def actionable(self) -> bool:
        """Serialized for the UI: fully specified, generate without caveats."""
        return self.is_actionable()

    @computed_field  # type: ignore[prop-decorator]
    @property
    def generate_with_assumptions_available(self) -> bool:
        """Serialized for the UI: offer "Generate CAD with assumptions"."""
        return self.generatable_with_assumptions()

    def maps_to_template(self) -> bool:
        """True when the detected type is a legacy DesignSpec template (built via
        to_design_spec); otherwise it's built via the feature-graph engine."""
        from app.schemas.design_spec import ObjectType as _OT

        return self.suggested_object_type in {t.value for t in _OT}
