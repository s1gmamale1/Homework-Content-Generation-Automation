"""Drop the dead `difficulty` column from homework_jobs.

The classify/easy-hard phase was removed long ago (single flow per subject,
`difficulty` pinned None at the pipeline) — the column has been all-NULL and
unreferenced since. api-3 cleanup removes the last code paths; this drops the
column to match. Safe: dead, all-NULL data."""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a8c7e6d5f4b3"
down_revision: Union[str, Sequence[str], None] = "b9d8e7f6a5c4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("homework_jobs", "difficulty")


def downgrade() -> None:
    op.add_column(
        "homework_jobs",
        sa.Column("difficulty", sa.String(length=16), nullable=True),
    )
