"""phase_outputs.validation_warnings

Revision ID: d1f4a9b3c7e2
Revises: c9e3f1a07b62
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d1f4a9b3c7e2"
down_revision: Union[str, Sequence[str], None] = "c9e3f1a07b62"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "phase_outputs",
        sa.Column("validation_warnings", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("phase_outputs", "validation_warnings")
