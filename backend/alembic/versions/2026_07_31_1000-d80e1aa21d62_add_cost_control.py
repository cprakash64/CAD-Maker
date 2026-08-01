"""add cost control

Per-account cost/quota controls and abuse protection for a controlled beta
(docs/operations/cost-control-architecture.md): an atomic, DB-backed budget
reservation ledger (budget_counters / budget_reservations), per-account limit
overrides, a global emergency stop, an admin audit log, and a User.is_admin
flag gating the new admin endpoints.

Revision ID: d80e1aa21d62
Revises: f2a8b3a98437
Create Date: 2026-07-31 10:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d80e1aa21d62"
down_revision: Union[str, None] = "f2a8b3a98437"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # users.is_admin -- NOT NULL boolean on a possibly-populated table: add
    # with a server_default so existing rows backfill atomically, then drop
    # the server_default (Python-side default=False on the model is enough
    # going forward; matches the pattern established in migration
    # f2a8b3a98437 for the same class of change).
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(sa.Column(
            "is_admin", sa.Boolean(), nullable=False, server_default=sa.false(),
        ))
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.alter_column("is_admin", server_default=None)

    op.create_table(
        "account_limit_overrides",
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("daily_generation_limit", sa.Integer(), nullable=True),
        sa.Column("monthly_generation_limit", sa.Integer(), nullable=True),
        sa.Column("daily_drawing_limit", sa.Integer(), nullable=True),
        sa.Column("concurrent_job_limit", sa.Integer(), nullable=True),
        sa.Column("daily_cost_cap_cents", sa.Integer(), nullable=True),
        sa.Column("storage_quota_mb", sa.Integer(), nullable=True),
        sa.Column("max_design_versions", sa.Integer(), nullable=True),
        sa.Column("generation_disabled", sa.Boolean(), nullable=False),
        sa.Column("disabled_reason", sa.Text(), nullable=True),
        sa.Column("updated_by_user_id", sa.String(length=32), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("user_id"),
    )

    op.create_table(
        "budget_counters",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("scope_id", sa.String(length=64), nullable=False),
        sa.Column("period_key", sa.String(length=64), nullable=False),
        sa.Column("used", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scope", "scope_id", "period_key", name="uq_budget_counters_key"),
    )

    op.create_table(
        "budget_reservations",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("job_id", sa.String(length=32), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("operation_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("estimated_tokens", sa.Integer(), nullable=False),
        sa.Column("estimated_cost_cents", sa.Integer(), nullable=False),
        sa.Column("actual_tokens", sa.Integer(), nullable=True),
        sa.Column("actual_cost_cents", sa.Integer(), nullable=True),
        sa.Column("counter_keys", sa.JSON(), nullable=False),
        sa.Column("policy_version", sa.String(length=16), nullable=False),
        sa.Column("release_reason", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reconciled_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "idempotency_key",
                            name="uq_budget_reservations_user_idempotency_key"),
    )
    with op.batch_alter_table("budget_reservations", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_budget_reservations_user_id"), ["user_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_budget_reservations_job_id"), ["job_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_budget_reservations_operation_type"),
                              ["operation_type"], unique=False)
        batch_op.create_index(batch_op.f("ix_budget_reservations_status"), ["status"], unique=False)
        batch_op.create_index(batch_op.f("ix_budget_reservations_created_at"),
                              ["created_at"], unique=False)
        batch_op.create_index("ix_budget_reservations_status_created",
                              ["status", "created_at"], unique=False)

    op.create_table(
        "admin_audit_log",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("admin_user_id", sa.String(length=32), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("target_user_id", sa.String(length=32), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["admin_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["target_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("admin_audit_log", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_admin_audit_log_admin_user_id"),
                              ["admin_user_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_admin_audit_log_action"), ["action"], unique=False)
        batch_op.create_index(batch_op.f("ix_admin_audit_log_target_user_id"),
                              ["target_user_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_admin_audit_log_created_at"),
                              ["created_at"], unique=False)

    op.create_table(
        "emergency_stop",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("activated_by_user_id", sa.String(length=32), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["activated_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("emergency_stop")
    with op.batch_alter_table("admin_audit_log", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_admin_audit_log_created_at"))
        batch_op.drop_index(batch_op.f("ix_admin_audit_log_target_user_id"))
        batch_op.drop_index(batch_op.f("ix_admin_audit_log_action"))
        batch_op.drop_index(batch_op.f("ix_admin_audit_log_admin_user_id"))
    op.drop_table("admin_audit_log")
    with op.batch_alter_table("budget_reservations", schema=None) as batch_op:
        batch_op.drop_index("ix_budget_reservations_status_created")
        batch_op.drop_index(batch_op.f("ix_budget_reservations_created_at"))
        batch_op.drop_index(batch_op.f("ix_budget_reservations_status"))
        batch_op.drop_index(batch_op.f("ix_budget_reservations_operation_type"))
        batch_op.drop_index(batch_op.f("ix_budget_reservations_job_id"))
        batch_op.drop_index(batch_op.f("ix_budget_reservations_user_id"))
    op.drop_table("budget_reservations")
    op.drop_table("budget_counters")
    op.drop_table("account_limit_overrides")
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("is_admin")
