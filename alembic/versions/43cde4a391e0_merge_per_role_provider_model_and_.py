"""merge per-role provider/model and custom-prompts branches

Revision ID: 43cde4a391e0
Revises: 0027_per_role_provider_model, d2e3f4a5b6c7
Create Date: 2026-06-19 10:18:56.691660

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '43cde4a391e0'
down_revision: Union[str, Sequence[str], None] = ('0027_per_role_provider_model', 'd2e3f4a5b6c7')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
