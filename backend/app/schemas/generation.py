"""Generation routing + general-plan schemas (v0.4-GEN).

A prompt is first routed to one of four strategies, then (for the SCAD fallback)
the LLM emits a validated GeneralCADPlan — data only, never code. The backend
compiles the plan to restricted SCAD source.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class GenerationRouteKind(str, Enum):
    precision_template = "precision_template"  # strong template exists
    feature_graph = "feature_graph"            # buildable from safe primitives
    scad_generator = "scad_generator"          # broader mechanical shape via restricted SCAD
    clarification = "clarification"            # impossible / unsafe / too ambiguous


class GenerationRoute(BaseModel):
    route: GenerationRouteKind
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    reason: str = Field(default="", max_length=1024)
    target_template: Optional[str] = Field(default=None, max_length=64)
    required_assumptions: list[str] = Field(default_factory=list)
    unsupported_features: list[str] = Field(default_factory=list)


# --- GeneralCADPlan (input to the restricted SCAD generator) ---------------
# NB: machine fields (kind/op) are validated against a known set rather than by a
# tight character limit — a too-short max_length used to crash valid prompts
# (e.g. a "bearing block" whose kind text exceeded 16 chars). Human-readable text
# never goes in these enum-like fields.
_PRIM_KINDS = {"box", "cylinder", "tube", "hex_prism", "polygon_prism", "sphere", "cone"}
_PLAN_OPS = {"union", "subtract"}
_HOLE_KINDS = {"simple", "counterbore", "countersink"}


def _coerce_enum(value, allowed: set[str], default: str) -> str:
    s = str(value or default).strip().lower()
    return s if s in allowed else default


class PlanPrimitive(BaseModel):
    kind: str = Field(max_length=64)  # box|cylinder|tube|hex_prism|polygon_prism|sphere|cone
    id: Optional[str] = Field(default=None, max_length=64)
    params: dict[str, float] = Field(default_factory=dict)
    at: list[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    op: str = Field(default="union", max_length=32)  # union | subtract

    @field_validator("op", mode="before")
    @classmethod
    def _norm_op(cls, v):
        return _coerce_enum(v, _PLAN_OPS, "union")


class PlanHole(BaseModel):
    diameter: float = Field(gt=0, le=1000)
    x: float = 0.0
    y: float = 0.0
    depth: Optional[float] = Field(default=None, gt=0, le=5000)
    kind: str = Field(default="simple", max_length=32)  # simple|counterbore|countersink

    @field_validator("kind", mode="before")
    @classmethod
    def _norm_kind(cls, v):
        return _coerce_enum(v, _HOLE_KINDS, "simple")


class GeneralCADPlan(BaseModel):
    object_name: str = Field(default="part", max_length=128)
    units: str = Field(default="mm", max_length=8)
    coordinate_system: str = Field(default="z_up", max_length=16)
    overall_dimensions: dict[str, float] = Field(default_factory=dict)
    primitives: list[PlanPrimitive] = Field(default_factory=list)
    holes: list[PlanHole] = Field(default_factory=list)
    patterns: list[dict] = Field(default_factory=list)
    cuts: list[dict] = Field(default_factory=list)
    fillets: list[dict] = Field(default_factory=list)
    chamfers: list[dict] = Field(default_factory=list)
    annotations: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    visual_notes: Optional[str] = Field(default=None, max_length=4000)
    export_targets: list[str] = Field(default_factory=lambda: ["stl"])

    def is_nonempty(self) -> bool:
        return bool(self.primitives)
