"""Mechanical Sketch IR — the intermediate representation for Drawing → CAD.

Raw traced contours are NOT sent to CAD. They are first reconstructed into this
structured 2D mechanical sketch (outer profile + classified cut/nested/additive
features + dimensions + construction entities), validated, and only then
compiled to geometry. This is what turns "a rough silhouette with random holes"
into "a rounded plate with 4 counterbores, a central bore, and a slot".

All coordinates are in the sketch's own millimetre frame (x right, y up, origin
at the outer-profile bbox centre) after the ``scale`` has been applied — the
CAD generator consumes mm directly. Everything is plain dataclasses so it
serializes cleanly (``to_dict``) into the design metadata and the debug artifact.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal, Optional

OuterKind = Literal["polygon", "rounded_rectangle", "capsule", "symmetric_plate",
                    "arbitrary_closed_contour"]
CutKind = Literal["circular_hole", "polygon_hole", "rectangular_slot",
                  "rounded_slot", "arc_slot", "arched_rectangular_cutout",
                  "arbitrary_cutout",
                  # internal dark (filled) region cut as a recess/channel, not
                  # solid material — the "key looks solid" field failure:
                  "recessed_channel", "blind_recess", "through_channel_cut"]
NestedKind = Literal["counterbore", "countersink", "concentric_ring",
                     "boss_with_hole"]
AdditiveKind = Literal["boss", "raised_ring", "flange_pad", "tube", "branch"]
DimKind = Literal["horizontal", "vertical", "diameter", "radius",
                  "count_diameter", "angle"]


@dataclass
class Scale:
    px_per_mm: float
    source_dimension_label: Optional[str] = None
    estimated: bool = True


@dataclass
class OuterProfile:
    id: str
    kind: OuterKind
    vertices: list[list[float]] = field(default_factory=list)   # mm, y-up, centred
    arcs: list[dict] = field(default_factory=list)              # {cx,cy,r,a0,a1}
    fillets: list[dict] = field(default_factory=list)           # {x,y,radius}
    bbox_mm: dict = field(default_factory=dict)                 # {w,h}
    corner_radius_mm: Optional[float] = None
    confidence: float = 0.6


@dataclass
class CutFeature:
    id: str
    kind: CutKind
    center: list[float] = field(default_factory=lambda: [0.0, 0.0])  # mm
    diameter_mm: Optional[float] = None
    radius_mm: Optional[float] = None
    width_mm: Optional[float] = None
    height_mm: Optional[float] = None
    angle_deg: float = 0.0
    vertices: list[list[float]] = field(default_factory=list)   # mm, y-up, centred
    arcs: list[dict] = field(default_factory=list)
    through: bool = True
    count: int = 1
    confidence: float = 0.6


@dataclass
class NestedFeature:
    """A concentric group at one centre — a counterbore/countersink is one
    feature (outer recess + inner through-hole), NOT two unrelated holes."""

    id: str
    kind: NestedKind
    center: list[float] = field(default_factory=lambda: [0.0, 0.0])
    outer_diameter_mm: float = 0.0
    inner_diameter_mm: float = 0.0
    outer_depth_mm: Optional[float] = None
    count: int = 1
    confidence: float = 0.6


GroupKind = Literal["boss_with_through_hole", "counterbore_with_through_hole",
                    "ring_with_through_hole", "through_hole_only"]


@dataclass
class ConcentricHoleGroup:
    """Normalized concentric circle group — the mechanical feature that N nearly
    co-centred circles form (a through-hole with counterbore/ring/boss steps),
    NOT N independent holes. All circles share ONE snapped centre; the smallest
    is the through-hole and larger circles are shallow recesses/rings/bosses.

    ``circles_mm`` is the full set of diameters sorted DESCENDING (outer→inner),
    so a 3-ring group (Ø8.2/Ø4.8/Ø3.6) survives intact instead of collapsing to
    outer+inner."""

    id: str
    center: list[float] = field(default_factory=lambda: [0.0, 0.0])   # snapped, mm
    circles_mm: list[float] = field(default_factory=list)             # DESC
    outer_diameter_mm: float = 0.0
    counterbore_or_boss_diameter_mm: float = 0.0
    through_hole_diameter_mm: float = 0.0
    group_kind: GroupKind = "through_hole_only"
    source_entity_ids: list[str] = field(default_factory=list)
    confidence: float = 0.6
    depth_mm: Optional[float] = None
    depth_policy: str = "counterbore_default"
    associated_dimension_labels: list[str] = field(default_factory=list)
    generated_metadata: dict = field(default_factory=dict)

    @property
    def circle_count(self) -> int:
        return len(self.circles_mm)

    def to_nested(self) -> "NestedFeature":
        """Back-compat NestedFeature view (outer recess + inner through bore)."""
        return NestedFeature(
            id=self.id, kind="counterbore", center=list(self.center),
            outer_diameter_mm=self.counterbore_or_boss_diameter_mm,
            inner_diameter_mm=self.through_hole_diameter_mm,
            outer_depth_mm=self.depth_mm, confidence=self.confidence)


@dataclass
class AdditiveFeature:
    id: str
    kind: AdditiveKind
    center: list[float] = field(default_factory=lambda: [0.0, 0.0])
    diameter_mm: Optional[float] = None
    height_mm: Optional[float] = None
    confidence: float = 0.6


@dataclass
class ConstructionEntity:
    """Non-geometry ink: centerlines, dimension/extension/arrow lines, labels,
    watermark. Recorded so the debug overlay can show what was IGNORED."""

    kind: Literal["centerline", "dimension", "arrow", "extension_line",
                  "watermark", "label", "hatch"]
    bbox: list[float] = field(default_factory=list)  # px [x0,y0,x1,y1]
    text: Optional[str] = None


@dataclass
class Dimension:
    value_mm: float
    kind: DimKind
    text: str = ""
    associated_entity_ids: list[str] = field(default_factory=list)
    confidence: float = 0.5


@dataclass
class MechanicalSketchIR:
    units: str = "mm"
    source: Literal["drawing_fallback", "vision", "hybrid"] = "drawing_fallback"
    confidence: float = 0.55
    scale: Optional[Scale] = None
    outer_profiles: list[OuterProfile] = field(default_factory=list)
    cut_features: list[CutFeature] = field(default_factory=list)
    additive_features: list[AdditiveFeature] = field(default_factory=list)
    # Concentric groups are the RICH source of truth (full circle list +
    # classification); ``nested_features`` is the derived 1:1 back-compat view.
    concentric_groups: list[ConcentricHoleGroup] = field(default_factory=list)
    nested_features: list[NestedFeature] = field(default_factory=list)
    construction_entities: list[ConstructionEntity] = field(default_factory=list)
    dimensions: list[Dimension] = field(default_factory=list)
    default_thickness_mm: float = 6.0
    assumptions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------ helpers
    @property
    def outer(self) -> Optional[OuterProfile]:
        return self.outer_profiles[0] if self.outer_profiles else None

    def through_hole_count(self) -> int:
        """Actual through openings — every nested group contributes ONE through
        hole (its inner bore), never one-per-circle-contour."""
        return len([c for c in self.cut_features if c.through]) + len(self.nested_features)

    def feature_summary(self) -> dict:
        by_kind: dict[str, int] = {}
        for c in self.cut_features:
            by_kind[c.kind] = by_kind.get(c.kind, 0) + max(1, c.count)
        return {
            "outer_kind": self.outer.kind if self.outer else None,
            "cut_features": len(self.cut_features),
            "nested_features": len(self.nested_features),
            "concentric_groups": len(self.concentric_groups),
            "additive_features": len(self.additive_features),
            "through_holes": self.through_hole_count(),
            "counterbores": sum(1 for n in self.nested_features
                                if n.kind in ("counterbore", "countersink")),
            "cut_by_kind": by_kind,
            "scale_estimated": self.scale.estimated if self.scale else True,
        }

    def is_usable(self) -> bool:
        return bool(self.outer_profiles)

    def to_dict(self) -> dict:
        return asdict(self)
