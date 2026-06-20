"""Point-and-prompt + circle-to-edit localized editing.

The viewer reports a *selected feature* (resolved from a circle/lasso region or a
direct pick) plus a plain-English instruction. We map that to a constrained,
validated operation and translate it into trusted DesignSpec edits — the LLM
never emits geometry, only this validated data.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class SelectedEntityType(str, Enum):
    face = "face"
    edge = "edge"
    hole = "hole"
    feature = "feature"
    body = "body"
    flange = "flange"
    boss = "boss"
    vent = "vent"
    web = "web"
    journal = "journal"
    bolt_pattern = "bolt_pattern"


class LocalizedOperation(str, Enum):
    change_hole_diameter = "change_hole_diameter"
    change_hole_type = "change_hole_type"
    add_counterbore = "add_counterbore"
    add_countersink = "add_countersink"
    add_fillet = "add_fillet"
    add_chamfer = "add_chamfer"
    thicken_wall = "thicken_wall"
    add_cutout = "add_cutout"
    move_hole = "move_hole"
    add_gusset = "add_gusset"
    change_bolt_hole_diameter = "change_bolt_hole_diameter"
    thicken_flange = "thicken_flange"


class LocalizedModificationSpec(BaseModel):
    """Legacy/direct selection edit (kept for backward compatibility)."""

    model_config = {"use_enum_values": True}

    selected_entity_type: SelectedEntityType
    selected_entity_id: str = Field(max_length=64)
    allowed_operation: LocalizedOperation
    natural_language_instruction: str = Field(min_length=1, max_length=300)
    validated_parameters: dict[str, float] = Field(default_factory=dict)


# --- Circle-to-edit (Gemini "Circle to Search"-style) ---------------------
class CircleSelectionSpec(BaseModel):
    """A circle/lasso region drawn over the viewport, in normalized [0,1] coords
    relative to the canvas (origin top-left). Used to resolve features."""

    cx: float = Field(ge=0.0, le=1.0)
    cy: float = Field(ge=0.0, le=1.0)
    radius: float = Field(gt=0.0, le=1.5)
    # Optional explicit polygon (normalized) for a freehand lasso.
    polygon: list[tuple[float, float]] = Field(default_factory=list)


class SelectedFeatureSpec(BaseModel):
    """A feature resolved from a selection, by stable id (not raw coordinates)."""

    model_config = {"use_enum_values": True}

    entity_type: SelectedEntityType
    entity_id: str = Field(max_length=64)
    label: Optional[str] = Field(default=None, max_length=80)


class SelectedRegionSpec(BaseModel):
    """The full result of a circle/lasso selection: the region plus the
    candidate features the frontend (or backend) resolved inside it."""

    circle: Optional[CircleSelectionSpec] = None
    features: list[SelectedFeatureSpec] = Field(default_factory=list)


class LocalizedEditRequest(BaseModel):
    """Apply an edit to a resolved selection. The frontend supplies the chosen
    feature (from a circle selection or a direct pick) and the instruction."""

    model_config = {"use_enum_values": True}

    selected: SelectedFeatureSpec
    operation: Optional[LocalizedOperation] = None  # inferred if omitted
    instruction: str = Field(min_length=1, max_length=300)
    validated_parameters: dict[str, float] = Field(default_factory=dict)


class LocalizedEditResult(BaseModel):
    applied: bool
    message: str
    operation: Optional[str] = None
    selected_entity_id: Optional[str] = None
