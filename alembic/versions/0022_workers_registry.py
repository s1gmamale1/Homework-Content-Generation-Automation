"""Add workers registry table (fleet liveness)."""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d5e9f1a2b3c4"
down_revision: Union[str, Sequence[str], None] = "c4a7b2d3e6f0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workers",
        sa.Column("pc_id", sa.String(length=128), primary_key=True),
        sa.Column("last_heartbeat", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="online"),
        sa.Column("notes", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("workers")
