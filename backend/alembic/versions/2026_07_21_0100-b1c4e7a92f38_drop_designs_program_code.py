"""drop designs.program_code

Removes the last storage location for model-authored source code.

Historically the generation pipeline asked the LLM for a CadQuery/OpenSCAD
program and executed it in a sandbox; the program text was persisted here so a
build could be audited or replayed. That executor was removed in the
production-hardening work (docs/production-readiness.md, F-1) and nothing has
written this column since. A column that can hold executable model output is a
replay surface, so it is dropped rather than left dormant.

DATA LOSS IS INTENTIONAL. Any legacy contents are discarded: executable model
source is no longer a supported product artifact, and preserving or migrating it
into another column would recreate exactly the surface this removes. No
replacement field is added. Designs keep their spec, feature graph, semantic
report, and exported STEP/STL — nothing a user can see or download is affected.

The downgrade recreates only an inert NULLable column so the schema can be
rolled back. It does NOT restore any data (there is none to restore) and does
not reinstate the executor, the provider hook, or any API field.

SQLite cannot DROP COLUMN before 3.35, so both directions use Alembic batch
operations (a no-op wrapper on PostgreSQL).

Revision ID: b1c4e7a92f38
Revises: 7fbf0c6446aa
Create Date: 2026-07-21 01:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b1c4e7a92f38"
down_revision: Union[str, None] = "7fbf0c6446aa"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("designs") as batch:
        batch.drop_column("program_code")


def downgrade() -> None:
    # Inert, nullable, and never populated — present only so the previous
    # revision's schema shape can be restored.
    with op.batch_alter_table("designs") as batch:
        batch.add_column(sa.Column("program_code", sa.Text(), nullable=True))
