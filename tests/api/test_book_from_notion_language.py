"""Task 4 — source_language threading tests.

Verifies that:
- from-notion with language="ru" + a Russian title -> book tagged source_language="ru"
  with subject resolved via the ru mapper.
- from-notion with language="ru" + an unrecognised Russian title -> 422.
- from-notion with language omitted -> "uz" default (byte-identical to before).
- upload with source_language="en" -> book tagged "en".
- from-notion / upload with an invalid language ("fr") -> 422.

Bite-proof: if ingest_pdf is hardcoded to source_language="uz", the ru/en kwarg
assertions will fail — the tests are not vacuous.

BE-19 task 4 note: tests below that hit the `/from-notion` route (whether or
not they pass an explicit `grade`) patch `notion_fetch.verify_page_ancestry`
as a no-op — the route runs the ancestry walk before subject mapping
UNCONDITIONALLY (BE-19 merge-gate fix 3: grade=None no longer skips it), and
this file's mocked `NotionClientWrapper` instance carries no parent-chain
data, so the walk would otherwise TypeError on a bare MagicMock. Adaptation is
scoped to adding that one patch per affected test; no assertions changed.

BE-19 task 5 additions (bottom of file): the PDF script guard. A live-
confirmed case has an Uzbek (Latin) PDF attached to the Russian "Математика"
part page in Notion — once child pages are reachable (Task 4), naive
ingestion would silently generate a whole book of wrong-language homework.
These tests patch `app.api.v1.books.pdf_lang.detect_pdf_script` directly
(rather than building real Cyrillic/Latin-bearing PDFs — pypdf can't easily
write extractable text without reportlab, which isn't in this env; the pure
classifier itself is covered against real/faked pypdf readers in
tests/services/test_pdf_lang.py) to drive the route's block/warn/pass
branches. `grade` is omitted throughout (these tests are entirely about the
language guard, not ancestry) — but the ancestry walk still runs (see the
task 4 note above), so each one also patches `verify_page_ancestry` as a
no-op.
"""

import pytest
from uuid import uuid4
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient
from main import app
from app.auth import get_current_user
from app.schemas import BookOut
import app.services.notion_fetch as nf
import app.api.v1.books as books_api

client = TestClient(app)


@pytest.fixture(autouse=True)
def _auth_override():
    app.dependency_overrides[get_current_user] = lambda: {"user": "test"}
    yield
    app.dependency_overrides.pop(get_current_user, None)


# ─── from-notion: language="ru" with a recognised Russian title ─────────────

def test_from_notion_ru_language_passes_source_language_ru():
    """from-notion language="ru" with a Russian title -> ingest_pdf called with
    source_language="ru" and subject resolved by the Russian mapper."""
    fake = BookOut(id=uuid4(), subject="math-algebra",
                   original_filename="alg_ru.pdf", status="uploading")
    with patch("app.api.v1.books.NotionClientWrapper"), \
         patch("app.api.v1.books._notion_subject_title", return_value="Алгебра"), \
         patch("app.api.v1.books.notion_fetch.verify_page_ancestry"), \
         patch("app.api.v1.books.notion_fetch.download_textbook",
               return_value=(b"%PDF-1.4 x", "alg_ru.pdf")), \
         patch("app.api.v1.books.ingest_pdf", AsyncMock(return_value=fake)) as ing:
        r = client.post("/api/v1/books/from-notion",
                        json={"subject_page_id": "ru_alg", "grade": "8",
                              "language": "ru"})
    assert r.status_code == 201
    ing.assert_awaited_once()
    kwargs = ing.await_args.kwargs
    assert kwargs["source_language"] == "ru", \
        f"source_language should be 'ru', got {kwargs.get('source_language')!r}"
    assert kwargs["subject"] == "math-algebra", \
        f"subject should be 'math-algebra' (ru mapper), got {kwargs.get('subject')!r}"


# ─── from-notion: language="ru" with an unrecognised title -> 422 ───────────

def test_from_notion_ru_unrecognised_title_returns_422():
    """A title that the Russian mapper doesn't know -> 422 with an actionable message."""
    with patch("app.api.v1.books.NotionClientWrapper"), \
         patch("app.api.v1.books.notion_fetch.verify_page_ancestry"), \
         patch("app.api.v1.books._notion_subject_title",
               return_value="Несуществующий предмет"):  # not in any ru keyword set
        r = client.post("/api/v1/books/from-notion",
                        json={"subject_page_id": "ru_unknown", "grade": "5",
                              "language": "ru"})
    assert r.status_code == 422
    assert "Несуществующий предмет" in r.text
    assert "ru" in r.text


# ─── from-notion: language omitted -> "uz" default ──────────────────────────

def test_from_notion_default_language_is_uz():
    """Omitting language falls back to 'uz' — byte-identical to the pre-Task-4 behavior."""
    fake = BookOut(id=uuid4(), subject="math-algebra",
                   original_filename="alg.pdf", status="uploading")
    with patch("app.api.v1.books.NotionClientWrapper"), \
         patch("app.api.v1.books._notion_subject_title", return_value="Algebra"), \
         patch("app.api.v1.books.notion_fetch.verify_page_ancestry"), \
         patch("app.api.v1.books.notion_fetch.download_textbook",
               return_value=(b"%PDF-1.4 x", "alg.pdf")), \
         patch("app.api.v1.books.ingest_pdf", AsyncMock(return_value=fake)) as ing:
        r = client.post("/api/v1/books/from-notion",
                        json={"subject_page_id": "alg", "grade": "9"})
    assert r.status_code == 201
    ing.assert_awaited_once()
    kwargs = ing.await_args.kwargs
    assert kwargs["source_language"] == "uz", \
        f"default source_language should be 'uz', got {kwargs.get('source_language')!r}"


# ─── from-notion: invalid language -> 422 ───────────────────────────────────

def test_from_notion_invalid_language_returns_422():
    """Sending language='fr' is rejected with 422 before any Notion I/O."""
    with patch("app.api.v1.books.NotionClientWrapper"), \
         patch("app.api.v1.books._notion_subject_title", return_value="Algebra"):
        r = client.post("/api/v1/books/from-notion",
                        json={"subject_page_id": "alg", "grade": "9",
                              "language": "fr"})
    assert r.status_code == 422
    assert "fr" in r.text


# ─── upload: source_language="en" -> book tagged "en" ───────────────────────

@pytest.mark.asyncio
async def test_ingest_pdf_en_passes_source_language_en():
    """ingest_pdf called with source_language='en' passes it into books_repo.create."""
    from types import SimpleNamespace

    session = AsyncMock()
    created_book = SimpleNamespace(id=uuid4(), status="uploading", source_language="en")
    with patch.object(books_api.books_repo, "find_ready_by_hash",
                      AsyncMock(return_value=None)), \
         patch.object(books_api.books_repo, "create",
                      AsyncMock(return_value=created_book)) as mock_create, \
         patch.object(books_api, "BookOut") as MockOut, \
         patch.object(books_api.toc_extractor, "run", AsyncMock()), \
         patch.object(books_api.storage, "book_pdf_path") as MockPdfPath:
        MockOut.model_validate.return_value = "OUT"
        MockPdfPath.return_value = SimpleNamespace(
            parent=SimpleNamespace(mkdir=lambda **k: None),
            write_bytes=lambda b: None,
        )
        await books_api.ingest_pdf(
            session, body=b"%PDF-1.4 x", subject="biology",
            grade="9", filename="bio_en.pdf", source_language="en"
        )
    mock_create.assert_awaited_once()
    kwargs = mock_create.await_args.kwargs
    assert kwargs["source_language"] == "en", \
        f"create() should receive source_language='en', got {kwargs.get('source_language')!r}"


# ─── upload endpoint: source_language="en" form field ───────────────────────

def test_upload_book_en_source_language():
    """POST /books with source_language=en form field -> ingest_pdf called with source_language='en'."""
    fake = BookOut(id=uuid4(), subject="biology",
                   original_filename="bio.pdf", status="uploading")
    with patch("app.api.v1.books.ingest_pdf", AsyncMock(return_value=fake)) as ing:
        r = client.post(
            "/api/v1/books",
            files={"file": ("bio.pdf", b"%PDF-1.4 x", "application/pdf")},
            data={"subject": "biology", "source_language": "en"},
        )
    assert r.status_code == 201
    ing.assert_awaited_once()
    kwargs = ing.await_args.kwargs
    assert kwargs["source_language"] == "en", \
        f"ingest_pdf should receive source_language='en', got {kwargs.get('source_language')!r}"


# ─── upload endpoint: invalid source_language -> 422 ────────────────────────

def test_upload_book_invalid_source_language_returns_422():
    """Uploading with source_language='fr' is rejected with 422."""
    r = client.post(
        "/api/v1/books",
        files={"file": ("bio.pdf", b"%PDF-1.4 x", "application/pdf")},
        data={"subject": "biology", "source_language": "fr"},
    )
    assert r.status_code == 422
    assert "fr" in r.text


# ─── Bite-proof: source_language hardcoded to "uz" breaks ru/en checks ──────
#
# These tests directly call ingest_pdf with source_language="ru" / "en" and
# assert the value is forwarded to books_repo.create. If ingest_pdf were to
# hardcode source_language="uz" instead of reading the parameter, mock_create
# would receive source_language="uz" and both assertions would fail.
# (See test_ingest_pdf_en_passes_source_language_en above for the 'en' case.)

@pytest.mark.asyncio
async def test_ingest_pdf_ru_passes_source_language_ru():
    """ingest_pdf called with source_language='ru' forwards it to books_repo.create."""
    from types import SimpleNamespace

    session = AsyncMock()
    created_book = SimpleNamespace(id=uuid4(), status="uploading", source_language="ru")
    with patch.object(books_api.books_repo, "find_ready_by_hash",
                      AsyncMock(return_value=None)), \
         patch.object(books_api.books_repo, "create",
                      AsyncMock(return_value=created_book)) as mock_create, \
         patch.object(books_api, "BookOut") as MockOut, \
         patch.object(books_api.toc_extractor, "run", AsyncMock()), \
         patch.object(books_api.storage, "book_pdf_path") as MockPdfPath:
        MockOut.model_validate.return_value = "OUT"
        MockPdfPath.return_value = SimpleNamespace(
            parent=SimpleNamespace(mkdir=lambda **k: None),
            write_bytes=lambda b: None,
        )
        await books_api.ingest_pdf(
            session, body=b"%PDF-1.4 x", subject="biology",
            grade="9", filename="bio_ru.pdf", source_language="ru"
        )
    mock_create.assert_awaited_once()
    kwargs = mock_create.await_args.kwargs
    assert kwargs["source_language"] == "ru", \
        f"create() should receive source_language='ru', got {kwargs.get('source_language')!r}"


# ═══════════════ BE-19 task 5: PDF script guard ═════════════════════════════
#
# Route order under test: NotionClientWrapper -> subject title -> subject
# mapping -> download_textbook -> [NEW] detect_pdf_script -> hard-block on
# confident mismatch, else thread `warnings` -> ingest_pdf. All tests below
# patch `download_textbook` to return real-looking (but tiny) PDF bytes and
# patch `detect_pdf_script` directly to control the detected script without
# needing a real Cyrillic/Latin-bearing PDF (see module docstring above).

_FAKE_PDF_BYTES = b"%PDF-1.4 x"


def _patch_detect(script: str):
    return patch("app.api.v1.books.pdf_lang.detect_pdf_script", return_value=script)


# ─── ru + latin-script PDF -> 422, names filename + detected script ─────────

def test_from_notion_ru_language_latin_pdf_blocks_422():
    with patch("app.api.v1.books.NotionClientWrapper"), \
         patch("app.api.v1.books.notion_fetch.verify_page_ancestry"), \
         patch("app.api.v1.books._notion_subject_title", return_value="Алгебра"), \
         patch("app.api.v1.books.notion_fetch.download_textbook",
               return_value=(_FAKE_PDF_BYTES, "algebra_uz.pdf")), \
         _patch_detect("latin"), \
         patch("app.api.v1.books.ingest_pdf", AsyncMock()) as ing:
        r = client.post("/api/v1/books/from-notion",
                        json={"subject_page_id": "ru_alg", "language": "ru"})
    assert r.status_code == 422
    assert "algebra_uz.pdf" in r.text
    assert "latin" in r.text
    ing.assert_not_awaited()


# ─── uz (default) + cyrillic-script PDF -> 422, names filename + script ─────

def test_from_notion_uz_language_cyrillic_pdf_blocks_422():
    with patch("app.api.v1.books.NotionClientWrapper"), \
         patch("app.api.v1.books.notion_fetch.verify_page_ancestry"), \
         patch("app.api.v1.books._notion_subject_title", return_value="Algebra"), \
         patch("app.api.v1.books.notion_fetch.download_textbook",
               return_value=(_FAKE_PDF_BYTES, "algebra_ru.pdf")), \
         _patch_detect("cyrillic"), \
         patch("app.api.v1.books.ingest_pdf", AsyncMock()) as ing:
        r = client.post("/api/v1/books/from-notion",
                        json={"subject_page_id": "alg"})
    assert r.status_code == 422
    assert "algebra_ru.pdf" in r.text
    assert "cyrillic" in r.text
    ing.assert_not_awaited()


# ─── en (treated like uz -- Latin-expected) + cyrillic-script PDF -> 422 ────
#
# Explicit nuance: "en" is not "ru", so a naive `language != "ru"` mismatch
# rule would need to treat it as Latin-expected too (same as uz). Without this
# case, an English-tagged Notion fetch of a mistakenly-Cyrillic PDF would slip
# through as a false "match".

def test_from_notion_en_language_cyrillic_pdf_blocks_422():
    with patch("app.api.v1.books.NotionClientWrapper"), \
         patch("app.api.v1.books.notion_fetch.verify_page_ancestry"), \
         patch("app.api.v1.books._notion_subject_title", return_value="Biology"), \
         patch("app.api.v1.books.notion_fetch.download_textbook",
               return_value=(_FAKE_PDF_BYTES, "biology_ru.pdf")), \
         _patch_detect("cyrillic"), \
         patch("app.api.v1.books.ingest_pdf", AsyncMock()) as ing:
        r = client.post("/api/v1/books/from-notion",
                        json={"subject_page_id": "bio_en", "language": "en"})
    assert r.status_code == 422
    assert "biology_ru.pdf" in r.text
    assert "cyrillic" in r.text
    ing.assert_not_awaited()


# ─── indeterminate ("unknown") script -> 201, proceeds with a warning ───────

def test_from_notion_unknown_script_proceeds_with_warning():
    fake = BookOut(id=uuid4(), subject="math-algebra",
                   original_filename="algebra_scan.pdf", status="uploading")
    with patch("app.api.v1.books.NotionClientWrapper"), \
         patch("app.api.v1.books._notion_subject_title", return_value="Algebra"), \
         patch("app.api.v1.books.notion_fetch.verify_page_ancestry"), \
         patch("app.api.v1.books.notion_fetch.download_textbook",
               return_value=(_FAKE_PDF_BYTES, "algebra_scan.pdf")), \
         _patch_detect("unknown"), \
         patch("app.api.v1.books.ingest_pdf", AsyncMock(return_value=fake)) as ing:
        r = client.post("/api/v1/books/from-notion",
                        json={"subject_page_id": "alg"})
    assert r.status_code == 201
    ing.assert_awaited_once()
    body = r.json()
    assert body["warnings"], f"expected a non-empty warnings list, got {body.get('warnings')!r}"
    assert "language" in body["warnings"][0].lower()


# ─── matching script -> 201, no warning ─────────────────────────────────────

def test_from_notion_matching_script_no_warning():
    fake = BookOut(id=uuid4(), subject="math-algebra",
                   original_filename="algebra.pdf", status="uploading")
    with patch("app.api.v1.books.NotionClientWrapper"), \
         patch("app.api.v1.books._notion_subject_title", return_value="Algebra"), \
         patch("app.api.v1.books.notion_fetch.verify_page_ancestry"), \
         patch("app.api.v1.books.notion_fetch.download_textbook",
               return_value=(_FAKE_PDF_BYTES, "algebra.pdf")), \
         _patch_detect("latin"), \
         patch("app.api.v1.books.ingest_pdf", AsyncMock(return_value=fake)) as ing:
        r = client.post("/api/v1/books/from-notion",
                        json={"subject_page_id": "alg"})
    assert r.status_code == 201
    ing.assert_awaited_once()
    body = r.json()
    assert not body.get("warnings"), \
        f"expected no warnings on a script match, got {body.get('warnings')!r}"


# ═══════ BE-19 task 5 review fix: Rus tili subject exemption ═══════════════
#
# "Rus tili" (Russian-as-a-language, app subject code "russian") legitimately
# has a Cyrillic-dominant textbook sitting under the uz Notion container —
# fetched with language="uz" the naive guard above would hard-422 a CORRECT
# book. Doctrine: hard gates only for wrongness; blocking a right book is a
# false positive we must not ship. Fix: when the resolved subject is
# "russian", downgrade the script guard to warn-only for this request.

def test_from_notion_russian_subject_cyrillic_pdf_warns_not_blocks():
    """Rus tili page (subject 'russian') under the uz container, Cyrillic PDF,
    language='uz' -> 201 with an advisory warning, NOT a 422."""
    fake = BookOut(id=uuid4(), subject="russian",
                   original_filename="rus_tili.pdf", status="uploading")
    with patch("app.api.v1.books.NotionClientWrapper"), \
         patch("app.api.v1.books._notion_subject_title", return_value="Rus tili"), \
         patch("app.api.v1.books.notion_fetch.verify_page_ancestry"), \
         patch("app.api.v1.books.notion_fetch.download_textbook",
               return_value=(_FAKE_PDF_BYTES, "rus_tili.pdf")), \
         _patch_detect("cyrillic"), \
         patch("app.api.v1.books.ingest_pdf", AsyncMock(return_value=fake)) as ing:
        r = client.post("/api/v1/books/from-notion",
                        json={"subject_page_id": "rus_tili"})
    assert r.status_code == 201, r.text
    ing.assert_awaited_once()
    body = r.json()
    assert body["warnings"], f"expected an advisory warning, got {body.get('warnings')!r}"
    assert "rus tili" in body["warnings"][0].lower()
    assert "cyrillic" in body["warnings"][0].lower()


# ─── control: same cyrillic+uz mismatch, NON-language subject -> still 422 ──

def test_from_notion_non_russian_subject_cyrillic_pdf_still_blocks_422():
    """Same script mismatch shape as the exemption above, but a non-language
    subject ('Algebra') must still hard-422 — the exemption is subject-scoped,
    not a blanket downgrade of the guard."""
    with patch("app.api.v1.books.NotionClientWrapper"), \
         patch("app.api.v1.books._notion_subject_title", return_value="Algebra"), \
         patch("app.api.v1.books.notion_fetch.verify_page_ancestry"), \
         patch("app.api.v1.books.notion_fetch.download_textbook",
               return_value=(_FAKE_PDF_BYTES, "algebra_ru.pdf")), \
         _patch_detect("cyrillic"), \
         patch("app.api.v1.books.ingest_pdf", AsyncMock()) as ing:
        r = client.post("/api/v1/books/from-notion",
                        json={"subject_page_id": "alg"})
    assert r.status_code == 422
    assert "algebra_ru.pdf" in r.text
    assert "cyrillic" in r.text
    ing.assert_not_awaited()


# ═══════ BE-19 task 5 review fix 2: generalize to all foreign-language ═══════
# subjects, BOTH directions.
#
# The mirror-direction class: `english` (ru keyword "нгл") and `ona-tili`
# (ru keyword "узб. яз") are Latin-content language subjects fetchable under
# the RU container with language="ru" -> expected cyrillic -> the guard would
# hard-422 legitimate Latin-dominant books. The exemption predicate is now:
# the resolved subject teaches a specific language, so its textbook's dominant
# script is fixed by the SUBJECT (russian->cyrillic, english/ona-tili->latin),
# not by the container it was fetched under. A mismatch consistent with the
# subject's own content script downgrades to an advisory; anything else
# (including a mismatch that ALSO contradicts the subject's content script,
# e.g. a Latin PDF on a Rus-tili page under the ru container) stays a hard 422.

def test_from_notion_english_subject_under_ru_latin_pdf_warns_not_blocks():
    """'Английский язык' page under the ru container (subject 'english'),
    Latin-detected PDF, language='ru' -> 201 with advisory, NOT 422."""
    fake = BookOut(id=uuid4(), subject="english",
                   original_filename="english_g5.pdf", status="uploading")
    with patch("app.api.v1.books.NotionClientWrapper"), \
         patch("app.api.v1.books._notion_subject_title",
               return_value="Английский язык"), \
         patch("app.api.v1.books.notion_fetch.verify_page_ancestry"), \
         patch("app.api.v1.books.notion_fetch.download_textbook",
               return_value=(_FAKE_PDF_BYTES, "english_g5.pdf")), \
         _patch_detect("latin"), \
         patch("app.api.v1.books.ingest_pdf", AsyncMock(return_value=fake)) as ing:
        r = client.post("/api/v1/books/from-notion",
                        json={"subject_page_id": "en_ru", "language": "ru"})
    assert r.status_code == 201, r.text
    ing.assert_awaited_once()
    body = r.json()
    assert body["warnings"], f"expected an advisory warning, got {body.get('warnings')!r}"
    assert "english" in body["warnings"][0].lower()
    assert "latin" in body["warnings"][0].lower()


def test_from_notion_ona_tili_under_ru_latin_pdf_warns_not_blocks():
    """'Узб. язык' page under the ru container (subject 'ona-tili' — Uzbek
    taught in RU-medium schools), Latin-detected PDF, language='ru' -> 201
    with advisory, NOT 422."""
    fake = BookOut(id=uuid4(), subject="ona-tili",
                   original_filename="uzb_yaz.pdf", status="uploading")
    with patch("app.api.v1.books.NotionClientWrapper"), \
         patch("app.api.v1.books._notion_subject_title",
               return_value="Узб. язык"), \
         patch("app.api.v1.books.notion_fetch.verify_page_ancestry"), \
         patch("app.api.v1.books.notion_fetch.download_textbook",
               return_value=(_FAKE_PDF_BYTES, "uzb_yaz.pdf")), \
         _patch_detect("latin"), \
         patch("app.api.v1.books.ingest_pdf", AsyncMock(return_value=fake)) as ing:
        r = client.post("/api/v1/books/from-notion",
                        json={"subject_page_id": "onatili_ru", "language": "ru"})
    assert r.status_code == 201, r.text
    ing.assert_awaited_once()
    body = r.json()
    assert body["warnings"], f"expected an advisory warning, got {body.get('warnings')!r}"
    assert "ona tili" in body["warnings"][0].lower()
    assert "latin" in body["warnings"][0].lower()


def test_from_notion_russian_under_ru_latin_pdf_still_blocks_422():
    """Tightening vs review-fix-1: a LATIN pdf on a Russian-subject page under
    the ru container contradicts BOTH the container expectation (cyrillic) and
    the subject's own content script (cyrillic) — that's a wrong book, still
    a hard 422 (fix 1's bare `subject == "russian"` check would have warned)."""
    with patch("app.api.v1.books.NotionClientWrapper"), \
         patch("app.api.v1.books._notion_subject_title",
               return_value="Русский язык"), \
         patch("app.api.v1.books.notion_fetch.verify_page_ancestry"), \
         patch("app.api.v1.books.notion_fetch.download_textbook",
               return_value=(_FAKE_PDF_BYTES, "rus_latin.pdf")), \
         _patch_detect("latin"), \
         patch("app.api.v1.books.ingest_pdf", AsyncMock()) as ing:
        r = client.post("/api/v1/books/from-notion",
                        json={"subject_page_id": "ru_rus", "language": "ru"})
    assert r.status_code == 422
    assert "rus_latin.pdf" in r.text
    assert "latin" in r.text
    ing.assert_not_awaited()


def test_language_subject_script_map_is_registry_consistent():
    """Map hygiene: every exempted subject exists in the registry AND is a
    languages-family subject — the exemption doctrine only covers subjects
    that teach a specific language (content script fixed by the subject)."""
    from app.services import subjects

    assert books_api._LANGUAGE_SUBJECT_CONTENT_SCRIPT == {
        "russian": "cyrillic",
        "english": "latin",
        "ona-tili": "latin",
    }
    for code in books_api._LANGUAGE_SUBJECT_CONTENT_SCRIPT:
        assert code in subjects.REGISTRY, f"{code!r} not in subjects.REGISTRY"
        assert subjects.REGISTRY[code].family == "languages", \
            f"{code!r} is not a languages-family subject"
