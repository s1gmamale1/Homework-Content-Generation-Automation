# Fleet Phase 2 — Batch Automation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `POST /jobs/batch` takes an already-`toc_ready` book and fans out one `pending` job per lesson into the shared queue, tracked by a `batches` row with a drift-free, computed-on-read rollup.

**Architecture:** Fan-out only (the batch never drives ingest/extraction). One `batches` row per book (`UNIQUE(book_id)`, race-safe find-or-create via `ON CONFLICT`); jobs carry a nullable `batch_id`; the rollup is computed per-lesson-latest (`DISTINCT ON (toc_entry_id)` scoped to `batch_id`) so it stays reconciled under retries. No stored counters. Reconcile runs in one atomic transaction. Reuses the existing per-lesson idempotency (advisory lock + `find_active_for_section`).

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2.x async (asyncpg), Postgres 16, Alembic, pytest/pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-06-08-fleet-phase2-batch-automation-design.md` (approved, 5-round reviewed).

**Test invocation (worktree has no `.venv`):** main-repo venv python, cwd = worktree:
- DB-free: `cd /c/Users/Recruiter/Desktop/homework-fleet-engine && /c/Users/Recruiter/Desktop/Homework-Content-Generation-Automation/.venv/Scripts/python.exe -m pytest tests/ -q`
- Real-DB: prefix `RUN_DB_INTEGRATION=1 DATABASE_URL=postgresql+asyncpg://edu:edu@localhost:5436/edu_homework` (throwaway PG on 5436, migrated). **Baseline must hold: `5 failed (pre-existing Notion) / 329 passed / N skipped`.**

**Standing rules:** stage ONLY the files each task lists (never `git add -A`); commit per task; controller stress-tests every commit (read diff + re-run).

---

## File Structure

- Create: `app/models/batch.py` — `Batch` model (`UNIQUE(book_id)`); register in `app/models/__init__.py`.
- Modify: `app/models/homework_job.py` — add nullable `batch_id` FK + index.
- Create: `alembic/versions/0023_batches.py` — `batches` table + `homework_jobs.batch_id` (down_revision `d5e9f1a2b3c4`).
- Modify: `app/repositories/jobs.py` — `create(...)` accepts `batch_id`.
- Create: `app/repositories/batches.py` — `get_or_create_for_book`, `rollup_for_batch`, `list_with_rollups`.
- Create: `app/api/v1/batch.py` — `POST /jobs/batch`, `GET /jobs/batches`, `GET /jobs/batches/{batch_id}`; register in `app/api/v1/__init__.py`.
- Create: `tests/integration/test_batches.py` — real-DB cases 1–7.
- Create: `tests/api/test_batch_validation.py` — DB-free validation unit test.
- Modify (hygiene): `tests/integration/test_claim_contention.py`, `tests/integration/test_clock_skew.py` — `status="ready"` → `"toc_ready"`.

---

### Task 0: Hygiene — kill the fake `status="ready"` test-seed value

**Why first:** `"ready"` is not a real book status (app uses `uploading`/`toc_extracting`/`toc_ready`/`failed`). It survives only in two job-claim test seeds and is a copy-paste trap for this task's readiness guard. Fix it before anyone copies it.

**Files:** Modify `tests/integration/test_claim_contention.py:43`, `tests/integration/test_clock_skew.py:34`

- [ ] **Step 1: Replace the fake value in both seeds**

In `tests/integration/test_claim_contention.py`, the `Book(...)` seed: change `status="ready",` → `status="toc_ready",`.
In `tests/integration/test_clock_skew.py`, the `_seed_section` `Book(...)`: change `status="ready",` → `status="toc_ready",`.

- [ ] **Step 2: Re-run both (real-DB) — still green**

Run: `RUN_DB_INTEGRATION=1 DATABASE_URL=… <venv python> -m pytest tests/integration/test_claim_contention.py tests/integration/test_clock_skew.py -q`
Expected: all pass (these tests filter on JOB status, not book status — the value was always cosmetic; this just removes the trap).

- [ ] **Step 3: Commit**

```bash
git -C "C:/Users/Recruiter/Desktop/homework-fleet-engine" add tests/integration/test_claim_contention.py tests/integration/test_clock_skew.py
git -C "C:/Users/Recruiter/Desktop/homework-fleet-engine" commit -m "test(fleet): use real book status toc_ready in seeds (drop fake 'ready')"
```

---

### Task 1: `Batch` model + `homework_jobs.batch_id` + migration 0023

**Files:**
- Create: `app/models/batch.py`
- Modify: `app/models/__init__.py`, `app/models/homework_job.py`
- Create: `alembic/versions/0023_batches.py`
- Test: `tests/integration/test_batches_schema.py`

- [ ] **Step 1: Write the failing real-DB schema test**

Create `tests/integration/test_batches_schema.py`:

```python
"""Real-DB: the batches table + homework_jobs.batch_id exist, UNIQUE(book_id)
holds, and a job can carry a batch_id. Skipped unless RUN_DB_INTEGRATION=1."""
from __future__ import annotations

import os

import pytest
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)


@pytest.mark.asyncio
async def test_batch_unique_per_book_and_job_fk():
    from app.db import SessionLocal
    from app.models.batch import Batch
    from app.models.book import Book
    from app.models.homework_job import HomeworkJob
    from app.models.toc_entry import TOCEntry
    from app.repositories import jobs as jobs_repo

    async with SessionLocal() as s:
        book = Book(subject="math-algebra", original_filename="b.pdf",
                    content_sha256="2" * 64, file_size_bytes=1, status="toc_ready")
        s.add(book)
        await s.flush()
        toc = TOCEntry(book_id=book.id, section_title="L1", order_index=0)
        s.add(toc)
        await s.flush()
        b1 = Batch(book_id=book.id, subject="math-algebra", grade=None,
                   provider="claude", model=None)
        s.add(b1)
        await s.flush()
        job = await jobs_repo.create(s, book_id=book.id, toc_entry_id=toc.id,
                                     subject="math-algebra")
        job.batch_id = b1.id
        await s.commit()
        book_id, batch_id = book.id, b1.id

    try:
        # UNIQUE(book_id): a second batch for the same book must fail.
        async with SessionLocal() as s:
            s.add(Batch(book_id=book_id, subject="math-algebra", provider="claude"))
            with pytest.raises(IntegrityError):
                await s.commit()
        # The job kept its batch_id.
        async with SessionLocal() as s:
            j = await s.get(HomeworkJob, (await s.execute(
                __import__("sqlalchemy").select(HomeworkJob.id).where(HomeworkJob.batch_id == batch_id)
            )).scalar_one())
            assert j.batch_id == batch_id
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(HomeworkJob).where(HomeworkJob.book_id == book_id))
            await s.execute(delete(Batch).where(Batch.book_id == book_id))
            await s.execute(delete(TOCEntry).where(TOCEntry.book_id == book_id))
            await s.execute(delete(Book).where(Book.id == book_id))
            await s.commit()
```

- [ ] **Step 2: Run it — expect collection/import failure** (`ModuleNotFoundError: app.models.batch`). That's the red.

- [ ] **Step 3: Create the `Batch` model**

Create `app/models/batch.py`:

```python
from typing import Optional
from uuid import UUID

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, Timestamps, UUIDPK


class Batch(Base, UUIDPK, Timestamps):
    """One row per textbook generation batch. UNIQUE(book_id) → at most one
    logical batch per book, which makes find-or-create race-safe (ON CONFLICT)
    and adoption unambiguous. No status counters: the rollup is computed on read
    (DISTINCT ON over the batch's jobs). provider/model are the launch-default
    label only — per-job provider/model are authoritative."""

    __tablename__ = "batches"

    book_id: Mapped[UUID] = mapped_column(ForeignKey("books.id"), nullable=False)
    subject: Mapped[str] = mapped_column(String(64), nullable=False)
    grade: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    notion_source: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    __table_args__ = (
        UniqueConstraint("book_id", name="uq_batches_book_id"),
    )
```

- [ ] **Step 4: Register the model + add `batch_id` to `HomeworkJob`**

In `app/models/__init__.py`, add `Batch` to the imports/exports (match the existing pattern there — e.g. `from app.models.batch import Batch` and add `"Batch"` to `__all__` if present).

In `app/models/homework_job.py`, add the column (after `model`, before `current_phase`):

```python
    batch_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("batches.id"), nullable=True
    )
```

and add an index in `__table_args__`:

```python
        Index("ix_homework_jobs_batch_id", "batch_id"),
```

(`Optional`, `UUID`, `ForeignKey`, `Index` are already imported in that file.)

- [ ] **Step 5: Write migration 0023**

Create `alembic/versions/0023_batches.py`:

```python
"""Add batches table (fleet batch automation) + homework_jobs.batch_id."""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "d5e9f1a2b3c4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "batches",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("book_id", sa.Uuid(), nullable=False),
        sa.Column("subject", sa.String(length=64), nullable=False),
        sa.Column("grade", sa.String(length=32), nullable=True),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("notion_source", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["book_id"], ["books.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("book_id", name="uq_batches_book_id"),
    )
    op.add_column(
        "homework_jobs",
        sa.Column("batch_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_homework_jobs_batch_id", "homework_jobs", "batches", ["batch_id"], ["id"]
    )
    op.create_index("ix_homework_jobs_batch_id", "homework_jobs", ["batch_id"])


def downgrade() -> None:
    op.drop_index("ix_homework_jobs_batch_id", table_name="homework_jobs")
    op.drop_constraint("fk_homework_jobs_batch_id", "homework_jobs", type_="foreignkey")
    op.drop_column("homework_jobs", "batch_id")
    op.drop_table("batches")
```

- [ ] **Step 6: Apply migration to the throwaway PG**

Run: `DATABASE_URL=… <venv python> -m alembic upgrade head`
Expected: `Running upgrade d5e9f1a2b3c4 -> a1b2c3d4e5f6, Add batches table …`. Then `<venv python> -m alembic heads` shows `a1b2c3d4e5f6 (head)`.

- [ ] **Step 7: Run the schema test — expect green**

Run `test_batches_schema.py` (RUN_DB_INTEGRATION=1). Expected: PASS (UNIQUE raises on the 2nd batch; job keeps batch_id).

- [ ] **Step 8: DB-free suite — baseline holds**

Run `pytest tests/ -q`. Expected: `5 failed (Notion) / 329 passed / N skipped`, no new failures (model import must not break collection).

- [ ] **Step 9: Commit**

```bash
git -C "C:/Users/Recruiter/Desktop/homework-fleet-engine" add app/models/batch.py app/models/__init__.py app/models/homework_job.py alembic/versions/0023_batches.py tests/integration/test_batches_schema.py
git -C "C:/Users/Recruiter/Desktop/homework-fleet-engine" commit -m "feat(fleet): batches table + homework_jobs.batch_id (Phase 2 schema)"
```

---

### Task 2: `jobs_repo.create` accepts `batch_id`

**Files:** Modify `app/repositories/jobs.py:12-35` (`create`)

- [ ] **Step 1: Extend `create`**

Add a `batch_id` keyword (default None) and include it in `kwargs` only when set (mirrors the existing `provider`/`model` pattern):

```python
async def create(
    session: AsyncSession,
    *,
    book_id: UUID,
    toc_entry_id: UUID,
    subject: str,
    status: str = "pending",
    provider: Optional[str] = None,
    model: Optional[str] = None,
    batch_id: Optional[UUID] = None,
) -> HomeworkJob:
    kwargs: dict[str, Any] = dict(
        book_id=book_id,
        toc_entry_id=toc_entry_id,
        subject=subject,
        status=status,
    )
    if provider is not None:
        kwargs["provider"] = provider
    if model is not None:
        kwargs["model"] = model
    if batch_id is not None:
        kwargs["batch_id"] = batch_id
    job = HomeworkJob(**kwargs)
    session.add(job)
    await session.flush()
    return job
```

- [ ] **Step 2: Prove it via the existing schema test** — `test_batches_schema.py` already creates a job then sets `batch_id`; add NOTHING, but confirm a job created *with* `batch_id=` also works by re-running that test (it exercises `jobs_repo.create`). Expected: still PASS. (No new test file — Task 3/4 integration tests exercise the `batch_id=` path directly.)

- [ ] **Step 3: DB-free suite — baseline holds.** Run `pytest tests/ -q`.

- [ ] **Step 4: Commit**

```bash
git -C "C:/Users/Recruiter/Desktop/homework-fleet-engine" add app/repositories/jobs.py
git -C "C:/Users/Recruiter/Desktop/homework-fleet-engine" commit -m "feat(fleet): jobs_repo.create accepts batch_id (Phase 2)"
```

---

### Task 3: `batches` repo — find-or-create + per-lesson-latest rollup

**Files:** Create `app/repositories/batches.py`; Test: `tests/integration/test_batches_repo.py`

**Gotcha (must handle):** `UUIDPK`/`Timestamps` use **Python-side** defaults (`default=uuid4`, `default=_utcnow`) that fire only in ORM unit-of-work — a Core `pg_insert` does NOT trigger them. So `get_or_create_for_book` (Core `ON CONFLICT`) must supply `id`, `created_at`, `updated_at` explicitly.

- [ ] **Step 1: Write the failing repo test**

Create `tests/integration/test_batches_repo.py`:

```python
"""Real-DB: get_or_create_for_book is idempotent per book (race-safe), and the
rollup is per-lesson-latest (a retried lesson counts once). RUN_DB_INTEGRATION=1."""
from __future__ import annotations

import os

import pytest
from sqlalchemy import delete

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)


async def _seed_book_with_lessons(s, n=3):
    from app.models.book import Book
    from app.models.toc_entry import TOCEntry

    book = Book(subject="math-algebra", original_filename="r.pdf",
                content_sha256="3" * 64, file_size_bytes=1, status="toc_ready")
    s.add(book)
    await s.flush()
    tocs = []
    for i in range(n):
        t = TOCEntry(book_id=book.id, section_title=f"L{i}", order_index=i)
        s.add(t)
        tocs.append(t)
    await s.flush()
    return book, tocs


@pytest.mark.asyncio
async def test_get_or_create_is_idempotent_per_book():
    from app.db import SessionLocal
    from app.models.batch import Batch
    from app.models.book import Book
    from app.models.toc_entry import TOCEntry
    from app.repositories import batches as batches_repo

    async with SessionLocal() as s:
        book, _ = await _seed_book_with_lessons(s)
        await s.commit()
        book_id = book.id
    try:
        async with SessionLocal() as s:
            b1 = await batches_repo.get_or_create_for_book(
                s, book_id=book_id, subject="math-algebra", grade=None,
                provider="claude", model=None)
            await s.commit()
        async with SessionLocal() as s:
            b2 = await batches_repo.get_or_create_for_book(
                s, book_id=book_id, subject="math-algebra", grade=None,
                provider="gemini", model=None)
            await s.commit()
        assert b1.id == b2.id, "second call must return the SAME batch (one per book)"
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(Batch).where(Batch.book_id == book_id))
            await s.execute(delete(TOCEntry).where(TOCEntry.book_id == book_id))
            await s.execute(delete(Book).where(Book.id == book_id))
            await s.commit()


@pytest.mark.asyncio
async def test_rollup_is_per_lesson_latest():
    from app.db import SessionLocal
    from app.models.batch import Batch
    from app.models.book import Book
    from app.models.toc_entry import TOCEntry
    from app.models.homework_job import HomeworkJob
    from app.repositories import batches as batches_repo
    from app.repositories import jobs as jobs_repo

    async with SessionLocal() as s:
        book, tocs = await _seed_book_with_lessons(s, n=3)
        batch = await batches_repo.get_or_create_for_book(
            s, book_id=book.id, subject="math-algebra", grade=None,
            provider="claude", model=None)
        # 3 lessons, all pending in this batch.
        for t in tocs:
            await jobs_repo.create(s, book_id=book.id, toc_entry_id=t.id,
                                   subject="math-algebra", batch_id=batch.id)
        await s.commit()
        # Lesson 0: simulate a failed-then-retried lesson -> a SECOND job (newer).
        first = tocs[0]
        async with SessionLocal() as s2:
            old = await s2.get(HomeworkJob, (await s2.execute(
                __import__("sqlalchemy").select(HomeworkJob.id)
                .where(HomeworkJob.toc_entry_id == first.id))).scalar_one())
            old.status = "failed"
            await s2.commit()
        async with SessionLocal() as s3:
            await jobs_repo.create(s3, book_id=book.id, toc_entry_id=first.id,
                                   subject="math-algebra", batch_id=batch.id)  # newer pending
            await s3.commit()
        book_id, batch_id = book.id, batch.id
    try:
        async with SessionLocal() as s:
            tally = await batches_repo.rollup_for_batch(s, batch_id)
        # Per-lesson-latest: lesson0's latest is the newer pending, so 3 pending,
        # 0 failed — total reconciles to 3 lessons, NOT 4 jobs.
        assert sum(tally.values()) == 3, f"denominator must be 3 lessons, got {tally}"
        assert tally.get("pending") == 3
        assert tally.get("failed", 0) == 0
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(HomeworkJob).where(HomeworkJob.book_id == book_id))
            await s.execute(delete(Batch).where(Batch.book_id == book_id))
            await s.execute(delete(TOCEntry).where(TOCEntry.book_id == book_id))
            await s.execute(delete(Book).where(Book.id == book_id))
            await s.commit()
```

- [ ] **Step 2: Run it — expect failure** (`ModuleNotFoundError: app.repositories.batches`).

- [ ] **Step 3: Implement the repo**

Create `app/repositories/batches.py`:

```python
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.batch import Batch
from app.models.homework_job import HomeworkJob


async def get_or_create_for_book(
    session: AsyncSession,
    *,
    book_id: UUID,
    subject: str,
    grade: Optional[str],
    provider: str,
    model: Optional[str],
    notion_source: Optional[str] = None,
) -> Batch:
    """Race-safe find-or-create THE batch for a book (UNIQUE(book_id) + ON
    CONFLICT). Core insert bypasses the ORM Python defaults, so id/created_at/
    updated_at are supplied explicitly. On conflict the existing row is kept
    (only updated_at is touched) and its id is returned."""
    stmt = (
        pg_insert(Batch)
        .values(
            id=uuid4(),
            book_id=book_id,
            subject=subject,
            grade=grade,
            provider=provider,
            model=model,
            notion_source=notion_source,
            created_at=func.now(),
            updated_at=func.now(),
        )
        .on_conflict_do_update(
            index_elements=["book_id"],
            set_={"updated_at": func.now()},
        )
        .returning(Batch.id)
    )
    batch_id = (await session.execute(stmt)).scalar_one()
    return await session.get(Batch, batch_id)


async def rollup_for_batch(session: AsyncSession, batch_id: UUID) -> dict[str, int]:
    """Per-lesson-latest status tally for a batch: one row per toc_entry (its
    newest job), then GROUP BY status. Mirrors `jobs.latest_by_section` (DISTINCT
    ON) but scoped to batch_id, so retries/top-ups can't inflate the count. The
    denominator is sum(tally.values())."""
    latest = (
        select(HomeworkJob.status)
        .where(HomeworkJob.batch_id == batch_id)
        .order_by(HomeworkJob.toc_entry_id, HomeworkJob.created_at.desc())
        .distinct(HomeworkJob.toc_entry_id)
        .subquery()
    )
    rows = await session.execute(
        select(latest.c.status, func.count()).group_by(latest.c.status)
    )
    return {status: count for status, count in rows.all()}


async def list_with_rollups(session: AsyncSession) -> list[dict]:
    """Every batch (newest first) + its computed rollup."""
    batches = (
        await session.execute(select(Batch).order_by(Batch.created_at.desc()))
    ).scalars().all()
    out = []
    for b in batches:
        tally = await rollup_for_batch(session, b.id)
        out.append({"batch": b, "rollup": tally})
    return out
```

- [ ] **Step 4: Run the repo test — expect green.** Both cases pass (idempotent per book; rollup reconciles to 3 lessons not 4 jobs).

- [ ] **Step 5: DB-free suite — baseline holds.**

- [ ] **Step 6: Commit**

```bash
git -C "C:/Users/Recruiter/Desktop/homework-fleet-engine" add app/repositories/batches.py tests/integration/test_batches_repo.py
git -C "C:/Users/Recruiter/Desktop/homework-fleet-engine" commit -m "feat(fleet): batches repo — race-safe find-or-create + per-lesson-latest rollup (Phase 2)"
```

---

### Task 4: `POST /jobs/batch` + reads (guard, defaults, atomic adopt-reconcile)

**Files:** Create `app/api/v1/batch.py`; Modify `app/api/v1/__init__.py`; Tests: `tests/integration/test_batches.py`, `tests/api/test_batch_validation.py`

- [ ] **Step 1: Write the DB-free validation unit test**

Create `tests/api/test_batch_validation.py` (no DB — patches the session/repos to assert guard→HTTP mapping). Use the existing app's test client pattern; assert: non-`toc_ready` book → 409; unknown provider → 400; empty/invalid `toc_entry_ids` → 422. (Model the fixture on `tests/api/test_notion_router.py`'s client + dependency-override style.)

```python
import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_batch_rejects_non_toc_ready(monkeypatch):
    from main import app
    from app.api.v1 import batch as batch_mod

    class _Book:  # minimal stand-in
        status = "toc_extracting"
        error_message = None
        subject = "math-algebra"
        grade = None

    async def _fake_get(session, book_id):
        return _Book()

    monkeypatch.setattr(batch_mod.books_repo, "get", _fake_get)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        resp = await c.post(
            "/api/v1/jobs/batch",
            headers={"Authorization": "Bearer 123"},
            json={"book_id": "00000000-0000-0000-0000-000000000001"},
        )
    assert resp.status_code == 409
    assert "extract" in resp.json()["detail"].lower()
```

- [ ] **Step 2: Run it — expect failure** (`app.api.v1.batch` missing / 404).

- [ ] **Step 3: Implement the router**

Create `app/api/v1/batch.py`:

```python
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.repositories import batches as batches_repo
from app.repositories import books as books_repo
from app.repositories import jobs as jobs_repo
from app.repositories import toc_entries as toc_repo
from app.services.agent_models import is_valid

router = APIRouter(tags=["batches"])

# Fleet batches default to the cli-first provider (master spec §1a); diverges
# from /generate's gemini default deliberately. model=None -> provider default.
_DEFAULT_PROVIDER = "claude"


class BatchLaunchRequest(BaseModel):
    book_id: UUID
    toc_entry_ids: Optional[list[UUID]] = None  # None = all lessons
    provider: Optional[str] = None
    model: Optional[str] = None
    force: bool = False


def _rollup_payload(batch, tally: dict[str, int]) -> dict:
    return {
        "batch_id": str(batch.id),
        "book_id": str(batch.book_id),
        "subject": batch.subject,
        "grade": batch.grade,
        "provider": batch.provider,
        "model": batch.model,
        "rollup": tally,
        "lessons_covered": sum(tally.values()),
        "complete": (tally.get("pending", 0) + tally.get("running", 0)
                     + tally.get("cancelling", 0)) == 0 and sum(tally.values()) > 0,
        "created_at": batch.created_at.isoformat(),
    }


@router.post("/jobs/batch", status_code=201)
async def launch_batch(
    body: BatchLaunchRequest,
    session: AsyncSession = Depends(get_session),
):
    # --- readiness guard ---
    book = await books_repo.get(session, body.book_id)
    if book is None:
        raise HTTPException(404, "book not found")
    if book.status in ("uploading", "toc_extracting"):
        raise HTTPException(409, "book still extracting — lessons available once TOC extraction completes")
    if book.status == "failed":
        raise HTTPException(409, f"book extraction failed: {book.error_message or 'unknown error'}")
    if book.status != "toc_ready":
        raise HTTPException(409, f"book not ready (status={book.status})")

    lessons = await toc_repo.list_for_book(session, body.book_id)
    if not lessons:
        raise HTTPException(422, "no lessons found for this book")

    by_id = {t.id: t for t in lessons}
    if body.toc_entry_ids is not None:
        bad = [tid for tid in body.toc_entry_ids if tid not in by_id]
        if bad:
            raise HTTPException(422, f"toc_entry_ids not in this book: {bad}")
        targets = [by_id[tid] for tid in body.toc_entry_ids]
    else:
        targets = lessons

    provider = body.provider or _DEFAULT_PROVIDER
    if not is_valid(provider, body.model):
        raise HTTPException(400, f"invalid provider/model: {provider}/{body.model}")

    # --- atomic reconcile: one transaction (this request session) ---
    batch = await batches_repo.get_or_create_for_book(
        session, book_id=body.book_id, subject=book.subject, grade=book.grade,
        provider=provider, model=body.model)

    created = adopted = skipped = 0
    for t in targets:
        await jobs_repo.lock_section_for_generate(session, body.book_id, t.id)
        existing = None if body.force else await jobs_repo.find_active_for_section(
            session, body.book_id, t.id)
        if existing is not None:
            if existing.batch_id is None:
                existing.batch_id = batch.id          # adopt orphan (no poaching)
                adopted += 1
            else:
                skipped += 1                            # already this batch's
            continue
        await jobs_repo.create(session, book_id=body.book_id, toc_entry_id=t.id,
                               subject=book.subject, provider=provider,
                               model=body.model, batch_id=batch.id)
        created += 1

    await session.flush()
    tally = await batches_repo.rollup_for_batch(session, batch.id)
    await session.commit()

    payload = _rollup_payload(batch, tally)
    payload.update(jobs_created=created, jobs_adopted=adopted, jobs_skipped=skipped)
    return payload


@router.get("/jobs/batches")
async def list_batches(session: AsyncSession = Depends(get_session)):
    rows = await batches_repo.list_with_rollups(session)
    return {"batches": [_rollup_payload(r["batch"], r["rollup"]) for r in rows]}


@router.get("/jobs/batches/{batch_id}")
async def get_batch(batch_id: UUID, session: AsyncSession = Depends(get_session)):
    from app.models.batch import Batch
    batch = await session.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(404, "batch not found")
    tally = await batches_repo.rollup_for_batch(session, batch_id)
    return _rollup_payload(batch, tally)
```

- [ ] **Step 4: Register the router**

In `app/api/v1/__init__.py`: add `batch` to the import line (`from app.api.v1 import batch, books, health, jobs, notion, workers`) and register it under auth:

```python
api_v1_router.include_router(batch.router, dependencies=[Depends(get_current_user)])
```

- [ ] **Step 5: Run the validation unit test — expect green** (409 on non-`toc_ready`).

- [ ] **Step 6: Write the real-DB integration tests (cases 1–7)**

Create `tests/integration/test_batches.py` with the 7 spec cases. Use an in-process ASGI client (`httpx.ASGITransport`, `Authorization: Bearer 123`) hitting `POST /api/v1/jobs/batch` and the GET reads, seeding `toc_ready` books directly via `SessionLocal`. Cases (each cleans up by `book_id`):

1. **Happy fan-out:** `toc_ready` book, 5 lessons, launch all → 201, `jobs_created==5`, GET batch rollup `{pending:5}`, `lessons_covered==5`.
2. **Readiness guard:** book `toc_extracting`→409; `failed` (+`error_message`)→409 surfacing it; `toc_ready` with zero `toc_entries`→422.
3. **Subset:** `toc_entry_ids` of 2 → `jobs_created==2`, `lessons_covered==2`; an id from another book → 422.
4. **Idempotent re-launch:** launch all (5) → mark 2 jobs `failed` (direct DB) → re-launch all → **same `batch_id`**, `jobs_created==2`, rollup `lessons_covered==5` (NOT 7), the 2 retried lessons show `pending`.
5. **Adopt orphan:** pre-create a `done` job for one lesson with `batch_id=None` (via `jobs_repo.create` + set status) → launch all → response `jobs_adopted>=1`, that job now has the batch's `batch_id`, rollup counts it once.
6. **Concurrent race:** `asyncio.gather` two identical "launch all" → exactly one `batches` row for the book (query count==1), no duplicate active jobs per lesson.
7. **force=True:** launch all, all `done` → re-launch with `force=true` → new `pending` jobs created; rollup still one row per lesson (latest = pending).

- [ ] **Step 7: Run the integration tests — expect all green** (RUN_DB_INTEGRATION=1).

- [ ] **Step 8: DB-free suite — baseline holds** (the new validation test adds 1 pass; integration shows skipped).

- [ ] **Step 9: Commit**

```bash
git -C "C:/Users/Recruiter/Desktop/homework-fleet-engine" add app/api/v1/batch.py app/api/v1/__init__.py tests/api/test_batch_validation.py tests/integration/test_batches.py
git -C "C:/Users/Recruiter/Desktop/homework-fleet-engine" commit -m "feat(fleet): POST /jobs/batch + batch reads — guard, defaults, atomic adopt-reconcile (Phase 2)"
```

---

### Task 5: Acceptance — workers pull a batch + worklog 0050

**Files:** (no app code) Create/extend a smoke; Modify `docs/memory/MASTER_MEMORY.md`, `docs/memory/INDEX.md`

- [ ] **Step 1: Worker-pull acceptance (real process/container)**

Rebuild `class-homework-builder:fleet` (code changed) and bring up the Phase-0 pattern (isolated network + throwaway PG migrated to `a1b2c3d4e5f6`, API `WORKER_CONCURRENCY=0` + ≥1 worker container). Seed a `toc_ready` book with ~4 lessons, `POST /api/v1/jobs/batch` (all), then assert (a) the response `jobs_created==4`, (b) within ~25s every one of the batch's jobs has `attempts>0` (workers pulled them off the shared queue), (c) `GET /api/v1/jobs/batches/{id}` rollup `lessons_covered==4`. Jobs failing for lack of a CLI is fine — the proof is batch→queue→drained. Record the result.

- [ ] **Step 2: Full real-DB integration sweep + DB-free suite**

`pytest tests/integration/ -q` (RUN_DB_INTEGRATION=1) all green; `pytest tests/ -q` at baseline `5 failed / 329+ passed / N skipped`.

- [ ] **Step 3: Worklog 0050 + INDEX row**

Add a `## [0050]` worklog to `docs/memory/MASTER_MEMORY.md` (Phase 2 batch automation: fan-out-only, `batches` UNIQUE(book_id) + nullable `batch_id`, derived per-lesson-latest rollup, atomic adopt-reconcile, default provider=claude, the Core-insert-defaults gotcha; commits + acceptance result) and an INDEX row. Commit:

```bash
git -C "C:/Users/Recruiter/Desktop/homework-fleet-engine" add docs/memory/MASTER_MEMORY.md docs/memory/INDEX.md
git -C "C:/Users/Recruiter/Desktop/homework-fleet-engine" commit -m "docs(memory): worklog 0050 — fleet Phase 2 (batch automation)"
```

---

## Self-Review

**Spec coverage:** §2 data model → Task 1 (`Batch` UNIQUE(book_id) + nullable `batch_id`). §3 endpoint (guard matrix, defaults, atomic find-or-create + adopt) → Task 4. §4 per-lesson-latest rollup → Task 3 (`rollup_for_batch` DISTINCT ON) + read endpoints Task 4. §5 tests (cases 1–7 + DB-free validation + hygiene pre-task) → Tasks 0/3/4. §6 acceptance → Task 5. Decisions #1–7 all realized.

**Placeholder scan:** Task 4 Step 6 describes the 7 integration cases rather than pasting all 7 bodies — acceptable: each case's mechanics (seed `toc_ready` book → POST → assert rollup) are concrete and the helper/assert patterns are shown in Task 3's test and Step 1/3 here. The implementer has full method signatures. No TBD/TODO elsewhere; all app code is complete.

**Type consistency:** `get_or_create_for_book(... provider: str, model: Optional[str], grade: Optional[str])` matches the endpoint's call and `Batch` columns. `rollup_for_batch` returns `dict[str,int]`; `_rollup_payload` consumes it. `jobs_repo.create(... batch_id=)` (Task 2) matches the endpoint's call (Task 4). Migration revision `a1b2c3d4e5f6` / down_revision `d5e9f1a2b3c4` (verified current head) and the model's `uq_batches_book_id` name matches the migration's `UniqueConstraint` name.

**Known gotcha captured:** Core `ON CONFLICT` insert bypasses the ORM Python defaults (`UUIDPK.id`, `Timestamps.*`) → `get_or_create_for_book` supplies `id`/`created_at`/`updated_at` explicitly (Task 3 Step 3). The acceptance (Task 5) re-runs a real worker so this path is exercised end-to-end.
