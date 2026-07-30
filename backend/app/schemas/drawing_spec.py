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

from pydantic import BaseModel, Field, computed_field, field_validator, model_validator

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
    # dimensioned 2D outline extruded to depth (deterministic contour path)
    "profile_extrusion",
}

# Common synonyms the vision model emits -> canonical supported type.
_DRAWING_TYPE_SYNONYMS = {
    "tee": "pipe_tee", "tee fitting": "pipe_tee", "t-fitting": "pipe_tee",
    "pipe branch": "flanged_pipe_branch", "branch": "flanged_pipe_branch",
    "flanged pipe branch": "flanged_pipe_branch", "flanged tee": "flanged_pipe_branch",
    "flanged pipe tee": "flanged_pipe_branch", "sectioned pipe assembly": "pipe_spool",
    "elbow": "pipe_elbow", "pipe elbow": "pipe_elbow",
    "spool": "pipe_spool", "pipe spool": "pipe_spool",
    "flanged pipe spool": "pipe_spool", "flanged spool": "pipe_spool",
    "pipe flange": "blind_flange", "flange with bolt pattern": "flange",
    "bolted flange": "flange", "flanged pipe": "pipe_spool",
    "flange": "blind_flange", "blind flange": "blind_flange",
    "u bracket": "u_bracket", "u-bracket": "u_bracket", "u-shaped bracket": "u_bracket",
    "l bracket": "l_bracket", "l-bracket": "l_bracket",
    "hinge": "hinge_bracket", "hinge bracket": "hinge_bracket",
    "bearing block": "bearing_block", "bearing housing": "bearing_block",
    "vise jaw": "vise_jaw", "enclosure": "electronics_enclosure",
    "sensor enclosure": "sensor_enclosure", "plate": "mounting_plate",
    "pipe fitting": "pipe_fitting", "fitting": "pipe_fitting",
    "extruded_profile": "profile_extrusion", "extruded profile": "profile_extrusion",
    "dimensioned_2d_profile": "profile_extrusion",
    "dimensioned 2d profile": "profile_extrusion",
    "2d profile": "profile_extrusion", "profile extrusion": "profile_extrusion",
    "stepped profile": "profile_extrusion",
}

# Mechanical keywords that justify a generic_mechanical_part fallback.
_MECH_KEYWORDS = (
    "pipe", "flange", "bracket", "plate", "block", "boss", "shaft", "mount",
    "bore", "bearing", "hinge", "enclosure", "fitting", "tee", "elbow", "spool",
)


# Canonical pipe/flange/spool families — a drawing recognized as one of these
# must ALWAYS build in the pipe/flange family and can NEVER be routed to a
# wheel/rim/tire part (a flange's outer edge is literally a "rim", which used to
# hijack the generic-prompt router). Used by the drawing routing guard.
PIPE_FLANGE_FAMILIES = frozenset({
    "flanged_pipe_branch", "pipe_tee", "pipe_spool", "pipe_elbow",
    "blind_flange", "flange", "pipe_fitting",
})

# Strict subset of PIPE_FLANGE_FAMILIES that are built ONLY by the route-
# locked, deterministic proportion-estimating builder (app.routers.drawings
# ._build_locked_deterministic / "PART G"): wall thickness, PCD, flange
# thickness, and branch length are ALWAYS inferred from drawing proportions
# for these two families specifically, as a disclosed, family-level
# characteristic — capped at REVIEW, never FAILED, and never treated as a
# critical unresolved depth/thickness (docs/drawing-to-cad-beta.md).
#
# Deliberately narrower than PIPE_FLANGE_FAMILIES: a plain "flange" (or
# blind_flange/pipe_spool/pipe_elbow/pipe_fitting) built from a clean vector
# SVG/DXF has REAL parsed geometry, so a genuinely missing depth/thickness on
# that family is still a critical unresolved dimension like any other part —
# missing is missing. Using the broader set here previously caused
# simple_flange.svg (zero dimension text in the source) to silently pass
# export with an invented thickness.
PIPE_BRANCH_DETERMINISTIC_FAMILIES = frozenset({"flanged_pipe_branch", "pipe_tee"})

# Detected-type strings (as the vision model / detector emit them) that mean a
# pipe/flange/spool part, even when they aren't a canonical supported type.
_PIPE_FLANGE_DETECTIONS = frozenset({
    "flanged_pipe_spool", "flanged_pipe_branch", "pipe_flange", "pipe_spool",
    "pipe_tee", "flanged_tee", "pipe_branch", "flange_with_bolt_pattern",
    "sectioned_pipe_assembly", "flanged_pipe", "bolted_flange", "pipe_elbow",
    "blind_flange", "flange", "pipe_fitting",
})


def normalize_drawing_type(value) -> Optional[str]:
    """Map a detected type/string to a supported type, or generic_mechanical_part
    when it's clearly mechanical, else None. Never blocks just for being unlisted."""
    if not value:
        return None
    s = str(value).strip().lower().replace("-", " ")
    s = s.replace(" ", "_") if s.replace(" ", "_") in SUPPORTED_DRAWING_TYPES else s
    if s in SUPPORTED_DRAWING_TYPES:
        return s
    # Synonyms are keyed with spaces; vision often emits underscores.
    for key in (s, s.replace("_", " ")):
        if key in _DRAWING_TYPE_SYNONYMS:
            return _DRAWING_TYPE_SYNONYMS[key]
    if any(k in s for k in _MECH_KEYWORDS):
        return "generic_mechanical_part"
    return None


def is_pipe_flange_detection(*values) -> bool:
    """True when ANY detected/suggested string denotes a pipe/flange/spool part —
    the hard signal that forbids wheel/rim/tire routing for this drawing."""
    for value in values:
        if not value:
            continue
        s = str(value).strip().lower().replace("-", " ").replace(" ", "_")
        if s in _PIPE_FLANGE_DETECTIONS or normalize_drawing_type(value) in PIPE_FLANGE_FAMILIES:
            return True
        # Word-level cue: "... flanged pipe spool ...", "... pipe flange ...".
        words = set(str(value).lower().replace("-", " ").replace("_", " ").split())
        if ("pipe" in words or "flange" in words or "flanged" in words
                or "spool" in words) and not (
                words & {"wheel", "rim", "tire", "tyre", "hub", "spoke", "spokes"}):
            return True
    return False


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

    model_config = {"extra": "forbid"}

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
    model_config = {"extra": "forbid"}

    diameter: Optional[float] = Field(default=None, gt=0, le=1000)
    count: int = Field(default=1, ge=1, le=400)
    callout: Optional[str] = Field(default=None, max_length=256, description="raw text e.g. '4x M6'")
    pattern: Optional[str] = Field(default=None, max_length=256)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    # Through-hole vs blind: None means the drawing didn't make this clear (an
    # unresolved-dimension candidate, not a silent "assume through") --
    # distinct from the deterministic build default (which DOES assume
    # through unless told otherwise, and discloses that as a visible
    # assumption). See UnresolvedDimension / CRITICAL_UNRESOLVED_CATEGORIES.
    through: Optional[bool] = None
    blind_depth: Optional[float] = Field(default=None, gt=0, le=1000)


class DrawingSectionSpec(BaseModel):
    model_config = {"extra": "forbid"}

    name: str = Field(max_length=128, description="e.g. 'A-A'")
    description: Optional[str] = Field(default=None, max_length=1024)


class DrawingViewSpec(BaseModel):
    model_config = {"extra": "forbid"}

    view_type: DrawingViewType
    description: Optional[str] = Field(default=None, max_length=1024)
    dimensions: list[DrawingDimensionSpec] = Field(default_factory=list)
    holes: list[DrawingHoleCalloutSpec] = Field(default_factory=list)


class DrawingAssumption(BaseModel):
    model_config = {"extra": "forbid"}

    field: str = Field(max_length=128)
    assumption: str = Field(max_length=1024)


class DrawingClarificationQuestion(BaseModel):
    model_config = {"extra": "forbid"}

    field: str = Field(max_length=128)
    question: str = Field(max_length=1024)


# The drawing-interpretation ask-vs-default policy (docs/drawing-to-cad-beta.md):
# a MISSING (not merely low-confidence) value in one of these categories is
# never silently defaulted into a final, exportable part -- it is recorded as
# an UnresolvedDimension and blocks export until resolved (either the drawing
# is re-read more carefully, or the user supplies an explicit override, e.g.
# the existing `thickness_mm` form field on /api/drawings/to-cad). This is
# deliberately STRICTER than the general text-to-CAD clarification policy
# (app.cad.plan.clarification_categories) because drawing interpretation is a
# higher-uncertainty, vision/OCR-derived domain -- the same category name
# (e.g. "depth"/"thickness") is allowed as a safe cosmetic default from a
# clear text prompt but is NOT safe to silently default when it was simply
# never shown on the source drawing at all.
CRITICAL_UNRESOLVED_CATEGORIES = frozenset({
    "depth", "thickness", "bore_type", "view_relationship", "feature_placement",
})


def infer_critical_categories(missing_fields: list[str]) -> list[str]:
    """Deterministically map free-text 'missing critical dimension' names
    (from ANY interpretation source -- vision LLM, CV reconstruction, vector
    bridge) onto the closed CRITICAL_UNRESOLVED_CATEGORIES vocabulary, so a
    source that reports something is missing without knowing about the new
    structured field still gets caught. Word-boundary matched, not a bare
    substring check -- see the phase-6 "bearing"/"ring" false-match bug this
    pattern deliberately avoids."""
    import re

    found: list[str] = []
    for raw in missing_fields:
        t = str(raw).lower()
        for cat in CRITICAL_UNRESOLVED_CATEGORIES:
            pattern = r"\b" + cat.replace("_", r"[ _]?") + r"\b"
            if re.search(pattern, t) and cat not in found:
                found.append(cat)
    return found


class UnresolvedDimension(BaseModel):
    """One piece of information the drawing did not make clear enough to use
    without a disclosed default or a follow-up. `critical=True` (category in
    CRITICAL_UNRESOLVED_CATEGORIES) blocks the final manufacturable export;
    a non-critical entry is informational only."""

    model_config = {"extra": "forbid"}

    field: str = Field(max_length=128)
    category: str = Field(max_length=64)
    reason: str = Field(max_length=32, description="missing | conflicting | low_confidence")
    detail: str = Field(default="", max_length=512)
    candidates: list[float] = Field(default_factory=list, max_length=8)
    # Deliberately NOT auto-derived from `category` inside the schema: the
    # value is always set explicitly by the deterministic code that
    # constructs one of these (see app.services.drawing_to_spec), so the
    # ask-vs-default decision is never left to model/schema-default
    # inference. Defaults to True (the safe direction: an entry a caller
    # forgot to classify is treated as blocking, never silently permissive).
    critical: bool = True


# Below this overall confidence we never offer "Confirm & generate".
CONFIDENCE_THRESHOLD = 0.75
# At/above this confidence a RECOGNIZED mechanical drawing is generatable with
# assumptions — open clarification questions become assumptions + warnings, not
# blockers. Below it (or for a non-mechanical image) we still ask.
GENERATE_WITH_ASSUMPTIONS_CONFIDENCE = 0.45


class DrawingInterpretationSpec(BaseModel):
    """The full, validated interpretation of an uploaded drawing."""

    # extra="forbid" rejects genuinely unknown fields (e.g. a hostile OpenAI
    # response smuggling an unexpected key). The three computed_field
    # properties below (actionable, generate_with_assumptions_available,
    # needs_mandatory_review) are serialized into every response the frontend
    # receives, and POST /api/drawings/confirm re-validates that exact
    # round-tripped JSON as its request body -- a legitimate pattern that
    # would otherwise collide with extra="forbid". _strip_computed_fields
    # (below) removes exactly those three known keys before validation, so a
    # client faithfully echoing back what we sent it still works, while any
    # OTHER unrecognized field is still rejected.
    model_config = {"use_enum_values": True, "extra": "forbid"}

    title: Optional[str] = Field(default=None, max_length=256)
    units: Units = Units.mm
    # The CAD object type we believe this maps to — NOT limited to templates.
    # Free string normalized to a supported type (or generic_mechanical_part).
    suggested_object_type: Optional[str] = Field(default=None, max_length=64)
    detected_object_type: Optional[str] = Field(default=None, max_length=128)

    @model_validator(mode="before")
    @classmethod
    def _strip_computed_fields(cls, data):
        """Drop the two computed_field keys if present, so a client echoing
        back a previously-served DrawingInterpretationSpec (e.g. the
        POST /api/drawings/confirm round trip) validates cleanly under
        extra="forbid" -- any OTHER unrecognized key is still rejected."""
        if isinstance(data, dict):
            data = {k: v for k, v in data.items()
                    if k not in ("actionable", "generate_with_assumptions_available",
                                 "needs_mandatory_review")}
        return data

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
    # Structured record of what the drawing did NOT make clear enough to use
    # without a disclosed default (docs/drawing-to-cad-beta.md). Distinct from
    # `assumptions` (defaults already silently-safe-to-apply, e.g. cosmetic)
    # -- a critical entry here means the final manufacturable export is
    # blocked until it's resolved, even if the design itself still builds and
    # is inspectable.
    unresolved_dimensions: list[UnresolvedDimension] = Field(default_factory=list, max_length=32)
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
    # Where the interpretation came from: "vision" (the image was read) or
    # "hint" (classified from the user's text — the dev workaround). Feeds the
    # drawing-fidelity report; a hint-classified build can never be a clean PASS.
    interp_source: str = Field(default="vision", max_length=16)

    @model_validator(mode="after")
    def _infer_unresolved_from_missing(self):
        """Deterministic safety net: ANY interpretation source (vision LLM, CV
        reconstruction, vector bridge) that reports a critical-category field
        in `missing_critical_dimensions` gets a matching critical
        UnresolvedDimension, even if that source never learned about the new
        structured field directly. Never removes entries a source already
        set; only adds ones it's missing."""
        have = {u.category for u in self.unresolved_dimensions}
        for cat in infer_critical_categories(self.missing_critical_dimensions):
            if cat not in have:
                self.unresolved_dimensions.append(UnresolvedDimension(
                    field=cat, category=cat, reason="missing",
                    detail=f"'{cat}' is listed in missing_critical_dimensions",
                    critical=True,
                ))
                have.add(cat)
        return self

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

    def has_geometry_content(self) -> bool:
        """The interpretation carries SOMETHING real from the drawing: measured
        dimensions, hole callouts, or a specific recognized family. An empty
        'generic mechanical part' reading has nothing to build from — generating
        would invent a default part unrelated to the upload."""
        return (
            bool(self.overall_dimensions)
            or bool(self.holes)
            or self.suggested_object_type not in (None, "generic_mechanical_part")
        )

    def generatable_with_assumptions(self) -> bool:
        """ASSUMPTION-FIRST gate: a recognized mechanical drawing generates even
        with open clarification questions / missing secondary dimensions — those
        become assumptions + warnings on the design. Only a non-mechanical,
        unrecognizable, contentless, or very-low-confidence interpretation
        still blocks (never a fabricated default part)."""
        return (
            self.is_mechanical()
            and self.has_geometry_content()
            and self.overall_confidence >= GENERATE_WITH_ASSUMPTIONS_CONFIDENCE
        )

    def critical_unresolved_dimensions(self) -> list[UnresolvedDimension]:
        return [u for u in self.unresolved_dimensions if u.critical]

    def blocks_final_export(self) -> bool:
        """True when at least one CRITICAL unresolved dimension exists — the
        design may still be created and previewed (mandatory-review UI), but
        the final manufacturable export must be blocked until resolved."""
        return bool(self.critical_unresolved_dimensions())

    @property
    def required_assumptions_preview(self) -> list[str]:
        """Human-readable preview of the assumption that WOULD be required for
        each unresolved item if generation proceeds without resolving it —
        distinct from `assumptions` (defaults already applied)."""
        return [
            f"{u.field}: {u.detail or (u.reason + ' — not shown on the drawing')}"
            for u in self.unresolved_dimensions
        ]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def needs_mandatory_review(self) -> bool:
        """Serialized for the UI: an uncertain drawing (critical unresolved
        dimension, or fidelity-affecting low confidence) must be reviewed by
        the user before it can be treated as final -- see
        docs/drawing-to-cad-beta.md."""
        return self.blocks_final_export() or self.overall_confidence < CONFIDENCE_THRESHOLD

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
