"""toc_entries teacher-deck notion columns

Revision ID: 0059_toc_teacher_deck_notion
Revises: 0054_teacher_material_kind
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0059_toc_teacher_deck_notion"
down_revision = "0054_teacher_material_kind"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "toc_entries",
        sa.Column("notion_lesson_page_id", sa.String(128), nullable=True),
    )
    op.add_column(
        "toc_entries",
        sa.Column(
            "notion_teacher_deck_job_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("toc_entries", "notion_teacher_deck_job_id")
    op.drop_column("toc_entries", "notion_lesson_page_id")
