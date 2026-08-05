"""Strongly typed analysis of an interpreted 2D drawing for Drawing → CAD.

``DrawingToCADAnalysis`` is the single intermediate every ingest path produces:

* deterministic vector parsing (DXF / SVG) fills it with EXACT geometry,
* the vision provider (PNG / JPG / rendered PDF) fills it from the image,
* user hints (notes / units / thickness / family) override either.

It then maps onto the existing generation pipeline (DrawingInterpretationSpec /
CadPlan) — it is a bridge into the normal validated prompt-to-CAD flow, never a
separate pipeline. ``needs_clarification`` is true ONLY when the drawing is
genuinely unusable; a merely incomplete drawing generates with assumptions.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

# Families the mapper knows depth defaults / builders for. Free strings are
# normalized; anything unrecognized becomes generic_extruded_part.
KNOWN_FAMILIES = {
    "adapter_plate", "mounting_plate", "plate", "bracket", "l_bracket",
    "u_bracket", "enclosure", "flange", "blind_flange", "clamp", "spacer",
    "standoff", "gear", "jig", "wheel", "tire", "generic_extruded_part",
}

_FAMILY_SYNONYMS = {
    "l-bracket": "l_bracket", "u-bracket": "u_bracket",
    "adapter plate": "adapter_plate", "mounting plate": "mounting_plate",
    "blind flange": "blind_flange", "drill jig": "jig",
    "generic": "generic_extruded_part", "extruded": "generic_extruded_part",
}

# Depth defaults (mm) when the drawing has no depth/thickness information.
FAMILY_DEPTH_DEFAULTS_MM: dict[str, float] = {
    "plate": 6.0, "adapter_plate": 6.0, "mounting_plate": 6.0,
    "bracket": 6.0, "l_bracket": 6.0, "u_bracket": 6.0,
    "enclosure": 2.5,  # wall thickness for shells
    "spacer": 20.0, "standoff": 20.0,
    "flange": 10.0, "blind_flange": 10.0,
    "clamp": 12.0, "gear": 8.0, "jig": 10.0,
    "wheel": 25.0, "tire": 25.0,
    "generic_extruded_part": 10.0,
}


def normalize_family(value: str | None) -> Optional[str]:
    if not value:
        return None
    s = str(value).strip().lower()
    s = _FAMILY_SYNONYMS.get(s, s).replace("-", "_").replace(" ", "_")
    if s in KNOWN_FAMILIES:
        return s
    for fam in KNOWN_FAMILIES:  # "flange plate" -> flange, "bracket arm" -> bracket
        if fam in s:
            return fam
    return "generic_extruded_part"


def default_depth_mm(family: str | None) -> float:
    return FAMILY_DEPTH_DEFAULTS_MM.get(family or "", 10.0)


class DetectedView(BaseModel):
    view: Literal["front", "top", "side", "isometric", "section", "unknown"] = "unknown"
    description: Optional[str] = Field(default=None, max_length=512)


class OverallDimensions(BaseModel):
    width_mm: Optional[float] = Field(default=None, gt=0, le=10_000)
    height_mm: Optional[float] = Field(default=None, gt=0, le=10_000)
    depth_mm: Optional[float] = Field(default=None, gt=0, le=10_000)


class ProfilePoint(BaseModel):
    x: float
    y: float


class DrawingProfile(BaseModel):
    """A closed 2D profile in mm, centered on the part origin."""

    kind: Literal["rectangle", "circle", "polygon", "unknown"] = "unknown"
    width_mm: Optional[float] = Field(default=None, gt=0)
    height_mm: Optional[float] = Field(default=None, gt=0)
    diameter_mm: Optional[float] = Field(default=None, gt=0)
    corner_radius_mm: Optional[float] = Field(default=None, ge=0)
    points: list[ProfilePoint] = Field(default_factory=list, max_length=512)


class HoleFeature(BaseModel):
    """A hole/cutout. Positions are mm from the part center (x right, y up).
    ``shape`` records the traced opening geometry so a hexagonal cutout is
    reported (and rendered) as a hexagon, not a circle."""

    diameter_mm: float = Field(gt=0, le=2_000)
    x_mm: float = 0.0
    y_mm: float = 0.0
    count: int = Field(default=1, ge=1, le=400)
    through: bool = True
    depth_mm: Optional[float] = Field(default=None, gt=0)
    kind: Literal["plain", "counterbore", "countersink", "tapped"] = "plain"
    shape: Literal["circle", "ellipse", "hexagon", "regular_polygon",
                   "rectangle", "rounded_slot", "arbitrary_polygon"] = "circle"
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)


class SlotFeature(BaseModel):
    width_mm: float = Field(gt=0)
    length_mm: float = Field(gt=0)
    x_mm: float = 0.0
    y_mm: float = 0.0
    angle_deg: float = 0.0
    through: bool = True


class PocketFeature(BaseModel):
    width_mm: float = Field(gt=0)
    height_mm: float = Field(gt=0)
    depth_mm: Optional[float] = Field(default=None, gt=0)
    x_mm: float = 0.0
    y_mm: float = 0.0


class BossFeature(BaseModel):
    diameter_mm: float = Field(gt=0)
    height_mm: float = Field(gt=0)
    x_mm: float = 0.0
    y_mm: float = 0.0


class PatternFeature(BaseModel):
    kind: Literal["circular", "rectangular"] = "circular"
    count: int = Field(default=4, ge=2, le=400)
    hole_diameter_mm: float = Field(gt=0)
    pitch_circle_diameter_mm: Optional[float] = Field(default=None, gt=0)
    spacing_x_mm: Optional[float] = Field(default=None, gt=0)
    spacing_y_mm: Optional[float] = Field(default=None, gt=0)


class DrawingFeatures(BaseModel):
    through_holes: list[HoleFeature] = Field(default_factory=list)
    blind_holes: list[HoleFeature] = Field(default_factory=list)
    counterbores: list[HoleFeature] = Field(default_factory=list)
    countersinks: list[HoleFeature] = Field(default_factory=list)
    slots: list[SlotFeature] = Field(default_factory=list)
    pockets: list[PocketFeature] = Field(default_factory=list)
    cutouts: list[PocketFeature] = Field(default_factory=list)
    bosses: list[BossFeature] = Field(default_factory=list)
    ribs: list[dict] = Field(default_factory=list)
    fillets_mm: Optional[float] = Field(default=None, ge=0)
    chamfers_mm: Optional[float] = Field(default=None, ge=0)
    patterns: list[PatternFeature] = Field(default_factory=list)

    def hole_count(self) -> int:
        n = sum(h.count for h in self.through_holes + self.blind_holes
                + self.counterbores + self.countersinks)
        n += sum(p.count for p in self.patterns)
        return n


class DimensionAnnotation(BaseModel):
    text: str = Field(max_length=256)
    value: Optional[float] = None
    unit: str = Field(default="mm", max_length=8)
    target_feature: Optional[str] = Field(default=None, max_length=128)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    @field_validator("value", mode="before")
    @classmethod
    def _coerce(cls, v):
        from app.schemas.coerce import to_float

        return v if v is None else to_float(v)


class DrawingToCADAnalysis(BaseModel):
    """Everything Drawing → CAD extracted (or assumed) from one uploaded file."""

    source: Literal["vision", "dxf", "svg", "pdf", "hint", "sketch_ir",
                    "raster_profile", "deterministic_family_builder"] = "vision"
    units: Literal["mm", "inch"] = "mm"
    drawing_type: Literal["mechanical_part", "assembly", "unknown"] = "mechanical_part"
    title: Optional[str] = Field(default=None, max_length=256)
    detected_views: list[DetectedView] = Field(default_factory=list)
    scale_confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    overall_dimensions: OverallDimensions = Field(default_factory=OverallDimensions)
    inferred_depth_mm: Optional[float] = Field(default=None, gt=0)
    outer_profile: DrawingProfile = Field(default_factory=DrawingProfile)
    inner_profiles: list[DrawingProfile] = Field(default_factory=list)
    features: DrawingFeatures = Field(default_factory=DrawingFeatures)
    dimension_annotations: list[DimensionAnnotation] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    ambiguities: list[str] = Field(default_factory=list)
    # Subset of `ambiguities` that fall in a CRITICAL category (see
    # app.schemas.drawing_spec.CRITICAL_UNRESOLVED_CATEGORIES): depth,
    # thickness, bore_type, view_relationship, feature_placement. Non-empty
    # means the final manufacturable export must be blocked (see
    # docs/drawing-to-cad-beta.md) even though the design still builds and
    # stays inspectable. Each entry is a bare category name, e.g. "depth".
    critical_ambiguities: list[str] = Field(default_factory=list)
    recommended_family: Optional[str] = Field(default=None, max_length=64)
    confidence_score: float = Field(default=0.5, ge=0.0, le=1.0)
    # True ONLY when the file is genuinely unusable (unreadable image, empty
    # vector file, non-mechanical content). Incomplete drawings stay false and
    # generate with assumptions instead.
    needs_clarification: bool = False
    clarification_questions: list[str] = Field(default_factory=list)

    @field_validator("recommended_family", mode="before")
    @classmethod
    def _normalize_family(cls, v):
        return normalize_family(v)

    def assume(self, text: str) -> None:
        if text not in self.assumptions:
            self.assumptions.append(text)

    def mark_critical_ambiguity(self, category: str, text: str) -> None:
        """Record a CRITICAL unresolved item: appends the human-readable form
        to `ambiguities` (existing, already-surfaced-as-warning mechanism) AND
        the bare category to `critical_ambiguities` (new -- the deterministic
        export-blocking signal)."""
        if text not in self.ambiguities:
            self.ambiguities.append(text)
        if category not in self.critical_ambiguities:
            self.critical_ambiguities.append(category)

    def usable(self) -> bool:
        """Can anything be generated from this analysis at all?"""
        d = self.overall_dimensions
        has_size = bool(d.width_mm or d.height_mm or self.outer_profile.diameter_mm
                        or self.outer_profile.width_mm)
        return not self.needs_clarification and has_size


__all__ = [
    "DrawingToCADAnalysis", "DrawingFeatures", "DrawingProfile", "HoleFeature",
    "SlotFeature", "PocketFeature", "BossFeature", "PatternFeature",
    "DimensionAnnotation", "OverallDimensions", "DetectedView",
    "normalize_family", "default_depth_mm", "FAMILY_DEPTH_DEFAULTS_MM",
    "KNOWN_FAMILIES",
]
