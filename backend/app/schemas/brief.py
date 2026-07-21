"""Design brief and semantic report schemas.

Pipeline: prompt → CADDesignBrief → trusted repository-owned builder →
SemanticReport. The LLM emits only validated JSON describing *what the part is*;
it never supplies a program, and there is deliberately no schema field anywhere
capable of carrying model-authored source (the removed `CADProgramSpec` once
had `generated_code` / `generated_scad` — see docs/production-readiness.md F-1).
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


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
