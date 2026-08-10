"""Migration 0053: persist the fail-closed solver outcome.

The source-contract tests run in every suite.  The database test is opt-in and
must target a disposable PostgreSQL database because it moves the migration
head backward and forward.
"""

from __future__ import annotations

import asyncio
import importlib.util
import inspect
import os
from pathlib import Path

import asyncpg
import pytest


REV = "0053_solver_mismatch_blocked"
PREV = "0052_job_lease_fencing"


def _migration():
    path = Path(__file__).parents[2] / "alembic" / "versions" / f"{REV}.py"
    spec = importlib.util.spec_from_file_location(REV, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_0053_descends_from_current_head_and_names_blocked_status():
    migration = _migration()

    assert migration.down_revision == PREV
    assert "mismatch_blocked" in migration._STATUS
    assert "mismatch_shipped" in migration._STATUS


def test_0053_downgrade_relabels_blocked_before_shrinking_constraint():
    migration = _migration()
    source = inspect.getsource(migration.downgrade)

    assert source.index("mismatch_blocked") < source.index("drop_constraint")
    assert "mismatch_shipped" in source


def _cfg():
    from alembic.config import Config

    config = Config("alembic.ini")
    config.set_main_option(
        "sqlalchemy.url", os.environ["DATABASE_URL"].replace("+asyncpg", "")
    )
    return config


def _asyncpg_url() -> str:
    return os.environ["DATABASE_URL"].replace("+asyncpg", "")


@pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION") != "1",
    reason="requires RUN_DB_INTEGRATION=1 + a disposable PostgreSQL DATABASE_URL",
)
def test_0053_round_trips_blocked_row_on_real_postgres():
    from alembic import command

    config = _cfg()
    ids: dict[str, object] = {}

    async def _seed_phase() -> None:
        conn = await asyncpg.connect(_asyncpg_url())
        try:
            book_id = await conn.fetchval(
                """
                INSERT INTO books (
                    id, subject, original_filename, content_sha256,
                    file_size_bytes, status, created_at, updated_at
                ) VALUES (
                    gen_random_uuid(), 'matematika', 'solver-0053.pdf',
                    'sha-solver-0053', 10, 'toc_ready', now(), now()
                ) RETURNING id
                """
            )
            toc_id = await conn.fetchval(
                """
                INSERT INTO toc_entries (
                    id, book_id, section_title, order_index,
                    created_at, updated_at
                ) VALUES (
                    gen_random_uuid(), $1, 'Solver migration lesson', 0,
                    now(), now()
                ) RETURNING id
                """,
                book_id,
            )
            job_id = await conn.fetchval(
                """
                INSERT INTO homework_jobs (
                    id, book_id, toc_entry_id, subject, status,
                    created_at, updated_at
                ) VALUES (
                    gen_random_uuid(), $1, $2, 'matematika', 'failed',
                    now(), now()
                ) RETURNING id
                """,
                book_id,
                toc_id,
            )
            phase_id = await conn.fetchval(
                """
                INSERT INTO phase_outputs (
                    id, job_id, phase_name, phase_order, prompt_hash,
                    model_name, status
                ) VALUES (
                    gen_random_uuid(), $1, 'memory-check', 1,
                    'solver-0053', 'test-model', 'failed'
                ) RETURNING id
                """,
                job_id,
            )
            ids.update(book=book_id, toc=toc_id, job=job_id, phase=phase_id)
        finally:
            await conn.close()

    async def _set_and_check_blocked() -> None:
        conn = await asyncpg.connect(_asyncpg_url())
        try:
            await conn.execute(
                "UPDATE phase_outputs SET solver_status='mismatch_blocked' "
                "WHERE id=$1",
                ids["phase"],
            )
            assert (
                await conn.fetchval(
                    "SELECT solver_status FROM phase_outputs WHERE id=$1",
                    ids["phase"],
                )
                == "mismatch_blocked"
            )
        finally:
            await conn.close()

    async def _check_downgrade_and_cleanup() -> None:
        conn = await asyncpg.connect(_asyncpg_url())
        try:
            assert (
                await conn.fetchval(
                    "SELECT solver_status FROM phase_outputs WHERE id=$1",
                    ids["phase"],
                )
                == "mismatch_shipped"
            )
            await conn.execute("DELETE FROM homework_jobs WHERE id=$1", ids["job"])
            await conn.execute("DELETE FROM toc_entries WHERE id=$1", ids["toc"])
            await conn.execute("DELETE FROM books WHERE id=$1", ids["book"])
        finally:
            await conn.close()

    try:
        command.upgrade(config, PREV)
        asyncio.run(_seed_phase())
        command.upgrade(config, REV)
        asyncio.run(_set_and_check_blocked())
        command.downgrade(config, PREV)
        asyncio.run(_check_downgrade_and_cleanup())
    finally:
        command.upgrade(config, "head")
