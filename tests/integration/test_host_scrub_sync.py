"""Real-DB proof of the host-scoped SA-key scrub-sync primitives (round 3):

- jobs_repo.count_active_for_host — the HOST-WIDE busy signal the SA-key
  scrub path uses so an idle process does not clear shared credential files
  while a SIBLING process on the same host is mid-job. Renamed from
  count_running_for_host (round 2) and extended to also count `cancelling`
  jobs — a job the API told to stop but that hasn't finished unwinding yet
  is still "busy" from the credential-file point of view (worklog
  0147/0148 follow-up).
- sa_keys_repo.scrub_pending_for_host — EXISTS check for a pending scrub.
- app.repositories.workers.lock_host_shared / lock_host_exclusive — the
  host-scoped advisory lock pair (BE-02 book-lock pattern, host key
  namespace) that a later task wires into the claim path and the
  scrub-clear, proven here via a deterministic two-connection try-lock
  race.

`claimed_by` is `hostname:pid`, so two distinct pids on one hostname are two
processes on one host. The count must include a sibling pid's running OR
cancelling job (the whole point) and must exclude other hosts / prefix hosts
/ terminal statuses.

Run:
  RUN_DB_INTEGRATION=1 \\
    DATABASE_URL=postgresql+asyncpg://edu:edu@127.0.0.1:5432/edu_scratch_scrub \\
    uv run python -m pytest tests/integration/test_host_scrub_sync.py -q
"""
from __future__ import annotations

import os
from uuid import uuid4

import pytest
from sqlalchemy import delete, text

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)


async def _seed_section(s):
    from app.models.book import Book
    from app.models.toc_entry import TOCEntry

    book = Book(
        subject="math-algebra",
        original_filename="scrub-hostidle.pdf",
        content_sha256="c" * 64,
        file_size_bytes=1,
        status="toc_ready",
    )
    s.add(book)
    await s.flush()
    toc = TOCEntry(book_id=book.id, section_title="L1", order_index=0)
    s.add(toc)
    await s.flush()
    return book, toc


async def _make_job(s, book, toc, *, status: str, claimed_by: str | None):
    from app.repositories import jobs as jobs_repo

    job = await jobs_repo.create(
        s, book_id=book.id, toc_entry_id=toc.id, subject="math-algebra",
        output_language="uz",
    )
    await s.execute(
        text("UPDATE homework_jobs SET status=:st, claimed_by=:cb WHERE id=:id"),
        {"st": status, "cb": claimed_by, "id": job.id},
    )
    return job


@pytest.mark.asyncio
async def test_count_active_for_host_counts_sibling_pid_excludes_others():
    from app.db import SessionLocal
    from app.models import HomeworkJob
    from app.repositories import jobs as jobs_repo

    async with SessionLocal() as s:
        book, toc = await _seed_section(s)
        # A SIBLING process on this host (same hostname, different pid) running a job.
        await _make_job(s, book, toc, status="running", claimed_by="hostA:1111")
        # Same host, a second running sibling — should add to the count.
        await _make_job(s, book, toc, status="running", claimed_by="hostA:2222")
        # Same host but the job is DONE — must not count.
        await _make_job(s, book, toc, status="done", claimed_by="hostA:3333")
        # A DIFFERENT host running — must not count for hostA.
        await _make_job(s, book, toc, status="running", claimed_by="hostB:4444")
        # A host whose NAME is a prefix of hostA — the ':' boundary must exclude it.
        await _make_job(s, book, toc, status="running", claimed_by="host:5555")
        await s.commit()

        try:
            n_hostA = await jobs_repo.count_active_for_host(s, "hostA")
            n_hostB = await jobs_repo.count_active_for_host(s, "hostB")
            n_prefix = await jobs_repo.count_active_for_host(s, "host")
            n_absent = await jobs_repo.count_active_for_host(s, "nope")

            assert n_hostA == 2, "two running sibling pids on hostA must both count"
            assert n_hostB == 1, "hostB's single running job counts for hostB only"
            assert n_prefix == 1, "'host' must match only host:5555, never hostA:*"
            assert n_absent == 0, "no jobs for an unknown host"
        finally:
            await s.execute(delete(HomeworkJob).where(HomeworkJob.book_id == book.id))
            from app.models.toc_entry import TOCEntry
            from app.models.book import Book
            await s.execute(delete(TOCEntry).where(TOCEntry.book_id == book.id))
            await s.execute(delete(Book).where(Book.id == book.id))
            await s.commit()


@pytest.mark.asyncio
async def test_count_active_for_host_counts_cancelling_sibling():
    """A job the API told to stop (`cancelling`) but that hasn't unwound yet
    is still ACTIVE from the shared-credential-file point of view — the
    worker task is still running and could still be mid-spawn. Round 2 only
    counted `running`; round 3 must also count `cancelling`."""
    from app.db import SessionLocal
    from app.models import HomeworkJob
    from app.repositories import jobs as jobs_repo

    async with SessionLocal() as s:
        book, toc = await _seed_section(s)
        await _make_job(s, book, toc, status="cancelling", claimed_by="hostC:9001")
        # A cancelling job on a different host must not count for hostX.
        await _make_job(s, book, toc, status="cancelling", claimed_by="hostD:9002")
        await s.commit()

        try:
            n_hostC = await jobs_repo.count_active_for_host(s, "hostC")
            n_other = await jobs_repo.count_active_for_host(s, "hostX")
            assert n_hostC == 1, "a cancelling sibling job must count as active"
            assert n_other == 0
        finally:
            await s.execute(delete(HomeworkJob).where(HomeworkJob.book_id == book.id))
            from app.models.toc_entry import TOCEntry
            from app.models.book import Book
            await s.execute(delete(TOCEntry).where(TOCEntry.book_id == book.id))
            await s.execute(delete(Book).where(Book.id == book.id))
            await s.commit()


# ─── scrub_pending_for_host ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scrub_pending_for_host_true_when_scrub_requested_at_set():
    from app.db import SessionLocal
    from app.models.sa_key import SAKeyAssignment
    from app.repositories import sa_keys as sa_keys_repo

    hostname = f"scrub-pending-{uuid4().hex[:8]}"
    async with SessionLocal() as s:
        try:
            await sa_keys_repo.scrub(s, hostname)
            await s.commit()

            assert await sa_keys_repo.scrub_pending_for_host(s, hostname) is True
        finally:
            await s.execute(delete(SAKeyAssignment).where(SAKeyAssignment.hostname == hostname))
            await s.commit()


@pytest.mark.asyncio
async def test_scrub_pending_for_host_false_when_keyed_assignment():
    from app.db import SessionLocal
    from app.models.sa_key import SAKey, SAKeyAssignment
    from app.repositories import sa_keys as sa_keys_repo

    hostname = f"scrub-keyed-{uuid4().hex[:8]}"
    async with SessionLocal() as s:
        key = await sa_keys_repo.create_or_get(
            s,
            original_filename="k.json",
            project_id="proj-1",
            client_email="svc@proj-1.iam.gserviceaccount.com",
            sha256=uuid4().hex + uuid4().hex,
            byte_size=1,
        )
        try:
            await sa_keys_repo.assign(s, hostname, key.id)
            await s.commit()

            assert await sa_keys_repo.scrub_pending_for_host(s, hostname) is False
        finally:
            await s.execute(delete(SAKeyAssignment).where(SAKeyAssignment.hostname == hostname))
            # …and the sa_keys row itself. Dropping only the assignment left a key
            # row behind with NO matching file in the vault, and
            # `sa_key_vault.verify_uuid_inventory` compares vault files against
            # sa_keys rows and fails closed on a mismatch — so this leftover broke
            # `test_sa_key_delete_atomicity.py`, a LATER file, with
            # `SAKeyVaultError: SA-key vault operation failed closed`.
            # Assignment first, then the key: the assignment references it.
            await s.execute(delete(SAKey).where(SAKey.id == key.id))
            await s.commit()


@pytest.mark.asyncio
async def test_scrub_pending_for_host_false_when_no_row():
    from app.db import SessionLocal
    from app.repositories import sa_keys as sa_keys_repo

    hostname = f"scrub-absent-{uuid4().hex[:8]}"
    async with SessionLocal() as s:
        assert await sa_keys_repo.scrub_pending_for_host(s, hostname) is False


# ─── two-connection lock race (deterministic try-lock oracle, BE-02 style) ──


@pytest.mark.asyncio
async def test_shared_lock_blocks_concurrent_exclusive_try_until_commit():
    """conn A takes lock_host_shared (uncommitted) → conn B's exclusive
    try-lock on the same host key must return False (blocked); after A
    commits (releasing the tx-scoped lock), B's try must return True."""
    from app.db import SessionLocal
    from app.repositories import workers as workers_repo

    hostname = f"race-shared-{uuid4().hex[:8]}"

    async with SessionLocal() as sa, SessionLocal() as sb:
        await sa.begin()
        await workers_repo.lock_host_shared(sa, hostname)

        key = (
            await sb.execute(text("SELECT hashtext(:key)"), {"key": f"host:{hostname}"})
        ).scalar_one()
        got_before = (
            await sb.execute(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": key})
        ).scalar_one()
        assert got_before is False, (
            "an uncommitted SHARED holder must block a concurrent EXCLUSIVE try-lock"
        )
        await sb.rollback()  # release B's failed-try transaction

        await sa.commit()  # releases A's tx-scoped SHARED lock

        got_after = (
            await sb.execute(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": key})
        ).scalar_one()
        assert got_after is True, (
            "after the SHARED holder commits, an EXCLUSIVE try-lock must succeed"
        )
        await sb.rollback()


@pytest.mark.asyncio
async def test_exclusive_lock_blocks_concurrent_shared_try_until_commit():
    """Mirror: conn A holds lock_host_exclusive (uncommitted) → conn B's
    SHARED try-lock on the same host key must return False (blocked); after
    A commits, B's SHARED try must succeed."""
    from app.db import SessionLocal
    from app.repositories import workers as workers_repo

    hostname = f"race-exclusive-{uuid4().hex[:8]}"

    async with SessionLocal() as sa, SessionLocal() as sb:
        await sa.begin()
        await workers_repo.lock_host_exclusive(sa, hostname)

        key = (
            await sb.execute(text("SELECT hashtext(:key)"), {"key": f"host:{hostname}"})
        ).scalar_one()
        got_before = (
            await sb.execute(text("SELECT pg_try_advisory_xact_lock_shared(:k)"), {"k": key})
        ).scalar_one()
        assert got_before is False, (
            "an uncommitted EXCLUSIVE holder must block a concurrent SHARED try-lock"
        )
        await sb.rollback()

        await sa.commit()

        got_after = (
            await sb.execute(text("SELECT pg_try_advisory_xact_lock_shared(:k)"), {"k": key})
        ).scalar_one()
        assert got_after is True, (
            "after the EXCLUSIVE holder commits, a SHARED try-lock must succeed"
        )
        await sb.rollback()
