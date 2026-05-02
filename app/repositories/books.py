from datetime import datetime
from typing import Optional
from uuid import UUID

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
) -> Book:
    book = Book(
        subject=subject,
        original_filename=original_filename,
        content_sha256=content_sha256,
        file_size_bytes=file_size_bytes,
        status=status,
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


async def set_gemini_file(
    session: AsyncSession, book_id: UUID, *, file_uri: str, expires_at: datetime
) -> None:
    book = await session.get(Book, book_id)
    if book is None:
        return
    book.gemini_file_uri = file_uri
    book.gemini_file_expires_at = expires_at


async def set_gemini_cache(
    session: AsyncSession,
    book_id: UUID,
    *,
    cache_name: Optional[str],
    expires_at: Optional[datetime],
) -> None:
    book = await session.get(Book, book_id)
    if book is None:
        return
    book.gemini_cache_name = cache_name
    book.gemini_cache_expires_at = expires_at


async def set_status(
    session: AsyncSession, book_id: UUID, status: str, error_message: Optional[str] = None
) -> None:
    book = await session.get(Book, book_id)
    if book is None:
        return
    book.status = status
    if error_message is not None:
        book.error_message = error_message


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

    `toc_entries` cascade automatically (FK ondelete=CASCADE), but
    `homework_jobs.book_id` has no cascade, so we delete jobs explicitly
    first. `phase_outputs` cascade off jobs. `gemini_usages` keep their rows
    with FKs nulled (ondelete=SET NULL) for billing audit retention.
    """
    from app.models import HomeworkJob

    # Delete jobs first (and their phase_outputs cascade); ORM-level delete
    # so cascade rules on relationships fire correctly.
    job_rows = (
        await session.execute(select(HomeworkJob).where(HomeworkJob.book_id == book_id))
    ).scalars().all()
    for job in job_rows:
        await session.delete(job)

    book = await session.get(Book, book_id)
    if book is None:
        return False
    await session.delete(book)
    return True
