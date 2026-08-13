"""homework_jobs.reclaims — scheduling-failure counter, separate from `attempts`

`attempts` is charged at CLAIM time and bounds *execution* failures. A job
reclaimed by the stale sweep before it ever started a phase was charged for a
*scheduling* failure; `reclaims` counts those instead so the retry budget is
not destroyed by transient infrastructure contention (retry-accounting-1).

Revision ID: 0060_job_reclaims
Revises: 0059_toc_teacher_deck_notion
"""
from alembic import op
import sqlalchemy as sa

revision = "0060_job_reclaims"
down_revision = "0059_toc_teacher_deck_notion"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "homework_jobs",
        sa.Column("reclaims", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("homework_jobs", "reclaims")
