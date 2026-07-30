"""ORM models: User, Project, DesignPrompt, DesignSpec, GeneratedModel,
ExportFile, ManufacturingCheck.

Kept deliberately simple (no over-engineered job system). JSON columns store
the validated spec / mesh / check payloads.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import JSON as SAJSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    # Opt-IN, default False: prompts/designs are never used to improve models
    # unless the user explicitly turns this on (docs/legal/ai-model-data-usage.md).
    data_improvement_opt_in: Mapped[bool] = mapped_column(Boolean, default=False)

    projects: Mapped[list["Project"]] = relationship(back_populates="user")


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(255), default="Untitled part")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    user: Mapped["User | None"] = relationship(back_populates="projects")
    designs: Mapped[list["Design"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class Design(Base):
    """One part: its prompt, current validated spec, mesh, exports, and checks.

    Combines DesignPrompt/DesignSpec/GeneratedModel conceptually into one row
    that owns the latest state, while keeping prompt history in `prompt`.
    """

    __tablename__ = "designs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    prompt: Mapped[str] = mapped_column(Text, default="")
    object_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    spec_json: Mapped[dict | None] = mapped_column(SAJSON, nullable=True)
    assumptions: Mapped[list | None] = mapped_column(SAJSON, nullable=True)
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    clarification_question: Mapped[str | None] = mapped_column(Text, nullable=True)
    missing_required: Mapped[list | None] = mapped_column(SAJSON, nullable=True)
    can_generate_with_defaults: Mapped[bool] = mapped_column(default=False)
    clarified_spec_candidate: Mapped[dict | None] = mapped_column(SAJSON, nullable=True)
    route: Mapped[str | None] = mapped_column(String(32), nullable=True)
    route_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    auto_repaired: Mapped[bool] = mapped_column(default=False)
    export_formats: Mapped[list | None] = mapped_column(SAJSON, nullable=True)
    # NOTE: there is deliberately no column here for model-authored source. The
    # legacy `program_code` column was dropped in migration b1c4e7a92f38 — a
    # persisted program is a replay surface, and executable model output is no
    # longer a supported product artifact. Do not add a replacement field.
    # Enforced by tests/test_phase1_llm_trust_boundary.py.
    semantic_json: Mapped[dict | None] = mapped_column(SAJSON, nullable=True)
    repair_attempts: Mapped[int] = mapped_column(default=0)
    preview_json: Mapped[dict | None] = mapped_column(SAJSON, nullable=True)
    features_json: Mapped[list | None] = mapped_column(SAJSON, nullable=True)
    bounding_box: Mapped[dict | None] = mapped_column(SAJSON, nullable=True)
    spec_hash: Mapped[str | None] = mapped_column(String(32), nullable=True)
    thumbnail_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    generation_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    project: Mapped["Project"] = relationship(back_populates="designs")
    exports: Mapped[list["ExportFile"]] = relationship(
        back_populates="design", cascade="all, delete-orphan"
    )
    checks: Mapped[list["ManufacturingCheck"]] = relationship(
        back_populates="design", cascade="all, delete-orphan"
    )
    feedback: Mapped[list["Feedback"]] = relationship(
        back_populates="design", cascade="all, delete-orphan"
    )
    versions: Mapped[list["DesignVersion"]] = relationship(
        back_populates="design", cascade="all, delete-orphan"
    )


class ExportFile(Base):
    __tablename__ = "export_files"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    design_id: Mapped[str] = mapped_column(ForeignKey("designs.id"))
    fmt: Mapped[str] = mapped_column(String(16))  # "stl" | "step"
    storage_key: Mapped[str] = mapped_column(String(512))
    url: Mapped[str] = mapped_column(String(1024))
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    design: Mapped["Design"] = relationship(back_populates="exports")


class ManufacturingCheck(Base):
    __tablename__ = "manufacturing_checks"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    design_id: Mapped[str] = mapped_column(ForeignKey("designs.id"))
    check: Mapped[str] = mapped_column(String(64))
    severity: Mapped[str] = mapped_column(String(16))
    passed: Mapped[bool] = mapped_column(default=True)
    message: Mapped[str] = mapped_column(Text)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    design: Mapped["Design"] = relationship(back_populates="checks")


class Feedback(Base):
    """User feedback on a generated design.

    Linked to the user and the design (which in turn owns the prompt, spec,
    checks and exports), so feedback is fully traceable for beta analysis.
    """

    __tablename__ = "feedback"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    design_id: Mapped[str] = mapped_column(ForeignKey("designs.id"), index=True)
    rating: Mapped[str] = mapped_column(String(8))  # "up" | "down"
    categories: Mapped[list | None] = mapped_column(SAJSON, nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Snapshot for traceability even if the design is later edited.
    spec_hash: Mapped[str | None] = mapped_column(String(32), nullable=True)
    object_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    # --- "Report a bad result" workflow fields (all nullable/off by default;
    # plain thumbs up/down feedback never populates these). See
    # design_service.report_bad_result / docs/ops/data-retention.md.
    is_bad_result_report: Mapped[bool] = mapped_column(default=False)
    design_version_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # A snapshot of validation_summary(design) at report time -- traceable
    # even if the design is later edited/re-validated.
    validation_snapshot: Mapped[dict | None] = mapped_column(SAJSON, nullable=True)
    # Physical-world outcomes the user reports back, for the controlled-beta
    # print/fit success metrics. None = not reported (never assume False).
    print_success: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    fit_success: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # Explicit, separate consent to store free-text explanation content
    # (comment / report_reason). Privacy-conscious default: consent is
    # required before any free text is persisted -- see report_bad_result,
    # which drops the text entirely when this is False.
    report_consent: Mapped[bool] = mapped_column(default=False)
    report_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    design: Mapped["Design"] = relationship(back_populates="feedback")


class DesignVersion(Base):
    """An immutable snapshot of a design's buildable state, captured right
    after every successful edit that actually commits (never on a rejected /
    rolled-back edit — see CriticalEditRejected in app.services.design_service).

    Exactly one of ``spec_snapshot`` / ``plan_snapshot`` is populated, matching
    the design's ``route`` at that point (template-built designs carry a
    DesignSpec; CadPlan-built designs carry a feature graph). Restoring a
    version replays its snapshot through the SAME apply_spec_edit /
    apply_plan_edit validation pipeline as any other edit (see
    app.services.version_service.restore_version) rather than being a separate,
    unvalidated code path.
    """

    __tablename__ = "design_versions"
    __table_args__ = (
        UniqueConstraint("design_id", "version_number", name="uq_design_versions_number"),
        Index("ix_design_versions_design_created", "design_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    design_id: Mapped[str] = mapped_column(ForeignKey("designs.id"), index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    # "create" | "regenerate" | "modify_prompt" | "localized_edit" |
    # "face_edit" | "face_edit_plan" | "restore"
    edit_kind: Mapped[str] = mapped_column(String(32))
    summary: Mapped[str] = mapped_column(Text, default="")
    spec_snapshot: Mapped[dict | None] = mapped_column(SAJSON, nullable=True)
    plan_snapshot: Mapped[dict | None] = mapped_column(SAJSON, nullable=True)
    spec_hash: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Old/new value pairs for the fields this edit actually touched, e.g.
    # [{"field": "hole.diameter", "old": 6.0, "new": 9.0}] — populated for
    # edits (empty for the initial "create" snapshot).
    diff_json: Mapped[list | None] = mapped_column(SAJSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)

    design: Mapped["Design"] = relationship(back_populates="versions")


class SafetyAcknowledgment(Base):
    """An explicit, timestamped record that a user acknowledged the
    engineering-review notice for a design flagged
    ``policy=require_acknowledgment`` (app.safety.policy). This is the audit
    trail proving the acknowledgment gate ran and was actually agreed to --
    never inferred, never silently defaulted to True.

    ``notice_text`` snapshots the EXACT message shown at ack time, so a later
    change to category wording never retroactively changes what the record
    proves the user agreed to."""

    __tablename__ = "safety_acknowledgments"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    design_id: Mapped[str] = mapped_column(ForeignKey("designs.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    categories: Mapped[list] = mapped_column(SAJSON)
    notice_text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class CalibrationProfile(Base):
    """One versioned physical-calibration profile row (app.schemas.calibration
    .CalibrationProfile is the validated Pydantic shape this table backs).

    A DRAFT profile is edited in place. Once ``status='validated'`` the
    service layer refuses further in-place edits — a change instead creates a
    NEW row with ``derived_from_id`` pointing at this one and ``version``
    incremented, so a validated profile's numbers can never silently drift
    out from under a design that already used them."""

    __tablename__ = "calibration_profiles"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    created_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True)
    derived_from_id: Mapped[str | None] = mapped_column(
        ForeignKey("calibration_profiles.id"), nullable=True, index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    label: Mapped[str] = mapped_column(String(200))
    source_type: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)

    printer: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    firmware: Mapped[str | None] = mapped_column(String(128), nullable=True)
    nozzle_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    material_type: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    material_brand: Mapped[str | None] = mapped_column(String(128), nullable=True)
    material_product: Mapped[str | None] = mapped_column(String(128), nullable=True)
    material_color: Mapped[str | None] = mapped_column(String(64), nullable=True)
    slicer: Mapped[str | None] = mapped_column(String(64), nullable=True)
    slicer_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    line_width_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    layer_height_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    nozzle_temp_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    bed_temp_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    flow_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    wall_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cooling_pct: Mapped[float | None] = mapped_column(Float, nullable=True)

    test_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")

    reviewed: Mapped[bool] = mapped_column(Boolean, default=False)
    reviewed_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Only one row per (printer, material_type, nozzle_mm) should be active at
    # a time — enforced by the service layer, not a DB constraint (SQLite
    # partial-unique-index support is inconsistent across the dev/prod split).
    active: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now)

    measurements: Mapped[list["CalibrationMeasurement"]] = relationship(
        back_populates="profile", cascade="all, delete-orphan")


class CalibrationMeasurement(Base):
    """One measured quantity within a CalibrationProfile. ``raw_samples_mm``
    is the physical source of truth; ``median_mm``/``range_mm``/``stddev_mm``/
    ``confidence`` are always SERVER-recomputed from it (see
    app.services.calibration_service), never trusted from client input."""

    __tablename__ = "calibration_measurements"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    profile_id: Mapped[str] = mapped_column(
        ForeignKey("calibration_profiles.id"), index=True)
    measurement_type: Mapped[str] = mapped_column(String(32), index=True)
    feature: Mapped[str] = mapped_column(String(128), index=True)
    fit_class: Mapped[str | None] = mapped_column(String(16), nullable=True)
    nominal_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    raw_samples_mm: Mapped[list] = mapped_column(SAJSON, default=list)
    sample_count: Mapped[int] = mapped_column(Integer, default=0)
    median_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    range_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    stddev_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    profile: Mapped["CalibrationProfile"] = relationship(back_populates="measurements")


class Job(Base):
    """A queued unit of CAD work (docs/adr/0001-job-queue-database-backed.md).

    This table IS the queue — no Redis/Celery broker. A worker subprocess
    claims a row with an atomic conditional UPDATE (see
    app.services.job_service.claim_next_job), does the actual drawing-parsing/
    CadPlan-compile/OCCT/mesh/export work in ITS OWN isolated OS process, and
    writes progress back here so the API process (which never touches CAD
    geometry itself once this is wired in) can serve GET /api/jobs/{id} from
    cheap DB reads alone.

    ``status`` is the safe, closed state machine exposed to clients (never an
    internal traceback): queued, running, validating, exporting, succeeded,
    failed, timed_out, cancelled. ``stage`` is optional finer-grained, free-
    text progress WITHIN "running" (e.g. "reading_drawing", "interpreting" for
    a drawing job) for richer UI progress — safe-state validity does not
    depend on it.
    """

    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_jobs_user_idempotency_key"),
        Index("ix_jobs_status_created", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    # Client-supplied (Idempotency-Key header) OR server-generated; unique per
    # user so a retried submission returns the SAME job instead of a duplicate.
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    job_type: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    stage: Mapped[str | None] = mapped_column(String(32), nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Validated, JSON-serializable arguments the worker needs to reconstruct
    # the call (e.g. prompt/project_id/name for design_create; notes/units/
    # thickness_mm/family + an uploaded-file storage key for drawing_to_cad).
    # Never raw file bytes -- those are written to storage/job_tmp first.
    payload_json: Mapped[dict] = mapped_column(SAJSON, default=dict)
    # {"design_id": ..., "generated": ..., ...} on success; the shape mirrors
    # today's synchronous response payloads so callers see the same contract.
    result_json: Mapped[dict | None] = mapped_column(SAJSON, nullable=True)
    # ALWAYS a safe, user-facing sentence -- never str(exc) for an
    # unclassified/internal error. See job_service.classify_error.
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_category: Mapped[str | None] = mapped_column(String(24), nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=1)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    worker_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Updated periodically by the worker while running; a "running" job whose
    # heartbeat has gone stale is presumed orphaned (its worker process died)
    # and is reaped -- see job_service.reap_stale_jobs.
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
