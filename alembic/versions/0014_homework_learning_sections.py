"""homework_jobs: add cbp_json + memory_check_json (Learning Sections)

Revision ID: a1c7e9d3b5f8
Revises: f4b8d2e6a9c1
Create Date: 2026-05-29

PR-2 (Flow v2) Learning Sections: Case-Based Preview and Memory Check, the
two new structured learning phases that replace preview-* and memory-sprint.
Persisted as JSONB alongside the other phase columns.
- cbp_json: the CaseBasedPreview structure
- memory_check_json: {"items": [...], "pass_threshold": 0.60}
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "a1c7e9d3b5f8"
down_revision: Union[str, Sequence[str], None] = "f4b8d2e6a9c1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "homework_jobs",
        sa.Column("cbp_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "homework_jobs",
        sa.Column(
            "memory_check_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("homework_jobs", "memory_check_json")
    op.drop_column("homework_jobs", "cbp_json")
