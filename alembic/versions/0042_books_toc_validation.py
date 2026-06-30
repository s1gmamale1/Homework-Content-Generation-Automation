"""books: add toc_validation + toc_validation_detail columns (post-TOC vision validator)."""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0042_books_toc_validation"
down_revision: Union[str, Sequence[str], None] = "0041_sa_keys"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "books",
        sa.Column("toc_validation", sa.String(16), nullable=True),
    )
    op.add_column(
        "books",
        sa.Column("toc_validation_detail", sa.Text(), nullable=True),
    )
    op.create_check_constraint(
        "ck_books_toc_validation",
        "books",
        "toc_validation IS NULL OR toc_validation IN ('verified','mismatch','skipped')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_books_toc_validation", "books", type_="check")
    op.drop_column("books", "toc_validation_detail")
    op.drop_column("books", "toc_validation")
