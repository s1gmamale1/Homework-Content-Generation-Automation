"""Real-DB: migration 0047 adds credential_slots table + sa_keys.max_concurrent_calls
(fleet-wide per-credential api concurrency limiter, BE-16 task 1).

credential_slots has NO SQLAlchemy model — the Task 3 limiter accesses it via raw
SQL (text()) only, so its `id` must be generated server-side (gen_random_uuid()),
unlike the app-level uuid4() defaults used by sa_keys/sa_key_assignments (0041).

Recipe:
  createdb -h 127.0.0.1 -U macmini5 -O edu edu_scratch_credlim
  DATABASE_URL=postgresql+asyncpg://edu:edu@127.0.0.1:5432/edu_scratch_credlim \
    uv run alembic upgrade head
  DATABASE_URL=... RUN_DB_INTEGRATION=1 uv run python -m pytest \
    tests/migrations/test_0047_credential_slots.py -q
"""
from __future__ import annotations

import os
import subprocess
import sys
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION") != "1", reason="needs a real Postgres"
)

_DB_URL = os.getenv("DATABASE_URL", "")


def _run_alembic(cmd: list[str]) -> None:
    env = {**os.environ, "DATABASE_URL": _DB_URL}
    result = subprocess.run(
        [sys.executable, "-m", "alembic"] + cmd,
        capture_output=True,
        text=True,
        env=env,
        cwd=os.path.join(os.path.dirname(__file__), "..", ".."),
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"alembic {' '.join(cmd)} failed:\n{result.stdout}\n{result.stderr}"
        )


async def _has_table(engine, name: str) -> bool:
    async with engine.begin() as conn:
        got = await conn.scalar(text("SELECT to_regclass(:t)"), {"t": name})
    return got == name


async def _has_column(engine, table: str, column: str) -> bool:
    async with engine.begin() as conn:
        got = await conn.scalar(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name=:t AND column_name=:c"
            ),
            {"t": table, "c": column},
        )
    return got == column


@pytest.mark.asyncio
async def test_0047_credential_slots_table_and_sa_keys_column():
    engine = create_async_engine(_DB_URL)
    try:
        # --- RED baseline: at 0046, neither the table nor the column exist ---
        _run_alembic(["downgrade", "0046_worker_version_floor"])
        assert not await _has_table(engine, "credential_slots")
        assert not await _has_column(engine, "sa_keys", "max_concurrent_calls")

        # --- GREEN: upgrade to head (0047) creates both ---
        _run_alembic(["upgrade", "head"])
        assert await _has_table(engine, "credential_slots")
        assert await _has_column(engine, "sa_keys", "max_concurrent_calls")

        # ix_credential_slots_credential index exists
        async with engine.begin() as conn:
            idx = await conn.scalar(
                text("SELECT indexname FROM pg_indexes WHERE indexname=:i"),
                {"i": "ix_credential_slots_credential"},
            )
        assert idx == "ix_credential_slots_credential"

        # id is server-generated (gen_random_uuid()) — raw insert without id works
        async with engine.begin() as conn:
            row_id = await conn.scalar(
                text(
                    "INSERT INTO credential_slots (credential, pc_id) "
                    "VALUES ('gemini:sa-1', 'host-a:123') RETURNING id"
                )
            )
        assert row_id is not None

        # acquired_at defaults to now()
        async with engine.begin() as conn:
            acquired_at = await conn.scalar(
                text("SELECT acquired_at FROM credential_slots WHERE credential='gemini:sa-1'")
            )
        assert acquired_at is not None

        # sa_keys.max_concurrent_calls is nullable, no server default
        async with engine.begin() as conn:
            result = await conn.execute(
                text(
                    "SELECT is_nullable, column_default FROM information_schema.columns "
                    "WHERE table_name='sa_keys' AND column_name='max_concurrent_calls'"
                )
            )
            row = result.first()
        assert row.is_nullable == "YES"
        assert row.column_default is None

        # --- downgrade removes both ---
        _run_alembic(["downgrade", "0046_worker_version_floor"])
        assert not await _has_table(engine, "credential_slots")
        assert not await _has_column(engine, "sa_keys", "max_concurrent_calls")
    finally:
        await engine.dispose()
        # Restore the shared scratch DB to head for any subsequent test in this run.
        _run_alembic(["upgrade", "head"])


@pytest.mark.asyncio
async def test_0047_max_concurrent_calls_check_constraint_bites():
    """Task 4 amendment: `ck_sa_keys_max_concurrent_calls_min` rejects 0 (and
    by the same `>= 1` clause, any negative value), while NULL (no override)
    and a real positive value both insert cleanly."""
    engine = create_async_engine(_DB_URL)
    tag = uuid.uuid4().hex[:12]

    def _insert_sql(mcc_literal: str) -> str:
        return (
            "INSERT INTO sa_keys "
            "(id, original_filename, project_id, client_email, sha256, byte_size, "
            "created_at, max_concurrent_calls) "
            "VALUES (gen_random_uuid(), 'k.json', 'proj-check', "
            "'sa@x.iam.gserviceaccount.com', :sha, 100, now(), " + mcc_literal + ")"
        )

    try:
        _run_alembic(["upgrade", "head"])

        # NULL override: no cap set — must insert cleanly.
        async with engine.begin() as conn:
            await conn.execute(
                text(_insert_sql("NULL")), {"sha": f"check-null-{tag}"}
            )

        # A real positive override — must insert cleanly.
        async with engine.begin() as conn:
            await conn.execute(
                text(_insert_sql(":mcc")), {"sha": f"check-five-{tag}", "mcc": 5}
            )

        # Zero is never a valid override (acquire() treats <=0 as "no cap",
        # the opposite of an admin's intent) — the CHECK must bite.
        with pytest.raises(IntegrityError):
            async with engine.begin() as conn:
                await conn.execute(
                    text(_insert_sql(":mcc")), {"sha": f"check-zero-{tag}", "mcc": 0}
                )
    finally:
        async with engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM sa_keys WHERE sha256 LIKE :pat"),
                {"pat": f"check-%-{tag}"},
            )
        await engine.dispose()
