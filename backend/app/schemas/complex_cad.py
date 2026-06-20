"""Schemas for complex-CAD planning and the trusted feature-graph interpreter.

The LLM may classify intent and (for the feature-graph route) emit a list of
operations drawn ONLY from a fixed whitelist. The interpreter dispatches on the
operation name — it never evaluates arbitrary code or equations.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional, Union

from pydantic import BaseModel, Field


class CADIntentKind(str, Enum):
    simple_template = "simple_template"
    advanced_template = "advanced_template"
    feature_graph = "feature_graph"
    unsupported = "unsupported"


class CADIntentClassification(BaseModel):
    kind: CADIntentKind
    template_candidate: Optional[str] = Field(default=None, max_length=64)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    reason: Optional[str] = Field(default=None, max_length=300)
    unsupported_reason: Optional[str] = Field(default=None, max_length=300)


# --- Whitelisted feature-graph operations ---------------------------------
class CADOpType(str, Enum):
    box = "box"
    cylinder = "cylinder"
    cone = "cone"
    sphere = "sphere"
    extrude_profile = "extrude_profile"
    revolve_profile = "revolve_profile"
    cut_hole = "cut_hole"
    circular_pattern = "circular_pattern"
    linear_pattern = "linear_pattern"
    boolean_union = "boolean_union"
    boolean_cut = "boolean_cut"
    fillet = "fillet"
    chamfer = "chamfer"


class CADPrimitive(BaseModel):
    """A box/cylinder/cone/sphere primitive with numeric params only."""

    op: CADOpType
    id: str = Field(max_length=40)
    params: dict[str, float] = Field(default_factory=dict)
    at: tuple[float, float, float] = (0.0, 0.0, 0.0)


class CADBooleanOperation(BaseModel):
    op: CADOpType  # boolean_union | boolean_cut
    id: str = Field(max_length=40)
    target: str = Field(max_length=40)
    tool: str = Field(max_length=40)


class CADPatternOperation(BaseModel):
    op: CADOpType  # circular_pattern | linear_pattern
    id: str = Field(max_length=40)
    source: str = Field(max_length=40)
    count: int = Field(ge=1, le=200)
    # circular: radius + about axis; linear: spacing + direction
    params: dict[str, float] = Field(default_factory=dict)


class CADFilletChamferOperation(BaseModel):
    op: CADOpType  # fillet | chamfer
    id: str = Field(max_length=40)
    target: str = Field(max_length=40)
    size: float = Field(gt=0, le=200)


CADFeatureOperation = Union[
    CADPrimitive, CADBooleanOperation, CADPatternOperation, CADFilletChamferOperation
]


class CADFeatureGraph(BaseModel):
    """An ordered list of trusted operations producing one solid.

    ``result_id`` names the operation whose solid is the final part.
    """

    units: str = "mm"
    operations: list[dict] = Field(default_factory=list, max_length=200)
    result_id: Optional[str] = None

    def is_nonempty(self) -> bool:
        return len(self.operations) > 0


class ComplexCADPlan(BaseModel):
    """Top-level output of the complex-CAD planner (strict JSON, no code)."""

    classification: CADIntentClassification
    # Present only when kind == advanced_template / simple_template.
    template_object_type: Optional[str] = Field(default=None, max_length=64)
    template_dimensions: dict[str, float] = Field(default_factory=dict)
    # Present only when kind == feature_graph.
    feature_graph: Optional[CADFeatureGraph] = None
    # Engineering vs. visual/material requirements, separated.
    materials: list[str] = Field(default_factory=list)
    visual_notes: list[str] = Field(default_factory=list)
    unsupported_features: list[str] = Field(default_factory=list)
    clarification_question: Optional[str] = None
