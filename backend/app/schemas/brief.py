"""v0.5-GEN2 schemas: design brief, CAD program plan, and semantic report.

Pipeline: prompt → CADDesignBrief → CADProgramSpec → sandboxed generation →
SemanticReport → (repair). The LLM emits only validated JSON / restricted code;
the backend compiles and runs it in a locked sandbox.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class CADGenerationMode(str, Enum):
    precision_template = "precision_template"
    cadquery_program = "cadquery_program"
    openscad_program = "openscad_program"
    feature_graph = "feature_graph"
    clarification = "clarification"


class BriefHole(BaseModel):
    purpose: str = Field(default="mounting", max_length=64)
    diameter_mm: Optional[float] = Field(default=None, ge=0, le=2000)
    count: int = Field(default=1, ge=0, le=400)
    pattern: Optional[str] = Field(default=None, max_length=64)  # row|grid|bolt_circle
    bolt_circle_diameter_mm: Optional[float] = Field(default=None, ge=0, le=5000)
    counterbore: bool = False
    countersink: bool = False


class CADDesignBrief(BaseModel):
    """Structured understanding of the prompt — what the part *is* and *needs*."""

    object_type: str = Field(max_length=64)
    object_family: str = Field(default="generic", max_length=64)
    mechanical_function: str = Field(default="", max_length=1024)
    units: str = Field(default="mm", max_length=8)
    overall_dimensions: dict[str, float] = Field(default_factory=dict)
    required_features: list[str] = Field(default_factory=list)
    holes: list[BriefHole] = Field(default_factory=list)
    bores: list[float] = Field(default_factory=list)
    slots: list[str] = Field(default_factory=list)
    flanges: list[str] = Field(default_factory=list)
    bosses: list[str] = Field(default_factory=list)
    patterns: list[str] = Field(default_factory=list)
    fillets: list[float] = Field(default_factory=list)
    chamfers: list[float] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    manufacturing_intent: str = Field(default="3d_print", max_length=64)
    visual_notes: Optional[str] = Field(default=None, max_length=4000)
    missing_noncritical_info: list[str] = Field(default_factory=list)
    missing_critical_info: list[str] = Field(default_factory=list)


class CADProgramSpec(BaseModel):
    """A structured plan for generating the part as code (data only — the code is
    restricted and sandbox-executed)."""

    generation_mode: CADGenerationMode = CADGenerationMode.cadquery_program
    kernel: str = Field(default="cadquery", max_length=16)  # cadquery | openscad
    operations_summary: list[str] = Field(default_factory=list)
    expected_features: list[str] = Field(default_factory=list)
    expected_dimensions: dict[str, float] = Field(default_factory=dict)
    expected_exports: list[str] = Field(default_factory=lambda: ["stl", "step"])
    generated_code: Optional[str] = Field(default=None, max_length=20000)
    generated_scad: Optional[str] = Field(default=None, max_length=20000)
    assumptions: list[str] = Field(default_factory=list)
    semantic_checks: list[str] = Field(default_factory=list)


class SemanticCheck(BaseModel):
    name: str = Field(max_length=64)
    passed: bool
    expected: Optional[str] = Field(default=None, max_length=256)
    actual: Optional[str] = Field(default=None, max_length=256)
    severity: str = Field(default="error", max_length=16)  # error | warning


class SemanticReport(BaseModel):
    passed: bool = True
    checks: list[SemanticCheck] = Field(default_factory=list)

    @property
    def failures(self) -> list[SemanticCheck]:
        return [c for c in self.checks if not c.passed and c.severity == "error"]

    def summary(self) -> str:
        if self.passed:
            return "All semantic checks passed."
        fails = "; ".join(f"{c.name}: expected {c.expected}, got {c.actual}" for c in self.failures)
        return f"Semantic check failures: {fails}"
