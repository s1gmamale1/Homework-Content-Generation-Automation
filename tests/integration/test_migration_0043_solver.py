import asyncio
import os
import pytest
import asyncpg

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION") != "1",
    reason="requires RUN_DB_INTEGRATION=1 + DATABASE_URL",
)
REV = "0043_solver_role_columns"
PREV = "0042_books_toc_validation"  # re-verify current head at execution


def _cfg():
    from alembic.config import Config
    c = Config("alembic.ini")
    c.set_main_option("sqlalchemy.url",
                      os.environ["DATABASE_URL"].replace("+asyncpg", ""))
    return c


def _asyncpg_url():
    return os.environ["DATABASE_URL"].replace("+asyncpg", "")


async def _cols(conn, table):
    rows = await conn.fetch(
        "select column_name from information_schema.columns where table_name=$1", table)
    return {r["column_name"] for r in rows}


def test_0043_adds_and_drops_solver_columns():
    from alembic import command
    cfg = _cfg()

    async def _check_upgraded():
        conn = await asyncpg.connect(_asyncpg_url())
        try:
            assert {"solver_provider", "solver_model", "solver_transport"} <= await _cols(conn, "launch_defaults")
            assert {"solver_provider", "solver_model", "solver_transport"} <= await _cols(conn, "homework_jobs")
            assert {"solver_provider", "solver_model", "solver_transport"} <= await _cols(conn, "batches")
            assert "solver_status" in await _cols(conn, "phase_outputs")
            row = await conn.fetchrow(
                "select column_default, is_nullable from information_schema.columns "
                "where table_name='homework_jobs' and column_name='solver_transport'")
            assert "inherit" in (row["column_default"] or "") and row["is_nullable"] == "NO"
        finally:
            await conn.close()

    async def _check_downgraded():
        conn = await asyncpg.connect(_asyncpg_url())
        try:
            cols = await _cols(conn, "homework_jobs")
            assert "solver_transport" not in cols
        finally:
            await conn.close()

    try:
        command.upgrade(cfg, REV)
        asyncio.run(_check_upgraded())
        command.downgrade(cfg, PREV)
        asyncio.run(_check_downgraded())
    finally:
        command.upgrade(cfg, "head")   # never strand the shared scratch DB
