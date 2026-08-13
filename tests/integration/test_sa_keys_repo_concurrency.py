"""Repo-layer tests for the Task 6 concurrency-override write path
(``sa_keys.set_max_concurrent_calls``) and the grouped in-flight query
(``sa_keys.slots_in_use_by_credential``).

Real-Postgres only (RUN_DB_INTEGRATION=1) — the project-wide atomic UPDATE
is raw SQL against a real table, and slots_in_use reads the real
credential_slots table seeded by Task 1's migration.
"""
import os
import uuid

import pytest
from sqlalchemy import text

from app.db import SessionLocal
from app.repositories import sa_keys as repo

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION") != "1", reason="needs a real Postgres"
)


@pytest.mark.asyncio
async def test_set_max_concurrent_calls_updates_every_row_sharing_project():
    """codex #2: project_id is non-unique on sa_keys — two rows can share
    one GCP project. The write must update ALL of them in one statement,
    never just the row named by key_id, or the two rows could disagree."""
    async with SessionLocal() as s:
        async with s.begin():
            a = await repo.create_or_get(
                s, original_filename="a.json", project_id="proj-shared",
                client_email="e1", sha256="SHA-CONC-A", byte_size=9,
            )
            b = await repo.create_or_get(
                s, original_filename="b.json", project_id="proj-shared",
                client_email="e2", sha256="SHA-CONC-B", byte_size=9,
            )
            c = await repo.create_or_get(
                s, original_filename="c.json", project_id="proj-other",
                client_email="e3", sha256="SHA-CONC-C", byte_size=9,
            )
        a_id, b_id, c_id = a.id, b.id, c.id

    # Fresh session for the update — the raw-SQL UPDATE bypasses the ORM, so
    # reusing the session that loaded `a`/`b`/`c` would read stale identity-
    # mapped objects on the next `get()` rather than what's really in the DB.
    async with SessionLocal() as s:
        async with s.begin():
            rowcount = await repo.set_max_concurrent_calls(s, a_id, 5)
        assert rowcount == 2  # a and b, not c

    async with SessionLocal() as s:
        async with s.begin():
            a2 = await repo.get(s, a_id)
            b2 = await repo.get(s, b_id)
            c2 = await repo.get(s, c_id)
        assert a2.max_concurrent_calls == 5
        assert b2.max_concurrent_calls == 5
        assert c2.max_concurrent_calls is None  # untouched — different project


@pytest.mark.asyncio
async def test_set_max_concurrent_calls_missing_key_returns_zero():
    async with SessionLocal() as s:
        async with s.begin():
            rowcount = await repo.set_max_concurrent_calls(s, uuid.uuid4(), 3)
        assert rowcount == 0


@pytest.mark.asyncio
async def test_set_max_concurrent_calls_null_clears_override():
    async with SessionLocal() as s:
        async with s.begin():
            k = await repo.create_or_get(
                s, original_filename="d.json", project_id="proj-clear",
                client_email="e4", sha256="SHA-CONC-D", byte_size=9,
            )
        k_id = k.id

    async with SessionLocal() as s:
        async with s.begin():
            await repo.set_max_concurrent_calls(s, k_id, 4)
        async with s.begin():
            rowcount = await repo.set_max_concurrent_calls(s, k_id, None)
        assert rowcount == 1

    async with SessionLocal() as s:
        async with s.begin():
            row = await repo.get(s, k_id)
        assert row.max_concurrent_calls is None


@pytest.mark.asyncio
async def test_slots_in_use_by_credential_groups_fresh_rows_only():
    # Unique per test run — this hits a persistent scratch DB (not a
    # per-test transaction rollback), so a fixed credential string would
    # accumulate rows across repeated local runs and inflate the count.
    suffix = uuid.uuid4().hex[:8]
    cred_a = f"gemini:proj-slots-a-{suffix}"
    cred_b = f"gemini:proj-slots-b-{suffix}"
    async with SessionLocal() as s:
        async with s.begin():
            # two fresh slots for cred_a, one fresh for cred_b, one STALE
            # for cred_a (acquired far in the past) that must NOT be counted.
            # slot_index is part of UNIQUE(credential, slot_index) since
            # migration 0060 — distinct per credential, reused across them.
            await s.execute(
                text(
                    "INSERT INTO credential_slots "
                    "(credential, slot_index, pc_id, acquired_at) "
                    "VALUES (:cred, 0, 'pc-1', now())"
                ),
                {"cred": cred_a},
            )
            await s.execute(
                text(
                    "INSERT INTO credential_slots "
                    "(credential, slot_index, pc_id, acquired_at) "
                    "VALUES (:cred, 1, 'pc-2', now())"
                ),
                {"cred": cred_a},
            )
            await s.execute(
                text(
                    "INSERT INTO credential_slots "
                    "(credential, slot_index, pc_id, acquired_at) "
                    "VALUES (:cred, 0, 'pc-3', now())"
                ),
                {"cred": cred_b},
            )
            await s.execute(
                text(
                    "INSERT INTO credential_slots "
                    "(credential, slot_index, pc_id, acquired_at) "
                    "VALUES (:cred, 2, 'pc-stale', now() - interval '999999 seconds')"
                ),
                {"cred": cred_a},
            )

        async with s.begin():
            counts = await repo.slots_in_use_by_credential(s, ttl_seconds=1200)

    assert counts.get(cred_a) == 2  # stale row excluded
    assert counts.get(cred_b) == 1
