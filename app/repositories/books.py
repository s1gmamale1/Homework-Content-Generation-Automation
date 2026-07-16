from typing import Optional
from uuid import UUID

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Book


async def create(
    session: AsyncSession,
    *,
    subject: str,
    original_filename: str,
    content_sha256: str,
    file_size_bytes: int,
    status: str = "uploading",
    grade: Optional[str] = None,
    source_language: str = "uz",
) -> Book:
    book = Book(
        subject=subject,
        grade=grade,
        original_filename=original_filename,
        content_sha256=content_sha256,
        file_size_bytes=file_size_bytes,
        status=status,
        source_language=source_language,
    )
    session.add(book)
    await session.flush()
    return book


async def get(session: AsyncSession, book_id: UUID) -> Optional[Book]:
    return await session.get(Book, book_id)


async def get_with_toc(session: AsyncSession, book_id: UUID) -> Optional[Book]:
    stmt = select(Book).where(Book.id == book_id).options(selectinload(Book.toc_entries))
    return (await session.execute(stmt)).scalar_one_or_none()


async def find_ready_by_hash(
    session: AsyncSession, content_sha256: str, subject: str
) -> Optional[Book]:
    stmt = (
        select(Book)
        .where(
            Book.content_sha256 == content_sha256,
            Book.subject == subject,
            Book.status == "toc_ready",
        )
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def set_status(
    session: AsyncSession, book_id: UUID, status: str, error_message: Optional[str] = None
) -> None:
    book = await session.get(Book, book_id)
    if book is None:
        return
    book.status = status
    # Always assign — passing error_message=None must CLEAR a stale error (the
    # retry path relies on this), and a status that isn't `failed` should never
    # carry a leftover message.
    book.error_message = error_message


async def set_toc_validation(
    session: AsyncSession, book_id: UUID, verdict: Optional[str], detail: Optional[str]
) -> None:
    book = await session.get(Book, book_id)
    if book is None:
        return
    book.toc_validation = verdict
    book.toc_validation_detail = detail


async def list_running_for_sweep(session: AsyncSession) -> list[Book]:
    stmt = select(Book).where(Book.status.in_(["uploading", "toc_extracting"]))
    return list((await session.execute(stmt)).scalars().all())


async def list_all(
    session: AsyncSession, *, limit: int = 100, offset: int = 0
) -> list[Book]:
    """Most-recent first. Caps at `limit` so the library never returns thousands."""
    stmt = (
        select(Book)
        .order_by(Book.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list((await session.execute(stmt)).scalars().all())


async def update(
    session: AsyncSession,
    book_id: UUID,
    *,
    original_filename: Optional[str] = None,
    subject: Optional[str] = None,
) -> Optional[Book]:
    """Patch user-editable fields on a book row. Returns the updated row, or
    None if the book doesn't exist."""
    book = await session.get(Book, book_id)
    if book is None:
        return None
    if original_filename is not None:
        book.original_filename = original_filename
    if subject is not None:
        book.subject = subject
    return book


async def delete(session: AsyncSession, book_id: UUID) -> bool:
    """Remove a book and everything that depends on it.

    Order: `homework_jobs` (and their `phase_outputs`, which cascade off
    jobs via FK ondelete=CASCADE) are deleted first, then `batches`, then the
    `book` itself — `toc_entries` cascade automatically off the book (FK
    ondelete=CASCADE). Neither `homework_jobs.book_id` nor `batches.book_id`
    has an ondelete rule, so both must be deleted explicitly before the book,
    or the book DELETE raises IntegrityError on the FK (BE-02 task 1 — the
    audit's reproduced 500 was `batches` being forgotten here). `agent_usages`
    rows are the one exception: their book/job/phase FKs are ondelete=SET
    NULL, so those rows deliberately survive with their FKs nulled, for
    billing/audit retention.
    """
    from app.models import Batch, HomeworkJob

    # Delete jobs first (and their phase_outputs cascade); ORM-level delete
    # so cascade rules on relationships fire correctly.
    job_rows = (
        await session.execute(select(HomeworkJob).where(HomeworkJob.book_id == book_id))
    ).scalars().all()
    for job in job_rows:
        await session.delete(job)

    # Batches have no ondelete on book_id — delete them before the book, or
    # the book DELETE below raises IntegrityError on batches_book_id_fkey.
    await session.execute(sa_delete(Batch).where(Batch.book_id == book_id))

    book = await session.get(Book, book_id)
    if book is None:
        return False
    await session.delete(book)
    return True
