"""homework_jobs: add real_life_json

Revision ID: b7c91d4e2a55
Revises: a3f5e2d18c44
Create Date: 2026-05-29

Canonical Real-Life Challenge column for the Practice Arc (Phase 4). Holds the
rich generator source-of-truth; the beta adapter down-maps it to the platform
`real_life_challenge` key at export time.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "b7c91d4e2a55"
down_revision: Union[str, Sequence[str], None] = "a3f5e2d18c44"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "homework_jobs",
        sa.Column("real_life_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("homework_jobs", "real_life_json")
