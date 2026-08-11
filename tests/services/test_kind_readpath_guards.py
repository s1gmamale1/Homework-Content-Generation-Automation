"""Task 9: cross-kind read-path guards so a `kind='teacher_material'` job never
corrupts a homework-oriented read path.

Two guards under test:
  1. `subject_coverage.job_status_by_book` (DB integration, RUN_DB_INTEGRATION=1) —
     a teacher-material job created AFTER a homework job for the same
     (book, toc_entry) must not replace the lesson's homework status.
  2. `notion_archive.archive_job` (pure unit, mocked — ZERO Notion/DB writes) —
     a `kind='teacher_material'` job is skipped entirely: no push attempt, no
     `notion_skip_reason` write.

Also covers `subject_coverage.batch_by_book` (Task 9's "same book/lesson-latest
shape" sweep) with the same DB-integration fixture.
"""
from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

pytestmark_db = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)


@pytest.mark.asyncio
@pytestmark_db
async def test_job_status_by_book_ignores_teacher_material_jobs():
    """A teacher-material job created AFTER a homework job for the same lesson
    must not overwrite the coverage dashboard's homework status."""
    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.toc_entry import TOCEntry
    from app.repositories import jobs as jobs_repo
    from app.repositories import subject_coverage as sc_repo

    async with SessionLocal() as s:
        book = Book(subject="biology", original_filename="bio9.pdf",
                    content_sha256=uuid4().hex.ljust(64, "0"), file_size_bytes=1,
                    status="toc_ready")
        s.add(book)
        await s.flush()
        toc = TOCEntry(book_id=book.id, section_title="Mavzu 1", order_index=0)
        s.add(toc)
        await s.flush()

        hw = await jobs_repo.create(
            s, book_id=book.id, toc_entry_id=toc.id, subject="biology",
            output_language="uz", kind="homework",
        )
        hw.status = "done"
        await s.flush()

        # A teacher-material job for the SAME lesson, created strictly later.
        teacher = await jobs_repo.create(
            s, book_id=book.id, toc_entry_id=toc.id, subject="biology",
            output_language="uz", kind="teacher_material",
        )
        teacher.status = "running"
        await s.flush()
        assert teacher.created_at >= hw.created_at

        out = await sc_repo.job_status_by_book(s, "uz")

        assert out[str(book.id)][str(toc.id)] == "done"  # homework status, NOT "running"

        await s.rollback()


@pytest.mark.asyncio
@pytestmark_db
async def test_batch_by_book_ignores_teacher_material_batch():
    """A teacher-material batch (its own row — batches fork per kind) must not
    be picked as "the" drill-in batch for a homework book."""
    from app.db import SessionLocal
    from app.models.book import Book
    from app.repositories import batches as batches_repo
    from app.repositories import subject_coverage as sc_repo

    from datetime import datetime, timedelta, timezone

    async with SessionLocal() as s:
        book = Book(subject="biology", original_filename="bio9-batch.pdf",
                    content_sha256=uuid4().hex.ljust(64, "1"), file_size_bytes=1,
                    status="toc_ready")
        s.add(book)
        await s.flush()

        hw_batch = await batches_repo.get_or_create_for_book(
            s, book_id=book.id, subject="biology", grade="9", provider="gemini",
            model=None, transport="api", output_language="uz", kind="homework",
        )
        # `get_or_create_for_book` stamps created_at via SQL `func.now()`, which
        # is the TRANSACTION start time in Postgres — both inserts below would
        # tie on created_at (same open transaction), making DISTINCT ON's tie-
        # break non-deterministic and the test flaky. Force the homework batch
        # deliberately OLDER so "teacher batch created after" is unambiguous.
        hw_batch.created_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
        await s.flush()
        # Teacher batch created after — forks its own row (different kind).
        teacher_batch = await batches_repo.get_or_create_for_book(
            s, book_id=book.id, subject="biology", grade="9", provider="gemini",
            model=None, transport="api", output_language="uz", kind="teacher_material",
        )
        teacher_batch.created_at = datetime(2020, 1, 1, tzinfo=timezone.utc) + timedelta(days=1)
        await s.flush()
        assert teacher_batch.created_at > hw_batch.created_at

        out = await sc_repo.batch_by_book(s, "uz")

        assert out[str(book.id)][0] == str(hw_batch.id)  # NOT the newer teacher batch

        await s.rollback()


def test_archive_job_skips_teacher_material_job_no_writes_no_notion():
    """The auto-archive hook must no-op for a teacher-material job: no Notion
    client touched, no notion_skip_reason write, no session-2 push."""
    import app.services.notion_archive as na

    jid = uuid4()
    job = SimpleNamespace(
        id=jid, kind="teacher_material", notion_archived_at=None,
        subject="matematika", output_language="uz",
        book_id=uuid4(), toc_entry_id=uuid4(), status="done", claim_token=None,
    )

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def commit(self):
            return None

    set_skip = AsyncMock()
    push = AsyncMock()
    client_cls = MagicMock()
    with patch.object(na.settings, "notion_enabled", True), \
         patch.object(na.settings, "notion_api_key", "k"), \
         patch.object(na, "SessionLocal", lambda: _FakeSession()), \
         patch.object(na.jobs_repo, "get", AsyncMock(return_value=job)), \
         patch.object(na.jobs_repo, "set_notion_skip_reason", set_skip), \
         patch.object(na, "_push_with_retry", push), \
         patch.object(na, "NotionClientWrapper", client_cls):
        asyncio.run(na.archive_job(jid))

    set_skip.assert_not_awaited()
    push.assert_not_awaited()
    client_cls.assert_not_called()


def test_archive_job_still_archives_homework_kind_default():
    """Sanity: a plain job with no `kind` attribute at all (test double, or a
    pre-migration row) still defaults to 'homework' behavior — the guard must
    not accidentally skip real homework archiving."""
    import app.services.notion_archive as na

    jid = uuid4()
    # Deliberately NO `kind` attribute — mirrors older test doubles / mirrors
    # `getattr(job, "kind", "homework")` semantics.
    job = SimpleNamespace(
        id=jid, notion_archived_at=None, subject="matematika",
        output_language="uz", book_id=uuid4(), toc_entry_id=uuid4(),
    )
    assert not hasattr(job, "kind")

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def commit(self):
            return None

    set_skip = AsyncMock()
    with patch.object(na.settings, "notion_enabled", True), \
         patch.object(na.settings, "notion_api_key", "k"), \
         patch.object(na.settings, "notion_subject_pages", {}), \
         patch.object(na, "SessionLocal", lambda: _FakeSession()), \
         patch.object(na.jobs_repo, "get", AsyncMock(return_value=job)), \
         patch.object(na.jobs_repo, "set_notion_skip_reason", set_skip):
        asyncio.run(na.archive_job(jid))

    # Falls through to the normal "no subject-page mapping" skip path — proof
    # the kind guard did NOT short-circuit this job.
    set_skip.assert_awaited_once()
