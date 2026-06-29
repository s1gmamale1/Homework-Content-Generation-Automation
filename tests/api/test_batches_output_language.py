"""Real-DB: GET /jobs/batches exposes each batch's output_language (Monitor language tabs).

RUN_DB_INTEGRATION=1 required (real Postgres via scratch DB recipe).
"""
from __future__ import annotations

import os

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)

_HDR = {"Authorization": "Bearer 123"}


@pytest.mark.asyncio
async def test_batches_list_exposes_output_language():
    from main import app
    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.batch import Batch
    from app.repositories import batches as batches_repo

    async with SessionLocal() as s:
        book = Book(subject="math-algebra", original_filename="x.pdf",
                    content_sha256="z" * 64, file_size_bytes=1, status="toc_ready")
        s.add(book)
        await s.flush()
        await batches_repo.get_or_create_for_book(
            s, book_id=book.id, subject="math-algebra", grade="8",
            provider="gemini", model="gemini-2.5-pro", transport="api",
            output_language="en")
        await s.commit()
        book_id = book.id

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.get("/api/v1/jobs/batches", headers=_HDR)
        assert r.status_code == 200, r.text
        mine = [b for b in r.json()["batches"] if b["book_id"] == str(book_id)]
        assert mine, "seeded batch not present in list"
        assert mine[0]["output_language"] == "en", (
            f"expected output_language='en', got {mine[0].get('output_language')!r}"
        )
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(Batch).where(Batch.book_id == book_id))
            await s.execute(delete(Book).where(Book.id == book_id))
            await s.commit()
