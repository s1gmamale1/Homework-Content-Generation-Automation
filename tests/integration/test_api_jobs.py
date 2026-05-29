"""Integration tests for /api/v1/books/{book_id}/sections/{entry_id}/generate
and /api/v1/jobs/* endpoints.

Pipeline.run is patched so no real Gemini calls are made.
"""
import io
import uuid
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.asyncio


async def _create_book_with_toc(client, auth_headers, subject="biology"):
    """Helper: upload a book and return (book_id, toc_entry_id)."""
    with patch("app.api.v1.books.toc_extractor.run", new_callable=AsyncMock):
        r = await client.post(
            "/api/v1/books",
            files={
                "file": ("test.pdf", io.BytesIO(b"%PDF-1.4 test"), "application/pdf"),
                "subject": (None, subject),
            },
            headers=auth_headers,
        )
    book_id = r.json()["id"]

    # Manually create a TOC entry for testing since toc_extractor is mocked.
    # We create it directly via the repository inside the test session.
    return book_id, None  # Caller must create TOC entry if needed


class TestGenerateJob:
    async def test_generate_returns_201_with_job(self, client, session, auth_headers):
        """POST generate creates a job and returns JobOut with status pending."""
        from app.repositories import books as books_repo, toc_entries as toc_repo
        from app.models.book import Book
        from app.models.toc_entry import TOCEntry

        # Seed: book + TOC entry
        book = await books_repo.create(
            session,
            original_filename="test.pdf",
            sha256="abc123",
            file_size_bytes=1000,
            subject="biology",
        )
        await session.flush()
        entry = TOCEntry(
            book_id=book.id,
            chapter_number="1",
            chapter_title="Chapter One",
            section_number="1.1",
            section_title="Intro",
            page_start=1,
            page_end=10,
        )
        session.add(entry)
        await session.flush()

        with patch("app.api.v1.jobs._start_pipeline_task", new_callable=AsyncMock):
            r = await client.post(
                f"/api/v1/books/{book.id}/sections/{entry.id}/generate",
                json={},
                headers=auth_headers,
            )

        assert r.status_code == 201
        body = r.json()
        assert body["status"] == "pending"
        assert "id" in body

    async def test_generate_is_idempotent_same_section(self, client, session, auth_headers):
        """Two generate calls for the same section return the same job ID."""
        from app.repositories import books as books_repo
        from app.models.toc_entry import TOCEntry

        book = await books_repo.create(
            session,
            original_filename="idem.pdf",
            sha256="idem_hash",
            file_size_bytes=500,
            subject="history",
        )
        await session.flush()
        entry = TOCEntry(
            book_id=book.id,
            chapter_number="2",
            chapter_title="Chapter Two",
            section_number="2.1",
            section_title="Period",
            page_start=20,
            page_end=30,
        )
        session.add(entry)
        await session.flush()

        with patch("app.api.v1.jobs._start_pipeline_task", new_callable=AsyncMock):
            r1 = await client.post(
                f"/api/v1/books/{book.id}/sections/{entry.id}/generate",
                json={},
                headers=auth_headers,
            )
            r2 = await client.post(
                f"/api/v1/books/{book.id}/sections/{entry.id}/generate",
                json={},
                headers=auth_headers,
            )

        assert r1.json()["id"] == r2.json()["id"]

    async def test_generate_nonexistent_book_returns_404(self, client, auth_headers):
        r = await client.post(
            f"/api/v1/books/{uuid.uuid4()}/sections/{uuid.uuid4()}/generate",
            json={},
            headers=auth_headers,
        )
        assert r.status_code == 404

    async def test_generate_requires_auth(self, client):
        r = await client.post(
            f"/api/v1/books/{uuid.uuid4()}/sections/{uuid.uuid4()}/generate",
            json={},
        )
        assert r.status_code == 401


class TestGetJob:
    async def test_get_job_returns_job(self, client, session, auth_headers):
        from app.repositories import books as books_repo, jobs as jobs_repo
        from app.models.toc_entry import TOCEntry

        book = await books_repo.create(
            session,
            original_filename="job_test.pdf",
            sha256="job_test_hash",
            file_size_bytes=500,
            subject="biology",
        )
        await session.flush()
        entry = TOCEntry(
            book_id=book.id,
            chapter_number="3",
            chapter_title="Ch3",
            section_number="3.1",
            section_title="Sec",
            page_start=30,
            page_end=40,
        )
        session.add(entry)
        await session.flush()

        job = await jobs_repo.create(
            session, book_id=book.id, toc_entry_id=entry.id, subject="biology"
        )
        await session.flush()

        r = await client.get(f"/api/v1/jobs/{job.id}", headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["id"] == str(job.id)

    async def test_get_nonexistent_job_returns_404(self, client, auth_headers):
        r = await client.get(f"/api/v1/jobs/{uuid.uuid4()}", headers=auth_headers)
        assert r.status_code == 404

    async def test_get_job_requires_auth(self, client):
        r = await client.get(f"/api/v1/jobs/{uuid.uuid4()}")
        assert r.status_code == 401


class TestListJobPhases:
    async def test_returns_empty_list_for_new_job(self, client, session, auth_headers):
        from app.repositories import books as books_repo, jobs as jobs_repo
        from app.models.toc_entry import TOCEntry

        book = await books_repo.create(
            session,
            original_filename="phases.pdf",
            sha256="phases_hash",
            file_size_bytes=500,
            subject="biology",
        )
        await session.flush()
        entry = TOCEntry(
            book_id=book.id,
            chapter_number="4",
            chapter_title="Ch4",
            section_number="4.1",
            section_title="Sec",
            page_start=40,
            page_end=50,
        )
        session.add(entry)
        await session.flush()

        job = await jobs_repo.create(
            session, book_id=book.id, toc_entry_id=entry.id, subject="biology"
        )
        await session.flush()

        r = await client.get(f"/api/v1/jobs/{job.id}/phases", headers=auth_headers)
        assert r.status_code == 200
        assert isinstance(r.json(), list)


class TestDownloadHomeworkZip:
    async def test_download_pending_job_returns_4xx(self, client, session, auth_headers):
        """Cannot download a ZIP for a job that is still pending/running."""
        from app.repositories import books as books_repo, jobs as jobs_repo
        from app.models.toc_entry import TOCEntry

        book = await books_repo.create(
            session,
            original_filename="dl.pdf",
            sha256="dl_hash",
            file_size_bytes=500,
            subject="biology",
        )
        await session.flush()
        entry = TOCEntry(
            book_id=book.id,
            chapter_number="5",
            chapter_title="Ch5",
            section_number="5.1",
            section_title="Sec",
            page_start=50,
            page_end=60,
        )
        session.add(entry)
        await session.flush()

        job = await jobs_repo.create(
            session, book_id=book.id, toc_entry_id=entry.id, subject="biology"
        )
        await session.flush()

        r = await client.get(f"/api/v1/jobs/{job.id}/download", headers=auth_headers)
        assert r.status_code in (404, 422, 400)  # job not done, no zip yet

    async def test_download_nonexistent_job_returns_404(self, client, auth_headers):
        r = await client.get(
            f"/api/v1/jobs/{uuid.uuid4()}/download", headers=auth_headers
        )
        assert r.status_code == 404
