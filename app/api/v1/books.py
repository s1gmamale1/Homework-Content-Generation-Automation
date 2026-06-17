import asyncio
import hashlib
import json
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
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
from app.services import events_bus, notion_fetch, storage, toc_extractor
from app.services.flows import SUPPORTED_SUBJECTS
from app.services.grade import derive_grade_from_filename
from app.services.notion.client import NotionClientWrapper


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

# R6: retain references to fire-and-forget TOC tasks so the event loop can't GC
# (and silently cancel) them mid-run. Mirrors the retain pattern in worker.py.
_TOC_TASKS: set = set()


async def ingest_pdf(
    session: AsyncSession,
    *,
    body: bytes,
    subject: str,
    grade: str | None,
    filename: str,
) -> BookOut:
    """Shared book-creation path for both upload and Notion-fetch. Mirrors the
    original inline upload logic EXACTLY, including its two return shapes:
    dedup hit -> _book_out_with_toc; new book -> plain BookOut."""
    if subject not in SUPPORTED_SUBJECTS:
        raise HTTPException(400, f"unknown subject; allowed: {SUPPORTED_SUBJECTS}")

    if len(body) > settings.max_file_mb * 1024 * 1024:
        raise HTTPException(413, f"file too large (>{settings.max_file_mb} MB)")
    if len(body) == 0:
        raise HTTPException(400, "empty file")

    sha = hashlib.sha256(body).hexdigest()

    existing = await books_repo.find_ready_by_hash(session, sha, subject)
    if existing is not None:
        return await _book_out_with_toc(session, existing.id)

    # Derive grade from the filename when the caller didn't supply one — a NULL
    # grade silently defeats Notion archiving ({subject}|{grade} key). Explicit
    # grade always wins; the dedup hit above already returned, so this only runs
    # for genuinely new books.
    grade = grade or derive_grade_from_filename(filename)

    book = await books_repo.create(
        session,
        subject=subject,
        grade=grade,
        original_filename=filename,
        content_sha256=sha,
        file_size_bytes=len(body),
        status="uploading",
    )
    await session.commit()

    # Persist the PDF to a deterministic on-disk location so every downstream
    # phase (TOC extract, lesson extract, content phases) can re-attach it via
    # the agent CLI subprocess driver. Base dir is settings.var_dir (VAR_DIR) —
    # point it at a shared volume for multi-PC fleets (ROADMAP R13).
    pdf_path = storage.book_pdf_path(book.id)
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.write_bytes(body)

    task = asyncio.create_task(toc_extractor.run(book.id, pdf_path, subject))
    _TOC_TASKS.add(task)
    task.add_done_callback(_TOC_TASKS.discard)

    return BookOut.model_validate(book)


@router.post("", status_code=201)
async def upload_book(
    file: UploadFile = File(...),
    subject: str = Form(...),
    grade: str | None = Form(default=None),
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
) -> BookOut:
    body = await file.read()
    return await ingest_pdf(
        session,
        body=body,
        subject=subject,
        grade=grade,
        filename=file.filename or "book.pdf",
    )


class FromNotionRequest(BaseModel):
    subject_page_id: str
    grade: str | None = None


def _notion_subject_title(client: NotionClientWrapper, subject_page_id: str) -> str:
    """Subject page title via the rate-limited wrapper (patched in tests)."""
    return client.get_page_title(subject_page_id)


@router.post("/from-notion", status_code=201)
async def book_from_notion(
    req: FromNotionRequest,
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
) -> BookOut:
    if not settings.notion_api_key:
        raise HTTPException(503, "Notion not configured")
    client = NotionClientWrapper(api_key=settings.notion_api_key)
    title = await asyncio.to_thread(_notion_subject_title, client, req.subject_page_id)
    subject = notion_fetch._map_subject(title)
    if subject is None:
        raise HTTPException(422, f"subject '{title}' is not supported for generation")
    try:
        body, filename = await asyncio.to_thread(
            notion_fetch.download_textbook, client, req.subject_page_id)
    except notion_fetch.TextbookTooLarge as exc:
        raise HTTPException(422, f"textbook too large ({exc}) - shrink and upload manually")
    except notion_fetch.NoTextbook:
        raise HTTPException(422, "this subject has no attached textbook")
    return await ingest_pdf(
        session, body=body, subject=subject, grade=req.grade, filename=filename)


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


@router.get("/{book_id}/source.pdf")
async def get_book_source_pdf(book_id: UUID):
    """Serve a book's raw source PDF so a remote fleet worker that's missing the
    bytes can fetch it on demand (ROADMAP R13). Auth is applied at the
    router-include level. File-presence only (no DB lookup) — a worker only
    asks for books in the shared DB it is already working from."""
    path = storage.book_pdf_path(book_id)
    if not path.exists():
        raise HTTPException(404, "source PDF not found")
    return FileResponse(path, media_type="application/pdf", filename="source.pdf")


@router.get("/{book_id}/toc/stream")
async def stream_toc(book_id: UUID, request: Request):
    resource_id = f"book:{book_id}"

    async def event_gen():
        async with SessionLocal() as session:
            book = await books_repo.get_with_toc(session, book_id)
            # Snapshot inside the session block, release the connection, THEN
            # yield — a yield while the session is checked out can orphan the
            # pooled connection on an abrupt client disconnect (GC then reaps it
            # with a "non-checked-in connection" warning).
            initial: list[dict] = []
            terminal = False
            if book is None:
                initial.append({"event": "error",
                                "data": json.dumps({"message": "book not found"})})
                terminal = True
            elif book.status in ("uploading", "toc_extracting"):
                initial.append({"event": "status",
                                "data": json.dumps({"status": book.status})})
            elif book.status == "toc_ready":
                enriched = await _enriched_toc_entries(session, book)
                entries = [eo.model_dump(mode="json") for eo in enriched]
                initial.append({"event": "toc_ready",
                                "data": json.dumps({"entries": entries})})
                terminal = True
            elif book.status == "failed":
                initial.append({"event": "error",
                                "data": json.dumps({"message": book.error_message or "failed"})})
                terminal = True

        # Session released — safe to yield without holding a pooled connection.
        for ev in initial:
            yield ev
        if terminal:
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


async def _enriched_toc_entries(session: AsyncSession, book) -> list[TOCEntryOut]:
    """TOC entries, each enriched with its latest homework-job id/status so the
    frontend can show a per-row indicator (Ready / Running / Failed).

    Shared by the REST book endpoint AND the SSE ``toc_ready`` replay so the two
    cannot drift: the SSE path used to emit status-less entries that raced in and
    wiped the section-list badges.
    """
    latest = await jobs_repo.latest_by_section(session, book.id)
    entries: list[TOCEntryOut] = []
    for e in book.toc_entries:
        entry_out = TOCEntryOut.model_validate(e)
        job = latest.get(e.id)
        if job is not None:
            entry_out.latest_job_id = job.id
            entry_out.latest_job_status = job.status
        entries.append(entry_out)
    return entries


async def _book_out_with_toc(session: AsyncSession, book_id: UUID) -> BookOut:
    book = await books_repo.get_with_toc(session, book_id)
    if book is None:
        raise HTTPException(404, "book not found")
    out = BookOut.model_validate(book)
    if book.status == "toc_ready":
        out.toc = await _enriched_toc_entries(session, book)
    return out
