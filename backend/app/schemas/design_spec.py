"""Strict design-spec schema.

This is the contract between the (untrusted) LLM and our (trusted) CAD
generators. The LLM only ever produces JSON conforming to this schema (a
``DesignSpec`` for new parts or a ``DesignModification`` for edits); it never
produces executable code. Everything is validated here before any geometry is
created.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class ObjectType(str, Enum):
    """The mechanical part templates supported by the MVP."""

    rectangular_bracket = "rectangular_bracket"
    l_bracket = "l_bracket"
    enclosure = "enclosure"
    spacer = "spacer"
    hex_standoff = "hex_standoff"
    hex_nut = "hex_nut"
    square_nut = "square_nut"
    bolt = "bolt"
    threaded_rod = "threaded_rod"
    shaft_coupler = "shaft_coupler"
    timing_pulley_gt2 = "timing_pulley_gt2"
    rpi4_enclosure = "rpi4_enclosure"
    rpi5_enclosure = "rpi5_enclosure"
    board_enclosure = "board_enclosure"
    motor_mount = "motor_mount"
    bearing_holder = "bearing_holder"
    generic_fitted_box = "generic_fitted_box"
    phone_holder = "phone_holder"
    pipe_clamp = "pipe_clamp"
    drill_jig = "drill_jig"
    handle = "handle"
    adapter_plate = "adapter_plate"
    inline_4_crankshaft = "inline_4_crankshaft"
    flanged_pipe_branch = "flanged_pipe_branch"
    simple_gear_or_pulley = "simple_gear_or_pulley"
    # Not a template — geometry comes from a validated trusted feature graph.
    feature_graph = "feature_graph"


class Units(str, Enum):
    mm = "mm"
    cm = "cm"
    inch = "inch"


class ManufacturingMethod(str, Enum):
    fdm_3d_print = "fdm_3d_print"
    sla_3d_print = "sla_3d_print"
    cnc_milling = "cnc_milling"
    laser_cut = "laser_cut"
    sheet_metal = "sheet_metal"


class HoleType(str, Enum):
    """How a hole is finished at the top face."""

    simple = "simple"  # plain through / clearance hole
    counterbore = "counterbore"  # flat recess for a socket-head cap screw
    countersink = "countersink"  # conical recess for a flat-head screw


class Hole(BaseModel):
    """A hole positioned on the part's primary (+Z) face.

    Coordinates are in the part's local frame, in the spec's units, measured
    from the part origin (template-defined, typically the centroid of the top
    face). ``hole_type`` selects the finish; the matching feature dimensions are
    required when that type is chosen (and auto-inferred when only the feature
    dimensions are supplied, for backward compatibility).
    """

    diameter: float = Field(gt=0, le=500, description="Through-hole (clearance) diameter")
    x: float = Field(description="X position of hole center")
    y: float = Field(description="Y position of hole center")
    hole_type: HoleType = HoleType.simple
    screw_size: Optional[str] = Field(
        default=None, max_length=12, description="e.g. 'M6' — for documentation only"
    )
    counterbore_diameter: Optional[float] = Field(default=None, gt=0, le=500)
    counterbore_depth: Optional[float] = Field(default=None, gt=0, le=500)
    countersink_diameter: Optional[float] = Field(default=None, gt=0, le=500)
    countersink_angle: float = Field(default=90.0, ge=60.0, le=140.0)

    @field_validator("countersink_angle", mode="before")
    @classmethod
    def _fix_angle(cls, v):
        """Never let a missing/garbage/out-of-range angle reject the hole — it's
        only meaningful for countersinks. Coerce to a sane, clamped value."""
        from app.schemas.coerce import clamp, to_float

        f = to_float(v)
        return clamp(f, 60.0, 140.0) if f is not None else 90.0

    @field_validator(
        "diameter", "counterbore_diameter", "counterbore_depth", "countersink_diameter",
        mode="before",
    )
    @classmethod
    def _coerce_numeric(cls, v):
        from app.schemas.coerce import to_float

        # None stays None (optional); strings like "Ø12" become 12.
        return v if v is None else to_float(v)

    @model_validator(mode="after")
    def _validate_feature(self) -> "Hole":
        # Infer the hole type from supplied feature dimensions when left simple,
        # so older specs (counterbore_* without hole_type) still work.
        if self.hole_type == HoleType.simple:
            if self.counterbore_diameter is not None:
                self.hole_type = HoleType.counterbore
            elif self.countersink_diameter is not None:
                self.hole_type = HoleType.countersink

        if self.hole_type == HoleType.counterbore:
            if self.counterbore_diameter is None:
                raise ValueError("counterbore hole requires counterbore_diameter")
            if self.counterbore_diameter <= self.diameter:
                raise ValueError("counterbore_diameter must exceed hole diameter")
            if self.counterbore_depth is None:
                raise ValueError("counterbore hole requires counterbore_depth")
        elif self.hole_type == HoleType.countersink:
            if self.countersink_diameter is None:
                raise ValueError("countersink hole requires countersink_diameter")
            if self.countersink_diameter <= self.diameter:
                raise ValueError("countersink_diameter must exceed hole diameter")
        return self


class DesignSpec(BaseModel):
    """Validated, generator-ready description of a part.

    ``dimensions`` is a free-form map of named lengths (in ``units``). Each
    template declares which keys it understands; range/required validation
    happens in the generator layer so the schema stays flexible while
    generation stays strict.
    """

    model_config = {"use_enum_values": True}

    object_type: ObjectType
    units: Units = Units.mm
    manufacturing_method: ManufacturingMethod = ManufacturingMethod.fdm_3d_print
    # Normalized material keyword (long/descriptive text is moved to visual_notes).
    material: str = Field(default="PLA", max_length=64)
    dimensions: dict[str, float] = Field(default_factory=dict)
    holes: list[Hole] = Field(default_factory=list)
    fillet_radius: Optional[float] = Field(default=None, ge=0, le=500)
    chamfer_size: Optional[float] = Field(default=None, ge=0, le=500)
    notes: Optional[str] = Field(default=None, max_length=4000)
    # Free-form appearance / style / finish text (never affects geometry).
    visual_notes: Optional[str] = Field(default=None, max_length=4000)
    # Optional trusted feature-graph (set when object_type == feature_graph).
    feature_graph: Optional[dict] = None
    # Object-Intelligence board/device preset id (set when object_type is the
    # generic ``board_enclosure`` — selects the curated board preset to build).
    preset_id: Optional[str] = Field(default=None, max_length=64)

    @field_validator("dimensions", mode="before")
    @classmethod
    def _coerce_dimensions(cls, v):
        """Coerce string dimension values ('14.8', 'Ø12', 'approx 90mm') to
        floats, dropping any that can't be parsed (a messy key shouldn't reject
        the whole spec)."""
        from app.schemas.coerce import coerce_float_map

        return coerce_float_map(v) if isinstance(v, dict) else v

    @field_validator("material", mode="before")
    @classmethod
    def _normalize_material(cls, v):
        """Normalize an arbitrary material string to a short keyword so a long,
        descriptive value never rejects the whole spec."""
        if not isinstance(v, str) or not v.strip():
            return "PLA"
        text = v.lower()
        known = [
            "stainless steel", "mild steel", "steel", "aluminum", "aluminium",
            "titanium", "brass", "bronze", "copper", "cast iron", "iron",
            "abs", "petg", "nylon", "tpu", "resin", "pla", "carbon fiber",
            "delrin", "acetal", "polycarbonate", "wood", "acrylic",
        ]
        for kw in known:
            if kw in text:
                return "aluminum" if kw == "aluminium" else kw
        return v[:64]  # truncate unknown material to the limit (never reject)

    @model_validator(mode="after")
    def _check_dimensions(self) -> "DesignSpec":
        for name, value in self.dimensions.items():
            if value <= 0:
                raise ValueError(f"dimension '{name}' must be positive, got {value}")
            if value > 5000:
                raise ValueError(
                    f"dimension '{name}'={value} exceeds 5000 (unrealistically large)"
                )
        if self.fillet_radius and self.chamfer_size:
            raise ValueError("specify either fillet_radius or chamfer_size, not both")
        return self

    def to_mm(self, value: float) -> float:
        """Convert a value expressed in this spec's units to millimeters."""
        factor = {"mm": 1.0, "cm": 10.0, "inch": 25.4}[self.units]
        return value * factor

    def dims_in_mm(self) -> dict[str, float]:
        return {k: self.to_mm(v) for k, v in self.dimensions.items()}


class ParseResult(BaseModel):
    """What the prompt parser returns: either a spec, or clarifying questions.

    If ``spec`` is None the caller should ask ``clarification_question`` before
    attempting generation.
    """

    spec: Optional[DesignSpec] = None
    missing_required: list[str] = Field(default_factory=list)
    clarification_question: Optional[str] = None
    assumptions: list[str] = Field(default_factory=list)
    raw_llm_output: Optional[dict] = None
    # Generate-first support: when only non-critical info is missing we can build
    # with defaults. If we still chose to clarify, this offers a one-click path.
    can_generate_with_defaults: bool = False
    default_assumptions: list[str] = Field(default_factory=list)
    clarified_spec_candidate: Optional[dict] = None
    # v0.4-GEN routing/repair transparency.
    route: Optional[str] = None  # precision_template|feature_graph|scad_generator|clarification
    route_reason: Optional[str] = None
    auto_repaired: bool = False
    export_formats: list[str] = Field(default_factory=lambda: ["stl", "step"])


class DesignModification(BaseModel):
    """A deterministic delta applied to an existing DesignSpec.

    The LLM (or mock) maps an edit prompt like "make it wider" into this strict
    structure; we never let it mutate geometry directly. Values are in the
    units of the modification (default mm). ``apply_modification`` produces a new
    validated DesignSpec.
    """

    set_dimensions: dict[str, float] = Field(default_factory=dict)
    scale_dimensions: dict[str, float] = Field(default_factory=dict)
    set_fillet_radius: Optional[float] = Field(default=None, ge=0, le=500)
    set_chamfer_size: Optional[float] = Field(default=None, ge=0, le=500)
    hole_spread_factor: Optional[float] = Field(default=None, gt=0, le=10)
    set_material: Optional[str] = Field(default=None, max_length=64)
    set_manufacturing_method: Optional[ManufacturingMethod] = None
    clarification_question: Optional[str] = None
    summary: Optional[str] = None

    def is_empty(self) -> bool:
        return not (
            self.set_dimensions
            or self.scale_dimensions
            or self.set_fillet_radius is not None
            or self.set_chamfer_size is not None
            or self.hole_spread_factor is not None
            or self.set_material
            or self.set_manufacturing_method
        )


def apply_modification(spec: DesignSpec, mod: DesignModification) -> DesignSpec:
    """Return a new DesignSpec (in mm) with the modification applied.

    Deterministic and LLM-free at the geometry level: scaling first, then
    absolute overrides, then hole spread and edge treatments. The result is
    re-validated by constructing a fresh DesignSpec.
    """
    dims = spec.dims_in_mm()  # work in mm so edits are unit-stable
    for key, factor in mod.scale_dimensions.items():
        if key in dims:
            dims[key] = round(dims[key] * factor, 4)
    for key, value in mod.set_dimensions.items():
        dims[key] = float(value)

    holes = [h.model_copy(deep=True) for h in spec.holes]
    if mod.hole_spread_factor is not None:
        f = mod.hole_spread_factor
        for h in holes:
            h.x = round(spec.to_mm(h.x) * f, 4)
            h.y = round(spec.to_mm(h.y) * f, 4)
    else:
        for h in holes:
            h.x = spec.to_mm(h.x)
            h.y = spec.to_mm(h.y)
    # Hole feature dimensions were authored in spec units; convert to mm too.
    for h in holes:
        h.diameter = spec.to_mm(h.diameter)
        if h.counterbore_diameter is not None:
            h.counterbore_diameter = spec.to_mm(h.counterbore_diameter)
        if h.counterbore_depth is not None:
            h.counterbore_depth = spec.to_mm(h.counterbore_depth)
        if h.countersink_diameter is not None:
            h.countersink_diameter = spec.to_mm(h.countersink_diameter)

    fillet = spec.to_mm(spec.fillet_radius) if spec.fillet_radius else None
    chamfer = spec.to_mm(spec.chamfer_size) if spec.chamfer_size else None
    if mod.set_fillet_radius is not None:
        fillet = mod.set_fillet_radius
        chamfer = None
    if mod.set_chamfer_size is not None:
        chamfer = mod.set_chamfer_size
        fillet = None

    return DesignSpec(
        object_type=spec.object_type,
        units=Units.mm,
        manufacturing_method=mod.set_manufacturing_method or spec.manufacturing_method,
        material=mod.set_material or spec.material,
        dimensions={k: v for k, v in dims.items() if v > 0},
        holes=[h.model_dump() for h in holes],
        fillet_radius=fillet,
        chamfer_size=chamfer,
        notes=spec.notes,
    )
