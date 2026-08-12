"""Real-DB: toc_entries.set_notion_lesson_page_id / set_notion_teacher_deck_job
persist the two new columns added by migration 0059 (notion_lesson_page_id,
notion_teacher_deck_job_id) — the shared Lesson Topic page id and the teacher
deck's job-id mirror of notion_archived_job_id. RUN_DB_INTEGRATION=1."""
import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION") != "1",
    reason="requires RUN_DB_INTEGRATION=1 + DATABASE_URL",
)


@pytest.mark.asyncio
async def test_set_notion_lesson_page_id_persists():
    from app.db import SessionLocal
    from app.repositories import books as books_repo
    from app.repositories import toc_entries as toc_repo
    from app.schemas import TOCEntryExtracted

    async with SessionLocal() as session:
        book = await books_repo.create(
            session,
            subject="matematika",
            grade="8",
            original_filename="teacher-deck-lesson-page.pdf",
            content_sha256="3" * 64,
            file_size_bytes=13,
        )
        [entry] = await toc_repo.bulk_create(
            session,
            book.id,
            [TOCEntryExtracted(section_number="1", section_title="Lesson 1",
                                page_start=1, page_end=3)],
        )
        await session.commit()

        assert entry.notion_lesson_page_id is None

        await toc_repo.set_notion_lesson_page_id(session, entry.id, "lesson-page-abc")
        await session.commit()

    async with SessionLocal() as session:
        refetched = await toc_repo.get(session, entry.id)
        assert refetched is not None
        assert refetched.notion_lesson_page_id == "lesson-page-abc"


@pytest.mark.asyncio
async def test_set_notion_teacher_deck_job_persists():
    from app.db import SessionLocal
    from app.repositories import books as books_repo
    from app.repositories import toc_entries as toc_repo
    from app.schemas import TOCEntryExtracted

    async with SessionLocal() as session:
        book = await books_repo.create(
            session,
            subject="matematika",
            grade="8",
            original_filename="teacher-deck-job.pdf",
            content_sha256="4" * 64,
            file_size_bytes=13,
        )
        [entry] = await toc_repo.bulk_create(
            session,
            book.id,
            [TOCEntryExtracted(section_number="1", section_title="Lesson 1",
                                page_start=1, page_end=3)],
        )
        await session.commit()

        assert entry.notion_teacher_deck_job_id is None

        job_id = uuid.uuid4()
        await toc_repo.set_notion_teacher_deck_job(session, entry.id, job_id)
        await session.commit()

    async with SessionLocal() as session:
        refetched = await toc_repo.get(session, entry.id)
        assert refetched is not None
        assert refetched.notion_teacher_deck_job_id == job_id


@pytest.mark.asyncio
async def test_setters_are_no_op_when_entry_missing():
    """Match the existing set_notion_homework_page_id / set_notion_archived_job
    convention: a missing toc_entry_id is a silent no-op, not an error."""
    from app.db import SessionLocal
    from app.repositories import toc_entries as toc_repo

    async with SessionLocal() as session:
        missing_id = uuid.uuid4()
        await toc_repo.set_notion_lesson_page_id(session, missing_id, "whatever")
        await toc_repo.set_notion_teacher_deck_job(session, missing_id, uuid.uuid4())
        await session.commit()
