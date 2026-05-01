"""homework_jobs: add final_challenge_json, memory_sprint_json, reading_json

Revision ID: e4a87cd16f02
Revises: c8d54a7f912b
Create Date: 2026-05-01

Three more structured-output columns so the frontend can render
final-challenge as a boss fight, memory-sprint as a tap-only quiz, and
reading (English) as a passage with inline checkpoints.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "e4a87cd16f02"
down_revision: Union[str, Sequence[str], None] = "c8d54a7f912b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "homework_jobs",
        sa.Column("final_challenge_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "homework_jobs",
        sa.Column("memory_sprint_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "homework_jobs",
        sa.Column("reading_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("homework_jobs", "reading_json")
    op.drop_column("homework_jobs", "memory_sprint_json")
    op.drop_column("homework_jobs", "final_challenge_json")
