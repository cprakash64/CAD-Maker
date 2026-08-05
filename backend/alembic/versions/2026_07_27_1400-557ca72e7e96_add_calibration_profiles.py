"""add calibration profiles + measurements

Physical calibration subsystem (docs/calibration.md): versioned
printer/material/process profiles and their raw physical measurements.

Revision ID: 557ca72e7e96
Revises: b1c4e7a92f38
Create Date: 2026-07-27 14:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "557ca72e7e96"
down_revision: Union[str, None] = "b1c4e7a92f38"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "calibration_profiles",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=32), nullable=True),
        sa.Column("derived_from_id", sa.String(length=32), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("printer", sa.String(length=128), nullable=True),
        sa.Column("firmware", sa.String(length=128), nullable=True),
        sa.Column("nozzle_mm", sa.Float(), nullable=True),
        sa.Column("material_type", sa.String(length=32), nullable=True),
        sa.Column("material_brand", sa.String(length=128), nullable=True),
        sa.Column("material_product", sa.String(length=128), nullable=True),
        sa.Column("material_color", sa.String(length=64), nullable=True),
        sa.Column("slicer", sa.String(length=64), nullable=True),
        sa.Column("slicer_version", sa.String(length=32), nullable=True),
        sa.Column("line_width_mm", sa.Float(), nullable=True),
        sa.Column("layer_height_mm", sa.Float(), nullable=True),
        sa.Column("nozzle_temp_c", sa.Float(), nullable=True),
        sa.Column("bed_temp_c", sa.Float(), nullable=True),
        sa.Column("flow_pct", sa.Float(), nullable=True),
        sa.Column("wall_count", sa.Integer(), nullable=True),
        sa.Column("cooling_pct", sa.Float(), nullable=True),
        sa.Column("test_date", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("reviewed", sa.Boolean(), nullable=False),
        sa.Column("reviewed_by_user_id", sa.String(length=32), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["derived_from_id"], ["calibration_profiles.id"]),
        sa.ForeignKeyConstraint(["reviewed_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("calibration_profiles", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_calibration_profiles_created_by_user_id"),
            ["created_by_user_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_calibration_profiles_derived_from_id"),
            ["derived_from_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_calibration_profiles_source_type"),
            ["source_type"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_calibration_profiles_status"), ["status"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_calibration_profiles_printer"), ["printer"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_calibration_profiles_material_type"),
            ["material_type"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_calibration_profiles_active"), ["active"], unique=False)

    op.create_table(
        "calibration_measurements",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("profile_id", sa.String(length=32), nullable=False),
        sa.Column("measurement_type", sa.String(length=32), nullable=False),
        sa.Column("feature", sa.String(length=128), nullable=False),
        sa.Column("fit_class", sa.String(length=16), nullable=True),
        sa.Column("nominal_mm", sa.Float(), nullable=True),
        sa.Column("raw_samples_mm", sa.JSON(), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column("median_mm", sa.Float(), nullable=True),
        sa.Column("range_mm", sa.Float(), nullable=True),
        sa.Column("stddev_mm", sa.Float(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["profile_id"], ["calibration_profiles.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("calibration_measurements", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_calibration_measurements_profile_id"),
            ["profile_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_calibration_measurements_measurement_type"),
            ["measurement_type"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_calibration_measurements_feature"),
            ["feature"], unique=False)


def downgrade() -> None:
    op.drop_table("calibration_measurements")
    op.drop_table("calibration_profiles")
