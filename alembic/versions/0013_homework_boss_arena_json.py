"""homework_jobs: add boss_arena_json

Revision ID: f4b8d2e6a9c1
Revises: e3a7c1d9b4f2
Create Date: 2026-05-29

PR-4 (Flow v2): Boss Arena reasoning content (Why -> How -> What questions),
persisted as JSONB alongside the other structured phase columns. Shape:
{"title","starting_hp","questions":[{concept_ids,difficulty,scenario,why,how,
 what,base_damage,hints,correct_feedback,partial_feedback,wrong_feedback}]}
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "f4b8d2e6a9c1"
down_revision: Union[str, Sequence[str], None] = "e3a7c1d9b4f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "homework_jobs",
        sa.Column(
            "boss_arena_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("homework_jobs", "boss_arena_json")
