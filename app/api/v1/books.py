import asyncio
import hashlib
import json
from pathlib import Path
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.models.homework_job import HomeworkJob

from app.auth import get_current_user
from app.config import settings
from app.db import get_session, SessionLocal
from app.repositories import books as books_repo
from app.repositories import jobs as jobs_repo
from app.repositories import toc_entries as toc_repo
from app.schemas import BookOut, TOCEntryOut
from app.services import events_bus, notion_fetch, storage, toc_extractor
from app.services.agent_models import validate_output_language
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


def _start_toc_extraction(book_id: UUID, pdf_path: Path, subject: str) -> None:
    """Fire-and-forget the background TOC extraction, keeping a strong task ref."""
    task = asyncio.create_task(toc_extractor.run(book_id, pdf_path, subject))
    _TOC_TASKS.add(task)
    task.add_done_callback(_TOC_TASKS.discard)


async def ingest_pdf(
    session: AsyncSession,
    *,
    body: bytes,
    subject: str,
    grade: str | None,
    filename: str,
    source_language: str = "uz",
) -> BookOut:
    """Shared book-creation path for both upload and Notion-fetch. Mirrors the
    original inline upload logic EXACTLY, including its two return shapes:
    dedup hit -> _book_out_with_toc; new book -> plain BookOut."""
    if subject not in SUPPORTED_SUBJECTS:
        raise HTTPException(400, f"unknown subject; allowed: {SUPPORTED_SUBJECTS}")

    if len(body) > settings.max_file_mb * 1024 * 1024:
        raise HTTPException(
            413,
            f"file too large: {len(body) // 1048576} MB exceeds the "
            f"{settings.max_file_mb} MB ingest cap — raise MAX_FILE_MB on the head "
            f"to accept larger books (the cap is an ingest/RAM guard, not a model limit)",
        )
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

    # Dedup is keyed on (sha, subject) — a different-language edition of the same
    # book will always have a different sha, so no false reuse across languages.
    # Re-fetching the exact same edition (same bytes) still deduplicates correctly
    # via the `find_ready_by_hash` check above.
    book = await books_repo.create(
        session,
        subject=subject,
        grade=grade,
        original_filename=filename,
        content_sha256=sha,
        file_size_bytes=len(body),
        status="uploading",
        source_language=source_language,
    )
    await session.commit()

    # Persist the PDF to a deterministic on-disk location so every downstream
    # phase (TOC extract, lesson extract, content phases) can re-attach it via
    # the agent CLI subprocess driver. Base dir is settings.var_dir (VAR_DIR) —
    # point it at a shared volume for multi-PC fleets (ROADMAP R13).
    pdf_path = storage.book_pdf_path(book.id)
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.write_bytes(body)

    _start_toc_extraction(book.id, pdf_path, subject)

    return BookOut.model_validate(book)


@router.post("", status_code=201)
async def upload_book(
    file: UploadFile = File(...),
    subject: str = Form(...),
    grade: str | None = Form(default=None),
    source_language: str = Form(default="uz"),
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
) -> BookOut:
    err = validate_output_language(source_language, allow_none=False)
    if err is not None:
        raise HTTPException(422, f"invalid source_language: {err}")
    body = await file.read()
    return await ingest_pdf(
        session,
        body=body,
        subject=subject,
        grade=grade,
        filename=file.filename or "book.pdf",
        source_language=source_language,
    )


class FromNotionRequest(BaseModel):
    subject_page_id: str
    grade: str | None = None
    language: str = "uz"


def _notion_subject_title(client: NotionClientWrapper, subject_page_id: str) -> str:
    """Subject page title via the rate-limited wrapper (patched in tests)."""
    return client.get_page_title(subject_page_id)


@router.post("/from-notion", status_code=201)
async def book_from_notion(
    req: FromNotionRequest,
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
) -> BookOut:
    lang_err = validate_output_language(req.language, allow_none=False)
    if lang_err is not None:
        raise HTTPException(422, f"invalid language: {lang_err}")
    if not settings.notion_api_key:
        raise HTTPException(503, "Notion not configured")
    client = NotionClientWrapper(api_key=settings.notion_api_key)
    title = await asyncio.to_thread(_notion_subject_title, client, req.subject_page_id)
    subject = notion_fetch._map_subject_for_language(title, req.language)
    if subject is None:
        raise HTTPException(
            422,
            f"subject '{title}' is not a recognized {req.language} subject — "
            f"for English, create an English page/container with the textbook in Notion "
            f"or upload the PDF directly; otherwise check the page is under the right "
            f"language container.",
        )
    try:
        body, filename = await asyncio.to_thread(
            notion_fetch.download_textbook, client, req.subject_page_id)
    except notion_fetch.TextbookTooLarge as exc:
        raise HTTPException(
            422,
            f"textbook too large ({exc}) — exceeds the {settings.max_file_mb} MB "
            f"ingest cap; raise MAX_FILE_MB on the head to ingest it (the cap is an "
            f"ingest/RAM guard, not a model limit — large books extract fine via "
            f"bounded page windows)",
        )
    except notion_fetch.NoTextbook:
        raise HTTPException(422, "this subject has no attached textbook")
    return await ingest_pdf(
        session, body=body, subject=subject, grade=req.grade, filename=filename,
        source_language=req.language)


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
    output_language: Optional[str] = Query(None),
    session: AsyncSession = Depends(get_session),
) -> BookOut:
    # The Fleet/Section launchers pass output_language so the per-lesson status
    # (and the "complete"/launch gate they derive from it) reflects the SELECTED
    # language. Omitted → all-language aggregate (unchanged for other callers).
    if output_language is not None:
        err = validate_output_language(output_language, allow_none=False)
        if err is not None:
            raise HTTPException(400, err)
    return await _book_out_with_toc(session, book_id, output_language)


@router.post("/{book_id}/toc/retry")
async def retry_toc_extraction(
    book_id: UUID,
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
) -> BookOut:
    """Re-run TOC extraction for a book stuck in `failed` or `toc_extracting`.
    Mirrors POST /jobs/{id}/retry for the book-preparation step. The extractor's
    clear-before-insert makes the re-run idempotent (replaces prior entries)."""
    book = await books_repo.get(session, book_id)
    if book is None:
        raise HTTPException(404, "book not found")
    if book.status not in ("failed", "toc_extracting", "toc_review"):
        raise HTTPException(
            409,
            f"cannot retry TOC extraction from status '{book.status}' "
            "(only `failed`, a stuck `toc_extracting`, or `toc_review`)",
        )
    pdf_path = storage.book_pdf_path(book_id)
    if not pdf_path.exists():
        raise HTTPException(409, "source PDF missing on disk — re-upload the book")
    # Re-extraction clears the book's TOC entries (toc_extractor's
    # clear-before-insert). homework_jobs.toc_entry_id is a NOT-NULL FK with no
    # cascade, so any referencing job — of ANY status — would make that DELETE
    # raise a ForeignKeyViolation, flip the book to `failed`, and leave the old
    # TOC in place (WISHLIST toc-reextract-fk-blocked-1). Refuse LOUDLY instead:
    # the book keeps its current status and the operator deletes the blocking
    # jobs (delete the affected sections) before retrying.
    blocking = await jobs_repo.list_for_book(session, book_id)
    if blocking:
        listed = ", ".join(f"{j.id} ({j.status})" for j in blocking[:20])
        more = f" (+{len(blocking) - 20} more)" if len(blocking) > 20 else ""
        raise HTTPException(
            409,
            f"cannot re-extract the TOC: {len(blocking)} homework job(s) "
            "reference this book's sections and would be orphaned. Delete the "
            "affected sections (or their jobs) first, then retry. Blocking jobs: "
            f"{listed}{more}",
        )
    await books_repo.set_status(session, book_id, "toc_extracting", error_message=None)
    await session.commit()
    _start_toc_extraction(book_id, pdf_path, book.subject)
    return await _book_out_with_toc(session, book_id)


@router.post("/{book_id}/toc/accept")
async def accept_toc(
    book_id: UUID,
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
) -> BookOut:
    """Accept the TOC entries for a `toc_review` book, promoting it to `toc_ready`.
    The toc_validation / toc_validation_detail columns are preserved as an audit trail.
    """
    book = await books_repo.get(session, book_id)
    if book is None:
        raise HTTPException(404, "book not found")
    if book.status != "toc_review":
        raise HTTPException(409, "can only accept a book in toc_review")
    await books_repo.set_status(session, book_id, "toc_ready")
    await session.commit()
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
            elif book.status == "toc_review":
                enriched = await _enriched_toc_entries(session, book)
                entries = [eo.model_dump(mode="json") for eo in enriched]
                initial.append({"event": "toc_review",
                                "data": json.dumps({
                                    "entries": entries,
                                    "validation": {
                                        "verdict": book.toc_validation,
                                        "detail": book.toc_validation_detail,
                                    },
                                })})
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
                if payload["event"] in ("toc_ready", "toc_review", "error"):
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
    # Don't delete a section out from under a worker — refuse while a job for it
    # is in flight (terminal jobs: done/failed/cancelled are fine to clean up).
    active = (
        await session.execute(
            select(HomeworkJob.id)
            .where(
                HomeworkJob.toc_entry_id == entry_id,
                HomeworkJob.status.in_(["pending", "running", "cancelling"]),
            )
            .limit(1)
        )
    ).first()
    if active is not None:
        raise HTTPException(
            409,
            "This section has a job in progress — cancel it before deleting the section.",
        )
    # homework_jobs.toc_entry_id is NO ACTION (no cascade), so remove the
    # section's jobs first. phase_outputs cascade automatically; agent_usages
    # rows are kept (their job/phase FKs are SET NULL) for billing history.
    await session.execute(delete(HomeworkJob).where(HomeworkJob.toc_entry_id == entry_id))
    await toc_repo.delete(session, entry_id)
    await session.commit()


async def _enriched_toc_entries(
    session: AsyncSession, book, output_language: Optional[str] = None
) -> list[TOCEntryOut]:
    """TOC entries, each enriched with its latest homework-job id/status so the
    frontend can show a per-row indicator (Ready / Running / Failed).

    Shared by the REST book endpoint AND the SSE ``toc_ready`` replay so the two
    cannot drift: the SSE path used to emit status-less entries that raced in and
    wiped the section-list badges.

    `output_language` (when given) scopes the per-row status to that language so
    the launcher's completion reflects the selected language; `None` keeps the
    all-language aggregate for non-launcher callers.
    """
    latest = await jobs_repo.latest_by_section(session, book.id, output_language)
    entries: list[TOCEntryOut] = []
    for e in book.toc_entries:
        entry_out = TOCEntryOut.model_validate(e)
        job = latest.get(e.id)
        if job is not None:
            entry_out.latest_job_id = job.id
            entry_out.latest_job_status = job.status
        entries.append(entry_out)
    return entries


async def _book_out_with_toc(
    session: AsyncSession, book_id: UUID, output_language: Optional[str] = None
) -> BookOut:
    book = await books_repo.get_with_toc(session, book_id)
    if book is None:
        raise HTTPException(404, "book not found")
    out = BookOut.model_validate(book)
    if book.status in ("toc_ready", "toc_review"):
        out.toc = await _enriched_toc_entries(session, book, output_language)
    return out
