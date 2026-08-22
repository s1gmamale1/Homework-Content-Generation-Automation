import asyncio
import hashlib
import json
import shutil
from pathlib import Path
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from loguru import logger
from notion_client.errors import APIResponseError
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.models.homework_job import HomeworkJob

from app.auth import get_current_user
from app.config import settings
from app.db import get_session, SessionLocal
from app.repositories import books as books_repo
from app.repositories import jobs as jobs_repo
from app.repositories import notion_sources as notion_sources_repo
from app.repositories import regeneration_targets as targets_repo
from app.repositories import toc_entries as toc_repo
from app.schemas import BookOut, TOCEntryOut
from app.services import events_bus, notion_fetch, pdf_lang, storage, subjects, toc_extractor
from app.services.agent_models import validate_output_language
from app.services.flows import SUPPORTED_SUBJECTS
from app.services.grade import derive_grade_from_filename
from app.services.notion.client import NotionClientWrapper
from app.services.toc_classifier import classify_entries


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
    notion_source: tuple[str, str] | None = None,
) -> BookOut:
    """Shared book-creation path for both upload and Notion-fetch. Mirrors the
    original inline upload logic EXACTLY, including its two return shapes:
    dedup hit -> _book_out_with_toc; new book -> plain BookOut.

    `notion_source` (`(notion_page_id, notion_block_id)`) is the RESOLVED
    Notion candidate's own identity — given only by the `/from-notion` route,
    never by the plain upload route (worklog 0144 task 2). When given, the
    (page, block) -> book link is upserted via `notion_sources_repo.upsert_link`
    BEFORE `session.commit()` on both paths: a fresh ingest so the link lands
    in the SAME commit as book creation (a route-level failure after a
    separate commit would otherwise strand an extracting-but-unlinked book),
    and a dedup hit so a re-prepare re-points an existing link at the deduped
    book before returning. `None` (the default) is a no-op — zero behavior
    change for plain uploads."""
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
        if notion_source is not None:
            await notion_sources_repo.upsert_link(
                session, book_id=existing.id,
                notion_page_id=notion_source[0], notion_block_id=notion_source[1],
            )
            await session.commit()
        out = await _book_out_with_toc(session, existing.id)
        out.deduplicated = True
        return out

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
    if notion_source is not None:
        # Same transaction/commit as the book insert above (`create` only
        # flushes) — a raise here propagates WITHOUT committing, so a real
        # session's rollback-on-close discards the flushed-but-uncommitted
        # book row too (see tests/integration/test_ingest_pdf_notion_source.py).
        await notion_sources_repo.upsert_link(
            session, book_id=book.id,
            notion_page_id=notion_source[0], notion_block_id=notion_source[1],
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


# BE-19 task 4: the Uzbek curriculum runs grades 1-11 — an explicit grade
# outside that range (or a non-numeric string like "banana") is now rejected
# at the Pydantic layer instead of silently flowing through to ingest_pdf.
_VALID_GRADES = {str(g) for g in range(1, 12)}


class FromNotionRequest(BaseModel):
    subject_page_id: str = Field(min_length=1)
    grade: str | None = None
    language: str = "uz"
    # Explicit textbook-candidate selector (BE-19 task 3). Omitted (None) ->
    # download_textbook auto-selects when the page's best-rank tier has exactly
    # one candidate, and 422s (AmbiguousTextbook) when it doesn't — e.g. a
    # multi-part textbook. The FE learns to send this in task 5.
    block_id: str | None = None

    @field_validator("grade")
    @classmethod
    def _validate_grade(cls, v: str | None) -> str | None:
        # `grade` stays legal when OMITTED (None) — ingest_pdf already derives
        # it from the filename in that case (pre-existing, unchanged behavior).
        # Only an EXPLICIT value is validated, so "" and "banana" and "12" (out
        # of the 1-11 curriculum range) are rejected here instead of silently
        # ingesting under a bogus/foreign grade.
        if v is not None and v not in _VALID_GRADES:
            raise ValueError(f"grade must be one of 1-11, got {v!r}")
        return v


def _notion_subject_title(client: NotionClientWrapper, subject_page_id: str) -> str:
    """Subject page title via the rate-limited wrapper (patched in tests)."""
    return client.get_page_title(subject_page_id)


def _notion_api_error_response(exc: APIResponseError, page_id: str) -> HTTPException:
    """Map a raw `notion_client` `APIResponseError` to a controlled response —
    404 when Notion reports the page itself is gone (deleted/unshared), 502 for
    any other Notion-side error. Shared by every step that talks to Notion in
    `book_from_notion` (title fetch, ancestry walk, download) so the 404/502
    mapping can't drift between call sites (review fix, task 4 residual-500)."""
    if exc.status == 404:
        return HTTPException(404, f"Notion page not found: {page_id!r} ({exc})")
    return HTTPException(502, f"Notion API error ({exc.status}): {exc}")


# Foreign-language subjects whose textbook's dominant script is fixed by the
# SUBJECT itself (the language it teaches), independent of which language
# container it's fetched under — the script guard downgrades to warn-only when
# a mismatch is consistent with this content script (BE-19 task 5 review
# fixes 1+2). All entries are `family="languages"` in subjects.REGISTRY (a
# test enforces this). Deliberately NOT in the map: `adabiyot`,
# `oqish-savodxonligi`, `alifbe` — literacy subjects taught in the medium of
# instruction, so their content script follows the container (uz edition =
# Latin, ru edition "Литература"/"Чтение"/"Букварь" = Cyrillic) and never
# legitimately diverges from the container expectation.
_LANGUAGE_SUBJECT_CONTENT_SCRIPT: dict[str, str] = {
    "russian": "cyrillic",   # Rus tili — teaches Russian, Cyrillic content
    "english": "latin",      # English — Latin content even under the ru container
    "ona-tili": "latin",     # Uzbek taught in RU-medium schools ("Узб. яз")
}


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
    try:
        title = await asyncio.to_thread(_notion_subject_title, client, req.subject_page_id)
        # Ancestry runs UNCONDITIONALLY, even when `grade` is omitted — a
        # direct API caller could otherwise ingest ANY foreign/out-of-root
        # page just by not passing `grade` (the ancestry walk used to be
        # skipped entirely in that case, a merge-gate-blocking bypass).
        # `grade=None` still keeps its pre-existing legal, filename-derived-
        # default INGEST behavior (see FromNotionRequest._validate_grade) —
        # only the WALK always runs now; `verify_page_ancestry` downgrades
        # its grade-number check to structural-only when `grade` is None
        # (see its docstring).
        await asyncio.to_thread(
            notion_fetch.verify_page_ancestry, client, req.subject_page_id,
            grade=req.grade, language=req.language,
            lessons_root=settings.notion_lessons_root,
        )
    except notion_fetch.PageOutsideRoot as exc:
        raise HTTPException(422, str(exc))
    except APIResponseError as exc:
        raise _notion_api_error_response(exc, req.subject_page_id)
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
        downloaded = await asyncio.to_thread(
            notion_fetch.download_textbook, client, req.subject_page_id, block_id=req.block_id)
    except notion_fetch.TextbookTooLarge as exc:
        raise HTTPException(
            422,
            f"textbook too large ({exc}) — exceeds the {settings.max_file_mb} MB "
            f"ingest cap; raise MAX_FILE_MB on the head to ingest it (the cap is an "
            f"ingest/RAM guard, not a model limit — large books extract fine via "
            f"bounded page windows)",
        )
    except notion_fetch.AmbiguousTextbook as exc:
        # Structured detail (review fix, task 3) — the FE (Task 5) consumes
        # this as JSON, not prose: {"error": "ambiguous_textbook", "message":
        # <short human text>, "candidates": [{"block_id","filename","rank"}, ...]}.
        raise HTTPException(
            422,
            {
                "error": "ambiguous_textbook",
                "message": (
                    f"{len(exc.candidates)} equally-ranked textbook candidates on "
                    "this subject page — pass `block_id` to pick one"
                ),
                "candidates": [
                    {"block_id": c["block_id"], "filename": c["filename"], "rank": c["rank"]}
                    for c in exc.candidates
                ],
            },
        )
    except notion_fetch.StaleSelector as exc:
        # Distinct from the generic empty-page message below (review fix, task
        # 2) — names the offending block_id so an operator can tell "your
        # selector is stale" apart from "this page truly has nothing attached".
        # Caught BEFORE the plain NoTextbook handler since StaleSelector is a
        # subclass of it.
        raise HTTPException(422, str(exc))
    except notion_fetch.NoTextbook:
        raise HTTPException(422, "this subject has no attached textbook")
    except APIResponseError as exc:
        # Residual-500 fix (task 4 review): the page can vanish (deleted /
        # unshared) AFTER ancestry passed but before/during the download call —
        # without this, that race escaped as a bare 500 instead of the
        # controlled 404/502 the earlier Notion calls already get.
        raise _notion_api_error_response(exc, req.subject_page_id)

    # BE-19 task 5: language guard. A live-confirmed case has an Uzbek
    # (Latin) PDF attached to the Russian "Математика" part page in Notion —
    # naive ingestion would silently generate a whole book of wrong-language
    # homework. Deterministic, script-only check: `ru` expects Cyrillic;
    # everything else (`uz`, `en`) expects Latin. Hard-block only on a
    # CONFIDENT mismatch; an indeterminate sample (scanned PDF, no
    # extractable text) proceeds but is surfaced as a response warning
    # instead of silently passing through unchecked.
    detected_script = await asyncio.to_thread(pdf_lang.detect_pdf_script, downloaded.body)
    expected_script = "cyrillic" if req.language == "ru" else "latin"
    warnings: list[str] | None = None
    if detected_script == "unknown":
        warnings = ["language check skipped: no extractable text (scanned PDF?)"]
    elif detected_script != expected_script:
        # Review fixes (task 5, both directions): a foreign-language subject's
        # textbook is dominated by the language it TEACHES, not by the
        # container it was fetched under — "Rus tili" under the uz container
        # is legitimately Cyrillic-heavy; "Английский язык" / "Узб. яз" under
        # the ru container are legitimately Latin-heavy. Hard-blocking those
        # is a false positive (doctrine: hard gates only for wrongness), so a
        # mismatch CONSISTENT with the subject's own content script (see
        # _LANGUAGE_SUBJECT_CONTENT_SCRIPT) downgrades to an advisory; any
        # other mismatch — including one that also contradicts the subject's
        # content script — stays a hard 422.
        if _LANGUAGE_SUBJECT_CONTENT_SCRIPT.get(subject) == detected_script:
            label = subjects.REGISTRY[subject].label
            warnings = [
                f"language check advisory: '{label}' textbooks are expected "
                f"to be {detected_script}-heavy; detected {detected_script}-"
                f"script for language={req.language!r}"
            ]
        else:
            raise HTTPException(
                422,
                f"language mismatch: '{downloaded.filename}' looks {detected_script}-script but "
                f"language={req.language!r} expects {expected_script}-script — pass "
                f"the correct `language`, or upload the PDF directly if this Notion "
                f"page has the wrong file attached",
            )

    out = await ingest_pdf(
        session, body=downloaded.body, subject=subject, grade=req.grade,
        filename=downloaded.filename, source_language=req.language,
        notion_source=(downloaded.source_page_id, downloaded.source_block_id))
    if warnings:
        out.warnings = warnings
    return out


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
    kind: str = Query("homework"),
    session: AsyncSession = Depends(get_session),
) -> BookOut:
    # The Fleet/Section launchers pass output_language so the per-lesson status
    # (and the "complete"/launch gate they derive from it) reflects the SELECTED
    # language. Omitted → all-language aggregate (unchanged for other callers).
    if output_language is not None:
        err = validate_output_language(output_language, allow_none=False)
        if err is not None:
            raise HTTPException(400, err)
    # `kind` scopes the per-lesson status the same way output_language does:
    # the teacher-material launcher card needs teacher-job status, not
    # homework's. Default "homework" keeps every existing caller (and the
    # homework launcher card) byte-identical.
    if kind not in ("homework", "teacher_material"):
        raise HTTPException(400, f"invalid kind: {kind!r} (must be 'homework' or 'teacher_material')")
    return await _book_out_with_toc(session, book_id, output_language, kind=kind)


def _regeneration_block(error: str, message: str, targets) -> HTTPException:
    """A structured 409 for a source-removal blocked by regeneration history.

    A target row records a publication version that is consumed forever, so
    `regeneration_targets.campaign_id` / `toc_entry_id` and
    `homework_jobs.revision_of_job_id` / `regeneration_target_id` are all ON
    DELETE RESTRICT rather than cascading that history away. Those would reach
    the operator as a raw `ForeignKeyViolation` (a 500, or a book flipped to
    `failed` with the old TOC still in place), so the routes refuse first and
    say WHAT blocks them.

    The routes' own guards — not the keys — are what make the refusal complete.
    `regeneration_targets.source_job_id` is the one deliberate exception at ON
    DELETE SET NULL (spec §8.3, so a documented child-first purge can retire a
    source and leave the target as a reporting row), which means the database
    alone would let a source job go and silently null the link. Each call site
    below therefore queries the history itself before deleting anything.

    Structured detail (never prose the FE must parse), listing capped at 20 like
    the `toc_retry_blocked_by_jobs` guard; `count` stays the uncapped total.
    """
    return HTTPException(
        409,
        {
            "error": error,
            "message": message,
            "count": len(targets),
            "targets": [
                {
                    "id": str(t.id),
                    "campaign_id": str(t.campaign_id),
                    "toc_entry_id": str(t.toc_entry_id),
                    "output_language": t.output_language,
                    "status": t.status,
                    "publication_version": t.publication_version,
                }
                for t in targets[:20]
            ],
        },
    )


@router.post("/{book_id}/toc/retry")
async def retry_toc_extraction(
    book_id: UUID,
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
) -> BookOut:
    """Re-run TOC extraction for a book stuck in `failed` or `toc_extracting`,
    still under review (`toc_review`), or already accepted (`toc_ready` — a
    deliberate redo, e.g. the source PDF was replaced). Mirrors POST
    /jobs/{id}/retry for the book-preparation step. The extractor's
    clear-before-insert makes the re-run idempotent (replaces prior entries);
    the prior validation verdict and ready stamp are cleared here too (after
    all guards pass) so a redo never carries a stale audit trail forward."""
    book = await books_repo.get(session, book_id)
    if book is None:
        raise HTTPException(404, "book not found")
    # Book-scoped SHARED advisory lock (BE-02 task 3, merged #100) — the
    # book_id IS the target here, so lock right after the 404 fetch and then
    # RE-FETCH: a concurrent DELETE holding the EXCLUSIVE form blocks us here
    # until it commits/rolls back, and the re-fetch below sees current state
    # (never a stale pre-lock book object).
    await books_repo.lock_book_shared(session, book_id)
    # `Session.get()` short-circuits via the identity map — expire the
    # pre-lock `book` object first so this re-fetch actually re-queries
    # instead of silently returning the same stale in-memory row (see the
    # identical comment on jobs.py::retry_job; caught for real by
    # tests/integration/test_book_delete_race.py).
    session.expire(book)
    book = await books_repo.get(session, book_id)
    if book is None:
        raise HTTPException(404, "book not found")
    # Allowlist gains `toc_ready` (worklog 0144 task 3) — a deliberate redo of
    # an already-accepted book; the guard runs against the RE-FETCHED status.
    if book.status not in ("failed", "toc_extracting", "toc_review", "toc_ready"):
        raise HTTPException(
            409,
            f"cannot retry TOC extraction from status '{book.status}' "
            "(only `failed`, a stuck `toc_extracting`, `toc_review`, or an "
            "already-ready `toc_ready` book)",
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
        # Structured detail, not prose — the FE (Task 5) must never parse this
        # as a string. Listing is capped at 20 (a full-TOC book can carry
        # 50-60+ jobs); `count` stays the uncapped total.
        raise HTTPException(
            409,
            {
                "error": "toc_retry_blocked_by_jobs",
                # Human message (Task 3 review rider) — matches the
                # ambiguous_textbook {error, message, ...} convention so every
                # structured-detail 409 in this router carries a human line
                # alongside the machine-readable fields. Uses the UNCAPPED
                # `count`, not the capped `jobs` listing length.
                "message": (
                    f"{len(blocking)} homework job(s) reference this book's "
                    "sections — delete the affected sections first"
                ),
                "count": len(blocking),
                "jobs": [{"id": str(j.id), "status": j.status} for j in blocking[:20]],
            },
        )
    # Regeneration history is a SEPARATE blocker from the jobs above: a
    # child-first purge can remove a campaign's revision job (nulling
    # `regeneration_targets.source_job_id`) while the reporting row survives, so
    # `list_for_book` comes back empty and the re-extract would sail through —
    # then the clear-before-insert would die on
    # `fk_regeneration_targets_toc_entry_id` and leave the book `failed` with
    # its old TOC. Refuse loudly instead; the book keeps its status.
    regen = await targets_repo.history_for_book(session, book_id)
    if regen:
        raise _regeneration_block(
            "toc_retry_blocked_by_regeneration",
            f"{len(regen)} regeneration target(s) reference this book's "
            "sections — re-extracting would replace TOC entries that a "
            "versioned homework publication is filed against",
            regen,
        )
    await books_repo.set_status(session, book_id, "toc_extracting", error_message=None)
    await books_repo.clear_toc_validation_and_ready(session, book_id)
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
    The toc_validation / toc_validation_detail columns are preserved as an audit
    trail (only /toc/retry clears them). Stamps `toc_ready_at` (Task 3
    lifecycle split) so the system-aware "Prepare a subject" dialog can tell
    this book apart from a stale/never-extracted one.
    """
    # Shared book lock BEFORE the first read (post-#100 follow-up): an
    # unlocked mutate racing DELETE /books/{id} hit StaleDataError -> 500;
    # under the lock a concurrent delete serializes and this read sees the
    # final state (404 instead).
    await books_repo.lock_book_shared(session, book_id)
    book = await books_repo.get(session, book_id)
    if book is None:
        raise HTTPException(404, "book not found")
    if book.status != "toc_review":
        raise HTTPException(409, "can only accept a book in toc_review")
    await books_repo.set_status(session, book_id, "toc_ready")
    await books_repo.set_toc_ready_at(session, book_id)
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


async def _refetch_book_event(book_id: UUID, event: str, marker: dict) -> dict:
    """Rebuild an oversized bus event from the DB — toc_ready with 75+
    enriched entries blows the ~8KB NOTIFY cap. Goes through the shared
    _enriched_toc_entries helper so the refetched shape is byte-identical
    to the inline/replay one (and composes with future changes to it)."""
    hint = {k: v for k, v in marker.items() if k != "__refetch__"}
    async with SessionLocal() as session:
        # get_with_toc: _enriched_toc_entries iterates book.toc_entries (async
        # ORM — lazy load raises MissingGreenlet)
        book = await books_repo.get_with_toc(session, book_id)
        if book is None:
            return hint
        if event in ("toc_ready", "toc_review"):
            enriched = await _enriched_toc_entries(session, book)
            data: dict = {"entries": [eo.model_dump(mode="json") for eo in enriched]}
            if event == "toc_review":
                # The live publisher's validation dict is small and usually
                # survives inline — prefer it; fall back to the replay shape.
                data["validation"] = hint.get("validation") or {
                    "verdict": book.toc_validation,
                    "detail": book.toc_validation_detail,
                }
            return data
        if event == "error" and book.error_message:
            return {**hint, "message": book.error_message}
    return hint


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
                data = payload["data"]
                if isinstance(data, dict) and data.get("__refetch__"):
                    data = await _refetch_book_event(book_id, payload["event"], data)
                yield {"event": payload["event"], "data": json.dumps(data)}
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

    # Shared book lock before the update read-modify-write (post-#100
    # follow-up — see toc/accept comment).
    await books_repo.lock_book_shared(session, book_id)
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
    # Book-scoped EXCLUSIVE advisory lock (BE-02 task 3) — taken at the very
    # top, BEFORE the 404 fetch. Blocks (and is blocked by) any of the five
    # activation paths' SHARED lock, and any other concurrent delete of the
    # same book, so this transaction's guard reads + deletes can never
    # interleave with an activator's guard read + write.
    await books_repo.lock_book_exclusive(session, book_id)
    # Fetch first — a missing book must 404 regardless of status/jobs below,
    # never get masked by the guards that follow (BE-02 task 2).
    book = await books_repo.get(session, book_id)
    if book is None:
        raise HTTPException(404, "book not found")
    # uploading/toc_extracting: the live _TOC_TASKS extractor is still reading
    # the on-disk PDF for this book — deleting out from under it would race
    # the extractor's own file access. failed/toc_review/toc_ready proceed
    # (the wedged-book escape hatch).
    if book.status in ("uploading", "toc_extracting"):
        raise HTTPException(
            409,
            f"cannot delete: book is still being ingested (status "
            f"'{book.status}') — wait for it to finish or fail, then delete",
        )
    active = await jobs_repo.count_active_for_book(session, book_id)
    if active:
        raise HTTPException(
            409,
            f"book has {active} active job(s) (pending/running/cancelling) — "
            "cancel the active job(s) or their batch first, then delete",
        )
    # Regeneration history: `books_repo.delete` removes the book's jobs and TOC
    # entries, both of which regeneration references with RESTRICT keys. Refuse
    # with a readable 409 rather than letting that reach the operator as a 500.
    regen = await targets_repo.history_for_book(session, book_id)
    if regen:
        raise _regeneration_block(
            "book_delete_blocked_by_regeneration",
            f"{len(regen)} regeneration target(s) reference this book's "
            "sections — deleting the book would destroy the audit trail of "
            "publication versions that are permanently consumed",
            regen,
        )
    deleted = await books_repo.delete(session, book_id)
    if not deleted:
        raise HTTPException(404, "book not found")
    await session.commit()
    # On-disk cleanup happens strictly AFTER commit (BE-02 task 4) — a rolled
    # back delete must never have destroyed files. Best-effort: a missing dir
    # is a silent no-op, and any other failure is logged but never turns a
    # committed delete into an error response (the DB rows are already gone).
    try:
        shutil.rmtree(storage.book_dir(book_id))
    except FileNotFoundError:
        pass
    except Exception as exc:
        logger.error(
            f"book delete: dir cleanup FAILED for {storage.book_dir(book_id)}: {exc}"
        )


@router.patch("/{book_id}/toc/{entry_id}")
async def update_toc_entry(
    book_id: UUID,
    entry_id: UUID,
    body: TOCEntryUpdateRequest,
    session: AsyncSession = Depends(get_session),
) -> TOCEntryOut:
    # Verify the entry belongs to this book — prevents accidentally editing
    # another book's TOC by guessing IDs. Shared book lock first (post-#100
    # follow-up — see toc/accept comment).
    await books_repo.lock_book_shared(session, book_id)
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
    await books_repo.lock_book_shared(session, book_id)  # post-#100 follow-up
    existing = await toc_repo.get(session, entry_id)
    if existing is None or existing.book_id != book_id:
        raise HTTPException(404, "toc entry not found")
    # Regeneration history first: this route DELETES the section's jobs before
    # the entry, and both deletes are blocked by RESTRICT keys (a revision job's
    # `revision_of_job_id`, and the target's `toc_entry_id`).
    regen = await targets_repo.history_for_toc_entry(session, entry_id)
    if regen:
        raise _regeneration_block(
            "toc_entry_delete_blocked_by_regeneration",
            f"{len(regen)} regeneration target(s) reference this section — "
            "deleting it would destroy the audit trail of publication versions "
            "that are permanently consumed",
            regen,
        )
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
    session: AsyncSession, book, output_language: Optional[str] = None,
    *, kind: str = "homework",
) -> list[TOCEntryOut]:
    """TOC entries, each enriched with its latest homework-job id/status so the
    frontend can show a per-row indicator (Ready / Running / Failed).

    Shared by the REST book endpoint AND the SSE ``toc_ready`` replay so the two
    cannot drift: the SSE path used to emit status-less entries that raced in and
    wiped the section-list badges.

    `output_language` (when given) scopes the per-row status to that language so
    the launcher's completion reflects the selected language; `None` keeps the
    all-language aggregate for non-launcher callers.

    `kind` scopes the per-row status to that job kind (mirrors
    `jobs_repo.latest_by_section`'s own `kind` param) — the teacher-material
    launcher card needs teacher-job status, not homework's. Default "homework"
    keeps the SSE callers (which never pass it) byte-identical.
    """
    latest = await jobs_repo.latest_by_section(session, book.id, output_language, kind=kind)
    classes = classify_entries(book.toc_entries)
    entries: list[TOCEntryOut] = []
    for i, e in enumerate(book.toc_entries):
        entry_out = TOCEntryOut.model_validate(e)
        entry_out.entry_class = classes[i]
        job = latest.get(e.id)
        if job is not None:
            entry_out.latest_job_id = job.id
            entry_out.latest_job_status = job.status
        entries.append(entry_out)
    return entries


async def _book_out_with_toc(
    session: AsyncSession, book_id: UUID, output_language: Optional[str] = None,
    *, kind: str = "homework",
) -> BookOut:
    book = await books_repo.get_with_toc(session, book_id)
    if book is None:
        raise HTTPException(404, "book not found")
    out = BookOut.model_validate(book)
    if book.status in ("toc_ready", "toc_review"):
        out.toc = await _enriched_toc_entries(session, book, output_language, kind=kind)
    return out
