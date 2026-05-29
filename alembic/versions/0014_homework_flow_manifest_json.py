"""homework_jobs.flow_manifest_json — Flow v2 division plan + provenance (Phase 2 C/E).

Per-job manifest: the division plan (enabled phases/games) plus, later, prompt
provenance (path/version/hash) and the registry-coverage result.

Revision ID: f5a6b7c80014
Revises: e4f5a6b70013
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "f5a6b7c80014"
down_revision: Union[str, Sequence[str], None] = "e4f5a6b70013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "homework_jobs",
        sa.Column(
            "flow_manifest_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("homework_jobs", "flow_manifest_json")
