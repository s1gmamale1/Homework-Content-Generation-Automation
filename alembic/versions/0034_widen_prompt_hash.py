"""Widen phase_outputs.prompt_hash for prefixed custom-prompt hashes.

A custom-prompt provenance hash is ``custom:sha256:<64 hex>`` = 78 chars, which
overflowed the original VARCHAR(64) (sized for a bare 64-char sha256 digest).
Widen to 128 so custom-prompt phase rows can be inserted. Backwards compatible:
existing 64-char values fit unchanged."""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0034_widen_prompt_hash"
down_revision: Union[str, Sequence[str], None] = "0033_custom_prompts_phases"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "phase_outputs",
        "prompt_hash",
        type_=sa.String(length=128),
        existing_type=sa.String(length=64),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "phase_outputs",
        "prompt_hash",
        type_=sa.String(length=64),
        existing_type=sa.String(length=128),
        existing_nullable=False,
    )
