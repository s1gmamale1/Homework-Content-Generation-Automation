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
import app.api.v1.books as books_api

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


def _dl(body: bytes = b"%PDF-1.4 x", filename: str = "alg.pdf",
        page_id: str = "alg", block_id: str = "b1") -> nf.DownloadedTextbook:
    """Build a `DownloadedTextbook` result for patching `download_textbook` —
    worklog 0144 task 2 changed its return shape from a bare (bytes, filename)
    tuple to this named result carrying the resolved candidate's own source
    page/block id."""
    return nf.DownloadedTextbook(
        body=body, filename=filename, source_page_id=page_id, source_block_id=block_id,
    )


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
               return_value=_dl()), \
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
               return_value=_dl()) as dl, \
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
               return_value=_dl()) as dl, \
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
    # pre-existing legal, filename-derived-default INGEST behavior (BE-19 task
    # 4 scope is validating explicit grade strings, not making grade
    # mandatory). BE-19 merge-gate fix 3: the ancestry WALK itself now always
    # runs (grade=None only downgrades its grade-NUMBER check, structurally —
    # see verify_page_ancestry) so a direct API caller can't bypass validation
    # entirely by omitting grade; this test now asserts the walk IS invoked
    # (with grade=None threaded through), not skipped.
    fake = BookOut(id=uuid4(), subject="math-algebra",
                   original_filename="alg.pdf", status="uploading")
    with patch("app.api.v1.books.NotionClientWrapper"), \
         patch("app.api.v1.books._notion_subject_title", return_value="Algebra"), \
         _no_ancestry() as ancestry, \
         patch("app.api.v1.books.notion_fetch.download_textbook",
               return_value=_dl()), \
         patch("app.api.v1.books.ingest_pdf", AsyncMock(return_value=fake)):
        r = client.post("/api/v1/books/from-notion", json={"subject_page_id": "alg"})
    assert r.status_code == 201
    ancestry.assert_called_once()
    assert ancestry.call_args.kwargs.get("grade") is None


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
    # Grade PAGE title uses the LIVE Notion shape "N Grade" (verified via
    # read-only crawl of the real workspace); the uz language CONTAINER one
    # level down keeps the separate "N - sinf" convention, untouched.
    titles = {"uz_cont": "9 - sinf", "g9": "9 Grade"}
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
               return_value=_dl()), \
         patch("app.api.v1.books.ingest_pdf", AsyncMock(return_value=fake)) as ing:
        r = client.post("/api/v1/books/from-notion",
                        json={"subject_page_id": "alg", "grade": "9"})
    assert r.status_code == 201
    ing.assert_awaited_once()


def test_from_notion_happy_path_legacy_sinf_grade_title_201():
    # BE-19 live-acceptance fix: keep accepting the legacy/doc "N-sinf" grade
    # PAGE title too (robustness against a future rename back), even though
    # the live workspace uses "N Grade" (covered by the test above).
    fake = BookOut(id=uuid4(), subject="math-algebra",
                   original_filename="alg.pdf", status="uploading")
    inst = MagicMock()
    parents = {"alg": "uz_cont", "uz_cont": "g7", "g7": settings.notion_lessons_root}
    titles = {"uz_cont": "7 - sinf", "g7": "7-sinf"}
    inst.get_page_parent.side_effect = lambda pid: parents.get(pid)
    inst.get_page_title.side_effect = lambda pid: titles.get(pid, "")
    inst.get_child_pages.side_effect = lambda pid: (
        [{"id": "uz_cont", "title": "7 - sinf"}] if pid == "g7" else []
    )
    with patch("app.api.v1.books.NotionClientWrapper", return_value=inst), \
         patch("app.api.v1.books._notion_subject_title", return_value="Algebra"), \
         patch("app.api.v1.books.notion_fetch.download_textbook",
               return_value=_dl()), \
         patch("app.api.v1.books.ingest_pdf", AsyncMock(return_value=fake)) as ing:
        r = client.post("/api/v1/books/from-notion",
                        json={"subject_page_id": "alg", "grade": "7"})
    assert r.status_code == 201
    ing.assert_awaited_once()


def test_from_notion_child_page_hosted_pdf_prepares_via_subject_page_id(monkeypatch):
    # BE-19 final-review critical fix, regression pin: FE part candidates can
    # carry a CHILD page's id for a child_page-hosted PDF, but
    # verify_page_ancestry requires the submitted page's DIRECT parent to be
    # the language container — a child page's parent is the SUBJECT page, not
    # the container, so submitting the child id 422s at hop 1 (the audit's
    # headline finding). The fixed contract: the FE always submits the
    # SUBJECT page id; `block_id` alone selects the file, matched by
    # `download_textbook` across `textbook_candidates`' flattened list
    # regardless of which page the PDF physically lives on.
    #
    # `download_textbook`/`textbook_candidates` run for REAL here (not
    # mocked) — only the Notion parent-chain calls and the httpx GET are
    # stubbed — so this pins the whole contract end-to-end, not just one
    # function's unit behavior.
    fake = BookOut(id=uuid4(), subject="math-algebra",
                   original_filename="alg.pdf", status="uploading")
    inst = MagicMock()
    parents = {"alg": "uz_cont", "uz_cont": "g9", "g9": settings.notion_lessons_root}
    titles = {"uz_cont": "9 - sinf", "g9": "9 Grade"}
    inst.get_page_parent.side_effect = lambda pid: parents.get(pid)
    inst.get_page_title.side_effect = lambda pid: titles.get(pid, "")
    inst.get_child_pages.side_effect = lambda pid: (
        [{"id": "uz_cont", "title": "9 - sinf"}] if pid == "g9" else []
    )
    # The SUBJECT page ("alg") hosts a single child_page part ("cp1"); the
    # textbook PDF block lives ON that child page, not directly on "alg" —
    # the real nested-part shape covered by BE-19 task 3's descent tests.
    blocks_by_page = {
        "alg": [{"id": "cp1", "type": "child_page", "child_page": {"title": "1-qism"}}],
        "cp1": [{"id": "b1", "type": "pdf", "pdf": {"file": {"url": "http://x/childpart.pdf"}}}],
    }
    inst.get_block_children.side_effect = lambda pid: blocks_by_page.get(pid, [])

    class _Stream:
        headers = {"Content-Length": "19"}
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def raise_for_status(self): pass
        def read(self): return b"%PDF-1.4 childpart"

    class _HTTP:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def stream(self, method, url, follow_redirects=True): return _Stream()

    monkeypatch.setattr("app.services.notion_fetch.httpx.Client", lambda **k: _HTTP())

    with patch("app.api.v1.books.NotionClientWrapper", return_value=inst), \
         patch("app.api.v1.books._notion_subject_title", return_value="Algebra"), \
         patch("app.api.v1.books.ingest_pdf", AsyncMock(return_value=fake)) as ing:
        # SUBJECT page id ("alg") + the CHILD-hosted block_id ("b1") — never
        # the child page id ("cp1"), which would fail ancestry.
        r = client.post("/api/v1/books/from-notion",
                        json={"subject_page_id": "alg", "grade": "9", "block_id": "b1"})
    assert r.status_code == 201, r.text
    ing.assert_awaited_once()
    # Binding identity decision (worklog 0144 task 2): the link must be keyed
    # by the CHILD page's own id ("cp1"), not the submitted subject page id
    # ("alg") — Task 4's availability enrichment matches candidates by their
    # OWN (page_id, block_id) as the crawl reports them.
    assert ing.await_args.kwargs["notion_source"] == ("cp1", "b1")


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


def test_from_notion_ancestry_no_parent_chain_end_422():
    # Chain-end / no-parent (review fix, task 4): the submitted page's parent
    # isn't a page at all (a top-level page, or lives in a database) —
    # `get_page_parent` returns None at hop 1. Must 422 naming exactly that,
    # not fall through to some other branch or crash.
    inst = MagicMock()
    inst.get_page_parent.side_effect = lambda pid: None
    inst.get_page_title.side_effect = lambda pid: ""
    inst.get_child_pages.side_effect = lambda pid: []
    with patch("app.api.v1.books.NotionClientWrapper", return_value=inst), \
         patch("app.api.v1.books._notion_subject_title", return_value="Algebra"), \
         patch("app.api.v1.books.notion_fetch.download_textbook") as dl:
        r = client.post("/api/v1/books/from-notion",
                        json={"subject_page_id": "alg", "grade": "9"})
    assert r.status_code == 422
    # Message text is specific to the hop-1 no-parent branch — a vacuous pass
    # (some other 422) can't slip through this assertion.
    assert "no parent page" in r.text
    assert "top-level page or lives in a database" in r.text
    dl.assert_not_called()  # fail fast: no wasted download


def test_from_notion_ancestry_root_mismatch_422():
    # Root-mismatch (review fix, task 4): the chain fully resolves (container
    # matches, grade matches) but the grade page's parent is NOT
    # settings.notion_lessons_root — a foreign/detached tree that happens to
    # look like grade 9 / uz. Must 422 naming the mismatched root.
    inst = MagicMock()
    parents = {"alg": "uz_cont", "uz_cont": "g9", "g9": "some-other-root"}
    titles = {"uz_cont": "9 - sinf", "g9": "9-sinf"}
    inst.get_page_parent.side_effect = lambda pid: parents.get(pid)
    inst.get_page_title.side_effect = lambda pid: titles.get(pid, "")
    inst.get_child_pages.side_effect = lambda pid: (
        [{"id": "uz_cont", "title": "9 - sinf"}] if pid == "g9" else []
    )
    assert "some-other-root" != settings.notion_lessons_root
    with patch("app.api.v1.books.NotionClientWrapper", return_value=inst), \
         patch("app.api.v1.books._notion_subject_title", return_value="Algebra"), \
         patch("app.api.v1.books.notion_fetch.download_textbook") as dl:
        r = client.post("/api/v1/books/from-notion",
                        json={"subject_page_id": "alg", "grade": "9"})
    assert r.status_code == 422
    # Message text is specific to the hop-3 root-mismatch branch.
    assert "some-other-root" in r.text
    assert "is not the lessons root" in r.text
    dl.assert_not_called()  # fail fast: no wasted download


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


# ---------------------------------------------------------------------------
# BE-19 merge-gate fix 3: grade-omitted ancestry bypass. Pre-fix, the route
# skipped `verify_page_ancestry` entirely whenever `grade` was omitted,
# letting a direct API caller (no grade in the payload) ingest ANY foreign
# page. These exercise the REAL `verify_page_ancestry` (not patched) so the
# whole route-to-function contract is pinned, not just one function's unit
# behavior.
# ---------------------------------------------------------------------------

def test_from_notion_grade_none_foreign_root_422():
    # RED pre-fix: grade omitted -> ancestry walk was skipped -> 201 even
    # though this chain's root is NOT settings.notion_lessons_root.
    inst = MagicMock()
    parents = {"alg": "uz_cont", "uz_cont": "g9", "g9": "some-foreign-root"}
    titles = {"uz_cont": "9 - sinf", "g9": "9 Grade"}
    inst.get_page_parent.side_effect = lambda pid: parents.get(pid)
    inst.get_page_title.side_effect = lambda pid: titles.get(pid, "")
    inst.get_child_pages.side_effect = lambda pid: (
        [{"id": "uz_cont", "title": "9 - sinf"}] if pid == "g9" else []
    )
    with patch("app.api.v1.books.NotionClientWrapper", return_value=inst), \
         patch("app.api.v1.books._notion_subject_title", return_value="Algebra"), \
         patch("app.api.v1.books.notion_fetch.download_textbook") as dl:
        r = client.post("/api/v1/books/from-notion", json={"subject_page_id": "alg"})
    assert r.status_code == 422
    assert "some-foreign-root" in r.text
    dl.assert_not_called()  # fail fast: no wasted download


def test_from_notion_grade_none_valid_chain_201():
    # grade omitted, but the chain genuinely resolves under the configured
    # lessons root -> still 201 (grade=None must not become a hard block on
    # legitimate requests, only a bypass-closer for foreign ones).
    fake = BookOut(id=uuid4(), subject="math-algebra",
                   original_filename="alg.pdf", status="uploading")
    inst = _ancestry_ok_client()
    with patch("app.api.v1.books.NotionClientWrapper", return_value=inst), \
         patch("app.api.v1.books._notion_subject_title", return_value="Algebra"), \
         patch("app.api.v1.books.notion_fetch.download_textbook",
               return_value=_dl()), \
         patch("app.api.v1.books.ingest_pdf", AsyncMock(return_value=fake)) as ing:
        r = client.post("/api/v1/books/from-notion", json={"subject_page_id": "alg"})
    assert r.status_code == 201
    ing.assert_awaited_once()


def test_from_notion_download_404_page_gone_mid_request():
    # Residual-500 fix (review, task 4): the subject page can be deleted or
    # unshared AFTER ancestry passed but during the download call (a race, not
    # a chain problem) — this must map to the same controlled 404, not escape
    # as a bare 500.
    with patch("app.api.v1.books.NotionClientWrapper"), \
         patch("app.api.v1.books._notion_subject_title", return_value="Algebra"), \
         _no_ancestry(), \
         patch("app.api.v1.books.notion_fetch.download_textbook",
               side_effect=_api_error(404, "object_not_found", "Could not find page")):
        r = client.post("/api/v1/books/from-notion",
                        json={"subject_page_id": "alg", "grade": "9"})
    assert r.status_code == 404
    assert "alg" in r.text


def test_from_notion_download_other_api_error_502():
    # Same race, non-404 Notion-side error -> 502, mirroring the existing
    # title-fetch/ancestry-step mapping.
    with patch("app.api.v1.books.NotionClientWrapper"), \
         patch("app.api.v1.books._notion_subject_title", return_value="Algebra"), \
         _no_ancestry(), \
         patch("app.api.v1.books.notion_fetch.download_textbook",
               side_effect=_api_error(503, "service_unavailable", "Notion is down")):
        r = client.post("/api/v1/books/from-notion",
                        json={"subject_page_id": "alg", "grade": "9"})
    assert r.status_code == 502


# ---------------------------------------------------------------------------
# Worklog 0144 task 2 — /from-notion upserts the resolved (page, block) ->
# book link, transactionally. `download_textbook` now returns a
# `DownloadedTextbook` (bytes, filename, source_page_id, source_block_id);
# the route threads (source_page_id, source_block_id) into `ingest_pdf` as
# `notion_source`, which performs the upsert INSIDE the same commit as book
# creation (fresh ingest) or before returning (dedup hit).
# ---------------------------------------------------------------------------


def test_from_notion_direct_candidate_threads_notion_source_into_ingest_pdf():
    # Direct (non-child-page) candidate: source_page_id == the submitted
    # subject page id, source_block_id == the resolved block.
    fake = BookOut(id=uuid4(), subject="math-algebra",
                   original_filename="alg.pdf", status="uploading")
    with patch("app.api.v1.books.NotionClientWrapper"), \
         patch("app.api.v1.books._notion_subject_title", return_value="Algebra"), \
         _no_ancestry(), \
         patch("app.api.v1.books.notion_fetch.download_textbook",
               return_value=_dl(page_id="alg", block_id="b1")), \
         patch("app.api.v1.books.ingest_pdf", AsyncMock(return_value=fake)) as ing:
        r = client.post("/api/v1/books/from-notion",
                        json={"subject_page_id": "alg", "grade": "9"})
    assert r.status_code == 201
    ing.assert_awaited_once()
    assert ing.await_args.kwargs["notion_source"] == ("alg", "b1")


def test_from_notion_guard_rejection_never_calls_ingest_pdf_so_no_link():
    # A rejection between download and ingest (here: the language guard) must
    # NOT call ingest_pdf at all — a guard rejection must never leave a link,
    # and linking only ever happens INSIDE ingest_pdf.
    with patch("app.api.v1.books.NotionClientWrapper"), \
         patch("app.api.v1.books._notion_subject_title", return_value="Algebra"), \
         _no_ancestry(), \
         patch("app.api.v1.books.notion_fetch.download_textbook",
               return_value=_dl(page_id="alg", block_id="b1")), \
         patch("app.api.v1.books.pdf_lang.detect_pdf_script", return_value="cyrillic"), \
         patch("app.api.v1.books.ingest_pdf", AsyncMock()) as ing:
        r = client.post("/api/v1/books/from-notion",
                        json={"subject_page_id": "alg", "grade": "9"})
    assert r.status_code == 422
    ing.assert_not_awaited()


# --- ingest_pdf unit tests: transactional linking -------------------------


def _pdf_path_stub(tmp_path):
    from types import SimpleNamespace
    return SimpleNamespace(
        parent=SimpleNamespace(mkdir=lambda **k: None),
        write_bytes=lambda b: None,
    )


@pytest.mark.asyncio
async def test_ingest_pdf_fresh_ingest_upserts_link_before_commit():
    """Fresh ingest + a notion_source: upsert_link must be awaited BEFORE
    session.commit() so book creation + link land in ONE commit (a
    route-level failure after commit could otherwise strand an
    extracting-but-unlinked book)."""
    from types import SimpleNamespace

    session = AsyncMock()
    book_id = uuid4()
    created_book = SimpleNamespace(id=book_id, status="uploading", source_language="uz")

    order: list[str] = []

    async def _fake_upsert_link(*a, **k):
        order.append("upsert_link")

    async def _fake_commit():
        order.append("commit")

    session.commit = _fake_commit

    with patch.object(books_api.books_repo, "find_ready_by_hash", AsyncMock(return_value=None)), \
         patch.object(books_api.books_repo, "create", AsyncMock(return_value=created_book)), \
         patch.object(books_api, "BookOut") as MockOut, \
         patch.object(books_api, "_start_toc_extraction"), \
         patch.object(books_api.storage, "book_pdf_path", return_value=_pdf_path_stub(None)), \
         patch.object(books_api, "notion_sources_repo") as mock_repo:
        mock_repo.upsert_link = AsyncMock(side_effect=_fake_upsert_link)
        MockOut.model_validate.return_value = "OUT"
        await books_api.ingest_pdf(
            session, body=b"%PDF-1.4 x", subject="matematika", grade="9",
            filename="alg.pdf", notion_source=("page-1", "block-1"),
        )

    mock_repo.upsert_link.assert_awaited_once_with(
        session, book_id=book_id, notion_page_id="page-1", notion_block_id="block-1"
    )
    assert order == ["upsert_link", "commit"]


@pytest.mark.asyncio
async def test_ingest_pdf_dedup_hit_upserts_and_repoints_link_before_returning():
    """A dedup hit (SHA already ingested) with a notion_source given must
    ALSO upsert+commit the link (re-pointing an existing link at the deduped
    book) BEFORE returning — not just the fresh-ingest path."""
    from types import SimpleNamespace

    session = AsyncMock()
    existing_id = uuid4()
    existing_book = SimpleNamespace(id=existing_id)
    fake_out = BookOut(id=existing_id, subject="matematika",
                        original_filename="alg.pdf", status="toc_ready")

    with patch.object(books_api.books_repo, "find_ready_by_hash",
                      AsyncMock(return_value=existing_book)), \
         patch.object(books_api, "_book_out_with_toc", AsyncMock(return_value=fake_out)), \
         patch.object(books_api, "notion_sources_repo") as mock_repo:
        mock_repo.upsert_link = AsyncMock()
        out = await books_api.ingest_pdf(
            session, body=b"%PDF-1.4 x", subject="matematika", grade="9",
            filename="alg.pdf", notion_source=("page-2", "block-2"),
        )

    mock_repo.upsert_link.assert_awaited_once_with(
        session, book_id=existing_id, notion_page_id="page-2", notion_block_id="block-2"
    )
    session.commit.assert_awaited_once()
    assert out.deduplicated is True


@pytest.mark.asyncio
async def test_ingest_pdf_plain_upload_no_notion_source_skips_link_fresh():
    """Plain upload (no Notion source) -> notion_source defaults to None ->
    zero behavior change: upsert_link is never called for a fresh ingest."""
    from types import SimpleNamespace

    session = AsyncMock()
    book_id = uuid4()
    created_book = SimpleNamespace(id=book_id, status="uploading", source_language="uz")

    with patch.object(books_api.books_repo, "find_ready_by_hash", AsyncMock(return_value=None)), \
         patch.object(books_api.books_repo, "create", AsyncMock(return_value=created_book)), \
         patch.object(books_api, "BookOut") as MockOut, \
         patch.object(books_api, "_start_toc_extraction"), \
         patch.object(books_api.storage, "book_pdf_path", return_value=_pdf_path_stub(None)), \
         patch.object(books_api, "notion_sources_repo") as mock_repo:
        mock_repo.upsert_link = AsyncMock()
        MockOut.model_validate.return_value = "OUT"
        await books_api.ingest_pdf(
            session, body=b"%PDF-1.4 x", subject="matematika", grade="9",
            filename="alg.pdf",
        )

    mock_repo.upsert_link.assert_not_awaited()


@pytest.mark.asyncio
async def test_ingest_pdf_plain_upload_no_notion_source_skips_link_dedup():
    """Same pin, dedup-hit path: no notion_source -> no link/commit attempt."""
    session = AsyncMock()
    existing_book = MagicMock(id=uuid4())
    fake_out = BookOut(id=existing_book.id, subject="matematika",
                        original_filename="alg.pdf", status="toc_ready")

    with patch.object(books_api.books_repo, "find_ready_by_hash",
                      AsyncMock(return_value=existing_book)), \
         patch.object(books_api, "_book_out_with_toc", AsyncMock(return_value=fake_out)), \
         patch.object(books_api, "notion_sources_repo") as mock_repo:
        mock_repo.upsert_link = AsyncMock()
        out = await books_api.ingest_pdf(
            session, body=b"%PDF-1.4 x", subject="matematika", grade="9",
            filename="alg.pdf",
        )

    mock_repo.upsert_link.assert_not_awaited()
    session.commit.assert_not_awaited()
    assert out.deduplicated is True


@pytest.mark.asyncio
async def test_ingest_pdf_link_failure_prevents_commit_no_partial_write():
    """Failure-atomicity: a raise from upsert_link (simulating a post-insert,
    pre-commit failure) must propagate WITHOUT session.commit() ever being
    called — the flushed-but-uncommitted book insert stays inside the open
    transaction so a real session's rollback-on-close discards it (see the
    real-DB proof in tests/integration/test_ingest_pdf_notion_source.py)."""
    from types import SimpleNamespace

    session = AsyncMock()
    created_book = SimpleNamespace(id=uuid4(), status="uploading", source_language="uz")

    with patch.object(books_api.books_repo, "find_ready_by_hash", AsyncMock(return_value=None)), \
         patch.object(books_api.books_repo, "create", AsyncMock(return_value=created_book)), \
         patch.object(books_api, "notion_sources_repo") as mock_repo:
        mock_repo.upsert_link = AsyncMock(side_effect=RuntimeError("simulated failure"))
        with pytest.raises(RuntimeError):
            await books_api.ingest_pdf(
                session, body=b"%PDF-1.4 x", subject="matematika", grade="9",
                filename="alg.pdf", notion_source=("page-3", "block-3"),
            )

    session.commit.assert_not_awaited()
