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
    # General "move a feature" name for the same handler as move_hole (today
    # only holes are moveable through this pipeline) — a distinct enum VALUE,
    # not a Python alias, so both names are independently accepted as input.
    move_feature = "move_feature"
    add_gusset = "add_gusset"
    change_bolt_hole_diameter = "change_bolt_hole_diameter"
    thicken_flange = "thicken_flange"
    # Resize every hole that currently shares the selected hole's diameter
    # (a natural "hole group": all the M6 holes, all the M3 holes, ...).
    resize_hole_group = "resize_hole_group"
    # Remove a feature, reversibly via version history/restore rather than a
    # toggle back on this same op (see app.editing.localized).
    suppress_feature = "suppress_feature"
    # Snap a hole's diameter to a real published metric clearance-hole size
    # (e.g. "M8"), as opposed to change_hole_diameter's arbitrary float.
    replace_standard = "replace_standard"
    # Resize a hole to fit a given pin/shaft diameter at a named fit class
    # (press/snug/normal/loose), using the calibration clearance tables.
    change_fit_class = "change_fit_class"


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


# --- Phase 4: localized *visual face* edits -------------------------------
# The viewer selects a continuous visual face (planar / cylindrical / curved)
# and sends its geometry context plus a plain-English instruction. We classify
# the edit, translate it into trusted DesignSpec changes, and regenerate real
# CAD — the instruction is only ever used for keyword classification + number
# extraction, never executed.
from pydantic import AliasChoices, field_validator  # noqa: E402


class FaceBounds(BaseModel):
    min: tuple[float, float, float] = (0.0, 0.0, 0.0)
    max: tuple[float, float, float] = (0.0, 0.0, 0.0)


class FaceLocalFrame(BaseModel):
    origin: tuple[float, float, float] = (0.0, 0.0, 0.0)
    normal: tuple[float, float, float] = (0.0, 0.0, 1.0)
    tangent: tuple[float, float, float] = (1.0, 0.0, 0.0)
    bitangent: tuple[float, float, float] = (0.0, 1.0, 0.0)


# Phase 6: selection kinds the localized-edit endpoint accepts. Unknown values
# degrade to the mesh-face fallback rather than being rejected outright.
_SELECTION_TYPES = {
    "backend_face", "backend_edge", "backend_hole", "backend_body",
    "backend_feature", "visual_face", "mesh_face",
}


class FaceSelectionSpec(BaseModel):
    """Selected geometry context reported by the viewer (world frame)."""

    model_config = {"extra": "ignore"}

    selection_type: str = Field(default="visual_face", max_length=32)
    # Accept the frontend's `frontend_visual_face_id` or legacy `visual_face_id`.
    frontend_visual_face_id: Optional[str] = Field(
        default=None,
        max_length=64,
        validation_alias=AliasChoices("frontend_visual_face_id", "visual_face_id"),
    )
    backend_face_id: Optional[str] = Field(default=None, max_length=64)
    # Phase 6: edge / hole ids for edge and hole selections.
    edge_id: Optional[str] = Field(default=None, max_length=64)
    hole_id: Optional[str] = Field(default=None, max_length=64)
    # Stable bbox-face id the frontend mapped the normal to (face_top/face_+X/…).
    feature_id: Optional[str] = Field(default=None, max_length=64)
    body_id: Optional[str] = Field(default=None, max_length=64)
    face_kind: str = Field(default="unknown", max_length=16)
    clicked_point: tuple[float, float, float] = (0.0, 0.0, 0.0)
    center: tuple[float, float, float] = (0.0, 0.0, 0.0)
    normal: tuple[float, float, float] = (0.0, 0.0, 1.0)
    bounds: Optional[FaceBounds] = None
    area: Optional[float] = Field(default=None, ge=0)
    local_frame: Optional[FaceLocalFrame] = None
    triangle_indices: Optional[list[int]] = None

    @field_validator("selection_type", mode="before")
    @classmethod
    def _known_selection_type(cls, v):
        """Degrade an unknown selection_type to the mesh-face fallback rather
        than rejecting the request (forward compatibility)."""
        return v if isinstance(v, str) and v in _SELECTION_TYPES else "visual_face"


class FaceLocalizedEditRequest(BaseModel):
    """POST /api/designs/{id}/face-edit body."""

    model_config = {"populate_by_name": True, "extra": "ignore"}

    instruction: str = Field(min_length=1, max_length=400)
    quick_action: Optional[str] = Field(default=None, max_length=32)
    selection: FaceSelectionSpec
