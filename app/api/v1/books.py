import asyncio
import hashlib
import json
from pathlib import Path
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.auth import get_current_user
from app.config import settings
from app.db import get_session, SessionLocal
from app.repositories import books as books_repo
from app.repositories import jobs as jobs_repo
from app.repositories import toc_entries as toc_repo
from app.schemas import BookOut, TOCEntryOut
from app.services import events_bus, toc_extractor
from app.services.flows import SUPPORTED_SUBJECTS


class BookUpdateRequest(BaseModel):
    original_filename: Optional[str] = None
    subject: Optional[str] = None


class TOCEntryUpdateRequest(BaseModel):
    chapter_number: Optional[str] = None
    chapter_title: Optional[str] = None
    section_number: Optional[str] = None
    section_title: Optional[str] = None
    page_start: Optional[int] = None
    page_end: Optional[int] = None

router = APIRouter(prefix="/books", tags=["books"])


@router.post("", status_code=201)
async def upload_book(
    file: UploadFile = File(...),
    subject: str = Form(...),
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
) -> BookOut:
    if subject not in SUPPORTED_SUBJECTS:
        raise HTTPException(400, f"unknown subject; allowed: {SUPPORTED_SUBJECTS}")

    body = await file.read()
    if len(body) > settings.max_file_mb * 1024 * 1024:
        raise HTTPException(413, f"file too large (>{settings.max_file_mb} MB)")
    if len(body) == 0:
        raise HTTPException(400, "empty file")

    sha = hashlib.sha256(body).hexdigest()

    existing = await books_repo.find_ready_by_hash(session, sha, subject)
    if existing is not None:
        return await _book_out_with_toc(session, existing.id)

    book = await books_repo.create(
        session,
        subject=subject,
        original_filename=file.filename or "book.pdf",
        content_sha256=sha,
        file_size_bytes=len(body),
        status="uploading",
    )
    await session.commit()

    # Persist the PDF to a deterministic on-disk location so every downstream
    # phase (TOC extract, lesson extract, content phases) can re-attach it via
    # the agent CLI subprocess driver. Wave 3E may move this under a settings
    # value; for now the path is hardcoded relative to the project root.
    pdf_path = Path("var") / "books" / str(book.id) / "source.pdf"
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.write_bytes(body)

    asyncio.create_task(toc_extractor.run(book.id, pdf_path, subject))

    return BookOut.model_validate(book)


@router.get("")
async def list_books(
    session: AsyncSession = Depends(get_session),
    limit: int = 100,
    offset: int = 0,
) -> list[BookOut]:
    """Library view — most-recent-first list of every book that's been uploaded.
    `toc` is omitted (None) here; fetch /books/{id} for the full record."""
    rows = await books_repo.list_all(session, limit=limit, offset=offset)
    return [BookOut.model_validate(b) for b in rows]


@router.get("/{book_id}")
async def get_book(
    book_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> BookOut:
    return await _book_out_with_toc(session, book_id)


@router.get("/{book_id}/toc/stream")
async def stream_toc(book_id: UUID, request: Request):
    resource_id = f"book:{book_id}"

    async def event_gen():
        async with SessionLocal() as session:
            book = await books_repo.get_with_toc(session, book_id)
            if book is None:
                yield {"event": "error", "data": json.dumps({"message": "book not found"})}
                return

            if book.status in ("uploading", "toc_extracting"):
                yield {"event": "status", "data": json.dumps({"status": book.status})}
            elif book.status == "toc_ready":
                entries = [TOCEntryOut.model_validate(e).model_dump(mode="json")
                           for e in book.toc_entries]
                yield {"event": "toc_ready", "data": json.dumps({"entries": entries})}
                return
            elif book.status == "failed":
                yield {"event": "error",
                       "data": json.dumps({"message": book.error_message or "failed"})}
                return

        q = events_bus.subscribe(resource_id)
        try:
            while True:
                if await request.is_disconnected():
                    break
                payload = await q.get()
                if payload is None:
                    break
                yield {"event": payload["event"], "data": json.dumps(payload["data"])}
                if payload["event"] in ("toc_ready", "error"):
                    break
        finally:
            events_bus.unsubscribe(resource_id, q)

    return EventSourceResponse(event_gen())


@router.patch("/{book_id}")
async def update_book(
    book_id: UUID,
    body: BookUpdateRequest,
    session: AsyncSession = Depends(get_session),
) -> BookOut:
    if body.subject is not None and body.subject not in SUPPORTED_SUBJECTS:
        raise HTTPException(400, f"unknown subject; allowed: {SUPPORTED_SUBJECTS}")
    if body.original_filename is not None and not body.original_filename.strip():
        raise HTTPException(400, "original_filename cannot be empty")

    book = await books_repo.update(
        session,
        book_id,
        original_filename=body.original_filename,
        subject=body.subject,
    )
    if book is None:
        raise HTTPException(404, "book not found")
    await session.commit()
    return await _book_out_with_toc(session, book_id)


@router.delete("/{book_id}", status_code=204)
async def delete_book(
    book_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> None:
    deleted = await books_repo.delete(session, book_id)
    if not deleted:
        raise HTTPException(404, "book not found")
    await session.commit()


@router.patch("/{book_id}/toc/{entry_id}")
async def update_toc_entry(
    book_id: UUID,
    entry_id: UUID,
    body: TOCEntryUpdateRequest,
    session: AsyncSession = Depends(get_session),
) -> TOCEntryOut:
    # Verify the entry belongs to this book — prevents accidentally editing
    # another book's TOC by guessing IDs.
    existing = await toc_repo.get(session, entry_id)
    if existing is None or existing.book_id != book_id:
        raise HTTPException(404, "toc entry not found")

    updated = await toc_repo.update(
        session,
        entry_id,
        chapter_number=body.chapter_number,
        chapter_title=body.chapter_title,
        section_number=body.section_number,
        section_title=body.section_title,
        page_start=body.page_start,
        page_end=body.page_end,
    )
    await session.commit()
    return TOCEntryOut.model_validate(updated)


@router.delete("/{book_id}/toc/{entry_id}", status_code=204)
async def delete_toc_entry(
    book_id: UUID,
    entry_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> None:
    existing = await toc_repo.get(session, entry_id)
    if existing is None or existing.book_id != book_id:
        raise HTTPException(404, "toc entry not found")
    await toc_repo.delete(session, entry_id)
    await session.commit()


async def _book_out_with_toc(session: AsyncSession, book_id: UUID) -> BookOut:
    book = await books_repo.get_with_toc(session, book_id)
    if book is None:
        raise HTTPException(404, "book not found")
    out = BookOut.model_validate(book)
    if book.status == "toc_ready":
        # Enrich each TOC entry with its latest homework-job status so the
        # frontend can show a per-row indicator (Ready / Running / Failed).
        latest = await jobs_repo.latest_by_section(session, book_id)
        entries: list[TOCEntryOut] = []
        for e in book.toc_entries:
            entry_out = TOCEntryOut.model_validate(e)
            job = latest.get(e.id)
            if job is not None:
                entry_out.latest_job_id = job.id
                entry_out.latest_job_status = job.status
            entries.append(entry_out)
        out.toc = entries
    return out
