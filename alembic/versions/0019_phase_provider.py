"""phase_outputs.provider (per-phase attribution)

Revision ID: a7c1e9d2b4f8
Revises: e2a5b8c4f1d9
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a7c1e9d2b4f8"
down_revision: Union[str, Sequence[str], None] = "e2a5b8c4f1d9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("phase_outputs", sa.Column("provider", sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column("phase_outputs", "provider")
