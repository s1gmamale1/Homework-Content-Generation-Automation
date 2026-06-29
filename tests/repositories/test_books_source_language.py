"""Real-DB: source_language column on books table — CHECK constraint + default.

Verifies:
- A book inserted without source_language defaults to "uz".
- Books with "ru" and "en" are accepted.
- A book with "fr" raises IntegrityError (CHECK constraint bites).
- Bite-proof: dropping the constraint lets "fr" through; restoring it blocks again.

Skipped unless RUN_DB_INTEGRATION=1 + DATABASE_URL are set.
"""
from __future__ import annotations

import os

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
import hashlib, time

def _sha(tag: str) -> str:
    return hashlib.sha256(f"srclang-{tag}-{time.time_ns()}".encode()).hexdigest()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_source_language_default_is_uz():
    """Insert a Book without setting source_language; it must default to 'uz'."""
    from app.db import SessionLocal
    from app.models.book import Book

    async with SessionLocal() as s:
        book = Book(
            subject="math-algebra",
            original_filename="sl_default.pdf",
            content_sha256=_sha("default"),
            file_size_bytes=1,
            status="toc_ready",
        )
        s.add(book)
        await s.commit()
        await s.refresh(book)
        book_id = book.id
        assert book.source_language == "uz", (
            f"expected 'uz', got {book.source_language!r}"
        )

    async with SessionLocal() as s:
        await s.execute(delete(Book).where(Book.id == book_id))
        await s.commit()


@pytest.mark.asyncio
async def test_source_language_ru_accepted():
    """source_language='ru' must be accepted."""
    from app.db import SessionLocal
    from app.models.book import Book

    async with SessionLocal() as s:
        book = Book(
            subject="math-algebra",
            original_filename="sl_ru.pdf",
            content_sha256=_sha("ru"),
            file_size_bytes=1,
            status="toc_ready",
            source_language="ru",
        )
        s.add(book)
        await s.commit()
        book_id = book.id

    async with SessionLocal() as s:
        await s.execute(delete(Book).where(Book.id == book_id))
        await s.commit()


@pytest.mark.asyncio
async def test_source_language_en_accepted():
    """source_language='en' must be accepted."""
    from app.db import SessionLocal
    from app.models.book import Book

    async with SessionLocal() as s:
        book = Book(
            subject="math-algebra",
            original_filename="sl_en.pdf",
            content_sha256=_sha("en"),
            file_size_bytes=1,
            status="toc_ready",
            source_language="en",
        )
        s.add(book)
        await s.commit()
        book_id = book.id

    async with SessionLocal() as s:
        await s.execute(delete(Book).where(Book.id == book_id))
        await s.commit()


@pytest.mark.asyncio
async def test_source_language_invalid_rejected():
    """source_language='fr' must raise IntegrityError (CHECK constraint)."""
    from app.db import SessionLocal
    from app.models.book import Book

    async with SessionLocal() as s:
        bad = Book(
            subject="math-algebra",
            original_filename="sl_fr.pdf",
            content_sha256=_sha("fr"),
            file_size_bytes=1,
            status="toc_ready",
            source_language="fr",
        )
        s.add(bad)
        with pytest.raises(IntegrityError):
            await s.commit()
        await s.rollback()


@pytest.mark.asyncio
async def test_source_language_bite_proof():
    """Bite-prove: drop the CHECK constraint → 'fr' is accepted; restore → 'fr' rejected again."""
    from app.db import SessionLocal
    from app.models.book import Book

    # Drop the constraint so 'fr' should pass
    async with SessionLocal() as s:
        await s.execute(
            text("ALTER TABLE books DROP CONSTRAINT IF EXISTS ck_books_source_language")
        )
        await s.commit()

    inserted_id = None
    try:
        async with SessionLocal() as s:
            no_ck = Book(
                subject="math-algebra",
                original_filename="sl_bite.pdf",
                content_sha256=_sha("bite"),
                file_size_bytes=1,
                status="toc_ready",
                source_language="fr",
            )
            s.add(no_ck)
            await s.commit()          # must NOT raise (constraint is gone)
            inserted_id = no_ck.id
    finally:
        # Clean up the 'fr' row
        async with SessionLocal() as s:
            if inserted_id is not None:
                await s.execute(delete(Book).where(Book.id == inserted_id))
            # Restore the constraint
            await s.execute(
                text(
                    "ALTER TABLE books ADD CONSTRAINT ck_books_source_language "
                    "CHECK (source_language IN ('uz','ru','en'))"
                )
            )
            await s.commit()

    # Now the constraint is back — 'fr' must be rejected again
    async with SessionLocal() as s:
        bad = Book(
            subject="math-algebra",
            original_filename="sl_bite2.pdf",
            content_sha256=_sha("bite2"),
            file_size_bytes=1,
            status="toc_ready",
            source_language="fr",
        )
        s.add(bad)
        with pytest.raises(IntegrityError):
            await s.commit()
        await s.rollback()
