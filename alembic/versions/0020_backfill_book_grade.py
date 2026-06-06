"""Backfill books.grade from the filename for NULL/empty rows.

A NULL grade silently defeats Notion archiving. New books are fixed at ingest;
this one-off heals existing rows. The grade regex is INLINED (not imported from
app code) because migrations are frozen snapshots and must not depend on
evolving application logic.
"""
import re
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b3f6a1c2d4e5"
down_revision: Union[str, Sequence[str], None] = "a7c1e9d2b4f8"
branch_labels = None
depends_on = None

_GRADE_RE = re.compile(r"(\d{1,2})\s*[-_ ]?\s*(?:sinf|klass|класс)", re.IGNORECASE)


def _derive(name: str | None) -> str | None:
    if not name:
        return None
    m = _GRADE_RE.search(name)
    if not m:
        return None
    n = int(m.group(1))
    return str(n) if 1 <= n <= 11 else None


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT id, original_filename FROM books "
            "WHERE grade IS NULL OR grade = ''"
        )
    ).fetchall()
    for row in rows:
        grade = _derive(row.original_filename)
        if grade is not None:
            conn.execute(
                sa.text("UPDATE books SET grade = :g WHERE id = :id"),
                {"g": grade, "id": row.id},
            )


def downgrade() -> None:
    # No-op: cannot know which rows were NULL before this backfill.
    pass
