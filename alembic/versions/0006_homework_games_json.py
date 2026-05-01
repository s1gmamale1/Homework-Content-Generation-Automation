"""homework_jobs: add games_json

Revision ID: a4e21cf08b73
Revises: 7b3091fa44c2
Create Date: 2026-05-01

After all phases complete, the pipeline extracts structured games (adaptive
quiz, tile match, memory match, sentence fill) from the game-breaks output
and stores them as JSONB. The frontend renders them as interactive React
components; the download endpoint zips homework.md + games.json.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "a4e21cf08b73"
down_revision: Union[str, Sequence[str], None] = "7b3091fa44c2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "homework_jobs",
        sa.Column(
            "games_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("homework_jobs", "games_json")
