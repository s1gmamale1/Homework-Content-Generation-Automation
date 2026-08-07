"""Real-DB proof that main._reconcile_on_startup is scoped to reclaimed jobs
(fenced job leases, Task 8) — it no longer globally force-fails every
pending/running phase_outputs row.

Scenario: a PEER-OWNED running job — fresh `claimed_at` (not stale), a
`workers` registry row for the job's `claimed_by` with a recent heartbeat
(so a live peer might still own it), and a `running` phase row.

RED-proof: against the old global sweep (`for p in
phase_repo.list_running_for_sweep(session): phase_repo.set_status(p.id,
"failed", ...)`) the phase row would have been force-failed to
`phase_repo.ORPHANED_RESTART_MESSAGE` regardless of the job's live-peer
status. The scoped reconcile must leave both the job and its phase row
untouched.

Run (against the scratch DB only — NEVER the .env/production DATABASE_URL):
  RUN_DB_INTEGRATION=1 \\
    DATABASE_URL=postgresql+asyncpg://macmini5@127.0.0.1:5432/edu_scratch_leases \\
    uv run python -m pytest tests/api/test_startup_reconcile_scoped.py -v
"""
from __future__ import annotations

import os

import pytest
from sqlalchemy import delete, text

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)

PEER_PC = "test-reconcile-peer:99999"


async def _seed_section(s):
    from app.models.book import Book
    from app.models.toc_entry import TOCEntry

    book = Book(
        subject="math-algebra",
        original_filename="startup-reconcile.pdf",
        content_sha256="b" * 64,
        file_size_bytes=1,
        status="toc_ready",
    )
    s.add(book)
    await s.flush()
    toc = TOCEntry(book_id=book.id, section_title="L1", order_index=0)
    s.add(toc)
    await s.flush()
    return book, toc


async def _make_peer_owned_running_job(s, book, toc):
    """A running HomeworkJob with a FRESH claimed_at, claimed_by the peer
    pc_id that has a live heartbeat row in the workers registry."""
    from app.repositories import jobs as jobs_repo

    job = await jobs_repo.create(
        s, book_id=book.id, toc_entry_id=toc.id, subject="math-algebra",
        output_language="uz",
    )
    await s.execute(
        text(
            "UPDATE homework_jobs SET status='running', claimed_at=now(), "
            "claimed_by=:pc WHERE id=:id"
        ),
        {"id": job.id, "pc": PEER_PC},
    )
    await s.flush()
    return job


async def _cleanup(book_ids: list):
    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.homework_job import HomeworkJob
    from app.models.phase_output import PhaseOutput
    from app.models.toc_entry import TOCEntry

    async with SessionLocal() as s:
        for bid in book_ids:
            job_ids_res = await s.execute(
                text("SELECT id FROM homework_jobs WHERE book_id=:bid"), {"bid": bid}
            )
            job_ids = [row[0] for row in job_ids_res.all()]
            if job_ids:
                await s.execute(
                    delete(PhaseOutput).where(PhaseOutput.job_id.in_(job_ids))
                )
            await s.execute(delete(HomeworkJob).where(HomeworkJob.book_id == bid))
            await s.execute(delete(TOCEntry).where(TOCEntry.book_id == bid))
            await s.execute(delete(Book).where(Book.id == bid))
        await s.commit()


async def _cleanup_workers(pc_ids: list):
    from app.db import SessionLocal
    from app.models.worker import WorkerNode

    async with SessionLocal() as s:
        for pc in pc_ids:
            await s.execute(delete(WorkerNode).where(WorkerNode.pc_id == pc))
        await s.commit()


@pytest.mark.asyncio
async def test_reconcile_leaves_fresh_peer_owned_job_and_phase_untouched():
    from main import _reconcile_on_startup
    from app.db import SessionLocal
    from app.models.homework_job import HomeworkJob
    from app.models.phase_output import PhaseOutput
    from app.repositories import phase_outputs as phase_repo
    from app.repositories import workers as workers_repo

    book_ids = []
    try:
        async with SessionLocal() as s:
            # Peer worker with a fresh heartbeat, matching the job's claimed_by.
            await workers_repo.upsert_heartbeat(s, PEER_PC)

            book, toc = await _seed_section(s)
            book_ids.append(book.id)

            job = await _make_peer_owned_running_job(s, book, toc)
            phase = await phase_repo.create(
                s,
                job_id=job.id,
                phase_name="preview",
                phase_order=1,
                prompt_hash="deadbeef",
                model_name="gemini-2.5-flash",
                status="running",
            )
            await s.commit()
            job_id = job.id
            phase_id = phase.id

        async with SessionLocal() as s:
            await _reconcile_on_startup(s)

        async with SessionLocal() as s:
            job_row = await s.get(HomeworkJob, job_id)
            phase_row = await s.get(PhaseOutput, phase_id)
            assert job_row is not None
            assert phase_row is not None

            assert job_row.status == "running", (
                f"Expected peer-owned job to stay 'running' but got "
                f"'{job_row.status}' — a live peer's job must not be stolen"
            )
            assert phase_row.status == "running", (
                f"Expected phase row to stay 'running' but got "
                f"'{phase_row.status}' — the scoped reconcile must not "
                "globally force-fail phase rows of jobs it didn't reclaim"
            )
            assert phase_row.error_message != phase_repo.ORPHANED_RESTART_MESSAGE, (
                "Phase row was force-failed with the orphan marker — the "
                "global phase sweep must be gone, not just narrowed"
            )
    finally:
        await _cleanup(book_ids)
        await _cleanup_workers([PEER_PC])
