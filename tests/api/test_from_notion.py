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


def test_from_notion_threads_block_id_to_download_textbook():
    fake = BookOut(id=uuid4(), subject="math-algebra",
                   original_filename="alg.pdf", status="uploading")
    with patch("app.api.v1.books.NotionClientWrapper"), \
         patch("app.api.v1.books._notion_subject_title", return_value="Algebra"), \
         patch("app.api.v1.books.notion_fetch.download_textbook",
               return_value=(b"%PDF-1.4 x", "alg.pdf")) as dl, \
         patch("app.api.v1.books.ingest_pdf", AsyncMock(return_value=fake)):
        r = client.post("/api/v1/books/from-notion",
                        json={"subject_page_id": "alg", "grade": "9", "block_id": "p2"})
    assert r.status_code == 201
    dl.assert_called_once()
    assert dl.call_args.kwargs.get("block_id") == "p2" or dl.call_args.args[-1] == "p2"


def test_from_notion_without_block_id_defaults_to_none():
    fake = BookOut(id=uuid4(), subject="math-algebra",
                   original_filename="alg.pdf", status="uploading")
    with patch("app.api.v1.books.NotionClientWrapper"), \
         patch("app.api.v1.books._notion_subject_title", return_value="Algebra"), \
         patch("app.api.v1.books.notion_fetch.download_textbook",
               return_value=(b"%PDF-1.4 x", "alg.pdf")) as dl, \
         patch("app.api.v1.books.ingest_pdf", AsyncMock(return_value=fake)):
        r = client.post("/api/v1/books/from-notion",
                        json={"subject_page_id": "alg", "grade": "9"})
    assert r.status_code == 201
    assert dl.call_args.kwargs.get("block_id") is None


def test_from_notion_ambiguous_textbook_422_lists_candidates():
    # Structured detail (review fix, task 3): the FE (Task 6) consumes this as
    # JSON, not prose — {"error": "ambiguous_textbook", "message": ...,
    # "candidates": [{"block_id", "filename", "rank"}, ...]}.
    candidates = [
        {"page_id": "alg", "block_id": "p1", "filename": "algebra 1-qism.pdf", "rank": 0, "url": "u1"},
        {"page_id": "alg", "block_id": "p2", "filename": "algebra 2-qism.pdf", "rank": 0, "url": "u2"},
    ]
    with patch("app.api.v1.books.NotionClientWrapper"), \
         patch("app.api.v1.books._notion_subject_title", return_value="Algebra"), \
         patch("app.api.v1.books.notion_fetch.download_textbook",
               side_effect=nf.AmbiguousTextbook(candidates)):
        r = client.post("/api/v1/books/from-notion",
                        json={"subject_page_id": "alg", "grade": "9"})
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["error"] == "ambiguous_textbook"
    assert isinstance(detail["message"], str) and detail["message"]
    got = {(c["block_id"], c["filename"], c["rank"]) for c in detail["candidates"]}
    assert got == {("p1", "algebra 1-qism.pdf", 0), ("p2", "algebra 2-qism.pdf", 0)}


def test_from_notion_stale_block_id_422_names_block_id_distinct_from_empty_page():
    # Review fix (task 2): a stale/invalid block_id selector must NOT collapse
    # into the generic "this subject has no attached textbook" text — it must
    # name the offending block_id so the caller can tell "you picked a selector
    # that no longer exists" apart from "this page truly has nothing attached".
    with patch("app.api.v1.books.NotionClientWrapper"), \
         patch("app.api.v1.books._notion_subject_title", return_value="Algebra"), \
         patch("app.api.v1.books.notion_fetch.download_textbook",
               side_effect=nf.StaleSelector(
                   "block_id 'does-not-exist' not found among this page's 2 "
                   "textbook candidates (stale selector?)")):
        r = client.post("/api/v1/books/from-notion",
                        json={"subject_page_id": "alg", "grade": "9",
                              "block_id": "does-not-exist"})
    assert r.status_code == 422
    assert "does-not-exist" in r.text
    assert "2" in r.text
    assert "stale selector" in r.text.lower()
    assert "this subject has no attached textbook" not in r.text


def test_from_notion_truly_empty_page_keeps_generic_message():
    # Control: the plain NoTextbook path (zero candidates at all) must still
    # get the generic, non-block_id-specific message.
    with patch("app.api.v1.books.NotionClientWrapper"), \
         patch("app.api.v1.books._notion_subject_title", return_value="Algebra"), \
         patch("app.api.v1.books.notion_fetch.download_textbook",
               side_effect=nf.NoTextbook("alg")):
        r = client.post("/api/v1/books/from-notion",
                        json={"subject_page_id": "alg", "grade": "9"})
    assert r.status_code == 422
    assert r.json()["detail"] == "this subject has no attached textbook"
