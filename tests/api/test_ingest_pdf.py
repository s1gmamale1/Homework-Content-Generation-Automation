from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4
import pytest
import app.api.v1.books as books_api


@pytest.mark.asyncio
async def test_ingest_pdf_new_book_returns_plain_bookout():
    session = AsyncMock()
    book = SimpleNamespace(id=uuid4(), status="uploading")
    with patch.object(books_api.books_repo, "find_ready_by_hash", AsyncMock(return_value=None)), \
         patch.object(books_api.books_repo, "create", AsyncMock(return_value=book)), \
         patch.object(books_api, "BookOut") as MockOut, \
         patch.object(books_api.toc_extractor, "run", AsyncMock()), \
         patch.object(books_api.storage, "book_pdf_path") as MockPdfPath:
        MockOut.model_validate.return_value = "PLAIN_OUT"
        MockPdfPath.return_value = SimpleNamespace(
            parent=SimpleNamespace(mkdir=lambda **k: None), write_bytes=lambda b: None)
        out = await books_api.ingest_pdf(session, body=b"%PDF-1.4 x", subject="biology",
                                         grade="9", filename="b.pdf")
    assert out == "PLAIN_OUT"


@pytest.mark.asyncio
async def test_ingest_pdf_dedup_hit_returns_with_toc():
    session = AsyncMock()
    existing = SimpleNamespace(id=uuid4())
    with patch.object(books_api.books_repo, "find_ready_by_hash", AsyncMock(return_value=existing)), \
         patch.object(books_api, "_book_out_with_toc", AsyncMock(return_value="WITH_TOC")) as wt:
        out = await books_api.ingest_pdf(session, body=b"%PDF-1.4 x", subject="biology",
                                         grade="9", filename="b.pdf")
    assert out == "WITH_TOC"
    wt.assert_awaited_once()


@pytest.mark.asyncio
async def test_ingest_pdf_rejects_empty_and_oversize():
    session = AsyncMock()
    with pytest.raises(books_api.HTTPException):
        await books_api.ingest_pdf(session, body=b"", subject="biology", grade=None, filename="b.pdf")
