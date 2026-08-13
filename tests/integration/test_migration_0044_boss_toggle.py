"""Real-DB: 0044 adds launch_defaults.solver_boss_arena_enabled (BOOL NOT NULL
default true) and drops it on downgrade. Skipped unless RUN_DB_INTEGRATION=1.

Recipe:
  createdb -U macmini5 edu_boss_test
  DATABASE_URL=postgresql+asyncpg://macmini5@127.0.0.1:5432/edu_boss_test \\
    RUN_DB_INTEGRATION=1 uv run --extra dev alembic upgrade head
  DATABASE_URL=... RUN_DB_INTEGRATION=1 uv run --extra dev python -m pytest \\
    tests/integration/test_migration_0044_boss_toggle.py -q
  dropdb -U macmini5 edu_boss_test
"""
from __future__ import annotations
import os
import asyncio
import asyncpg
import pytest
from alembic import command
from alembic.config import Config

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION") != "1", reason="needs real Postgres")
REV = "0044_solver_boss_toggle"
PREV = "0043_solver_role_columns"


def _cfg() -> Config:
    c = Config("alembic.ini")
    c.set_main_option("sqlalchemy.url", os.environ["DATABASE_URL"].replace("+asyncpg", ""))
    return c


async def _col(url):
    conn = await asyncpg.connect(url)
    try:
        return await conn.fetchrow(
            "select is_nullable, column_default from information_schema.columns "
            "where table_name='launch_defaults' and column_name='solver_boss_arena_enabled'")
    finally:
        await conn.close()


def test_0044_adds_and_drops_boss_toggle():
    url = os.environ["DATABASE_URL"].replace("+asyncpg", "")
    cfg = _cfg()
    try:
        command.upgrade(cfg, REV)
        row = asyncio.run(_col(url))
        assert row is not None, "column missing after upgrade"
        assert row["is_nullable"] == "NO"
        assert "true" in (row["column_default"] or "").lower()
        # the seeded singleton got the default
        assert asyncio.run(_singleton_val(url)) is True
        command.downgrade(cfg, PREV)
        assert asyncio.run(_col(url)) is None
    finally:
        command.upgrade(cfg, "head")   # never strand the shared scratch DB


async def _singleton_val(url):
    conn = await asyncpg.connect(url)
    try:
        return await conn.fetchval(
            "select solver_boss_arena_enabled from launch_defaults where id=1")
    finally:
        await conn.close()
