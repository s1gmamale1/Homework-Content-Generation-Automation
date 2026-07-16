from typing import Optional
from uuid import UUID

from sqlalchemy import delete as sa_delete
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Book
from app.models.base import _utcnow


async def lock_book_shared(session: AsyncSession, book_id: UUID) -> None:
    """Book-scoped Postgres advisory lock, SHARED form (BE-02 task 3).

    Every path that ACTIVATES work for a book (single-section `/generate`,
    job retry, batch launch, batch resume, TOC retry) takes this lock at the
    very top of its transaction, before it (re-)reads the state it's about to
    act on. Concurrent SHARED holders never block each other — two activators
    can run in parallel — but a SHARED holder blocks (and is blocked by) the
    EXCLUSIVE holder (`lock_book_exclusive`, taken by `DELETE /books/{id}`).

    `pg_advisory_xact_lock_shared` is transaction-scoped: it releases
    automatically on commit/rollback, so the caller MUST take it inside the
    same transaction that performs (and commits) its write — never take it
    and then return without committing/rolling back soon after, or it pins
    the delete path open for the life of the request.

    Mirrors the per-section advisory lock idiom in
    `app/repositories/jobs.py::lock_section_for_generate` (see also
    `app/api/v1/jobs.py:115`), scoped to the whole book instead of one
    (book, section) pair — the section-level lock serializes double-clicks
    on one lesson; this one serializes activation against deletion of the
    whole book.
    """
    await session.execute(
        text("SELECT pg_advisory_xact_lock_shared(hashtext(:key))"),
        {"key": f"book:{book_id}"},
    )


async def lock_book_exclusive(session: AsyncSession, book_id: UUID) -> None:
    """Book-scoped Postgres advisory lock, EXCLUSIVE form (BE-02 task 3).

    Taken by `DELETE /books/{id}` at the very top of its transaction, before
    the 404 fetch. Blocks (and is blocked by) any `lock_book_shared` holder,
    and blocks any other `lock_book_exclusive` holder — so two concurrent
    deletes of the same book, or a delete racing any of the five activation
    paths, always serialize instead of interleaving. Same key namespace as
    `lock_book_shared` (`f"book:{book_id}"`) so the two forms actually
    contend with each other; transaction-scoped, released on commit/rollback.
    """
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": f"book:{book_id}"},
    )


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


async def set_toc_ready_at(session: AsyncSession, book_id: UUID) -> None:
    """Stamp `toc_ready_at=now()` on the extractor success path (toc_extractor.run,
    final_status == "toc_ready"). Used by the system-aware "Prepare a subject"
    dialog (task 2) to distinguish an already-extracted book from a stale one.

    NOTE: the /toc/accept promotion path (toc_review -> toc_ready) does NOT call
    this yet — clearing/stamping across that lifecycle is Task 3's work
    (prepare-status-redo)."""
    book = await session.get(Book, book_id)
    if book is None:
        return
    book.toc_ready_at = _utcnow()


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
