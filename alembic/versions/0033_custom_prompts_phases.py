"""Add nullable custom_prompts + selected_phases JSONB to homework_jobs and batches.

Carry per-phase custom prompt overrides and the phase subset (closure) from the
launch request to the worker. Nullable, no server default. Backwards compatible."""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0033_custom_prompts_phases"
down_revision: Union[str, Sequence[str], None] = "0032_budget_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("homework_jobs", "batches"):
        op.add_column(table, sa.Column("custom_prompts", JSONB(), nullable=True))
        op.add_column(table, sa.Column("selected_phases", JSONB(), nullable=True))


def downgrade() -> None:
    for table in ("homework_jobs", "batches"):
        op.drop_column(table, "selected_phases")
        op.drop_column(table, "custom_prompts")
