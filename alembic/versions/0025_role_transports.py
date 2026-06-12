"""Add per-role transports extract_transport + judge_transport to homework_jobs
and batches: "cli" | "api" | "inherit" (default — follow the job/batch
transport). Phase 4.1."""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b9d8e7f6a5c4"
down_revision: Union[str, Sequence[str], None] = "f7e6d5c4b3a2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # server_default="inherit" backfills existing rows; no separate backfill needed.
    op.add_column(
        "homework_jobs",
        sa.Column("extract_transport", sa.String(length=16), nullable=False, server_default="inherit"),
    )
    op.add_column(
        "homework_jobs",
        sa.Column("judge_transport", sa.String(length=16), nullable=False, server_default="inherit"),
    )
    op.add_column(
        "batches",
        sa.Column("extract_transport", sa.String(length=16), nullable=False, server_default="inherit"),
    )
    op.add_column(
        "batches",
        sa.Column("judge_transport", sa.String(length=16), nullable=False, server_default="inherit"),
    )


def downgrade() -> None:
    op.drop_column("batches", "judge_transport")
    op.drop_column("batches", "extract_transport")
    op.drop_column("homework_jobs", "judge_transport")
    op.drop_column("homework_jobs", "extract_transport")
