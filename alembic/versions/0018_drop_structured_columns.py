"""drop assembled_md + structured *_json columns

Revision ID: e2a5b8c4f1d9
Revises: d1f4a9b3c7e2
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e2a5b8c4f1d9"
down_revision: Union[str, Sequence[str], None] = "d1f4a9b3c7e2"
branch_labels = None
depends_on = None

_COLS = [
    "assembled_md", "games_json", "flashcards_json", "final_challenge_json",
    "memory_sprint_json", "reading_json", "source_map_json", "boss_arena_json",
    "cbp_json", "memory_check_json", "practice_rlc_json",
    "practice_error_detection_json", "practice_memory_match_json",
    "practice_tictactoe_json", "practice_jigsaw_json", "practice_sentence_json",
]


def upgrade() -> None:
    for c in _COLS:
        op.drop_column("homework_jobs", c)


def downgrade() -> None:
    op.add_column("homework_jobs", sa.Column("assembled_md", sa.Text(), nullable=True))
    for c in _COLS:
        if c == "assembled_md":
            continue
        op.add_column("homework_jobs", sa.Column(c, postgresql.JSONB(astext_type=sa.Text()), nullable=True))
