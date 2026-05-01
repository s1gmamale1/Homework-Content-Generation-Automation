import asyncio
import hashlib
import json
import tempfile
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.auth import get_current_user
from app.config import settings
from app.db import get_session, SessionLocal
from app.repositories import books as books_repo
from app.repositories import jobs as jobs_repo
from app.schemas import BookOut, TOCEntryOut
from app.services import events_bus, toc_extractor
from app.services.flows import SUPPORTED_SUBJECTS

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

    tmp = Path(tempfile.gettempdir()) / f"edu-book-{book.id}.pdf"
    tmp.write_bytes(body)

    asyncio.create_task(toc_extractor.run(book.id, tmp, subject))

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
