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
            # CHECK constraint DEFINITIONS bite on the exact value sets (a typo'd
            # value list would pass a mere existence check — assert the def text).
            for name, needles in (
                ("ck_homework_jobs_solver_transport", ("cli", "api", "inherit")),
                ("ck_batches_solver_transport", ("cli", "api", "inherit")),
                ("ck_phase_outputs_solver_status",
                 ("ok", "mismatch_regen", "mismatch_shipped",
                  "mismatch_regen_failed", "unavailable", "refused")),
            ):
                cdef = await conn.fetchval(
                    "select pg_get_constraintdef(oid) from pg_constraint where conname=$1", name)
                assert cdef is not None, f"{name} missing"
                for needle in needles:
                    assert needle in cdef, f"{name} def missing {needle!r}: {cdef}"
            # R2 seed: when a launch_defaults row exists (a bare scratch DB may have
            # none), its solver_model must be the seeded default, never something else.
            seeded = await conn.fetchval(
                "select solver_model from launch_defaults where solver_model is not null limit 1")
            assert seeded in (None, "gemini-3.1-pro-preview"), seeded
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
