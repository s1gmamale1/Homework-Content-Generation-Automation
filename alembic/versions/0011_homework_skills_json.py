"""homework_jobs: add skills_json

Revision ID: c2e83f6a1d09
Revises: b7c91d4e2a55
Create Date: 2026-05-29

Lesson skill registry column (Deliverable #1): atomic concepts + can-do target
skills derived from the lesson. The source-of-truth every Practice Arc mission
maps to.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "c2e83f6a1d09"
down_revision: Union[str, Sequence[str], None] = "b7c91d4e2a55"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "homework_jobs",
        sa.Column("skills_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("homework_jobs", "skills_json")
