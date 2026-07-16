import pytest
from uuid import uuid4
from unittest.mock import patch, AsyncMock, MagicMock
import httpx
from notion_client.errors import APIResponseError
from fastapi.testclient import TestClient
from main import app
from app.auth import get_current_user
from app.config import settings
from app.schemas import BookOut
import app.services.notion_fetch as nf

client = TestClient(app)


@pytest.fixture(autouse=True)
def _auth_override():
    app.dependency_overrides[get_current_user] = lambda: {"user": "test"}
    yield
    app.dependency_overrides.pop(get_current_user, None)


def _api_error(status: int, code: str = "validation_error", message: str = "boom") -> APIResponseError:
    """Build a real notion_client APIResponseError (the SDK's not-found/other-
    error shape) for exercising the route's 404/502 mapping."""
    return APIResponseError(code, status, message, httpx.Headers(), "")


# Patches `verify_page_ancestry` as a no-op for tests that aren't specifically
# exercising ancestry validation (mirrors how `download_textbook` is already
# patched at the notion_fetch function boundary elsewhere in this file) —
# every existing test below supplies an explicit `grade`, which now triggers
# the ancestry walk; these tests care about behavior downstream of it.
def _no_ancestry():
    return patch("app.api.v1.books.notion_fetch.verify_page_ancestry")


def test_from_notion_unsupported_subject_422():
    with patch("app.api.v1.books.NotionClientWrapper"), \
         patch("app.api.v1.books._notion_subject_title", return_value="Geografiya"), \
         _no_ancestry():
        r = client.post("/api/v1/books/from-notion",
                        json={"subject_page_id": "geo", "grade": "9"})
    assert r.status_code == 422


def test_from_notion_oversize_422():
    with patch("app.api.v1.books.NotionClientWrapper"), \
         patch("app.api.v1.books._notion_subject_title", return_value="Algebra"), \
         _no_ancestry(), \
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
         _no_ancestry(), \
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
         _no_ancestry(), \
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
         _no_ancestry(), \
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
         _no_ancestry(), \
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
         _no_ancestry(), \
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
         _no_ancestry(), \
         patch("app.api.v1.books.notion_fetch.download_textbook",
               side_effect=nf.NoTextbook("alg")):
        r = client.post("/api/v1/books/from-notion",
                        json={"subject_page_id": "alg", "grade": "9"})
    assert r.status_code == 422
    assert r.json()["detail"] == "this subject has no attached textbook"


# ---------------------------------------------------------------------------
# BE-19 task 4: grade/subject_page_id Pydantic validation, controlled 404/502
# for Notion API errors, and ancestry validation (verify_page_ancestry).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_grade", ["banana", "", "12", "0", "sinf9"])
def test_from_notion_invalid_grade_string_422(bad_grade):
    # RED (pre-fix): ANY string was accepted and passed straight through to
    # ingest_pdf — no Notion I/O should even be attempted once this validates.
    with patch("app.api.v1.books.NotionClientWrapper") as MockClient:
        r = client.post("/api/v1/books/from-notion",
                        json={"subject_page_id": "alg", "grade": bad_grade})
    assert r.status_code == 422
    MockClient.assert_not_called()


def test_from_notion_grade_none_stays_legal():
    # Explicit values are validated; omitting grade entirely (None) keeps its
    # pre-existing legal, filename-derived-default behavior (BE-19 task 4 scope
    # is validating explicit grade strings, not making grade mandatory).
    fake = BookOut(id=uuid4(), subject="math-algebra",
                   original_filename="alg.pdf", status="uploading")
    with patch("app.api.v1.books.NotionClientWrapper"), \
         patch("app.api.v1.books._notion_subject_title", return_value="Algebra"), \
         _no_ancestry() as ancestry, \
         patch("app.api.v1.books.notion_fetch.download_textbook",
               return_value=(b"%PDF-1.4 x", "alg.pdf")), \
         patch("app.api.v1.books.ingest_pdf", AsyncMock(return_value=fake)):
        r = client.post("/api/v1/books/from-notion", json={"subject_page_id": "alg"})
    assert r.status_code == 201
    # No grade given -> nothing to check ancestry against; the walk is skipped.
    ancestry.assert_not_called()


def test_from_notion_empty_subject_page_id_422():
    with patch("app.api.v1.books.NotionClientWrapper") as MockClient:
        r = client.post("/api/v1/books/from-notion",
                        json={"subject_page_id": "", "grade": "9"})
    assert r.status_code == 422
    MockClient.assert_not_called()


def test_from_notion_missing_page_404():
    # RED (pre-fix): a not-found page fell through to an unhandled 500.
    with patch("app.api.v1.books.NotionClientWrapper"), \
         patch("app.api.v1.books._notion_subject_title",
               side_effect=_api_error(404, "object_not_found", "Could not find page")):
        r = client.post("/api/v1/books/from-notion",
                        json={"subject_page_id": "does-not-exist", "grade": "9"})
    assert r.status_code == 404
    assert "does-not-exist" in r.text


def test_from_notion_other_api_error_502():
    with patch("app.api.v1.books.NotionClientWrapper"), \
         patch("app.api.v1.books._notion_subject_title",
               side_effect=_api_error(503, "service_unavailable", "Notion is down")):
        r = client.post("/api/v1/books/from-notion",
                        json={"subject_page_id": "alg", "grade": "9"})
    assert r.status_code == 502


def _ancestry_ok_client():
    """A client mock whose parent chain satisfies grade=9/uz under
    settings.notion_lessons_root — models the genuine happy-path shape (page
    -> language container -> grade page -> lessons root) that the FE's
    available_languages-sourced page ids always produce."""
    inst = MagicMock()
    parents = {"alg": "uz_cont", "uz_cont": "g9", "g9": settings.notion_lessons_root}
    titles = {"uz_cont": "9 - sinf", "g9": "9-sinf"}
    inst.get_page_parent.side_effect = lambda pid: parents.get(pid)
    inst.get_page_title.side_effect = lambda pid: titles.get(pid, "")
    inst.get_child_pages.side_effect = lambda pid: (
        [{"id": "uz_cont", "title": "9 - sinf"}] if pid == "g9" else []
    )
    return inst


def test_from_notion_happy_path_ancestry_chain_201():
    # The REAL verify_page_ancestry runs here (not patched) — models the exact
    # chain shape the FE's normal flow produces: page ids come from
    # available_languages, which are genuine subject pages under grade/language
    # containers, so this must PASS for the normal UI flow.
    fake = BookOut(id=uuid4(), subject="math-algebra",
                   original_filename="alg.pdf", status="uploading")
    with patch("app.api.v1.books.NotionClientWrapper", return_value=_ancestry_ok_client()), \
         patch("app.api.v1.books._notion_subject_title", return_value="Algebra"), \
         patch("app.api.v1.books.notion_fetch.download_textbook",
               return_value=(b"%PDF-1.4 x", "alg.pdf")), \
         patch("app.api.v1.books.ingest_pdf", AsyncMock(return_value=fake)) as ing:
        r = client.post("/api/v1/books/from-notion",
                        json={"subject_page_id": "alg", "grade": "9"})
    assert r.status_code == 201
    ing.assert_awaited_once()


def test_from_notion_ancestry_wrong_grade_422():
    # Chain genuinely belongs to grade 9, caller asks for grade 7 -> 422 naming
    # what failed, BEFORE any bytes are downloaded.
    inst = _ancestry_ok_client()
    with patch("app.api.v1.books.NotionClientWrapper", return_value=inst), \
         patch("app.api.v1.books._notion_subject_title", return_value="Algebra"), \
         patch("app.api.v1.books.notion_fetch.download_textbook") as dl:
        r = client.post("/api/v1/books/from-notion",
                        json={"subject_page_id": "alg", "grade": "7"})
    assert r.status_code == 422
    assert "grade 7" in r.text and "uz" in r.text
    dl.assert_not_called()  # fail fast: no wasted download


def test_from_notion_ancestry_wrong_language_container_422():
    # Page's real parent is the ru container, request asks for uz -> 422.
    inst = MagicMock()
    parents = {"alg_ru": "ru_cont", "ru_cont": "g9", "g9": settings.notion_lessons_root}
    titles = {"ru_cont": "9 - класс", "g9": "9-sinf"}
    inst.get_page_parent.side_effect = lambda pid: parents.get(pid)
    inst.get_page_title.side_effect = lambda pid: titles.get(pid, "")
    inst.get_child_pages.side_effect = lambda pid: (
        [{"id": "ru_cont", "title": "9 - класс"}] if pid == "g9" else []
    )
    with patch("app.api.v1.books.NotionClientWrapper", return_value=inst), \
         patch("app.api.v1.books._notion_subject_title", return_value="Algebra"), \
         patch("app.api.v1.books.notion_fetch.download_textbook") as dl:
        r = client.post("/api/v1/books/from-notion",
                        json={"subject_page_id": "alg_ru", "grade": "9", "language": "uz"})
    assert r.status_code == 422
    dl.assert_not_called()


def test_from_notion_ancestry_duplicate_containers_422_names_both():
    inst = MagicMock()
    parents = {"alg": "uz_cont_a", "uz_cont_a": "g9", "g9": settings.notion_lessons_root}
    titles = {"uz_cont_a": "9 - sinf", "g9": "9-sinf"}
    inst.get_page_parent.side_effect = lambda pid: parents.get(pid)
    inst.get_page_title.side_effect = lambda pid: titles.get(pid, "")
    inst.get_child_pages.side_effect = lambda pid: (
        [
            {"id": "uz_cont_a", "title": "9 - sinf"},
            {"id": "uz_cont_b", "title": "9 - sinf (copy)"},
        ] if pid == "g9" else []
    )
    with patch("app.api.v1.books.NotionClientWrapper", return_value=inst), \
         patch("app.api.v1.books._notion_subject_title", return_value="Algebra"), \
         patch("app.api.v1.books.notion_fetch.download_textbook") as dl:
        r = client.post("/api/v1/books/from-notion",
                        json={"subject_page_id": "alg", "grade": "9"})
    assert r.status_code == 422
    assert "uz_cont_a" in r.text and "uz_cont_b" in r.text
    dl.assert_not_called()
