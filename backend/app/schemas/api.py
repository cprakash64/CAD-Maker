"""Request/response models for the HTTP API."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, field_validator

from app.schemas.design_spec import DesignSpec


class CreateDesignRequest(BaseModel):
    # Long enough for detailed multi-paragraph engineering prompts (>2000 words).
    prompt: str = Field(min_length=1, max_length=20000)
    project_id: Optional[str] = None
    name: Optional[str] = None


class RegenerateRequest(BaseModel):
    """Deterministic regeneration: edited parameters, no LLM call."""

    dimensions: dict[str, float]
    holes: Optional[list[dict]] = None
    fillet_radius: Optional[float] = None
    manufacturing_method: Optional[str] = None
    material: Optional[str] = None


class ModifyRequest(BaseModel):
    """A plain-English edit applied to an existing design (LLM → DesignModification)."""

    prompt: str = Field(min_length=1, max_length=500)


FEEDBACK_CATEGORIES = [
    "wrong_template",
    "wrong_dimensions",
    "bad_geometry",
    "export_failed",
    "confusing_explanation",
    "missing_feature",
    "other",
]


class FeedbackRequest(BaseModel):
    rating: str = Field(pattern="^(up|down)$")
    categories: list[str] = Field(default_factory=list, max_length=10)
    comment: Optional[str] = Field(default=None, max_length=2000)

    @field_validator("categories")
    @classmethod
    def _valid_categories(cls, v: list[str]) -> list[str]:
        bad = [c for c in v if c not in FEEDBACK_CATEGORIES]
        if bad:
            raise ValueError(f"unknown feedback categories: {bad}")
        return v


class FeedbackDTO(BaseModel):
    id: str
    design_id: str
    rating: str
    categories: list[str] = []
    comment: Optional[str] = None
    created_at: str


class PreviewMeshDTO(BaseModel):
    positions: list[float]
    indices: list[int]
    vertex_count: int
    triangle_count: int


class CheckDTO(BaseModel):
    check: str
    severity: str
    passed: bool
    message: str


class ExportDTO(BaseModel):
    fmt: str
    url: str
    size_bytes: int


class DesignDTO(BaseModel):
    id: str
    project_id: str
    prompt: str
    object_type: Optional[str]
    spec: Optional[DesignSpec]
    assumptions: list[str] = []
    explanation: Optional[str] = None
    clarification_question: Optional[str] = None
    needs_clarification: bool = False
    preview: Optional[PreviewMeshDTO] = None
    bounding_box_mm: Optional[dict] = None
    spec_hash: Optional[str] = None
    exports: list[ExportDTO] = []
    checks: list[CheckDTO] = []
    editable_parameters: dict[str, float] = {}
    provider: Optional[str] = None
    generation_ms: Optional[int] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    my_feedback: Optional[FeedbackDTO] = None
    features: list[dict] = []
    # Generate-first transparency: defaults we applied, and whether a missing-info
    # clarification could still be generated with defaults.
    default_assumptions: list[str] = []
    can_generate_with_defaults: bool = False
    missing_required: list[str] = []
    # Full-sentence clarification questions (CadPlan route) rendered as a list.
    clarification_questions: list[str] = []
    # Set when geometry came from the flexible CAD feature graph (not a template).
    feature_graph_ops: list[str] = []
    # v0.4-GEN routing/repair transparency.
    route: Optional[str] = None
    route_reason: Optional[str] = None
    auto_repaired: bool = False
    export_formats: list[str] = ["stl", "step"]
    # v0.5-GEN2 semantic verification.
    semantic_checks: list[dict] = []
    semantic_passed: Optional[bool] = None
    repair_attempts: int = 0
    has_program: bool = False
    # Assumption-first: non-blocking advisory warnings for a compiled model.
    warnings: list[str] = []
    # Feature-level audit: requested mechanical features vs. the compiled model
    # (stable feature ids like tube_bore / bearing_boss / pin_hole).
    feature_audit: list[dict] = []
    feature_audit_passed: Optional[bool] = None
    # Requested-vs-generated dimension report (BRep + mesh ground truth) and the
    # 3D-print readiness summary extracted from it. Null until a model is built.
    dimension_report: Optional[dict] = None
    print_readiness: Optional[dict] = None
    dimensions_within_tolerance: Optional[bool] = None
    # Overall validation severity: "pass" | "warning" | "critical_failure".
    validation_status: Optional[str] = None
    validation_critical_failures: list[str] = []
    validation_warnings: list[str] = []
    # Critical-failure recovery transparency + export gating.
    recovery_attempted: bool = False
    recovery_strategy: Optional[str] = None
    recovery_succeeded: bool = False
    # Non-null only when a manufacturable export is blocked (critical failure).
    download_blocked_reason: Optional[str] = None
    # Large-assembly gate: set when the prompt describes a whole machine /
    # multi-subsystem assembly that must be decomposed into single parts.
    needs_decomposition: bool = False
    decomposition: Optional[dict] = None
    # "single_part" | "assembly" — assembly designs are validated with the
    # assembly profile (multi-body allowed) and labelled as concept models.
    design_mode: Optional[str] = None
    # Structured, offline prompt classification (family/strategy/maturity/
    # limitations) computed before generation. Advisory metadata for the UI.
    classification: Optional[dict] = None


class DesignSummaryDTO(BaseModel):
    id: str
    project_id: str
    prompt: str
    object_type: Optional[str]
    created_at: str
    updated_at: Optional[str] = None
    needs_clarification: bool = False
    export_ready: bool = False
