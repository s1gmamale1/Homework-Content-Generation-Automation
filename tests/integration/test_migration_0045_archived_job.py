"""Real-DB check that migration 0045 adds toc_entries.notion_archived_job_id
(nullable, defaults NULL). Run:

  createdb -U macmini5 edu_mig0045_test
  DATABASE_URL=postgresql+asyncpg://macmini5@127.0.0.1:5432/edu_mig0045_test \
    RUN_DB_INTEGRATION=1 uv run --extra dev python -m pytest \
    tests/integration/test_migration_0045_archived_job.py -q
  dropdb -U macmini5 edu_mig0045_test
"""
import os

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION") != "1", reason="needs real Postgres"
)

from sqlalchemy import text
from app.db import engine


# `async def`, NOT `def` + `asyncio.run(...)`. `app.db.engine` is a
# process-wide async engine with a connection pool, and asyncpg binds each
# pooled connection to whatever loop was running when it was opened. Driving
# that shared pool from a throwaway loop breaks in BOTH directions:
#
#   * the connection this test opens goes back into the pool alive, then
#     `asyncio.run` closes the loop under it — the next integration test to
#     check it out dies with "Event loop is closed";
#   * and once any earlier test has filled the pool from pytest-asyncio's
#     session loop, this test checks one of those out and dies itself with
#     "got Future ... attached to a different loop".
#
# Both were real: the second is what made this file fail inside a full
# `tests/integration/` run while passing alone. Every other shared-engine test
# here is `async def` and rides the session-scoped loop (see
# `asyncio_default_test_loop_scope` in pyproject.toml) — so does this one now.
# The sibling migration tests that legitimately stay sync (0043/0044/0053 step
# alembic up and down) never touch this engine: they open their OWN raw
# `asyncpg.connect()` and close it inside the same `asyncio.run`, so nothing
# outlives the loop.
async def test_notion_archived_job_id_column_exists():
    async with engine.connect() as conn:
        row = (await conn.execute(text(
            "SELECT is_nullable, data_type FROM information_schema.columns "
            "WHERE table_name='toc_entries' AND column_name='notion_archived_job_id'"
        ))).first()
    assert row is not None, "column missing — did alembic upgrade head run?"
    assert row.is_nullable == "YES"
    assert row.data_type == "uuid"
