"""Real-DB proof of the scrub-CLEAR side of the host lock pair (task 4,
BE-16 SA-key dead-host lane, gate correction 1): `Worker._scrub_if_idle`
takes the EXCLUSIVE host lock (`workers_repo.lock_host_exclusive`, task 1)
and re-reads the drain state — `sa_keys_repo.scrub_pending_for_host` +
`jobs_repo.count_active_for_host` — under that lock, immediately before the
destructive credential clear.

Mirrors tests/integration/test_scrub_claim_gate.py (the claim-side SHARED
lock gate, task 3) and tests/integration/test_host_scrub_sync.py (the lock
primitives + count_active_for_host, round 3):

  - with a real `cancelling` sibling job claimed on the host: the host is
    NOT drained -> the clear DEFERS, residue survives.
  - with no active job on the host: the clear proceeds -> residue is wiped.

Run:
  RUN_DB_INTEGRATION=1 \\
    DATABASE_URL=postgresql+asyncpg://edu:edu@127.0.0.1:5432/edu_scratch_scrub \\
    uv run python -m pytest tests/integration/test_scrub_drain.py -q
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


async def _make_job(s, book, toc, *, status: str, claimed_by: str):
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


async def _cleanup(book_id, hostname):
    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.homework_job import HomeworkJob
    from app.models.sa_key import SAKeyAssignment
    from app.models.toc_entry import TOCEntry

    async with SessionLocal() as s:
        if book_id is not None:
            await s.execute(delete(HomeworkJob).where(HomeworkJob.book_id == book_id))
            await s.execute(delete(TOCEntry).where(TOCEntry.book_id == book_id))
            await s.execute(delete(Book).where(Book.id == book_id))
        await s.execute(delete(SAKeyAssignment).where(SAKeyAssignment.hostname == hostname))
        await s.commit()


def _make_worker(hostname: str, *, var_dir, monkeypatch):
    """An idle (in-process) Worker pinned to `hostname` — a test double for
    the fleet worker actually running on that host. `_tasks` empty means
    THIS process is idle; whether the clear proceeds depends purely on the
    HOST-WIDE `count_active_for_host` signal a real sibling job seeds below."""
    import app.config as config
    from app.services import worker as worker_mod

    monkeypatch.setattr(config.settings, "var_dir", str(var_dir))
    w = worker_mod.Worker(concurrency=1)
    w.hostname = hostname
    w.id = f"{hostname}:{uuid4().hex[:6]}"
    w._tasks = set()
    w._applied_key_sha = None
    return w


def _seed_residue(tmp_path, monkeypatch):
    """Write real on-disk residue (active.json) so `_scrub_if_idle`'s
    residue gate passes and reaches the lock + drain re-read. Also clears
    the two env vars first so this process's real environment (if any) is
    never mutated by the clear path outside monkeypatch's restore."""
    from app.services import worker as worker_mod

    for k in ("GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_CLOUD_PROJECT"):
        monkeypatch.delenv(k, raising=False)

    envfile = tmp_path / ".env"
    monkeypatch.setattr(worker_mod, "_WORKER_ENV_PATH", envfile, raising=False)

    active_path = tmp_path / "sa_keys" / "active.json"
    active_path.parent.mkdir(parents=True, exist_ok=True)
    active_path.write_text('{"type":"service_account"}')
    return active_path


@pytest.mark.asyncio
async def test_scrub_defers_while_sibling_job_active_on_host(tmp_path, monkeypatch):
    from app.db import SessionLocal
    from app.repositories import sa_keys as sa_keys_repo

    hostname = f"scrubdrain-busy-{uuid4().hex[:8]}"
    book_id = None
    try:
        async with SessionLocal() as s:
            book, toc = await _seed_section(s, "scrubdrain-busy.pdf")
            # A real sibling job, claimed by a DIFFERENT pid on this SAME host,
            # still mid-unwind (cancelling counts as active — see
            # count_active_for_host's docstring).
            await _make_job(s, book, toc, status="cancelling", claimed_by=f"{hostname}:9999")
            await sa_keys_repo.scrub(s, hostname)
            await s.commit()
            book_id = book.id

        active_path = _seed_residue(tmp_path, monkeypatch)
        w = _make_worker(hostname, var_dir=tmp_path, monkeypatch=monkeypatch)

        await w._sync_sa_key()

        assert active_path.exists(), (
            "the clear must defer while a sibling job is active on the host"
        )
        assert w._applied_key_sha is None
    finally:
        await _cleanup(book_id, hostname)


@pytest.mark.asyncio
async def test_scrub_clears_when_host_fully_drained(tmp_path, monkeypatch):
    from app.db import SessionLocal
    from app.repositories import sa_keys as sa_keys_repo

    hostname = f"scrubdrain-idle-{uuid4().hex[:8]}"
    try:
        async with SessionLocal() as s:
            await sa_keys_repo.scrub(s, hostname)
            await s.commit()

        active_path = _seed_residue(tmp_path, monkeypatch)
        w = _make_worker(hostname, var_dir=tmp_path, monkeypatch=monkeypatch)

        await w._sync_sa_key()

        assert not active_path.exists(), (
            "the clear must proceed when the host has no active jobs"
        )
        assert w._applied_key_sha is None
    finally:
        await _cleanup(None, hostname)
