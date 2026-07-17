from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import select, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import _utcnow
from app.models.notion_source import BookNotionSource


def normalize_notion_id(value: str) -> str:
    """Normalize a Notion page/block id for storage and exact-match lookups.

    The Notion API returns hyphenated UUIDs; a config-driven or manually
    entered source may carry the bare 32-hex form (or mixed case) instead.
    Hyphen-strip + lowercase so both resolve to the same
    ``book_notion_sources`` row (PR #96 gate class)."""
    return value.replace("-", "").lower()


async def upsert_link(
    session: AsyncSession,
    *,
    book_id: UUID,
    notion_page_id: str,
    notion_block_id: str,
) -> BookNotionSource:
    """Insert the (page, block) -> book link, or — if that normalized pair
    already has a row — re-point it at `book_id` and refresh `linked_at`.

    A re-prepare after SHA-dedup can land on a DIFFERENT book than the one
    originally linked (the upload deduped to an existing book row), so the
    UPDATE side of the upsert is not just a no-op refresh; it must actually
    move the link.

    Core-level `pg_insert` bypasses the ORM's Python-side `id` default (see
    `batches.get_or_create_for_book`'s note on the same gotcha), so `id` is
    supplied explicitly here rather than relying on the model's UUIDPK mixin.
    """
    page_id = normalize_notion_id(notion_page_id)
    block_id = normalize_notion_id(notion_block_id)
    stmt = (
        pg_insert(BookNotionSource)
        .values(
            id=uuid4(),
            book_id=book_id,
            notion_page_id=page_id,
            notion_block_id=block_id,
            linked_at=_utcnow(),
        )
        .on_conflict_do_update(
            index_elements=["notion_page_id", "notion_block_id"],
            set_={"book_id": book_id, "linked_at": _utcnow()},
        )
        .returning(BookNotionSource.id)
    )
    row_id = (await session.execute(stmt)).scalar_one()
    # populate_existing=True: a second upsert_link call for the same
    # (page, block) within the same session would otherwise hand back the
    # FIRST call's identity-mapped Python object (session.get short-circuits
    # to the identity map without a fresh SELECT), showing the caller a stale
    # book_id even though the ON CONFLICT UPDATE just moved the row in the DB.
    return await session.get(BookNotionSource, row_id, populate_existing=True)


async def links_for_sources(
    session: AsyncSession, sources: list[tuple[str, str]]
) -> dict[tuple[str, str], UUID]:
    """Batch exact-match lookup: normalized (page_id, block_id) pairs -> book_id.

    ONE query for the entire candidate list (GK2 expectation: no per-candidate
    query loop) via a `(col1, col2) IN (...)` tuple comparison. Ids are
    normalized before matching, and the returned mapping is keyed by the
    SAME normalized form so callers that also normalize their lookup keys
    match cleanly. Pairs with no match are simply absent from the result."""
    if not sources:
        return {}
    normalized = [
        (normalize_notion_id(page), normalize_notion_id(block))
        for page, block in sources
    ]
    stmt = select(
        BookNotionSource.notion_page_id,
        BookNotionSource.notion_block_id,
        BookNotionSource.book_id,
    ).where(
        tuple_(BookNotionSource.notion_page_id, BookNotionSource.notion_block_id).in_(
            normalized
        )
    )
    rows = (await session.execute(stmt)).all()
    return {(r.notion_page_id, r.notion_block_id): r.book_id for r in rows}
