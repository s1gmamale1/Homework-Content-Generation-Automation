"""homework_jobs: add Practice Arc game JSON columns (PR-3)

Revision ID: b6d2f8a4c3e9
Revises: a1c7e9d3b5f8
Create Date: 2026-05-29

PR-3 (Flow v2) Practice Arc: the single generic ``game-breaks`` plus the
standalone ``real-life`` and ``consolidation`` phases are replaced by typed,
source-traced conceptual games. Each game phase persists its structured output
to its own JSONB column (a subject only fills the columns for the games in its
flow):
- practice_rlc_json            — RealLifeChallenge
- practice_error_detection_json — ErrorDetection
- practice_memory_match_json   — CbpModeGame (memory_match)
- practice_tictactoe_json      — CbpModeGame (tictactoe)
- practice_jigsaw_json         — CbpModeGame (jigsaw)
- practice_sentence_json       — CbpModeGame (sentence_fill)
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "b6d2f8a4c3e9"
down_revision: Union[str, Sequence[str], None] = "a1c7e9d3b5f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_COLUMNS = (
    "practice_rlc_json",
    "practice_error_detection_json",
    "practice_memory_match_json",
    "practice_tictactoe_json",
    "practice_jigsaw_json",
    "practice_sentence_json",
)


def upgrade() -> None:
    for col in _COLUMNS:
        op.add_column(
            "homework_jobs",
            sa.Column(col, postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        )


def downgrade() -> None:
    for col in reversed(_COLUMNS):
        op.drop_column("homework_jobs", col)
