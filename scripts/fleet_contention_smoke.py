# scripts/fleet_contention_smoke.py
"""Phase 0 fleet smoke: seed N jobs into the head and prove live worker
containers pulled them off the one shared DB.

Asserts attempts>0 for every job (set on claim at jobs.py:243; the failure path
clears claimed_by but NOT attempts), so it stays valid even though the CLI-less
image fails each job right after claiming it. Multi-worker is sampled live
(best-effort) because claimed_by is cleared on failure. The deterministic
contention proof is Task 1. Run AFTER bringing up postgres + >=2 workers.

  DATABASE_URL=postgresql+asyncpg://edu:edu@localhost:5433/edu_homework \
    .venv/Scripts/python.exe scripts/fleet_contention_smoke.py
"""
from __future__ import annotations

import asyncio
import os
import sys

# Allow running as a plain script (`python scripts/fleet_contention_smoke.py`):
# put the repo root on sys.path so `import app...` resolves. Python otherwise
# only adds the script's own dir (scripts/), not the repo root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import delete, select

from app.db import SessionLocal
from app.models.book import Book
from app.models.homework_job import HomeworkJob
from app.models.toc_entry import TOCEntry
from app.repositories import jobs as jobs_repo

N = 4
SAMPLE_SECONDS = 25


async def _seed():
    async with SessionLocal() as s:
        book = Book(subject="math-algebra", original_filename="smoke.pdf",
                    content_sha256="1" * 64, file_size_bytes=1, status="ready")
        s.add(book)
        await s.flush()
        toc = TOCEntry(book_id=book.id, section_title="L1", order_index=0)
        s.add(toc)
        await s.flush()
        for _ in range(N):
            await jobs_repo.create(s, book_id=book.id, toc_entry_id=toc.id, subject="math-algebra")
        await s.commit()
        return book.id


async def main() -> None:
    book_id = await _seed()
    print(f"seeded {N} jobs; sampling up to {SAMPLE_SECONDS}s while workers pull...")
    workers_seen: set[str] = set()
    try:
        # Live-sample claimed_by (transient) and watch attempts climb.
        for _ in range(SAMPLE_SECONDS):
            await asyncio.sleep(1)
            async with SessionLocal() as s:
                rows = (await s.execute(
                    select(HomeworkJob.claimed_by, HomeworkJob.attempts)
                    .where(HomeworkJob.book_id == book_id)
                )).all()
            for r in rows:
                if r.claimed_by:
                    workers_seen.add(r.claimed_by)
            if all((r.attempts or 0) > 0 for r in rows):
                break  # every job has been claimed at least once

        async with SessionLocal() as s:
            final = (await s.execute(
                select(HomeworkJob.attempts, HomeworkJob.status)
                .where(HomeworkJob.book_id == book_id)
            )).all()
        pulled = [r for r in final if (r.attempts or 0) > 0]
        print(f"pulled {len(pulled)}/{N} jobs (attempts>0); "
              f"workers sampled: {sorted(workers_seen) or '<none captured>'}; "
              f"statuses: {sorted(r.status for r in final)}")
        assert len(pulled) == N, f"only {len(pulled)}/{N} jobs were pulled by a worker"
        print("PASS: all jobs pulled by live worker container(s) off one DB")
        if len(workers_seen) >= 2:
            print(f"BONUS: observed {len(workers_seen)} distinct workers live")
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(HomeworkJob).where(HomeworkJob.book_id == book_id))
            await s.execute(delete(TOCEntry).where(TOCEntry.book_id == book_id))
            await s.execute(delete(Book).where(Book.id == book_id))
            await s.commit()


if __name__ == "__main__":
    asyncio.run(main())
