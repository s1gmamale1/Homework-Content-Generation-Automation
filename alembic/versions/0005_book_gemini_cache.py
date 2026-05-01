"""books: add gemini_cache_name + gemini_cache_expires_at

Revision ID: 7b3091fa44c2
Revises: 92e8c4d10aa1
Create Date: 2026-05-01

Per-book Gemini context cache. After the first extract on a book, we create
a cache holding the PDF and store its name + expiry on the book row. Every
subsequent job reuses the cache (~25% input cost) until it expires.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "7b3091fa44c2"
down_revision: Union[str, Sequence[str], None] = "92e8c4d10aa1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "books",
        sa.Column("gemini_cache_name", sa.String(length=256), nullable=True),
    )
    op.add_column(
        "books",
        sa.Column("gemini_cache_expires_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("books", "gemini_cache_expires_at")
    op.drop_column("books", "gemini_cache_name")
