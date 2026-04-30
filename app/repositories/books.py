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
