"""toc_entries.section_number: NOT NULL -> NULL

Revision ID: c8e1d4b27a91
Revises: b71d3a4f6c20
Create Date: 2026-05-06

Real-world textbook TOCs frequently contain entries without a section
number (introductions, prefaces, appendices, "About this book", etc.).
The original schema treated ``section_number`` as required, which caused
the TOC extractor to reject otherwise-valid output from the agent.

This migration relaxes the column to ``NULL`` and matches the relaxed
Pydantic schemas in ``app/schemas/toc.py``.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c8e1d4b27a91"
down_revision: Union[str, Sequence[str], None] = "b71d3a4f6c20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "toc_entries",
        "section_number",
        existing_type=sa.String(length=32),
        nullable=True,
    )


def downgrade() -> None:
    # Backfill any nulls with empty string before re-imposing NOT NULL,
    # otherwise the migration would fail on existing rows that benefited
    # from the relaxed constraint.
    op.execute(
        "UPDATE toc_entries SET section_number = '' WHERE section_number IS NULL"
    )
    op.alter_column(
        "toc_entries",
        "section_number",
        existing_type=sa.String(length=32),
        nullable=False,
    )
