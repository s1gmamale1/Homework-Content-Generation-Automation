"""homework_jobs: add cbp_json and memory_check_json for Flow v2 Learning Sections

Revision ID: c9e2f3a17d85
Revises: c8e1d4b27a91
Create Date: 2026-05-29

Two new JSONB columns for the Phase 3 Learning Sections (Flow v2):

- ``cbp_json``: Case-Based Preview structured output. Exactly 3 recognition
  checkpoints + a Decision Process Explanation (DPE) in slot 7 (open-ended,
  never MCQ) + final simulation showing correct and wrong paths.

- ``memory_check_json``: Memory Check structured output. Items reference
  flashcard IDs; only three supported kinds (multiple_choice/fill_blank/
  choose_correct_explanation); 60%
  pass_threshold to unlock the Practice Arc.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "c9e2f3a17d85"
down_revision: Union[str, Sequence[str], None] = "c8e1d4b27a91"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "homework_jobs",
        sa.Column("cbp_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "homework_jobs",
        sa.Column("memory_check_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("homework_jobs", "memory_check_json")
    op.drop_column("homework_jobs", "cbp_json")
