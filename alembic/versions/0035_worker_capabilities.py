"""Add nullable workers.capabilities JSONB column (fleet capability gate).

Stores a per-worker capability map published at heartbeat time so the API can
surface which provider/transport combinations a worker can actually serve. NULL
means the worker pre-dates this column or has not yet published capabilities."""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0035_worker_capabilities"
down_revision: Union[str, Sequence[str], None] = "0034_widen_prompt_hash"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workers",
        sa.Column("capabilities", postgresql.JSONB, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("workers", "capabilities")
