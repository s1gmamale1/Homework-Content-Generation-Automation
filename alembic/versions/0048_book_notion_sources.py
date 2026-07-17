"""book_notion_sources: Notion (page,block) -> book mapping + books.toc_ready_at
(worklog 0144 task 1, prepare-status-redo).

Revision ID: 0048_book_notion_sources
Revises: 0047_credential_slots
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0048_book_notion_sources"
down_revision: Union[str, Sequence[str], None] = "0047_credential_slots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "book_notion_sources",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "book_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("books.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("notion_page_id", sa.Text(), nullable=False),
        sa.Column("notion_block_id", sa.Text(), nullable=False),
        sa.Column(
            "linked_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "notion_page_id", "notion_block_id",
            name="uq_book_notion_sources_page_block",
        ),
    )
    op.create_index(
        "ix_book_notion_sources_book_id", "book_notion_sources", ["book_id"]
    )
    op.add_column(
        "books", sa.Column("toc_ready_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("books", "toc_ready_at")
    op.drop_index("ix_book_notion_sources_book_id", table_name="book_notion_sources")
    op.drop_table("book_notion_sources")
