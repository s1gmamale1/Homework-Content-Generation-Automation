"""Real-DB: verify the 0037_launch_defaults backfill is unconditional across
all job statuses (including failed + cancelled — the retry/resume strand hole).

Recipe:
  createdb -U macmini5 edu_gld_test
  DATABASE_URL=postgresql+asyncpg://macmini5@localhost:5432/edu_gld_test \
    RUN_DB_INTEGRATION=1 uv run --extra dev alembic upgrade head
  DATABASE_URL=... RUN_DB_INTEGRATION=1 uv run --extra dev python -m pytest \
    tests/migrations/test_0037_backfill.py -q
  dropdb edu_gld_test

BITE-PROOF: re-adding a `status IN (...)` filter to the backfill UPDATE causes the
failed/cancelled assertions to fail (those rows are not touched).
"""
from __future__ import annotations

import os
import subprocess
import sys
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION") != "1", reason="needs real Postgres"
)

_DB_URL = os.getenv("DATABASE_URL", "")
# Convert asyncpg URL to standard psycopg URL for alembic subprocess
_SYNC_URL = _DB_URL.replace("postgresql+asyncpg://", "postgresql://")


def _run_alembic(cmd: list[str]) -> None:
    """Run an alembic command (upgrade/downgrade) in a subprocess."""
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


async def test_backfill_stamps_all_statuses():
    """Migration 0037 backfill must stamp ALL job rows regardless of status.

    BITE-PROOF: if a status IN ('pending', 'running') filter is added to the
    backfill UPDATE, the failed/cancelled rows will NOT be updated and the
    assertions below will fail with 'None' instead of 'gemini'.
    """
    import asyncpg

    # Step 1: downgrade to 0036 so we can seed pre-backfill rows.
    _run_alembic(["downgrade", "0036_session_limit_strategy"])

    # Step 2: seed homework_jobs rows at each status with NULL judge/extract.
    # Use raw SQL via asyncpg (bypasses ORM defaults) with gen_random_uuid().
    conn = await asyncpg.connect(_SYNC_URL)
    book_id = None
    toc_id = None
    job_ids = []
    try:
        # books and toc_entries have UUID PKs + created_at/updated_at timestamps
        book_id = await conn.fetchval(
            """
            INSERT INTO books (id, subject, original_filename, content_sha256,
                               file_size_bytes, status, created_at, updated_at)
            VALUES (gen_random_uuid(), 'math-algebra', 'test.pdf', $1,
                    1, 'toc_ready', now(), now())
            RETURNING id
            """,
            "a" * 64,
        )
        toc_id = await conn.fetchval(
            """
            INSERT INTO toc_entries (id, book_id, section_title, order_index,
                                     created_at, updated_at)
            VALUES (gen_random_uuid(), $1, 'L0', 0, now(), now())
            RETURNING id
            """,
            book_id,
        )
        statuses = ["pending", "running", "failed", "cancelled", "done"]
        for status in statuses:
            jid = await conn.fetchval(
                """
                INSERT INTO homework_jobs
                    (id, book_id, toc_entry_id, subject, status, provider,
                     transport, judge_provider, judge_model,
                     extract_provider, extract_model,
                     created_at, updated_at)
                VALUES (gen_random_uuid(), $1, $2, 'math-algebra', $3, 'gemini',
                        'cli', NULL, NULL, NULL, NULL, now(), now())
                RETURNING id
                """,
                book_id,
                toc_id,
                status,
            )
            job_ids.append((status, jid))
    finally:
        await conn.close()

    # Step 3: upgrade to 0037 — this runs the backfill.
    _run_alembic(["upgrade", "0037_launch_defaults"])

    # Step 4: verify ALL rows were backfilled, including failed + cancelled.
    conn = await asyncpg.connect(_SYNC_URL)
    try:
        for status, jid in job_ids:
            row = await conn.fetchrow(
                """
                SELECT judge_provider, judge_model, extract_provider, extract_model
                  FROM homework_jobs WHERE id = $1
                """,
                jid,
            )
            assert row["judge_provider"] == "gemini", (
                f"status={status}: expected judge_provider='gemini', got {row['judge_provider']!r}"
            )
            assert row["judge_model"] == "gemini-2.5-flash", (
                f"status={status}: expected judge_model='gemini-2.5-flash', got {row['judge_model']!r}"
            )
            assert row["extract_provider"] == "gemini", (
                f"status={status}: expected extract_provider='gemini', got {row['extract_provider']!r}"
            )
            assert row["extract_model"] == "gemini-2.5-flash", (
                f"status={status}: expected extract_model='gemini-2.5-flash', got {row['extract_model']!r}"
            )
    finally:
        # Cleanup seeded rows
        for _, jid in job_ids:
            await conn.execute("DELETE FROM homework_jobs WHERE id = $1", jid)
        if toc_id:
            await conn.execute("DELETE FROM toc_entries WHERE id = $1", toc_id)
        if book_id:
            await conn.execute("DELETE FROM books WHERE id = $1", book_id)
        await conn.close()
        # Restore the shared DB to head. This test pins the upgrade to
        # 0037_launch_defaults; once a later migration exists (e.g. 0038
        # output_language) that leaves the shared scratch DB one revision below
        # head, breaking every subsequent DB test with UndefinedColumnError.
        _run_alembic(["upgrade", "head"])
