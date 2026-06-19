"""merge custom-prompts/monitor line with nggaev-v2 (judge_status)

Revision ID: daa93bd3ce94
Revises: 0029_judge_status, 43cde4a391e0
Create Date: 2026-06-19 11:27:26.087070

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'daa93bd3ce94'
down_revision: Union[str, Sequence[str], None] = ('0029_judge_status', '43cde4a391e0')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
