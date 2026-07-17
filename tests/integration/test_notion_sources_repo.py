"""Real-DB: app.repositories.notion_sources — the Notion (page_id, block_id)
-> book mapping used by the system-aware "Prepare a subject" dialog (worklog
0144 task 1, prepare-status-redo). RUN_DB_INTEGRATION=1.

Recipe:
  createdb -h 127.0.0.1 -U macmini5 -O edu edu_scratch_prep
  DATABASE_URL=postgresql+asyncpg://edu:edu@127.0.0.1:5432/edu_scratch_prep \
    uv run alembic upgrade head
  DATABASE_URL=... RUN_DB_INTEGRATION=1 uv run python -m pytest \
    tests/integration/test_notion_sources_repo.py -q
"""
import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION") != "1",
    reason="requires RUN_DB_INTEGRATION=1 + DATABASE_URL",
)


async def _make_book(session, books_repo, tag: str):
    return await books_repo.create(
        session,
        subject="matematika",
        original_filename=f"book-{tag}.pdf",
        content_sha256=f"sha-{tag}",
        file_size_bytes=10,
    )


@pytest.mark.asyncio
async def test_upsert_link_idempotent_updates_book_id_and_linked_at():
    from app.db import SessionLocal
    from app.repositories import books as books_repo
    from app.repositories import notion_sources as repo

    tag = uuid.uuid4().hex[:10]
    async with SessionLocal() as session:
        book_a = await _make_book(session, books_repo, f"a-{tag}")
        book_b = await _make_book(session, books_repo, f"b-{tag}")
        await session.commit()

        page = f"page-{tag}"
        block = f"block-{tag}"

        row1 = await repo.upsert_link(
            session, book_id=book_a.id, notion_page_id=page, notion_block_id=block
        )
        await session.commit()
        first_linked_at = row1.linked_at

        # Re-prepare after SHA-dedup: same (page, block) now points at book_b.
        row2 = await repo.upsert_link(
            session, book_id=book_b.id, notion_page_id=page, notion_block_id=block
        )
        await session.commit()

        assert row1.id == row2.id, "same (page, block) must upsert the SAME row"
        assert row2.book_id == book_b.id, "ON CONFLICT must re-point book_id"
        assert row2.linked_at >= first_linked_at, "linked_at must refresh on re-prepare"

        # Exactly one row exists for this (page, block) pair. Compare against
        # the NORMALIZED form — upsert_link hyphen-strips before storing, and
        # these fixture ids use "-" as a readability separator, not a UUID
        # hyphen, so the stored value has it stripped too.
        from sqlalchemy import func, select
        from app.models.notion_source import BookNotionSource

        count = await session.scalar(
            select(func.count())
            .select_from(BookNotionSource)
            .where(
                BookNotionSource.notion_page_id == repo.normalize_notion_id(page),
                BookNotionSource.notion_block_id == repo.normalize_notion_id(block),
            )
        )
        assert count == 1


@pytest.mark.asyncio
async def test_upsert_link_normalizes_hyphenated_and_bare_ids_to_same_row():
    from app.db import SessionLocal
    from app.repositories import books as books_repo
    from app.repositories import notion_sources as repo

    tag = uuid.uuid4().hex[:10]
    async with SessionLocal() as session:
        book = await _make_book(session, books_repo, f"norm-{tag}")
        await session.commit()

        raw_uuid = uuid.uuid4()
        hyphenated_page = str(raw_uuid).upper()  # Notion API shape: hyphenated, may be any case
        bare_page = raw_uuid.hex  # config/other sources: no hyphens
        block = f"block-{tag}"

        row1 = await repo.upsert_link(
            session, book_id=book.id, notion_page_id=hyphenated_page, notion_block_id=block
        )
        await session.commit()
        row2 = await repo.upsert_link(
            session, book_id=book.id, notion_page_id=bare_page, notion_block_id=block
        )
        await session.commit()

        assert row1.id == row2.id, "hyphenated and bare-hex forms must resolve to one row"
        assert row1.notion_page_id == raw_uuid.hex


@pytest.mark.asyncio
async def test_deleting_book_cascades_source_row():
    from app.db import SessionLocal
    from app.repositories import books as books_repo
    from app.repositories import notion_sources as repo
    from app.models.notion_source import BookNotionSource

    tag = uuid.uuid4().hex[:10]
    async with SessionLocal() as session:
        book = await _make_book(session, books_repo, f"cascade-{tag}")
        await session.commit()
        book_id = book.id

        page, block = f"page-{tag}", f"block-{tag}"
        await repo.upsert_link(
            session, book_id=book_id, notion_page_id=page, notion_block_id=block
        )
        await session.commit()

    async with SessionLocal() as session:
        from sqlalchemy import select as _select

        before = (
            await session.execute(
                _select(BookNotionSource).where(
                    BookNotionSource.notion_page_id == repo.normalize_notion_id(page)
                )
            )
        ).scalar_one_or_none()
        assert before is not None, "sanity: the link row must exist before the book is deleted"

    async with SessionLocal() as session:
        from sqlalchemy import delete as sa_delete
        from app.models import Book

        await session.execute(sa_delete(Book).where(Book.id == book_id))
        await session.commit()

    async with SessionLocal() as session:
        from sqlalchemy import select

        remaining = (
            await session.execute(
                select(BookNotionSource).where(
                    BookNotionSource.notion_page_id == repo.normalize_notion_id(page)
                )
            )
        ).scalar_one_or_none()
        assert remaining is None, "FK ondelete=CASCADE must remove the source row"


@pytest.mark.asyncio
async def test_links_for_sources_batch_lookup_one_query():
    from app.db import SessionLocal
    from app.repositories import books as books_repo
    from app.repositories import notion_sources as repo

    tag = uuid.uuid4().hex[:10]
    async with SessionLocal() as session:
        book1 = await _make_book(session, books_repo, f"batch1-{tag}")
        book2 = await _make_book(session, books_repo, f"batch2-{tag}")
        await session.commit()

        p1, b1 = f"page1-{tag}", f"block1-{tag}"
        p2, b2 = f"page2-{tag}", f"block2-{tag}"
        unmatched_p, unmatched_b = f"page3-{tag}", f"block3-{tag}"

        await repo.upsert_link(session, book_id=book1.id, notion_page_id=p1, notion_block_id=b1)
        await repo.upsert_link(session, book_id=book2.id, notion_page_id=p2, notion_block_id=b2)
        await session.commit()

        result = await repo.links_for_sources(
            session,
            [
                (p1.upper(), b1.upper()),  # normalization must still match
                (p2, b2),
                (unmatched_p, unmatched_b),
            ],
        )

        # The returned mapping is keyed by the NORMALIZED form (hyphen-strip +
        # lowercase) — same as what's persisted — not the caller's raw input.
        assert result[(repo.normalize_notion_id(p1), repo.normalize_notion_id(b1))] == book1.id
        assert result[(repo.normalize_notion_id(p2), repo.normalize_notion_id(b2))] == book2.id
        assert (
            repo.normalize_notion_id(unmatched_p),
            repo.normalize_notion_id(unmatched_b),
        ) not in result
        assert len(result) == 2


@pytest.mark.asyncio
async def test_links_for_sources_empty_list_returns_empty_dict_no_query():
    from app.db import SessionLocal
    from app.repositories import notion_sources as repo

    async with SessionLocal() as session:
        assert await repo.links_for_sources(session, []) == {}


def test_normalize_notion_id_strips_hyphens_and_lowercases():
    from app.repositories.notion_sources import normalize_notion_id

    raw = "ABCD-1234-EF00"
    assert normalize_notion_id(raw) == "abcd1234ef00"
    assert normalize_notion_id("abcd1234ef00") == "abcd1234ef00"
