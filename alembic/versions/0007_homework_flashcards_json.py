"""homework_jobs: add flashcards_json

Revision ID: c8d54a7f912b
Revises: a4e21cf08b73
Create Date: 2026-05-01

Structured flashcards extracted from the flashcards phase output, persisted
as JSONB so the frontend can render a flippable deck on /preview/:id.
Shape: {"cards": [{"front", "back", "hint?", "cluster?"}]}
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "c8d54a7f912b"
down_revision: Union[str, Sequence[str], None] = "a4e21cf08b73"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "homework_jobs",
        sa.Column(
            "flashcards_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("homework_jobs", "flashcards_json")
