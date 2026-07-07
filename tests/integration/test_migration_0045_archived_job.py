"""Real-DB check that migration 0045 adds toc_entries.notion_archived_job_id
(nullable, defaults NULL). Run:

  createdb -U macmini5 edu_mig0045_test
  DATABASE_URL=postgresql+asyncpg://macmini5@127.0.0.1:5432/edu_mig0045_test \
    RUN_DB_INTEGRATION=1 uv run --extra dev python -m pytest \
    tests/integration/test_migration_0045_archived_job.py -q
  dropdb -U macmini5 edu_mig0045_test
"""
import asyncio
import os

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION") != "1", reason="needs real Postgres"
)

from sqlalchemy import text
from app.db import engine


def test_notion_archived_job_id_column_exists():
    async def _check():
        async with engine.connect() as conn:
            row = (await conn.execute(text(
                "SELECT is_nullable, data_type FROM information_schema.columns "
                "WHERE table_name='toc_entries' AND column_name='notion_archived_job_id'"
            ))).first()
        assert row is not None, "column missing — did alembic upgrade head run?"
        assert row.is_nullable == "YES"
        assert row.data_type == "uuid"

    asyncio.run(_check())
