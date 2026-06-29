import os

import pytest

_DB = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)


async def _seed_book(sha: str):
    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.toc_entry import TOCEntry
    async with SessionLocal() as s:
        book = Book(subject="math-algebra", original_filename="x.pdf",
                    content_sha256=sha * 64, file_size_bytes=1, status="toc_ready")
        s.add(book)
        await s.flush()
        t = TOCEntry(book_id=book.id, section_title="L0", order_index=0)
        s.add(t)
        await s.flush()
        await s.commit()
        return book.id, t.id


async def _cleanup(book_id):
    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.homework_job import HomeworkJob
    from app.models.toc_entry import TOCEntry
    from sqlalchemy import delete
    async with SessionLocal() as s:
        await s.execute(delete(HomeworkJob).where(HomeworkJob.book_id == book_id))
        await s.execute(delete(TOCEntry).where(TOCEntry.book_id == book_id))
        await s.execute(delete(Book).where(Book.id == book_id))
        await s.commit()


@_DB
@pytest.mark.asyncio
async def test_create_persists_custom_fields():
    from app.db import SessionLocal
    from app.repositories import jobs as jobs_repo
    book_id, sid = await _seed_book("C")
    try:
        async with SessionLocal() as s:
            job = await jobs_repo.create(
                s, book_id=book_id, toc_entry_id=sid, subject="math-algebra",
                output_language="uz",
                custom_prompts={"flashcards": "RULES"}, selected_phases=["flashcards"],
            )
            await s.commit()
            assert job.custom_prompts == {"flashcards": "RULES"}
            assert job.selected_phases == ["flashcards"]
    finally:
        await _cleanup(book_id)


@_DB
@pytest.mark.asyncio
async def test_create_without_custom_is_null():
    from app.db import SessionLocal
    from app.repositories import jobs as jobs_repo
    book_id, sid = await _seed_book("D")
    try:
        async with SessionLocal() as s:
            job = await jobs_repo.create(s, book_id=book_id, toc_entry_id=sid,
                                         subject="math-algebra", output_language="uz")
            await s.commit()
            assert job.custom_prompts is None
            assert job.selected_phases is None
    finally:
        await _cleanup(book_id)
