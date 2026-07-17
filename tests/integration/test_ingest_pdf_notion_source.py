"""Real-DB: `ingest_pdf`'s transactional Notion-source linking (worklog 0144
task 2, prepare-status-redo). RUN_DB_INTEGRATION=1.

A mocked session can only prove "commit wasn't called"; it can't prove that a
FLUSHED-but-uncommitted book insert actually gets discarded when the request
fails before commit — that's a real Postgres transaction-rollback behavior.
This file proves the load-bearing atomicity claim end-to-end against a real
DB: a failure between the book insert and the commit must leave NEITHER a
book row NOR a book_notion_sources row.

Recipe:
  createdb -h 127.0.0.1 -U macmini5 -O edu edu_scratch_prep
  DATABASE_URL=postgresql+asyncpg://edu:edu@127.0.0.1:5432/edu_scratch_prep \
    uv run alembic upgrade head
  DATABASE_URL=... RUN_DB_INTEGRATION=1 uv run python -m pytest \
    tests/integration/test_ingest_pdf_notion_source.py -q
"""
import hashlib
import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION") != "1",
    reason="requires RUN_DB_INTEGRATION=1 + DATABASE_URL",
)


@pytest.mark.asyncio
async def test_ingest_pdf_fresh_ingest_links_notion_source_same_commit(monkeypatch, tmp_path):
    """Fresh ingest with a notion_source: the book row AND the
    book_notion_sources link must both be visible after ingest_pdf returns
    (same commit — no route-level second write needed)."""
    from sqlalchemy import select
    import app.api.v1.books as books_api
    from app.db import SessionLocal
    from app.models import Book
    from app.models.notion_source import BookNotionSource
    from app.repositories import notion_sources as repo

    # Fire-and-forget TOC extraction would otherwise spawn a REAL background
    # LLM call (no-homework-spam-money rule) and the real PDF write would
    # touch the live var/ storage dir — neither is relevant to this test's
    # claim (the link lands in the SAME commit as the book row), so both are
    # stubbed out.
    monkeypatch.setattr(books_api, "_start_toc_extraction", lambda *a, **k: None)
    monkeypatch.setattr(books_api.storage, "book_pdf_path", lambda book_id: tmp_path / f"{book_id}.pdf")

    tag = uuid.uuid4().hex[:10]
    body = f"%PDF-1.4 fresh-{tag}".encode()
    page, block = f"page-fresh-{tag}", f"block-fresh-{tag}"

    async with SessionLocal() as session:
        out = await books_api.ingest_pdf(
            session, body=body, subject="matematika", grade="9",
            filename=f"fresh-{tag}.pdf", notion_source=(page, block),
        )

    async with SessionLocal() as session:
        book = (
            await session.execute(
                select(Book).where(Book.content_sha256 == hashlib.sha256(body).hexdigest())
            )
        ).scalar_one_or_none()
        assert book is not None
        assert str(book.id) == str(out.id)

        link = (
            await session.execute(
                select(BookNotionSource).where(
                    BookNotionSource.notion_page_id == repo.normalize_notion_id(page),
                    BookNotionSource.notion_block_id == repo.normalize_notion_id(block),
                )
            )
        ).scalar_one_or_none()
        assert link is not None
        assert link.book_id == book.id


@pytest.mark.asyncio
async def test_ingest_pdf_dedup_hit_repoints_link_to_deduped_book(monkeypatch, tmp_path):
    """A second ingest of the SAME bytes (dedup hit) with a DIFFERENT
    notion_source must re-point that source at the (pre-)existing deduped
    book, committed before ingest_pdf returns."""
    from sqlalchemy import select
    import app.api.v1.books as books_api
    from app.db import SessionLocal
    from app.models.notion_source import BookNotionSource
    from app.repositories import notion_sources as repo

    monkeypatch.setattr(books_api, "_start_toc_extraction", lambda *a, **k: None)
    monkeypatch.setattr(books_api.storage, "book_pdf_path", lambda book_id: tmp_path / f"{book_id}.pdf")

    tag = uuid.uuid4().hex[:10]
    body = f"%PDF-1.4 dedup-{tag}".encode()

    async with SessionLocal() as session:
        first = await books_api.ingest_pdf(
            session, body=body, subject="matematika", grade="9",
            filename=f"dedup-a-{tag}.pdf",
        )
    # Fresh ingest lands in status "uploading", not "toc_ready" (extraction is
    # fire-and-forget) — force it to "toc_ready" so find_ready_by_hash's dedup
    # query (which only matches toc_ready books) picks it up on the 2nd call.
    async with SessionLocal() as session:
        from app.repositories import books as books_repo
        await books_repo.set_status(session, first.id, "toc_ready")
        await session.commit()

    page, block = f"page-dedup-{tag}", f"block-dedup-{tag}"
    async with SessionLocal() as session:
        second = await books_api.ingest_pdf(
            session, body=body, subject="matematika", grade="9",
            filename=f"dedup-b-{tag}.pdf", notion_source=(page, block),
        )
    assert second.deduplicated is True
    assert str(second.id) == str(first.id)

    async with SessionLocal() as session:
        link = (
            await session.execute(
                select(BookNotionSource).where(
                    BookNotionSource.notion_page_id == repo.normalize_notion_id(page),
                    BookNotionSource.notion_block_id == repo.normalize_notion_id(block),
                )
            )
        ).scalar_one_or_none()
        assert link is not None
        assert str(link.book_id) == str(first.id)


@pytest.mark.asyncio
async def test_ingest_pdf_atomic_failure_leaves_no_book_and_no_source_row(monkeypatch):
    """The load-bearing atomicity proof: `upsert_link` raising AFTER the book
    insert has been flushed (but before session.commit()) must leave NEITHER
    row behind — the flushed book insert rolls back with the rest of the
    transaction when the session closes without committing."""
    from sqlalchemy import select
    import app.api.v1.books as books_api
    from app.db import SessionLocal
    from app.models import Book
    from app.models.notion_source import BookNotionSource

    tag = uuid.uuid4().hex[:10]
    body = f"%PDF-1.4 atomic-{tag}".encode()
    sha = hashlib.sha256(body).hexdigest()

    async def _boom(*a, **k):
        raise RuntimeError("simulated post-insert, pre-commit failure")

    monkeypatch.setattr(books_api.notion_sources_repo, "upsert_link", _boom)

    async with SessionLocal() as session:
        with pytest.raises(RuntimeError):
            await books_api.ingest_pdf(
                session, body=body, subject="matematika", grade="9",
                filename=f"atomic-{tag}.pdf",
                notion_source=(f"page-atomic-{tag}", f"block-atomic-{tag}"),
            )
        # Do NOT commit — mirrors get_session's `async with SessionLocal()`
        # dependency, whose __aexit__ closes (and thereby rolls back) the
        # session when the route handler's exception propagates past it.

    async with SessionLocal() as session2:
        book = (
            await session2.execute(select(Book).where(Book.content_sha256 == sha))
        ).scalar_one_or_none()
        assert book is None, "the flushed-but-uncommitted book insert must have rolled back"

        src = (
            await session2.execute(
                select(BookNotionSource).where(
                    BookNotionSource.notion_page_id.like(f"%atomic-{tag}%")
                )
            )
        ).scalar_one_or_none()
        assert src is None, "no source row may exist when the book itself never committed"
