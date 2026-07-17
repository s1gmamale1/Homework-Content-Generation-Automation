import asyncio
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.repositories import books as books_repo
from app.repositories import jobs as jobs_repo
from app.repositories import notion_sources as notion_sources_repo
from app.repositories import toc_entries as toc_repo
from app.services import notion_fetch
from app.services.notion.client import NotionClientWrapper

router = APIRouter(prefix="/notion", tags=["notion"])


def _client() -> NotionClientWrapper:
    if not settings.notion_api_key:
        raise HTTPException(503, "Notion not configured")
    return NotionClientWrapper(api_key=settings.notion_api_key)


@router.get("/grades")
async def get_grades() -> list[dict]:
    client = _client()
    try:
        return await asyncio.to_thread(
            notion_fetch.list_grades, client, settings.notion_lessons_root)
    except Exception as exc:  # noqa: BLE001 - surface a clean "unavailable" to the wizard
        raise HTTPException(502, f"Notion browse failed: {exc}")


@router.get("/grades/{grade_page_id}/subjects")
async def get_subjects(grade_page_id: str) -> list[dict]:
    client = _client()
    try:
        return await asyncio.to_thread(
            notion_fetch.list_subjects, client, grade_page_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Notion browse failed: {exc}")


def _fields_for_book(
    book_id: UUID,
    books_by_id: dict,
    toc_totals: dict,
    blocked_counts: dict,
) -> dict:
    """The five system-state fields for one linked book, or `{}` if the id
    isn't in `books_by_id` (defensive only — a source row whose book was
    deleted cannot occur, FK ondelete=CASCADE removes the link with it)."""
    book = books_by_id.get(book_id)
    if book is None:
        return {}
    return {
        "book_id": str(book_id),
        "book_status": book.status,
        "toc_validation": book.toc_validation,
        "toc_total": toc_totals.get(book_id, 0),
        "toc_ready_at": book.toc_ready_at.isoformat() if book.toc_ready_at else None,
        "redo_blocked_by_jobs": blocked_counts.get(book_id, 0),
    }


async def _enrich_available_languages(session: AsyncSession, result: dict) -> None:
    """Mutate the crawl's `{app_subject: {lang: {..., "parts": [...]}}}` tree
    IN PLACE, adding system-state fields wherever a crawled textbook
    candidate is already linked to a book row (`book_notion_sources`).

    Batch-loaded (GK2 task-4 expectation): no matter how many subjects/
    languages/parts/candidates the crawl returns, this issues exactly ONE
    `links_for_sources` call, ONE `books_repo.get_many`, ONE
    `toc_repo.count_by_book_ids`, and ONE `jobs_repo.count_by_book_ids` for
    the WHOLE response — never a per-candidate/per-part query.

    Per-CANDIDATE: when its own `(page_id, block_id)` (exactly as the crawl
    emits it — the candidate's own page id, which for a child-page-hosted
    PDF is the CHILD page, not the part page) resolves to a book, the
    candidate dict gains `book_id`/`book_status`/`toc_validation`/
    `toc_total`/`toc_ready_at`/`redo_blocked_by_jobs`. An unresolved
    candidate is left untouched — no new keys (back-compat).

    Per-PART rollup (FE convenience — the common case is one PDF per part):
    a part ALSO gains `prepared: true` plus the same five fields, but ONLY
    when EXACTLY ONE of its candidates resolved to a linked book. Zero
    matches (nothing prepared yet) or more than one match (ambiguous — e.g.
    two candidates on one part linked to two different books) leave the
    part itself untouched; the FE falls back to the per-candidate detail.

    A source row pointing at a book whose status is `uploading`/`failed`/
    `toc_extracting` surfaces that status honestly rather than being
    suppressed — the caller decides what to show.
    """
    candidates: list[dict] = []
    for lang_map in result.values():
        for entry in lang_map.values():
            for part in entry.get("parts", []):
                candidates.extend(part.get("candidates", []))

    if not candidates:
        return

    pairs = [(c["page_id"], c["block_id"]) for c in candidates]
    links = await notion_sources_repo.links_for_sources(session, pairs)
    if not links:
        return

    book_ids = sorted({book_id for book_id in links.values()}, key=str)
    books_by_id = await books_repo.get_many(session, book_ids)
    toc_totals = await toc_repo.count_by_book_ids(session, book_ids)
    blocked_counts = await jobs_repo.count_by_book_ids(session, book_ids)

    for lang_map in result.values():
        for entry in lang_map.values():
            for part in entry.get("parts", []):
                linked_book_ids: list[UUID] = []
                for c in part.get("candidates", []):
                    key = (
                        notion_sources_repo.normalize_notion_id(c["page_id"]),
                        notion_sources_repo.normalize_notion_id(c["block_id"]),
                    )
                    book_id = links.get(key)
                    if book_id is None:
                        continue
                    fields = _fields_for_book(book_id, books_by_id, toc_totals, blocked_counts)
                    if fields:
                        c.update(fields)
                        linked_book_ids.append(book_id)
                if len(linked_book_ids) == 1:
                    part.update(_fields_for_book(
                        linked_book_ids[0], books_by_id, toc_totals, blocked_counts))
                    part["prepared"] = True


@router.get("/grades/{grade_page_id}/available-languages")
async def get_available_languages(
    grade_page_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Per-subject language availability for a grade page.

    Returns ``{app_subject: {lang: {"page_id": …, "has_textbook": …, "parts":
    [...]}}}`` — only subjects/languages where the container exists AND has a
    textbook PDF are included. Consumers (e.g. the Fleet launcher) use this to
    decide which language badges to display and which source page to fetch
    the textbook from.

    After the crawl returns, every part's textbook candidates are enriched
    (batch-loaded, see `_enrich_available_languages`) with the system state
    of any book already linked to them — `book_id`/`book_status`/
    `toc_validation`/`toc_total`/`toc_ready_at`/`redo_blocked_by_jobs` per
    candidate, plus a `prepared: true` + same-fields rollup on the part
    itself when exactly one of its candidates is linked. This is what lets
    the "Prepare a subject" dialog show PREPARED/PREPARING/REVIEW/FAILED
    instead of always offering to re-upload."""
    client = _client()
    try:
        result = await asyncio.to_thread(
            notion_fetch.available_languages, client, grade_page_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Notion browse failed: {exc}")
    await _enrich_available_languages(session, result)
    return result
