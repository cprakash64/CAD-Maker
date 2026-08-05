"""add design_versions

Version history / undo for the studio editing UI: an immutable snapshot row
per successfully-applied edit, so a user can review history and restore an
earlier version through the same validation pipeline as any other edit.

Revision ID: 3f2c9a7d1b44
Revises: 7a0975b01cf8
Create Date: 2026-07-28 11:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "3f2c9a7d1b44"
down_revision: Union[str, None] = "7a0975b01cf8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "design_versions",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("design_id", sa.String(length=32), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("edit_kind", sa.String(length=32), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("spec_snapshot", sa.JSON(), nullable=True),
        sa.Column("plan_snapshot", sa.JSON(), nullable=True),
        sa.Column("spec_hash", sa.String(length=32), nullable=True),
        sa.Column("diff_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["design_id"], ["designs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("design_id", "version_number", name="uq_design_versions_number"),
    )
    with op.batch_alter_table("design_versions", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_design_versions_design_id"), ["design_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_design_versions_created_at"), ["created_at"], unique=False)
        batch_op.create_index(
            "ix_design_versions_design_created", ["design_id", "created_at"], unique=False
        )


def downgrade() -> None:
    op.drop_table("design_versions")
