"""homework_jobs.source_map_json — Flow v2 SourceMap (Phase 2).

Adds a nullable JSONB column holding the per-job SourceMap: the factual anchor
(concepts with stable ids, terms, formulas, examples, mistakes, skills) built
from the extracted lesson text and referenced by downstream phases.

Revision ID: d3e4f5a60012
Revises: c8e1d4b27a91
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "d3e4f5a60012"
down_revision: Union[str, Sequence[str], None] = "c8e1d4b27a91"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "homework_jobs",
        sa.Column(
            "source_map_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("homework_jobs", "source_map_json")
