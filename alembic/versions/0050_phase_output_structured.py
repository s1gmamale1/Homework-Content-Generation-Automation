"""phase_outputs: structured content_json columns

Revision ID: 0050_phase_output_structured
Revises: 0048_book_notion_sources
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0050_phase_output_structured"
down_revision: Union[str, Sequence[str], None] = "0048_book_notion_sources"
branch_labels = None
depends_on = None

_MODES = (
    "structured", "markdown_fallback", "markdown_builtin",
    "markdown_custom", "markdown_legacy",
)


def upgrade() -> None:
    op.add_column("phase_outputs", sa.Column("content_json", JSONB(), nullable=True))
    op.add_column("phase_outputs", sa.Column("authoring_mode", sa.String(32), nullable=True))
    op.add_column("phase_outputs", sa.Column("content_schema_version", sa.String(64), nullable=True))
    op.add_column("phase_outputs", sa.Column("renderer_version", sa.String(16), nullable=True))
    modes = ", ".join(f"'{m}'" for m in _MODES)
    op.create_check_constraint(
        "ck_phase_outputs_authoring_mode",
        "phase_outputs",
        f"authoring_mode IS NULL OR authoring_mode IN ({modes})",
    )


def downgrade() -> None:
    op.drop_constraint("ck_phase_outputs_authoring_mode", "phase_outputs", type_="check")
    for col in ("renderer_version", "content_schema_version", "authoring_mode", "content_json"):
        op.drop_column("phase_outputs", col)
