"""add toc_entries.notion_archived_job_id (which job's content is on the page)

Revision ID: 0045_notion_archived_job
Revises: 0044_solver_boss_toggle
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0045_notion_archived_job"
down_revision = "0044_solver_boss_toggle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "toc_entries",
        sa.Column("notion_archived_job_id", postgresql.UUID(as_uuid=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("toc_entries", "notion_archived_job_id")
