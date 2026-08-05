"""Account-level data lifecycle: privacy summary + self-service deletion.

Explicit, ordered deletion rather than relying on any implicit DB/ORM
cascade -- account deletion is exactly the kind of operation where "I assumed
cascade handled it" is the wrong thing to assume silently. See
docs/legal/data-retention-policy.md and docs/ops/data-retention.md (the
ops-facing retention/backup doc; this module is the user-facing feature that
doc says did not exist yet).
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import CalibrationProfile, Design, ExportFile, Project, SafetyAcknowledgment, User
from app.observability import log_event


def privacy_summary(db: Session, user: User) -> dict:
    """What's stored for this account, its retention posture, and the user's
    current data-usage choice -- the technical backing for
    docs/legal/privacy-policy.md's "what we store" section."""
    project_count = db.scalar(
        select(func.count()).select_from(Project).where(Project.user_id == user.id)
    ) or 0
    design_count = db.scalar(
        select(func.count()).select_from(Design).join(Project)
        .where(Project.user_id == user.id)
    ) or 0
    storage_bytes = db.scalar(
        select(func.coalesce(func.sum(ExportFile.size_bytes), 0))
        .select_from(ExportFile).join(Design).join(Project)
        .where(Project.user_id == user.id)
    ) or 0

    from app.config import settings

    return {
        "account_created_at": user.created_at.isoformat(),
        "data_improvement_opt_in": user.data_improvement_opt_in,
        "stored": {
            "projects": project_count,
            "designs": design_count,
            "export_file_bytes": int(storage_bytes),
        },
        "retention": {
            "artifact_retention_days": settings.artifact_retention_days,
            "note": (
                "Design rows (prompt, spec, dimensions, validation results) are "
                "kept until you delete your account or the design. Exported "
                "files (STL/STEP/GLB) beyond the retention window are "
                "reclaimed automatically by a background sweep; the design "
                "record itself is never deleted by that sweep -- only the "
                "downloadable files."
            ),
        },
        "model_improvement_usage": (
            "Your prompts and designs are used to improve LunaiCAD's models "
            "ONLY if data_improvement_opt_in is true. This is opt-IN and "
            "defaults to false for every account."
            if user.data_improvement_opt_in else
            "Your prompts and designs are NOT used to improve LunaiCAD's "
            "models. This is the default for every account (opt-in, not "
            "opt-out) -- see docs/legal/ai-model-data-usage.md."
        ),
        "artifact_visibility": (
            "Designs and their exports are private to your account. Every "
            "design/export route is owner-checked (a JOIN filter on your "
            "user id, not a fetch-then-check) -- see "
            "tests/test_ownership_export_hardening.py."
        ),
        "account_deletion": (
            "DELETE /api/auth/me permanently deletes your projects, designs, "
            "exports, calibration profiles, safety acknowledgments, and "
            "account. This cannot be undone."
        ),
    }


def delete_account(db: Session, user: User) -> None:
    """Permanently delete every row owned by this user, then the user row
    itself, in one transaction. Explicit and ordered -- does not rely on any
    assumed cascade behavior at the ORM or DB level (User.projects has no
    delete cascade; Project.user_id is nullable specifically so this
    function, not an implicit cascade, is the sole deletion path)."""
    user_id = user.id

    db.query(SafetyAcknowledgment).filter(
        SafetyAcknowledgment.user_id == user_id
    ).delete(synchronize_session=False)
    db.query(CalibrationProfile).filter(
        CalibrationProfile.created_by_user_id == user_id
    ).delete(synchronize_session=False)

    # Each Project delete cascades to its Designs (cascade="all,
    # delete-orphan" on Project.designs), which in turn cascade to
    # ExportFile/ManufacturingCheck/Feedback/DesignVersion (same pattern on
    # Design's own relationships) -- one ORM delete per project is enough.
    projects = db.scalars(select(Project).where(Project.user_id == user_id)).all()
    for project in projects:
        db.delete(project)

    # Feedback a user left on designs they don't own (if any) isn't reached
    # by the project cascade above -- delete it explicitly too.
    from app.models import Feedback

    db.query(Feedback).filter(Feedback.user_id == user_id).delete(synchronize_session=False)

    db.delete(user)
    db.commit()
    log_event("account_deleted", user_id=user_id, projects_deleted=len(projects))
