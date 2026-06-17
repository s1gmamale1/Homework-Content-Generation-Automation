"""Per-role provider/model override columns on homework_jobs + batches.

Adds nullable extract_provider/extract_model/judge_provider/judge_model to both
tables. NULL = fall back to today's role default (extract ->
settings.extract_provider/model; judge -> model_tiers auto). Additive + nullable
so the migration is online-safe by construction; nothing reads these yet."""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0027_per_role_provider_model"
down_revision: Union[str, Sequence[str], None] = "a8c7e6d5f4b3"
branch_labels = None
depends_on = None

_COLS = ("extract_provider", "extract_model", "judge_provider", "judge_model")
_LEN = {
    "extract_provider": 32,
    "extract_model": 128,
    "judge_provider": 32,
    "judge_model": 128,
}


def upgrade() -> None:
    for table in ("homework_jobs", "batches"):
        for col in _COLS:
            op.add_column(table, sa.Column(col, sa.String(_LEN[col]), nullable=True))


def downgrade() -> None:
    for table in ("homework_jobs", "batches"):
        for col in _COLS:
            op.drop_column(table, col)
