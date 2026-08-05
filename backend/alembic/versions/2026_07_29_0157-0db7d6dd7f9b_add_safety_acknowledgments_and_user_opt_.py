"""add safety acknowledgments and user opt-in

Safety-policy layer (app.safety): an explicit, timestamped audit trail for
the require_acknowledgment enforcement tier, and an opt-IN (default False)
flag controlling whether a user's prompts/designs may be used to improve
models (docs/legal/ai-model-data-usage.md).

Revision ID: 0db7d6dd7f9b
Revises: 3f2c9a7d1b44
Create Date: 2026-07-29 01:57:42.151952
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0db7d6dd7f9b'
down_revision: Union[str, None] = '3f2c9a7d1b44'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('safety_acknowledgments',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('design_id', sa.String(length=32), nullable=False),
        sa.Column('user_id', sa.String(length=32), nullable=False),
        sa.Column('categories', sa.JSON(), nullable=False),
        sa.Column('notice_text', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['design_id'], ['designs.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('safety_acknowledgments', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_safety_acknowledgments_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_safety_acknowledgments_design_id'), ['design_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_safety_acknowledgments_user_id'), ['user_id'], unique=False)

    with op.batch_alter_table('users', schema=None) as batch_op:
        # server_default so the ADD COLUMN backfills existing rows to False
        # (opt-OUT by default) instead of failing NOT NULL on existing data;
        # dropped after backfill so the ORM's Python-side default (also
        # False) is what governs new rows going forward.
        batch_op.add_column(sa.Column(
            'data_improvement_opt_in', sa.Boolean(), nullable=False,
            server_default=sa.false(),
        ))
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.alter_column('data_improvement_opt_in', server_default=None)


def downgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('data_improvement_opt_in')

    with op.batch_alter_table('safety_acknowledgments', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_safety_acknowledgments_user_id'))
        batch_op.drop_index(batch_op.f('ix_safety_acknowledgments_design_id'))
        batch_op.drop_index(batch_op.f('ix_safety_acknowledgments_created_at'))

    op.drop_table('safety_acknowledgments')
