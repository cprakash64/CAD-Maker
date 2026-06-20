"""ORM models: User, Project, DesignPrompt, DesignSpec, GeneratedModel,
ExportFile, ManufacturingCheck.

Kept deliberately simple (no over-engineered job system). JSON columns store
the validated spec / mesh / check payloads.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
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
    # v0.5-GEN2: auditable generated program + semantic verification.
    program_code: Mapped[str | None] = mapped_column(Text, nullable=True)
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

    design: Mapped["Design"] = relationship(back_populates="feedback")
