import pytest
from uuid import uuid4
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient
from main import app
from app.auth import get_current_user
from app.schemas import BookOut
import app.services.notion_fetch as nf

client = TestClient(app)


@pytest.fixture(autouse=True)
def _auth_override():
    app.dependency_overrides[get_current_user] = lambda: {"user": "test"}
    yield
    app.dependency_overrides.pop(get_current_user, None)


def test_from_notion_unsupported_subject_422():
    with patch("app.api.v1.books.NotionClientWrapper"), \
         patch("app.api.v1.books._notion_subject_title", return_value="Geografiya"):
        r = client.post("/api/v1/books/from-notion",
                        json={"subject_page_id": "geo", "grade": "9"})
    assert r.status_code == 422


def test_from_notion_oversize_422():
    with patch("app.api.v1.books.NotionClientWrapper"), \
         patch("app.api.v1.books._notion_subject_title", return_value="Algebra"), \
         patch("app.api.v1.books.notion_fetch.download_textbook",
               side_effect=nf.TextbookTooLarge("60.0 MB > 50 MB")):
        r = client.post("/api/v1/books/from-notion",
                        json={"subject_page_id": "alg", "grade": "9"})
    assert r.status_code == 422 and "50 MB" in r.text
    # fetch-1: de-conflated guidance — the cap is an ingest guard, not a model
    # limit, so point the operator at the real lever (MAX_FILE_MB) instead of the
    # stale, misleading "shrink and upload manually".
    assert "MAX_FILE_MB" in r.text
    assert "shrink and upload manually" not in r.text


def test_upload_oversize_413_points_at_cap_lever(monkeypatch):
    # fetch-1: oversize upload 413 must name MAX_FILE_MB as the lever, not just
    # say "too large". Shrink the cap to 1 MB and post a 2 MB body so we exercise
    # the reject path without building a real giant.
    from app.config import settings
    monkeypatch.setattr(settings, "max_file_mb", 1)
    body = b"%PDF-1.4\n" + b"0" * (2 * 1024 * 1024)
    r = client.post(
        "/api/v1/books",
        files={"file": ("big.pdf", body, "application/pdf")},
        data={"subject": "matematika"},
    )
    assert r.status_code == 413
    assert "MAX_FILE_MB" in r.text


def test_from_notion_happy_path_calls_ingest():
    # ingest_pdf is the response_model BookOut path, so the mock must return a
    # real BookOut (a bare dict fails FastAPI response validation -> 500).
    fake = BookOut(id=uuid4(), subject="math-algebra",
                   original_filename="alg.pdf", status="uploading")
    with patch("app.api.v1.books.NotionClientWrapper"), \
         patch("app.api.v1.books._notion_subject_title", return_value="Algebra"), \
         patch("app.api.v1.books.notion_fetch.download_textbook",
               return_value=(b"%PDF-1.4 x", "alg.pdf")), \
         patch("app.api.v1.books.ingest_pdf", AsyncMock(return_value=fake)) as ing:
        r = client.post("/api/v1/books/from-notion",
                        json={"subject_page_id": "alg", "grade": "9"})
    assert r.status_code == 201
    ing.assert_awaited_once()
    assert ing.await_args.kwargs["subject"] == "math-algebra"
