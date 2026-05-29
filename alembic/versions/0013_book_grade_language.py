"""books.grade + books.language — Flow v2 ingestion (Phase 2 A.2).

Adds grade band (1-11, nullable) and language code (default 'uz') to books.
Set at upload; used to calibrate difficulty and stamp the SourceMap with real
provenance (was hardcoded grade=None / language='uz').

Revision ID: e4f5a6b70013
Revises: d3e4f5a60012
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e4f5a6b70013"
down_revision: Union[str, Sequence[str], None] = "d3e4f5a60012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("books", sa.Column("grade", sa.Integer(), nullable=True))
    op.add_column(
        "books",
        sa.Column(
            "language", sa.String(length=8), nullable=False, server_default="uz"
        ),
    )


def downgrade() -> None:
    op.drop_column("books", "language")
    op.drop_column("books", "grade")
