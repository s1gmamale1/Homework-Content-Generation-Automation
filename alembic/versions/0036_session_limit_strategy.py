"""Add batches.session_limit_strategy — pause-vs-switch toggle on Claude session-limit hit.

NOT NULL String(16) with server_default 'inherit' so the column is backfilled
without a table rewrite on upgrade, and existing rows behave as 'inherit'
(follow the env default) until explicitly set.  The CHECK constraint mirrors the
pattern used for transport / extract_transport / judge_transport."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0036_session_limit_strategy"
down_revision: Union[str, Sequence[str], None] = "0035_worker_capabilities"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "batches",
        sa.Column(
            "session_limit_strategy",
            sa.String(16),
            nullable=False,
            server_default="inherit",
        ),
    )
    op.create_check_constraint(
        "ck_batches_session_limit_strategy",
        "batches",
        "session_limit_strategy IN ('pause','switch','inherit')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_batches_session_limit_strategy",
        "batches",
        type_="check",
    )
    op.drop_column("batches", "session_limit_strategy")
