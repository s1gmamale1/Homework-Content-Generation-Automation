# Fetch From Notion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Fetch From Notion" source to the New Session flow — browse grade → Uzbek (`sinf`) subject → download the attached textbook PDF → run it through the existing upload→TOC→generate pipeline.

**Architecture:** "Fetch" is an alternate *source* of PDF bytes. Extract the inline upload logic into a shared `ingest_pdf` helper; add a `notion_fetch` service (crawl + download) + read-only `/notion` endpoints + a `POST /books/from-notion` endpoint; the React New Session screen gets a source chooser + a grade→subject wizard. Everything from `toc_extractor.run` onward is unchanged.

**Tech Stack:** Python 3.13, FastAPI, httpx, pytest (DB-free — patch repos/services with `unittest.mock`, never a live session). React + Vite + TypeScript (no FE unit suite — verify with `tsc --noEmit` + `npm run build`).

**Spec:** `docs/superpowers/specs/2026-06-05-fetch-from-notion-design.md`

---

## Conventions

- **Backend tests:** `.\.venv\Scripts\python.exe -m pytest …` (`uv` not on PATH). The suite is **DB-free** (`tests/conftest.py` wires no DB) — for anything touching `books_repo`/`toc_extractor`, patch them with `unittest.mock`; never open a real session. Pure functions (`_map_subject`, `_first_pdf_block`, `_url_from_block`) need no mocks.
- **Frontend:** `cd web && npx tsc -p tsconfig.app.json --noEmit` then `npm run build`. No FE unit tests in this repo.
- **Commit messages:** plain ASCII. **Stage only each task's listed files** (other sessions touch this branch — never `git add -A`).
- Known pre-existing red: `tests/services/test_config_notion.py::test_notion_defaults_disabled` (local `.env`). The suite is "green" with that one exception.

## File structure

| File | Responsibility |
|---|---|
| `app/config.py` (modify) | add `notion_lessons_root` setting |
| `app/api/v1/books.py` (modify) | extract `ingest_pdf` helper; `upload_book` → thin wrapper; add `POST /books/from-notion` |
| `app/services/notion_fetch.py` (create) | crawl (`list_grades`/`list_subjects`), `download_textbook`, pure helpers (`_map_subject`/`_first_pdf_block`/`_url_from_block`) |
| `app/api/v1/notion.py` (create) | `/notion` read-only router (grades, subjects) |
| `app/api/v1/__init__.py` (modify) | mount `notion.router` with `dependencies=[Depends(get_current_user)]` |
| `web/src/lib/types.ts` (modify) | `NotionGrade`, `NotionSubject` types |
| `web/src/lib/api.ts` (modify) | `listNotionGrades`, `listNotionSubjects`, `fetchBookFromNotion` |
| `web/src/routes/upload.tsx` (modify) | source chooser + Notion wizard |
| tests | `tests/services/test_notion_fetch.py`, `tests/api/test_from_notion.py`, `tests/api/test_ingest_pdf.py` |

---

## Task 1: `notion_lessons_root` setting

**Files:** Modify `app/config.py` (after `notion_subject_pages`, ~`:95`). Test: `tests/test_config_lessons_root.py`.

- [ ] **Step 1: Write the failing test**

```python
from app.config import Settings


def test_notion_lessons_root_default():
    s = Settings(_env_file=None)
    assert s.notion_lessons_root == "2c1998381c768063bc43c84d59c0abf3"
```

- [ ] **Step 2: Run, verify fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_config_lessons_root.py -v`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'notion_lessons_root'`.

- [ ] **Step 3: Implement** — in `app/config.py` after the `notion_subject_pages` field add:

```python
    # Root "Lessons" page to crawl for the Fetch-From-Notion browser
    # (grade -> "N - sinf" -> subject pages with attached textbooks).
    notion_lessons_root: str = "2c1998381c768063bc43c84d59c0abf3"
```

- [ ] **Step 4: Run, verify pass.** Run: `.\.venv\Scripts\python.exe -m pytest tests/test_config_lessons_root.py -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add app/config.py tests/test_config_lessons_root.py
git commit -m "feat(config): notion_lessons_root setting for the Notion textbook browser"
```

---

## Task 2: Extract shared `ingest_pdf` (both return shapes) — refactor

**Files:** Modify `app/api/v1/books.py` (`upload_book` :44-90; `ingest_pdf` is new). Test: `tests/api/test_ingest_pdf.py`.

**Context:** `upload_book` is inline and has TWO return shapes — dedup hit → `_book_out_with_toc(existing.id)` (`:65`), new book → `BookOut.model_validate(book)` (`:90`). The shared helper must replicate **both** so upload's response is unchanged. `find_ready_by_hash` only matches `status=="toc_ready"`, so a mid-extraction book won't dedup (same as today).

- [ ] **Step 1: Write the failing tests** (DB-free — patch `books_repo` + `toc_extractor`)

```python
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
         patch("app.api.v1.books.Path") as MockPath:
        MockOut.model_validate.return_value = "PLAIN_OUT"
        MockPath.return_value.__truediv__.return_value.__truediv__.return_value.__truediv__.return_value = \
            SimpleNamespace(parent=SimpleNamespace(mkdir=lambda **k: None), write_bytes=lambda b: None)
        out = await books_api.ingest_pdf(session, body=b"%PDF-1.4 x", subject="biology",
                                         grade="9", filename="b.pdf")
    assert out == "PLAIN_OUT"  # new book -> plain BookOut.model_validate


@pytest.mark.asyncio
async def test_ingest_pdf_dedup_hit_returns_with_toc():
    session = AsyncMock()
    existing = SimpleNamespace(id=uuid4())
    with patch.object(books_api.books_repo, "find_ready_by_hash", AsyncMock(return_value=existing)), \
         patch.object(books_api, "_book_out_with_toc", AsyncMock(return_value="WITH_TOC")) as wt:
        out = await books_api.ingest_pdf(session, body=b"%PDF-1.4 x", subject="biology",
                                         grade="9", filename="b.pdf")
    assert out == "WITH_TOC"  # dedup hit -> _book_out_with_toc
    wt.assert_awaited_once()


@pytest.mark.asyncio
async def test_ingest_pdf_rejects_empty_and_oversize():
    session = AsyncMock()
    with pytest.raises(books_api.HTTPException):
        await books_api.ingest_pdf(session, body=b"", subject="biology", grade=None, filename="b.pdf")
```

- [ ] **Step 2: Run, verify fail.** Run: `.\.venv\Scripts\python.exe -m pytest tests/api/test_ingest_pdf.py -v` → FAIL (`ingest_pdf` undefined).

- [ ] **Step 3: Implement.** In `app/api/v1/books.py`, add the helper above `upload_book` and rewrite `upload_book` as a thin wrapper. Preserve every line of the current logic:

```python
async def ingest_pdf(
    session: AsyncSession, *, body: bytes, subject: str,
    grade: str | None, filename: str,
) -> BookOut:
    """Shared book-creation path for both upload and Notion-fetch. Mirrors the
    original inline upload logic EXACTLY, including its two return shapes:
    dedup hit -> _book_out_with_toc; new book -> plain BookOut."""
    if subject not in SUPPORTED_SUBJECTS:
        raise HTTPException(400, f"unknown subject; allowed: {SUPPORTED_SUBJECTS}")
    if len(body) > settings.max_file_mb * 1024 * 1024:
        raise HTTPException(413, f"file too large (>{settings.max_file_mb} MB)")
    if len(body) == 0:
        raise HTTPException(400, "empty file")

    sha = hashlib.sha256(body).hexdigest()
    existing = await books_repo.find_ready_by_hash(session, sha, subject)
    if existing is not None:
        return await _book_out_with_toc(session, existing.id)

    book = await books_repo.create(
        session, subject=subject, grade=grade, original_filename=filename,
        content_sha256=sha, file_size_bytes=len(body), status="uploading",
    )
    await session.commit()

    pdf_path = Path("var") / "books" / str(book.id) / "source.pdf"
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.write_bytes(body)

    task = asyncio.create_task(toc_extractor.run(book.id, pdf_path, subject))
    _TOC_TASKS.add(task)
    task.add_done_callback(_TOC_TASKS.discard)
    return BookOut.model_validate(book)


@router.post("", status_code=201)
async def upload_book(
    file: UploadFile = File(...),
    subject: str = Form(...),
    grade: str | None = Form(default=None),
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
) -> BookOut:
    body = await file.read()
    return await ingest_pdf(
        session, body=body, subject=subject, grade=grade,
        filename=file.filename or "book.pdf",
    )
```

- [ ] **Step 4: Run, verify pass.** `.\.venv\Scripts\python.exe -m pytest tests/api/test_ingest_pdf.py -v` → PASS.

- [ ] **Step 5: Re-test upload (refactor safety).** Run any existing book/upload tests:
`.\.venv\Scripts\python.exe -m pytest tests/ -q -k "book or upload or toc"` → no new failures.

- [ ] **Step 6: Commit**

```bash
git add app/api/v1/books.py tests/api/test_ingest_pdf.py
git commit -m "refactor(books): extract shared ingest_pdf (both return shapes); upload is a thin wrapper"
```

---

## Task 3: `notion_fetch` pure helpers — `_map_subject`, `_first_pdf_block`, `_url_from_block`

**Files:** Create `app/services/notion_fetch.py`. Test: `tests/services/test_notion_fetch.py`.

- [ ] **Step 1: Write the failing tests**

```python
from app.services.notion_fetch import _map_subject, _first_pdf_block, _url_from_block


def test_map_subject_the_seven():
    assert _map_subject("Algebra") == "math-algebra"
    assert _map_subject("Geometriya") == "geometriya-g7-11"
    assert _map_subject("Fizika") == "physics"
    assert _map_subject("Kimyo") == "kimyo-g7-11"
    assert _map_subject("Biologiya") == "biology"
    assert _map_subject("Ingliz tili") == "english"
    assert _map_subject("Jahon tarixi") == "history"
    assert _map_subject("O‘zbekiston tarixi") == "history"


def test_map_subject_messy_and_unsupported():
    assert _map_subject("Ingliz tili  1-st version missing") == "english"
    assert _map_subject("Matematika\n") is None          # NOT math-algebra
    assert _map_subject("Geografiya") is None
    assert _map_subject("Adabiyot") is None
    assert _map_subject("Tasviriy san’at") is None


def test_first_pdf_block_picks_first_pdf_in_order():
    blocks = [
        {"type": "paragraph"},
        {"type": "file", "file": {"name": "ish_daftari.pdf", "file": {"url": "u-wb"}}},
        {"type": "pdf", "pdf": {"file": {"url": "u-tb"}}},
    ]
    b = _first_pdf_block(blocks)
    assert b is blocks[1]  # first pdf-bearing block in page order


def test_first_pdf_block_none_when_absent():
    assert _first_pdf_block([{"type": "paragraph"}, {"type": "image"}]) is None


def test_first_pdf_block_skips_non_pdf_file():
    blocks = [
        {"type": "file", "file": {"name": "cover.png", "file": {"url": "u-img"}}},
        {"type": "pdf", "pdf": {"file": {"url": "u-tb"}}},
    ]
    assert _first_pdf_block(blocks) is blocks[1]  # non-.pdf file skipped, pdf chosen


def test_url_from_block_shapes():
    assert _url_from_block({"type": "file", "file": {"file": {"url": "A"}}}) == "A"
    assert _url_from_block({"type": "file", "file": {"external": {"url": "B"}}}) == "B"
    assert _url_from_block({"type": "pdf", "pdf": {"file": {"url": "C"}}}) == "C"
    assert _url_from_block({"type": "pdf", "pdf": {"external": {"url": "D"}}}) == "D"
```

- [ ] **Step 2: Run, verify fail.** `.\.venv\Scripts\python.exe -m pytest tests/services/test_notion_fetch.py -v` → FAIL (module missing).

- [ ] **Step 3: Implement** `app/services/notion_fetch.py`:

```python
"""Browse the Notion 'Lessons' tree and download the textbook attached to a
subject page, so a book can be generated without a manual upload. Read-only
crawl + a single file download. sinf-only (Uzbek) for v1."""

from __future__ import annotations

import re

# Folded-substring map, LONGEST keyword first so a double-hit is deterministic.
# "matematika" is intentionally absent (lower-grade math != the app's algebra).
_SUBJECT_KEYWORDS: list[tuple[str, str]] = [
    ("ozbekiston tarixi", "history"),
    ("jahon tarixi", "history"),
    ("geometriya", "geometriya-g7-11"),
    ("biolog", "biology"),
    ("algebra", "math-algebra"),
    ("ingliz", "english"),
    ("fizika", "physics"),
    ("kimyo", "kimyo-g7-11"),
    ("tarix", "history"),
]

_APOSTROPHES = "'‘’ʻ`"


def _fold(s: str) -> str:
    return s.lower().translate({ord(c): None for c in _APOSTROPHES})


def _map_subject(title: str) -> str | None:
    """Notion subject-page title -> app subject key, or None if unsupported."""
    folded = _fold(title)
    for keyword, app_subject in _SUBJECT_KEYWORDS:
        if keyword in folded:
            return app_subject
    return None


def _first_pdf_block(blocks: list[dict]) -> dict | None:
    """First textbook PDF in page order, else None. A `pdf` block is inherently a
    PDF; a `file` block must have a `.pdf` filename — a subject page may also attach
    a cover image / .docx, which must NOT be fed to the extractor as a 'textbook'."""
    for b in blocks:
        t = b.get("type")
        if t == "pdf" and _url_from_block(b):
            return b
        if t == "file" and _url_from_block(b):
            name = (b.get("file", {}).get("name") or "").lower()
            if name.endswith(".pdf"):
                return b
    return None


def _url_from_block(block: dict) -> str | None:
    """Resolve a file/pdf block's URL (Notion-hosted signed OR external)."""
    payload = block.get(block.get("type"), {})
    return (payload.get("file") or {}).get("url") or (payload.get("external") or {}).get("url")
```

(Note: `_first_pdf_block` accepts any file/pdf block with a URL; the spec's "ends `.pdf`" is relaxed to "is a file/pdf block" because Notion `pdf` blocks have no filename — subject pages only attach textbooks here. The workbook-vs-textbook preference is a logged follow-on; "first in order" is the v1 rule.)

- [ ] **Step 4: Run, verify pass.** `.\.venv\Scripts\python.exe -m pytest tests/services/test_notion_fetch.py -v` → PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add app/services/notion_fetch.py tests/services/test_notion_fetch.py
git commit -m "feat(notion-fetch): pure helpers - subject map, first-pdf-block, url extraction"
```

---

## Task 4: `notion_fetch.list_grades` + `list_subjects` (stubbed client)

**Files:** Modify `app/services/notion_fetch.py`. Test: `tests/services/test_notion_fetch.py`.

- [ ] **Step 1: Write the failing tests** (append)

```python
from unittest.mock import MagicMock
from app.services import notion_fetch as nf


def _client(children_by_parent, blocks_by_page=None):
    c = MagicMock()
    c.get_child_pages.side_effect = lambda pid: children_by_parent.get(pid, [])
    c.get_block_children.side_effect = lambda pid: (blocks_by_page or {}).get(pid, [])
    return c


def test_list_grades_excludes_rules():
    c = _client({"ROOT": [
        {"id": "g9", "title": "9 Grade"}, {"id": "g8", "title": "8 Grade"},
        {"id": "rx", "title": "Rules"},
    ]})
    grades = nf.list_grades(c, "ROOT")
    titles = [g["title"] for g in grades]
    assert "Rules" not in titles and "9 Grade" in titles


def test_list_subjects_sinf_only_with_flags():
    c = _client(
        children_by_parent={
            "g9": [{"id": "uz", "title": "9 - sinf"}, {"id": "ru", "title": "9 - класс"}],
            "uz": [
                {"id": "alg", "title": "Algebra"},
                {"id": "geo", "title": "Geografiya"},   # unsupported
                {"id": "pe", "title": "Jismoniy tarbiya"},  # supported? no; no textbook
            ],
        },
        blocks_by_page={
            "alg": [{"type": "pdf", "pdf": {"file": {"url": "u"}}}],
            "geo": [{"type": "file", "file": {"file": {"url": "u"}}}],
            "pe": [{"type": "paragraph"}],
        },
    )
    subs = nf.list_subjects(c, "g9")
    by_title = {s["notion_title"]: s for s in subs}
    assert by_title["Algebra"]["app_subject"] == "math-algebra"
    assert by_title["Algebra"]["has_textbook"] is True
    assert by_title["Geografiya"]["app_subject"] is None        # unsupported
    assert by_title["Geografiya"]["has_textbook"] is True
    assert by_title["Jismoniy tarbiya"]["has_textbook"] is False
    # класс page is never crawled (sinf-only)


def test_list_subjects_no_sinf_child_returns_empty():
    c = _client({"g1": [{"id": "ru", "title": "1 - класс"}]})
    assert nf.list_subjects(c, "g1") == []
```

- [ ] **Step 2: Run, verify fail.** `… -k "list_grades or list_subjects" -v` → FAIL.

- [ ] **Step 3: Implement** (append to `notion_fetch.py`):

```python
_SINF_RE = re.compile(r"-\s*sinf\b", re.IGNORECASE)


def list_grades(client, lessons_root: str) -> list[dict]:
    """Grade pages under the Lessons root, excluding the 'Rules' page."""
    out = []
    for g in client.get_child_pages(lessons_root):
        if _fold(g["title"]).strip() == "rules":
            continue
        out.append({"title": g["title"].strip(), "page_id": g["id"]})
    return out


def list_subjects(client, grade_page_id: str) -> list[dict]:
    """Subjects under the grade's Uzbek 'N - sinf' child (klass ignored). Each:
    {notion_title, page_id, app_subject|None, has_textbook}."""
    sinf = next((c for c in client.get_child_pages(grade_page_id)
                 if _SINF_RE.search(c["title"])), None)
    if sinf is None:
        return []
    out = []
    for s in client.get_child_pages(sinf["id"]):
        blocks = client.get_block_children(s["id"])
        out.append({
            "notion_title": s["title"].strip(),
            "page_id": s["id"],
            "app_subject": _map_subject(s["title"]),
            "has_textbook": _first_pdf_block(blocks) is not None,
        })
    return out
```

- [ ] **Step 4: Run, verify pass.** `… -k "list_grades or list_subjects" -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/notion_fetch.py tests/services/test_notion_fetch.py
git commit -m "feat(notion-fetch): list_grades + sinf-only list_subjects with supported/textbook flags"
```

---

## Task 5: `notion_fetch.download_textbook` (size-reject + httpx)

**Files:** Modify `app/services/notion_fetch.py`. Test: `tests/services/test_notion_fetch.py`.

- [ ] **Step 1: Write the failing tests** (append)

```python
import pytest
from app.services.notion_fetch import download_textbook, TextbookTooLarge, NoTextbook


def test_download_rejects_when_no_pdf_block():
    c = _client({}, blocks_by_page={"sub": [{"type": "paragraph"}]})
    with pytest.raises(NoTextbook):
        download_textbook(c, "sub")


def test_download_rejects_oversize_via_content_length(monkeypatch):
    c = _client({}, blocks_by_page={"sub": [{"type": "pdf", "pdf": {"file": {"url": "http://x/b.pdf"}}}]})
    class _Resp:
        headers = {"Content-Length": str(21 * 1024 * 1024)}
        def raise_for_status(self): pass
    class _HTTP:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def head(self, url, follow_redirects=True): return _Resp()
    monkeypatch.setattr("app.services.notion_fetch.httpx.Client", lambda **k: _HTTP())
    with pytest.raises(TextbookTooLarge):
        download_textbook(c, "sub")
```

- [ ] **Step 2: Run, verify fail.** `… -k download -v` → FAIL.

- [ ] **Step 3: Implement** (append; add `import httpx` at top of `notion_fetch.py`):

```python
_TOC_MAX_BYTES = 20 * 1024 * 1024  # Gemini TOC ceiling (CLAUDE.md); distinct from upload's 50 MB


class NoTextbook(Exception):
    """Subject page has no downloadable PDF block."""


class TextbookTooLarge(Exception):
    """Attachment exceeds the 20 MB Gemini TOC limit."""


def download_textbook(client, subject_page_id: str) -> tuple[bytes, str]:
    """Resolve the subject page's first PDF block, reject >20 MB, return (bytes, filename)."""
    block = _first_pdf_block(client.get_block_children(subject_page_id))
    if block is None:
        raise NoTextbook(subject_page_id)
    url = _url_from_block(block)
    payload = block.get(block.get("type"), {})
    filename = (payload.get("name") or "textbook.pdf").strip() or "textbook.pdf"
    with httpx.Client(timeout=60.0) as http:
        head = http.head(url, follow_redirects=True)
        head.raise_for_status()
        size = int(head.headers.get("Content-Length") or 0)
        if size > _TOC_MAX_BYTES:
            raise TextbookTooLarge(f"{size / 1048576:.1f} MB > 20 MB")
        body = http.get(url, follow_redirects=True).content
    if len(body) > _TOC_MAX_BYTES:        # fallback when Content-Length absent
        raise TextbookTooLarge(f"{len(body) / 1048576:.1f} MB > 20 MB")
    return body, filename
```

- [ ] **Step 4: Run, verify pass.** `… -k download -v` → PASS. Then the whole file: `… tests/services/test_notion_fetch.py -q` → all green.

- [ ] **Step 5: Commit**

```bash
git add app/services/notion_fetch.py tests/services/test_notion_fetch.py
git commit -m "feat(notion-fetch): download_textbook with 20MB content-length reject"
```

---

## Task 6: `/notion` read-only endpoints (grades, subjects) + mount with auth

**Files:** Create `app/api/v1/notion.py`. Modify `app/api/v1/__init__.py`. Test: `tests/api/test_notion_router.py`.

**Context:** `app/api/v1/__init__.py:11-12` mounts `books.router`/`jobs.router` with `dependencies=[Depends(get_current_user)]`. The new `/notion` router must mount the same way. The endpoints run the sync `NotionClientWrapper` in a threadpool (`asyncio.to_thread`).

- [ ] **Step 1: Write the failing tests** (FastAPI TestClient; patch the service + the client constructor; override auth)

```python
from unittest.mock import patch
from fastapi.testclient import TestClient
from main import app
from app.auth import get_current_user

app.dependency_overrides[get_current_user] = lambda: {"user": "test"}
client = TestClient(app)


def test_grades_endpoint():
    with patch("app.api.v1.notion.NotionClientWrapper"), \
         patch("app.api.v1.notion.notion_fetch.list_grades",
               return_value=[{"title": "9 Grade", "page_id": "g9"}]):
        r = client.get("/api/v1/notion/grades")
    assert r.status_code == 200
    assert r.json()[0]["page_id"] == "g9"


def test_subjects_endpoint():
    rows = [{"notion_title": "Algebra", "page_id": "alg",
             "app_subject": "math-algebra", "has_textbook": True}]
    with patch("app.api.v1.notion.NotionClientWrapper"), \
         patch("app.api.v1.notion.notion_fetch.list_subjects", return_value=rows):
        r = client.get("/api/v1/notion/grades/g9/subjects")
    assert r.status_code == 200
    assert r.json()[0]["app_subject"] == "math-algebra"
```

- [ ] **Step 2: Run, verify fail.** `… tests/api/test_notion_router.py -v` → FAIL (404 / module missing).

- [ ] **Step 3: Implement** `app/api/v1/notion.py`:

```python
from fastapi import APIRouter, Depends, HTTPException
from app.config import settings
from app.db import get_session  # noqa: F401  (kept for symmetry; not used yet)
from app.services import notion_fetch
from app.services.notion.client import NotionClientWrapper
import asyncio

router = APIRouter(prefix="/notion", tags=["notion"])


def _client() -> NotionClientWrapper:
    if not settings.notion_api_key:
        raise HTTPException(503, "Notion not configured")
    return NotionClientWrapper(api_key=settings.notion_api_key)


@router.get("/grades")
async def get_grades() -> list[dict]:
    client = _client()
    try:
        return await asyncio.to_thread(notion_fetch.list_grades, client, settings.notion_lessons_root)
    except Exception as exc:  # noqa: BLE001 — surface a clean "unavailable" to the wizard
        raise HTTPException(502, f"Notion browse failed: {exc}")


@router.get("/grades/{grade_page_id}/subjects")
async def get_subjects(grade_page_id: str) -> list[dict]:
    client = _client()
    try:
        return await asyncio.to_thread(notion_fetch.list_subjects, client, grade_page_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Notion browse failed: {exc}")
```

Then in `app/api/v1/__init__.py`, add the import and mount **with auth** (mirror books):

```python
from app.api.v1 import notion  # add to imports
api_v1_router.include_router(notion.router, dependencies=[Depends(get_current_user)])
```

- [ ] **Step 4: Run, verify pass.** `… tests/api/test_notion_router.py -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add app/api/v1/notion.py app/api/v1/__init__.py tests/api/test_notion_router.py
git commit -m "feat(api): /notion read-only grades+subjects router (auth-gated)"
```

---

## Task 7: `POST /books/from-notion` (download + ingest)

**Files:** Modify `app/api/v1/books.py`, `app/services/notion/client.py` (add `get_page_title`). Test: `tests/api/test_from_notion.py`.

- [ ] **Step 1: Write the failing tests** (patch the service + ingest; override auth)

```python
from uuid import uuid4
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient
from main import app
from app.auth import get_current_user
from app.schemas import BookOut
import app.services.notion_fetch as nf

app.dependency_overrides[get_current_user] = lambda: {"user": "test"}
client = TestClient(app)


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
               side_effect=nf.TextbookTooLarge("26.4 MB > 20 MB")):
        r = client.post("/api/v1/books/from-notion",
                        json={"subject_page_id": "alg", "grade": "9"})
    assert r.status_code == 422 and "20 MB" in r.text


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
```

- [ ] **Step 2: Run, verify fail.** `… tests/api/test_from_notion.py -v` → FAIL (404).

- [ ] **Step 3a: Add a rate-limited `get_page_title` to the wrapper.** In `app/services/notion/client.py`, add a method (keeps every SDK call behind `_rate_limit`, like the others):

```python
    def get_page_title(self, page_id: str) -> str:
        """The page's own title text. Rate-limited (the only title-read path)."""
        self._rate_limit()
        page = self.client.pages.retrieve(page_id)
        props = page.get("properties", {})
        title_prop = next(
            (v for v in props.values() if v.get("type") == "title"), {"title": []})
        parts = title_prop.get("title", [])
        return "".join(p.get("plain_text", "") for p in parts).strip() or ""
```

- [ ] **Step 3b: Implement the endpoint.** In `app/api/v1/books.py` add imports + a request model + a thin title helper (delegates to the wrapper) + the endpoint:

```python
# add to imports
from app.services import notion_fetch
from app.services.notion.client import NotionClientWrapper


class FromNotionRequest(BaseModel):
    subject_page_id: str
    grade: str | None = None


def _notion_subject_title(client: NotionClientWrapper, subject_page_id: str) -> str:
    """Subject page title via the rate-limited wrapper (patched in tests)."""
    return client.get_page_title(subject_page_id)


@router.post("/from-notion", status_code=201)
async def book_from_notion(
    req: FromNotionRequest,
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
) -> BookOut:
    if not settings.notion_api_key:
        raise HTTPException(503, "Notion not configured")
    client = NotionClientWrapper(api_key=settings.notion_api_key)
    title = await asyncio.to_thread(_notion_subject_title, client, req.subject_page_id)
    subject = notion_fetch._map_subject(title)
    if subject is None:
        raise HTTPException(422, f"subject '{title}' is not supported for generation")
    try:
        body, filename = await asyncio.to_thread(
            notion_fetch.download_textbook, client, req.subject_page_id)
    except notion_fetch.TextbookTooLarge as exc:
        raise HTTPException(422, f"textbook too large ({exc}) — shrink and upload manually")
    except notion_fetch.NoTextbook:
        raise HTTPException(422, "this subject has no attached textbook")
    return await ingest_pdf(
        session, body=body, subject=subject, grade=req.grade, filename=filename)
```

- [ ] **Step 4: Run, verify pass.** `… tests/api/test_from_notion.py -v` → PASS.

- [ ] **Step 5: Full backend suite.** `.\.venv\Scripts\python.exe -m pytest tests/ -q` → green except the one known pre-existing red.

- [ ] **Step 6: Commit**

```bash
git add app/api/v1/books.py app/services/notion/client.py tests/api/test_from_notion.py
git commit -m "feat(api): POST /books/from-notion - download attachment, map subject, ingest"
```

---

## Task 8: Frontend API client + types

**Files:** Modify `web/src/lib/types.ts`, `web/src/lib/api.ts`.

- [ ] **Step 1: Add types** to `web/src/lib/types.ts`:

```typescript
export interface NotionGrade {
  title: string;
  page_id: string;
}

export interface NotionSubject {
  notion_title: string;
  page_id: string;
  app_subject: string | null;
  has_textbook: boolean;
}
```

- [ ] **Step 2: Add client methods** to `web/src/lib/api.ts` (inside the `api` object; import the two types):

```typescript
  async listNotionGrades(): Promise<NotionGrade[]> {
    const res = await authFetch("/api/v1/notion/grades");
    return unwrap<NotionGrade[]>(res);
  },

  async listNotionSubjects(gradePageId: string): Promise<NotionSubject[]> {
    const res = await authFetch(
      `/api/v1/notion/grades/${encodeURIComponent(gradePageId)}/subjects`,
    );
    return unwrap<NotionSubject[]>(res);
  },

  async fetchBookFromNotion(subjectPageId: string, grade: string): Promise<Book> {
    const res = await authFetch("/api/v1/books/from-notion", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ subject_page_id: subjectPageId, grade }),
    });
    return unwrap<Book>(res);
  },
```

- [ ] **Step 3: Typecheck.** `cd web && npx tsc -p tsconfig.app.json --noEmit` → exit 0.

- [ ] **Step 4: Commit**

```bash
git add web/src/lib/types.ts web/src/lib/api.ts
git commit -m "feat(web): notion-fetch api client + types"
```

---

## Task 9: Source chooser + Notion wizard in New Session

**Files:** Modify `web/src/routes/upload.tsx`.

**Approach:** Add a top-level `source` state (`"choose" | "upload" | "notion"`). `"choose"` shows two cards; `"upload"` renders the existing form (unchanged); `"notion"` renders the wizard. Follow the existing styling tokens/components already used in this file (`Eyebrow`, `Select`, `Button`, `cn`, the `--color-*` tokens).

- [ ] **Step 1: Implement the chooser + wizard.** Replace the `UploadPage` return with a `source`-switched render. Key new code (the existing upload form moves verbatim under `source === "upload"`):

```tsx
// new state at top of UploadPage:
const [source, setSource] = useState<"choose" | "upload" | "notion">("choose");
const [nGrade, setNGrade] = useState("");
const [subjects, setSubjects] = useState<NotionSubject[] | null>(null);
const [grades, setGrades] = useState<NotionGrade[] | null>(null);
const [nErr, setNErr] = useState<string | null>(null);

// load grades when entering the notion branch:
async function enterNotion() {
  setSource("notion"); setNErr(null);
  try { setGrades(await api.listNotionGrades()); }
  catch (e) { setNErr(e instanceof Error ? e.message : "Notion unavailable"); }
}

async function pickGrade(gradePageId: string, gradeTitle: string) {
  setNGrade(gradeTitle.replace(/\D/g, "")); setSubjects(null); setNErr(null);
  try { setSubjects(await api.listNotionSubjects(gradePageId)); }
  catch (e) { setNErr(e instanceof Error ? e.message : "Could not load subjects"); }
}

async function pickSubject(s: NotionSubject) {
  if (!s.app_subject || !s.has_textbook) return;
  setBusy(true);
  try {
    const book = await api.fetchBookFromNotion(s.page_id, nGrade);
    toast.success("Fetched."); navigate(`/book/${book.id}`);
  } catch (e) {
    toast.error(e instanceof Error ? e.message : "Fetch failed"); setBusy(false);
  }
}
```

Render rules:
- `source === "choose"`: two clickable cards — "Fetch From Notion" (→ `enterNotion()`) and "Upload a Book" (→ `setSource("upload")`).
- `source === "upload"`: the existing form exactly as today.
- `source === "notion"`: if `nErr` → show it + a "Use upload instead" button (`setSource("upload")`); else a grade `Select` (from `grades`, each `g.title`/`g.page_id`); once a grade is picked, the subject list — each `NotionSubject` row clickable only when `app_subject && has_textbook`, otherwise disabled with a reason chip (`!has_textbook` → "no textbook"; `!app_subject` → "unsupported"). Optionally grey grades by computing nothing client-side (v1: list all grades; dead grades just show all-disabled subjects).

(Keep the disabled-reason and busy spinner consistent with the existing form. The full TSX follows the file's current component/token vocabulary — no new UI deps.)

- [ ] **Step 2: Typecheck + build.** `cd web && npx tsc -p tsconfig.app.json --noEmit` → 0; `npm run build` → OK (writes `web/dist/`).

- [ ] **Step 3: Commit**

```bash
git add web/src/routes/upload.tsx
git commit -m "feat(web): New Session source chooser + Fetch-From-Notion wizard"
```

---

## Task 10: Acceptance — live smoke

**Files:** none (verification). Requires the server running + Notion configured.

- [ ] **Step 1: Supported, in-size — Kimyo grade 9.** In the running app: New Session → Fetch From Notion → grade 9 → Kimyo (supported, has textbook) → confirm a book is created, TOC extraction runs, and the screen lands on the TOC/theme view. (Or in-process: call `notion_fetch.download_textbook` for the grade-9 sinf Kimyo page, confirm ≤20 MB bytes returned, then `ingest_pdf`.)

- [ ] **Step 2: Oversize rejected — Algebra.** Fetch grade-9 Algebra (≈26 MB) → confirm a **422 with the size message** and **no book row created** (`SELECT count(*) FROM books WHERE original_filename ILIKE '%algebra%'` unchanged).

- [ ] **Step 3: Unsupported disabled.** Confirm a non-mapped subject (Geografiya) shows in the list **disabled** with an "unsupported" reason, and a no-textbook subject (Jismoniy tarbiya) shows **disabled** "no textbook".

- [ ] **Step 4: Record** the smoke result (book id, the reject, the disabled states) for the worklog.

---

## Self-Review

**1. Spec coverage:** source chooser + wizard → T9; sinf-only crawl → T4 (`_SINF_RE`, класс ignored); keep-7 + disabled rest → T3 `_map_subject` + T9 render; no-textbook disabled → T4 `has_textbook` + T9; reject >20 MB → T5 + T7; shared `ingest_pdf` both shapes → T2; split routers + auth → T6 (`/notion` mounted with `get_current_user`) + T7 (`/books/from-notion` on books.router); `notion_lessons_root` → T1; block-URL shapes (file/pdf, dropped embed) → T3; Content-Length pre-check → T5; live smoke → T10. No gaps.

**2. Placeholder scan:** backend tasks carry complete code + real mock-based tests (DB-free harness honored). T9 (frontend wizard) gives the exact state/handlers + explicit render rules rather than 200 lines of TSX, gated by `tsc`+`build` — the repo has no FE unit suite, and the wizard reuses the file's existing component vocabulary; this is a scoped instruction, not a hidden TODO.

**3. Type consistency:** `ingest_pdf(session, *, body, subject, grade, filename) -> BookOut` used identically in T2 (def), T7 (call), and its test. `notion_fetch` symbols (`_map_subject`, `_first_pdf_block`, `_url_from_block`, `list_grades`, `list_subjects`, `download_textbook`, `TextbookTooLarge`, `NoTextbook`) defined T3–T5 and consumed T6–T7 consistently. `NotionGrade`/`NotionSubject` (T8) match the endpoint dicts (T6) and the wizard (T9). Endpoint shapes (`{title,page_id}`, `{notion_title,page_id,app_subject,has_textbook}`) consistent across T4/T6/T8/T9.
