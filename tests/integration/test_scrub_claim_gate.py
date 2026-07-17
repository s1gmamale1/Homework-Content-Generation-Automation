"""Real-DB proof of the claim-side scrub gate (task 3, SA-key dead-host
lane, gate round 3 finding 1): `Worker._claim_one` takes the SHARED host lock
(`workers_repo.lock_host_shared`, task 1) and re-reads the tombstone
(`sa_keys_repo.scrub_pending_for_host`, task 1) at the top of its claim
transaction — if a scrub is pending for this host, it refuses to claim (the
host parks and drains) instead of racing an in-flight credential revoke.

Task 2 made the three assignment-state write routes take the EXCLUSIVE host
lock before writing the tombstone. This file proves the OTHER side of that
same lock pair actually gates a real `_claim_one` call, plus the
winner-conditional two-connection ordering (deterministic via ordered
commits — no thread/async race, no any-of assertion):

  - scrub-writes-first: the tombstone is committed BEFORE `_claim_one` runs
    -> refused, job stays `pending`.
  - claim-first: `_claim_one` claims and commits FIRST; the tombstone lands
    only after -> the claim already won; `count_active_for_host` == 1 (the
    scrub will drain it later — task 4's job).

Run:
  RUN_DB_INTEGRATION=1 \\
    DATABASE_URL=postgresql+asyncpg://edu:edu@127.0.0.1:5432/edu_scratch_scrub \\
    uv run python -m pytest tests/integration/test_scrub_claim_gate.py -q
"""
from __future__ import annotations

import os
from uuid import uuid4

import pytest
from sqlalchemy import delete

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)


@pytest.fixture(autouse=True)
def _restore_capabilities():
    """`_make_worker` reassigns the module-global `worker.CAPABILITIES`; snapshot
    and restore it so this file's cli-only override never leaks into a later test
    in the same RUN_DB_INTEGRATION session."""
    from app.services import worker as worker_mod
    saved = worker_mod.CAPABILITIES
    yield
    worker_mod.CAPABILITIES = saved


async def _seed_section(s, name: str):
    from app.models.book import Book
    from app.models.toc_entry import TOCEntry

    book = Book(
        subject="math-algebra",
        original_filename=name,
        content_sha256=uuid4().hex + uuid4().hex,
        file_size_bytes=1,
        status="toc_ready",
    )
    s.add(book)
    await s.flush()
    toc = TOCEntry(book_id=book.id, section_title="L1", order_index=0)
    s.add(toc)
    await s.flush()
    return book, toc


async def _seed_pending_cli_job(s, book, toc):
    """A claimable job: transport='cli' passes content_ok with zero api caps."""
    from app.repositories import jobs as jobs_repo

    return await jobs_repo.create(
        s, book_id=book.id, toc_entry_id=toc.id, subject="math-algebra",
        output_language="uz", transport="cli",
    )


async def _cleanup(book_id, hostname):
    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.homework_job import HomeworkJob
    from app.models.sa_key import SAKeyAssignment
    from app.models.toc_entry import TOCEntry

    async with SessionLocal() as s:
        await s.execute(delete(HomeworkJob).where(HomeworkJob.book_id == book_id))
        await s.execute(delete(TOCEntry).where(TOCEntry.book_id == book_id))
        await s.execute(delete(Book).where(Book.id == book_id))
        await s.execute(delete(SAKeyAssignment).where(SAKeyAssignment.hostname == hostname))
        await s.commit()


def _make_worker(hostname: str):
    """A cli-capable Worker pinned to `hostname` (test double for the fleet
    worker actually running on that host).

    `self.id` (built from the REAL `socket.gethostname()` at __init__ time,
    see `worker._worker_id`) is what `claim_next_job` stamps into
    `claimed_by`, and `count_active_for_host` derives its host match from
    that `claimed_by` prefix — NOT from `self.hostname` (which only the
    scrub-gate lock/tombstone read consult). Both must carry the SAME test
    hostname for the winner-conditional race tests' `count_active_for_host`
    assertion to mean anything, so `w.id` is repointed too, not just
    `w.hostname`.
    """
    from app.services import worker as worker_mod

    w = worker_mod.Worker(concurrency=1)
    w.hostname = hostname
    w.id = f"{hostname}:{uuid4().hex[:6]}"
    # Force cli-only capabilities regardless of this test process's real env
    # (the seeded job is transport='cli' so this is enough either way, but
    # pinned per the brief for determinism).
    worker_mod.CAPABILITIES = worker_mod._compute_capabilities({})
    return w


# ─── basic gate: tombstone blocks, unassign un-blocks ──────────────────────


@pytest.mark.asyncio
async def test_claim_one_refuses_while_scrub_pending_then_claims_after_unassign():
    from app.db import SessionLocal
    from app.repositories import jobs as jobs_repo
    from app.repositories import sa_keys as sa_keys_repo

    hostname = f"claimgate-basic-{uuid4().hex[:8]}"
    async with SessionLocal() as s:
        book, toc = await _seed_section(s, "claimgate-basic.pdf")
        job = await _seed_pending_cli_job(s, book, toc)
        await sa_keys_repo.scrub(s, hostname)
        await s.commit()
        book_id, job_id = book.id, job.id

    try:
        w = _make_worker(hostname)

        # Scrub tombstone present -> refused, job untouched.
        claimed = await w._claim_one()
        assert claimed is None, "must refuse to claim while a scrub is pending for this host"

        async with SessionLocal() as s:
            assert await jobs_repo.get_status(s, job_id) == "pending", (
                "a refused claim must leave the job row untouched (still pending)"
            )

        # Clear the tombstone (real unassign write) -> claim now proceeds.
        async with SessionLocal() as s:
            await sa_keys_repo.unassign(s, hostname)
            await s.commit()

        claimed2 = await w._claim_one()
        assert claimed2 == job_id, "with no scrub pending, the seeded job must be claimed"

        async with SessionLocal() as s:
            assert await jobs_repo.get_status(s, job_id) == "running", (
                "a successful claim must flip the job to running"
            )
    finally:
        await _cleanup(book_id, hostname)


# ─── winner-conditional ordering: scrub-writes-first ───────────────────────


@pytest.mark.asyncio
async def test_scrub_write_before_claim_wins_claim_refused():
    """conn B commits the REAL tombstone write FIRST; THEN conn A's
    `_claim_one` runs — must be refused, job stays pending. Deterministic
    (strictly ordered commits), not a thread/async race."""
    from app.db import SessionLocal
    from app.repositories import jobs as jobs_repo
    from app.repositories import sa_keys as sa_keys_repo

    hostname = f"claimgate-scrubwins-{uuid4().hex[:8]}"
    async with SessionLocal() as s:
        book, toc = await _seed_section(s, "claimgate-scrubwins.pdf")
        job = await _seed_pending_cli_job(s, book, toc)
        await s.commit()
        book_id, job_id = book.id, job.id

    try:
        # conn B: the real scrub write, committed BEFORE any claim attempt.
        async with SessionLocal() as sb:
            await sa_keys_repo.scrub(sb, hostname)
            await sb.commit()

        # conn A: _claim_one opens its own connection/session internally.
        w = _make_worker(hostname)
        claimed = await w._claim_one()
        assert claimed is None, "a tombstone committed before the claim must refuse it"

        async with SessionLocal() as s:
            assert await jobs_repo.get_status(s, job_id) == "pending", (
                "scrub-wins: the job must NOT flip to running"
            )
    finally:
        await _cleanup(book_id, hostname)


# ─── winner-conditional ordering: claim-first ──────────────────────────────


@pytest.mark.asyncio
async def test_claim_before_scrub_write_wins_claim_succeeds():
    """conn A's `_claim_one` claims and commits FIRST (no tombstone yet);
    THEN conn B commits the real scrub write. The claim already won —
    `count_active_for_host` must reflect the one active (running) job; the
    scrub landing after is task 4's problem (drain), not this gate's."""
    from app.db import SessionLocal
    from app.repositories import jobs as jobs_repo
    from app.repositories import sa_keys as sa_keys_repo

    hostname = f"claimgate-claimwins-{uuid4().hex[:8]}"
    async with SessionLocal() as s:
        book, toc = await _seed_section(s, "claimgate-claimwins.pdf")
        job = await _seed_pending_cli_job(s, book, toc)
        await s.commit()
        book_id, job_id = book.id, job.id

    try:
        # conn A: no tombstone yet -> claims and commits.
        w = _make_worker(hostname)
        claimed = await w._claim_one()
        assert claimed == job_id, "claim-first: no tombstone present, the claim must succeed"

        async with SessionLocal() as s:
            assert await jobs_repo.get_status(s, job_id) == "running"

        # conn B: the scrub write lands AFTER the claim already committed.
        async with SessionLocal() as sb:
            await sa_keys_repo.scrub(sb, hostname)
            await sb.commit()

        async with SessionLocal() as s:
            n_active = await jobs_repo.count_active_for_host(s, hostname)
        assert n_active == 1, (
            "claim-first: the already-running job must count as active for "
            "this host (the scrub will drain it later, not undo the claim)"
        )
    finally:
        await _cleanup(book_id, hostname)
