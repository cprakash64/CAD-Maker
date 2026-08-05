"""Request/response models for the HTTP API."""
from __future__ import annotations

from typing import Any, Optional

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


class ReportBadResultRequest(BaseModel):
    """A structured "report a bad result" submission -- see
    app.services.design_service.report_bad_result. Privacy-conscious:
    ``reason`` is only ever stored if ``consent`` is True (see field docs)."""

    categories: list[str] = Field(default_factory=list, max_length=10)
    reason: Optional[str] = Field(
        default=None, max_length=2000,
        description="Free-text explanation. Only persisted if consent=True.")
    consent: bool = Field(
        default=False,
        description="Explicit consent to store the free-text `reason`. "
                    "Without it, every other field is still recorded, but "
                    "`reason` is dropped before it reaches the database.")
    print_success: Optional[bool] = Field(
        default=None, description="Did this design print successfully? "
                                   "Omit/null if not attempted or unknown.")
    fit_success: Optional[bool] = Field(
        default=None, description="Did the printed part fit as expected? "
                                   "Omit/null if not attempted or unknown.")

    @field_validator("categories")
    @classmethod
    def _valid_categories(cls, v: list[str]) -> list[str]:
        bad = [c for c in v if c not in FEEDBACK_CATEGORIES]
        if bad:
            raise ValueError(f"unknown feedback categories: {bad}")
        return v


class ReportBadResultDTO(BaseModel):
    id: str
    design_id: str
    categories: list[str] = []
    reason: Optional[str] = None
    consent: bool
    print_success: Optional[bool] = None
    fit_success: Optional[bool] = None
    design_version_number: Optional[int] = None
    prompt_version: Optional[str] = None
    created_at: str


class VersionDiffEntryDTO(BaseModel):
    field: str
    old: Any = None
    new: Any = None


class DesignVersionDTO(BaseModel):
    id: str
    version_number: int
    edit_kind: str
    summary: str
    spec_hash: Optional[str] = None
    diff: list[VersionDiffEntryDTO] = []
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


class FaceBoundsDTO(BaseModel):
    width: float = 0.0
    height: float = 0.0
    depth: float = 0.0


class FaceLocalFrameDTO(BaseModel):
    origin: tuple[float, float, float] = (0.0, 0.0, 0.0)
    x_axis: tuple[float, float, float] = (1.0, 0.0, 0.0)
    y_axis: tuple[float, float, float] = (0.0, 1.0, 0.0)
    z_axis: tuple[float, float, float] = (0.0, 0.0, 1.0)


class SelectableFaceDTO(BaseModel):
    """Phase 5: semantic, selectable CAD face metadata (model frame, mm)."""

    model_config = {"extra": "ignore"}

    face_id: str
    feature_id: Optional[str] = None
    body_id: Optional[str] = None
    face_kind: str = "unknown"
    label: str = ""
    normal: tuple[float, float, float] = (0.0, 0.0, 1.0)
    center: tuple[float, float, float] = (0.0, 0.0, 0.0)
    area_mm2: float = 0.0
    bounds_mm: Optional[FaceBoundsDTO] = None
    local_frame: Optional[FaceLocalFrameDTO] = None
    allowed_operations: list[str] = []
    confidence: float = 0.0


class SelectableHoleDTO(BaseModel):
    model_config = {"extra": "ignore"}

    hole_id: str
    feature_id: Optional[str] = None
    diameter_mm: float = 0.0
    center: tuple[float, float, float] = (0.0, 0.0, 0.0)
    axis: tuple[float, float, float] = (0.0, 0.0, 1.0)
    through: bool = True
    hole_type: str = "simple"
    allowed_operations: list[str] = []
    confidence: float = 0.0


class SelectableEdgeDTO(BaseModel):
    model_config = {"extra": "ignore"}

    edge_id: str
    feature_id: Optional[str] = None
    edge_kind: str = "linear"
    start: tuple[float, float, float] = (0.0, 0.0, 0.0)
    end: tuple[float, float, float] = (0.0, 0.0, 0.0)
    length_mm: float = 0.0
    allowed_operations: list[str] = []
    confidence: float = 0.0


class SelectableBodyDTO(BaseModel):
    model_config = {"extra": "ignore"}

    body_id: str
    feature_id: Optional[str] = None
    label: str = ""
    volume_mm3: float = 0.0
    bounds_mm: Optional[dict] = None
    material: Optional[str] = None
    allowed_operations: list[str] = []
    confidence: float = 0.0


class SelectableFeatureDTO(BaseModel):
    model_config = {"extra": "ignore"}

    feature_id: str
    feature_type: str = ""
    label: str = ""
    center: tuple[float, float, float] = (0.0, 0.0, 0.0)
    allowed_operations: list[str] = []
    confidence: float = 0.0


class DesignDTO(BaseModel):
    # The job (docs/adr/0001-job-queue-database-backed.md) that produced this
    # design, when generation went through the job queue. Present whenever
    # the request completed within the bounded synchronous wait; absent for
    # designs built via a path that doesn't use the queue (e.g. edits).
    job_id: Optional[str] = None
    id: str
    project_id: str
    prompt: str
    object_type: Optional[str]
    # Clean, family-accurate display name (e.g. "Spur gear", "Pulley", "Hex
    # standoff", "Flange") — the UI shows this instead of the raw object_type.
    title: Optional[str] = None
    spec: Optional[DesignSpec]
    assumptions: list[str] = []
    explanation: Optional[str] = None
    clarification_question: Optional[str] = None
    needs_clarification: bool = False
    preview: Optional[PreviewMeshDTO] = None
    bounding_box_mm: Optional[dict] = None
    spec_hash: Optional[str] = None
    exports: list[ExportDTO] = []
    # GLB preview/web format (app.export.glb): synthesized on the fly from
    # preview_json, never persisted, never a manufacturable file -- kept OUT
    # of `exports` (real ExportFile rows) so that list's meaning (and every
    # caller's exact-match assertions on it) stays STL/STEP only.
    preview_export: Optional[ExportDTO] = None
    checks: list[CheckDTO] = []
    editable_parameters: dict[str, float] = {}
    provider: Optional[str] = None
    generation_ms: Optional[int] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    my_feedback: Optional[FeedbackDTO] = None
    features: list[dict] = []
    # Phase 5/6: semantic selectable-geometry metadata (empty for older designs).
    selectable_faces: list[SelectableFaceDTO] = []
    selectable_edges: list[SelectableEdgeDTO] = []
    selectable_holes: list[SelectableHoleDTO] = []
    selectable_bodies: list[SelectableBodyDTO] = []
    selectable_features: list[SelectableFeatureDTO] = []
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
    # Prompt understanding (object_type/family/dimensions/features/missing_fields/
    # complexity/recommended_route) read from the prompt before generation.
    understanding: Optional[dict] = None
    # Universal contract terminal state: one of generated_single_part /
    # generated_assembly / needs_clarification / needs_decomposition / unsupported
    # / failed_safe. Every design always carries exactly one.
    generation_outcome: Optional[str] = None
    # Ready-to-run family suggestions for a vague category prompt; each item is
    # {label, prompt} and the prompt can be submitted directly.
    clarification_options: list[dict] = []
    # Flat beta-testing telemetry (route/family/confidence/outcome/validation/…).
    telemetry: Optional[dict] = None
    # Deterministic spur-gear debug block (family/route/tooth_count/module/
    # outside_diameter/root_diameter/bore_diameter/measured_tooth_count/
    # gear_visible_teeth). Present only for gear designs.
    gear_debug: Optional[dict] = None
    # Expectation-control presentation block (single source of truth for UI copy):
    # status_badge / status_detail / status_tone, is_concept, concept_notice,
    # export_kind / export_labels / export_notice, parametric_holes,
    # manual_hole_editing, beta_notice. A concept assembly never shows plain PASS.
    presentation: Optional[dict] = None
    # Deterministic hex-standoff debug block (family/route/across_flats/
    # across_corners/length/bore_diameter/measured_corner_count/hex_six_sided).
    # Present only for hex-standoff designs.
    hex_debug: Optional[dict] = None
    # Standard / catalog part block (standard_part=true, family, standard, thread,
    # pitch_mm, across_flats/corners, height, bore, thread_representation, badge,
    # assumed_message). Present only for recognized standard parts (e.g. hex nuts).
    standard_part: Optional[dict] = None
    # Part Family Contract (honesty): requested_family / resolved_family /
    # requested_variant / resolved_variant / standard_part / unsupported_features /
    # substituted_features / missing_inputs / generation_honesty_status. Always set.
    part_family_contract: Optional[dict] = None
    # Family-specific inspector detail (thread mode/length for bolts & rods;
    # bore/set-screw fields for couplers; teeth/pitch/PD for GT2 pulleys).
    part_family_detail: Optional[dict] = None
    # Device-enclosure validation block (board preset, mounting posts, port cutouts,
    # ventilation, lid, logo) — present for board enclosures.
    device_enclosure_validation: Optional[dict] = None
    # Object Intelligence: detected object, source type, confidence, dimensions used,
    # assumptions, generated family, match status, why PASS/REVIEW/CONCEPT.
    object_intelligence: Optional[dict] = None
    # Requested vs generated feature diff (cutouts, tread, mounting holes, …).
    feature_contract: Optional[dict] = None
    # Drawing → CAD fidelity: {source_drawing_confidence, drawing_fidelity_status
    # (ok|review|failed), used_default_fallback}. Present on drawing-built designs.
    drawing_fidelity: Optional[dict] = None
    # Flanged pipe branch/tee topology: {side_branch_present, flange_count,
    # branch_outer_diameter_mm, branch_flange_outer_diameter_mm, ...}. Present on
    # branch drawings so the UI can confirm the side branch was modeled.
    pipe_branch_detail: Optional[dict] = None
    # Reconstructed mechanical sketch: {summary, ir}. Present on plate/bracket
    # drawings built via the sketch-reconstruction pipeline (outer profile +
    # classified cuts + grouped counterbores).
    sketch_ir: Optional[dict] = None

    # --- Product contract (docs/product-contract.md) — additive, new fields.
    # None of these replace an existing field; every value here is also
    # derivable from fields already above (kept for backward compatibility),
    # surfaced here as a single top-level, always-in-the-same-place contract.
    #
    # One of Maturity's four values (production_ready/validated_beta/
    # experimental/unsupported) from the family registry, resolved from
    # object_type. Null only when object_type doesn't resolve to a
    # registered family (e.g. a standard/catalog part governed instead by
    # part_family_contract's generation_honesty_status).
    capability_level: Optional[str] = None
    # True whenever this design was built from an uploaded drawing (a
    # drawing_fidelity block is present). Drawing → CAD is a beta workflow
    # (docs/drawing-to-cad-beta.md): even a family whose text-prompt path is
    # production_ready is capped below that here, since capability_level
    # above already reflects that cap -- this flag is the explicit signal the
    # frontend uses to render the "Beta" label regardless of the numeric cap.
    drawing_beta: bool = False
    # True when this drawing-built design needs a human to review the
    # interpretation before trusting it: drawing_fidelity_status != "ok", or
    # a critical dimension (depth/thickness/bore_type/view_relationship/
    # feature_placement) was never shown on the source drawing. False for
    # every non-drawing design.
    drawing_review_required: bool = False
    # A single top-level confidence in [0,1], the best available of
    # object_intelligence.confidence_score / classification.confidence /
    # (for drawings) drawing_fidelity's source confidence. Null when no
    # confidence signal was computed for this design.
    confidence: Optional[float] = None
    # Short, human-readable restatement of what the system understood the
    # request to be (today: the design title, falling back to object_type).
    interpreted_intent: Optional[str] = None
    # The unit system all dimensions in `spec`/`bounding_box_mm` are in.
    # Always "mm" today (LunaiCAD's canonical internal unit).
    normalized_units: str = "mm"
    # Deduplicated limitations: this design's own feature/classification
    # limitations plus its family's known_limitations from the registry.
    limitations: list[str] = []
    # Every question still open for this design: clarification_question +
    # clarification_questions + missing_required, deduplicated. Empty means
    # nothing is outstanding (not necessarily that generation succeeded --
    # check generation_outcome/validation_status for that).
    unanswered_questions: list[str] = []
    # {"eligible": bool, "reason": str | None} -- whether a manufacturable
    # export can be handed out right now, and why not if it can't. Derived
    # from download_blocked_reason/validation_status/needs_clarification;
    # does not introduce a new blocking rule of its own.
    export_eligibility: Optional[dict] = None
    # Printer-profile provenance disclosure (docs/calibration.md) — see
    # design_service.CALIBRATION_PROVENANCE_NOTICE for why this is a fixed
    # string today rather than a per-design lookup.
    calibration_provenance: Optional[str] = None
    # Safety-policy classification (app.safety): None when no high-consequence
    # category was ever detected for this design. `categories`/`policy` are
    # STICKY (never downgrade across edits — see app.safety.policy). When
    # `policy == "require_acknowledgment"` and `acknowledged` is false, export
    # is blocked until POST .../acknowledge-safety records an explicit ack.
    safety: Optional[dict] = None
    # Version history (app.services.version_service): the number of the most
    # recent snapshot (None until this design has been edited at least once),
    # and the old/new field changes THIS response's edit just made, so the
    # studio can show a diff without a second round-trip to GET .../versions.
    latest_version_number: Optional[int] = None
    last_edit_diff: list[VersionDiffEntryDTO] = []


class DesignSummaryDTO(BaseModel):
    id: str
    project_id: str
    prompt: str
    object_type: Optional[str]
    title: Optional[str] = None
    created_at: str
    updated_at: Optional[str] = None
    needs_clarification: bool = False
    export_ready: bool = False
