"""notion archive phase 1: books.grade, homework_jobs.notion_archived_at,
toc_entries.notion_homework_page_id

Revision ID: c9e3f1a07b62
Revises: b6d2f8a4c3e9
Create Date: 2026-06-02
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c9e3f1a07b62"
down_revision: Union[str, Sequence[str], None] = "b6d2f8a4c3e9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("books", sa.Column("grade", sa.String(length=32), nullable=True))
    op.add_column(
        "homework_jobs",
        sa.Column("notion_archived_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "toc_entries",
        sa.Column("notion_homework_page_id", sa.String(length=128), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("toc_entries", "notion_homework_page_id")
    op.drop_column("homework_jobs", "notion_archived_at")
    op.drop_column("books", "grade")
