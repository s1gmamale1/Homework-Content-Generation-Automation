import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import app.api.v1.books as books_mod


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def commit(self):
        return None


def _run_ingest(grade, filename):
    captured = {}

    async def fake_create(session, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(id=uuid4(), **kwargs)

    with patch.object(books_mod.books_repo, "find_ready_by_hash", AsyncMock(return_value=None)), \
         patch.object(books_mod.books_repo, "create", side_effect=fake_create), \
         patch.object(books_mod.BookOut, "model_validate", MagicMock(return_value="ok")), \
         patch("pathlib.Path.write_bytes", MagicMock()), \
         patch("pathlib.Path.mkdir", MagicMock()), \
         patch.object(books_mod.toc_extractor, "run", MagicMock(return_value=MagicMock())), \
         patch.object(books_mod.asyncio, "create_task", MagicMock()):
        asyncio.run(books_mod.ingest_pdf(
            _FakeSession(), body=b"%PDF-1.4 minimal",
            subject="math-algebra", grade=grade, filename=filename,
        ))
    return captured


def test_ingest_derives_grade_when_absent():
    captured = _run_ingest(None, "7-sinf_Algebra_2022.pdf")
    assert captured["grade"] == "7"


def test_ingest_keeps_explicit_grade():
    captured = _run_ingest("9", "7-sinf_Algebra_2022.pdf")
    assert captured["grade"] == "9"   # explicit wins; filename ignored


def test_book_out_exposes_grade():
    """fleet-ui: the launcher's Ready list shows the book grade — BookOut must
    carry it (the model has had `grade` since the Notion-archive work; the
    schema silently dropped it)."""
    from app.schemas.book import BookOut

    b = SimpleNamespace(
        id=uuid4(), subject="history", original_filename="7-sinf_x.pdf",
        status="toc_ready", error_message=None, gemini_file_expires_at=None,
        file_size_bytes=1, created_at=None, toc=None, grade="7",
    )
    out = BookOut.model_validate(b)
    assert out.grade == "7"
