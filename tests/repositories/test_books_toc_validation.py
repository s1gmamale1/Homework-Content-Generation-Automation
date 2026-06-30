"""Real-DB: toc_validation + toc_validation_detail columns on books table.

Verifies:
- A book inserted without toc_validation defaults to NULL.
- Books with "verified", "mismatch", "skipped" are accepted.
- A book with "bogus" raises IntegrityError (CHECK constraint bites).
- Bite-proof: dropping the constraint lets "bogus" through; restoring it blocks again.

Skipped unless RUN_DB_INTEGRATION=1 + DATABASE_URL are set.
"""
from __future__ import annotations

import hashlib
import os
import time

import pytest
from sqlalchemy import delete, text
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)


# ---------------------------------------------------------------------------
# Helper: unique sha so parallel runs don't clash
# ---------------------------------------------------------------------------

def _sha(tag: str) -> str:
    return hashlib.sha256(f"tocval-{tag}-{time.time_ns()}".encode()).hexdigest()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_toc_validation_default_is_null():
    """Insert a Book without setting toc_validation; it must default to NULL."""
    from app.db import SessionLocal
    from app.models.book import Book

    async with SessionLocal() as s:
        book = Book(
            subject="math-algebra",
            original_filename="tv_default.pdf",
            content_sha256=_sha("default"),
            file_size_bytes=1,
            status="toc_ready",
        )
        s.add(book)
        await s.commit()
        await s.refresh(book)
        book_id = book.id
        assert book.toc_validation is None, (
            f"expected None, got {book.toc_validation!r}"
        )
        assert book.toc_validation_detail is None, (
            f"expected None, got {book.toc_validation_detail!r}"
        )

    async with SessionLocal() as s:
        await s.execute(delete(Book).where(Book.id == book_id))
        await s.commit()


@pytest.mark.asyncio
async def test_toc_validation_verified_accepted():
    """toc_validation='verified' must be accepted."""
    from app.db import SessionLocal
    from app.models.book import Book

    async with SessionLocal() as s:
        book = Book(
            subject="math-algebra",
            original_filename="tv_verified.pdf",
            content_sha256=_sha("verified"),
            file_size_bytes=1,
            status="toc_ready",
            toc_validation="verified",
            toc_validation_detail="All entries matched the cover page.",
        )
        s.add(book)
        await s.commit()
        await s.refresh(book)
        book_id = book.id
        assert book.toc_validation == "verified"
        assert book.toc_validation_detail == "All entries matched the cover page."

    async with SessionLocal() as s:
        await s.execute(delete(Book).where(Book.id == book_id))
        await s.commit()


@pytest.mark.asyncio
async def test_toc_validation_mismatch_accepted():
    """toc_validation='mismatch' must be accepted."""
    from app.db import SessionLocal
    from app.models.book import Book

    async with SessionLocal() as s:
        book = Book(
            subject="math-algebra",
            original_filename="tv_mismatch.pdf",
            content_sha256=_sha("mismatch"),
            file_size_bytes=1,
            status="toc_ready",
            toc_validation="mismatch",
        )
        s.add(book)
        await s.commit()
        book_id = book.id

    async with SessionLocal() as s:
        await s.execute(delete(Book).where(Book.id == book_id))
        await s.commit()


@pytest.mark.asyncio
async def test_toc_validation_skipped_accepted():
    """toc_validation='skipped' must be accepted."""
    from app.db import SessionLocal
    from app.models.book import Book

    async with SessionLocal() as s:
        book = Book(
            subject="math-algebra",
            original_filename="tv_skipped.pdf",
            content_sha256=_sha("skipped"),
            file_size_bytes=1,
            status="toc_ready",
            toc_validation="skipped",
        )
        s.add(book)
        await s.commit()
        book_id = book.id

    async with SessionLocal() as s:
        await s.execute(delete(Book).where(Book.id == book_id))
        await s.commit()


@pytest.mark.asyncio
async def test_toc_validation_invalid_rejected():
    """toc_validation='bogus' must raise IntegrityError (CHECK constraint)."""
    from app.db import SessionLocal
    from app.models.book import Book

    async with SessionLocal() as s:
        bad = Book(
            subject="math-algebra",
            original_filename="tv_bogus.pdf",
            content_sha256=_sha("bogus"),
            file_size_bytes=1,
            status="toc_ready",
            toc_validation="bogus",
        )
        s.add(bad)
        with pytest.raises(IntegrityError):
            await s.commit()
        await s.rollback()


@pytest.mark.asyncio
async def test_toc_validation_bite_proof():
    """Bite-prove: drop the CHECK constraint → 'bogus' is accepted; restore → 'bogus' rejected again."""
    from app.db import SessionLocal
    from app.models.book import Book

    # Drop the constraint so 'bogus' should pass
    async with SessionLocal() as s:
        await s.execute(
            text("ALTER TABLE books DROP CONSTRAINT IF EXISTS ck_books_toc_validation")
        )
        await s.commit()

    inserted_id = None
    try:
        async with SessionLocal() as s:
            no_ck = Book(
                subject="math-algebra",
                original_filename="tv_bite.pdf",
                content_sha256=_sha("bite"),
                file_size_bytes=1,
                status="toc_ready",
                toc_validation="bogus",
            )
            s.add(no_ck)
            await s.commit()          # must NOT raise (constraint is gone)
            inserted_id = no_ck.id
    finally:
        # Clean up the 'bogus' row
        async with SessionLocal() as s:
            if inserted_id is not None:
                await s.execute(delete(Book).where(Book.id == inserted_id))
            # Restore the constraint
            await s.execute(
                text(
                    "ALTER TABLE books ADD CONSTRAINT ck_books_toc_validation "
                    "CHECK (toc_validation IS NULL OR toc_validation IN ('verified','mismatch','skipped'))"
                )
            )
            await s.commit()

    # Now the constraint is back — 'bogus' must be rejected again
    async with SessionLocal() as s:
        bad = Book(
            subject="math-algebra",
            original_filename="tv_bite2.pdf",
            content_sha256=_sha("bite2"),
            file_size_bytes=1,
            status="toc_ready",
            toc_validation="bogus",
        )
        s.add(bad)
        with pytest.raises(IntegrityError):
            await s.commit()
        await s.rollback()
