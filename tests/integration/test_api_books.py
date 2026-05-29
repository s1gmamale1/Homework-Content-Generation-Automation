"""Integration tests for /api/v1/books endpoints.

Covers: upload validation, list, get, patch, delete, 404 paths.
TOC extraction and SSE stream are mocked to avoid real Gemini calls.
"""
import io
import uuid
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.asyncio


def _minimal_pdf_bytes() -> bytes:
    """1-byte placeholder — real PDF not needed for validation tests."""
    return b"%PDF-1.4 placeholder content for test"


def _multipart(filename: str = "test.pdf", subject: str = "biology", content: bytes | None = None):
    return {
        "file": (filename, io.BytesIO(content or _minimal_pdf_bytes()), "application/pdf"),
        "subject": (None, subject),
    }


class TestUploadBook:
    async def test_valid_upload_returns_201(self, client, auth_headers):
        with patch("app.api.v1.books.toc_extractor.run", new_callable=AsyncMock):
            response = await client.post(
                "/api/v1/books",
                files=_multipart(),
                headers=auth_headers,
            )
        assert response.status_code == 201
        body = response.json()
        assert "id" in body

    async def test_unknown_subject_returns_400(self, client, auth_headers):
        response = await client.post(
            "/api/v1/books",
            files=_multipart(subject="unknown-subject-xyz"),
            headers=auth_headers,
        )
        assert response.status_code == 400
        assert "subject" in response.json()["detail"].lower()

    async def test_empty_file_returns_400(self, client, auth_headers):
        response = await client.post(
            "/api/v1/books",
            files=_multipart(content=b""),
            headers=auth_headers,
        )
        assert response.status_code == 400

    async def test_requires_auth(self, client):
        response = await client.post(
            "/api/v1/books",
            files=_multipart(),
        )
        assert response.status_code == 401

    async def test_duplicate_upload_returns_existing_book(self, client, auth_headers):
        """Two uploads with same hash/subject return the same book ID."""
        content = b"%PDF-1.4 identical content"
        with patch("app.api.v1.books.toc_extractor.run", new_callable=AsyncMock):
            r1 = await client.post(
                "/api/v1/books",
                files=_multipart(content=content),
                headers=auth_headers,
            )
            r2 = await client.post(
                "/api/v1/books",
                files=_multipart(content=content),
                headers=auth_headers,
            )
        assert r1.status_code == 201
        assert r2.status_code == 200
        assert r1.json()["id"] == r2.json()["id"]

    async def test_file_too_large_returns_413(self, client, auth_headers):
        from app.config import settings
        oversized = b"x" * (settings.max_file_mb * 1024 * 1024 + 1)
        response = await client.post(
            "/api/v1/books",
            files=_multipart(content=oversized),
            headers=auth_headers,
        )
        assert response.status_code == 413


class TestListBooks:
    async def test_empty_list_returns_200(self, client, auth_headers):
        response = await client.get("/api/v1/books", headers=auth_headers)
        assert response.status_code == 200
        assert response.json() == []

    async def test_uploaded_book_appears_in_list(self, client, auth_headers):
        with patch("app.api.v1.books.toc_extractor.run", new_callable=AsyncMock):
            await client.post(
                "/api/v1/books",
                files=_multipart(),
                headers=auth_headers,
            )
        response = await client.get("/api/v1/books", headers=auth_headers)
        assert response.status_code == 200
        assert len(response.json()) >= 1

    async def test_requires_auth(self, client):
        response = await client.get("/api/v1/books")
        assert response.status_code == 401


class TestGetBook:
    async def test_returns_book_by_id(self, client, auth_headers):
        with patch("app.api.v1.books.toc_extractor.run", new_callable=AsyncMock):
            r = await client.post(
                "/api/v1/books",
                files=_multipart(),
                headers=auth_headers,
            )
        book_id = r.json()["id"]
        response = await client.get(f"/api/v1/books/{book_id}", headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["id"] == book_id

    async def test_nonexistent_book_returns_404(self, client, auth_headers):
        response = await client.get(
            f"/api/v1/books/{uuid.uuid4()}", headers=auth_headers
        )
        assert response.status_code == 404


class TestPatchBook:
    async def test_can_update_filename(self, client, auth_headers):
        with patch("app.api.v1.books.toc_extractor.run", new_callable=AsyncMock):
            r = await client.post(
                "/api/v1/books",
                files=_multipart(),
                headers=auth_headers,
            )
        book_id = r.json()["id"]
        response = await client.patch(
            f"/api/v1/books/{book_id}",
            json={"original_filename": "renamed.pdf"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["original_filename"] == "renamed.pdf"

    async def test_patch_nonexistent_book_returns_404(self, client, auth_headers):
        response = await client.patch(
            f"/api/v1/books/{uuid.uuid4()}",
            json={"original_filename": "x.pdf"},
            headers=auth_headers,
        )
        assert response.status_code == 404

    async def test_patch_with_invalid_subject_returns_400(self, client, auth_headers):
        with patch("app.api.v1.books.toc_extractor.run", new_callable=AsyncMock):
            r = await client.post(
                "/api/v1/books",
                files=_multipart(),
                headers=auth_headers,
            )
        book_id = r.json()["id"]
        response = await client.patch(
            f"/api/v1/books/{book_id}",
            json={"subject": "invalid-subject-xyz"},
            headers=auth_headers,
        )
        assert response.status_code == 400


class TestDeleteBook:
    async def test_delete_returns_204(self, client, auth_headers):
        with patch("app.api.v1.books.toc_extractor.run", new_callable=AsyncMock):
            r = await client.post(
                "/api/v1/books",
                files=_multipart(),
                headers=auth_headers,
            )
        book_id = r.json()["id"]
        response = await client.delete(f"/api/v1/books/{book_id}", headers=auth_headers)
        assert response.status_code == 204

    async def test_delete_nonexistent_book_returns_404(self, client, auth_headers):
        response = await client.delete(
            f"/api/v1/books/{uuid.uuid4()}", headers=auth_headers
        )
        assert response.status_code == 404

    async def test_deleted_book_not_in_list(self, client, auth_headers):
        with patch("app.api.v1.books.toc_extractor.run", new_callable=AsyncMock):
            r = await client.post(
                "/api/v1/books",
                files=_multipart(),
                headers=auth_headers,
            )
        book_id = r.json()["id"]
        await client.delete(f"/api/v1/books/{book_id}", headers=auth_headers)
        list_r = await client.get("/api/v1/books", headers=auth_headers)
        ids = [b["id"] for b in list_r.json()]
        assert book_id not in ids
