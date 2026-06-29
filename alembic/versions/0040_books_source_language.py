"""books: add source_language column (uz|ru|en)

Revision ID: 0040_books_source_language
Revises: 0039_launch_defaults_content
"""
from alembic import op
import sqlalchemy as sa

revision = "0040_books_source_language"
down_revision = "0039_launch_defaults_content"
branch_labels = None
depends_on = None

_CK_NAME = "ck_books_source_language"
_CK_EXPR = "source_language IN ('uz','ru','en')"


def upgrade() -> None:
    op.add_column(
        "books",
        sa.Column("source_language", sa.String(8), nullable=False, server_default="uz"),
    )
    op.create_check_constraint(_CK_NAME, "books", _CK_EXPR)


def downgrade() -> None:
    op.drop_constraint(_CK_NAME, "books", type_="check")
    op.drop_column("books", "source_language")
