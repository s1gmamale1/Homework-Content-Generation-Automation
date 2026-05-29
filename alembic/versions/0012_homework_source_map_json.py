"""homework_jobs: add source_map_json

Revision ID: e3a7c1d9b4f2
Revises: c8e1d4b27a91
Create Date: 2026-05-29

PR-1 (Flow v2): the structured source map derived from the extract — concepts
with stable IDs, the factual anchor every downstream phase cites. Persisted as
JSONB alongside the other structured phase columns. Shape:
{"subject_family", "chapter", "section",
 "concepts": [{"id","label","statement","kind","source_ref?"}]}
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "e3a7c1d9b4f2"
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
