# Notion Archive — Phase 1 (Push) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On job-done, find-or-create the lesson + `Homework` page in the existing Notion tree and write the finished homework (rendered blocks + attached `homework.md` + `content.json`), best-effort and never failing the job.

**Architecture:** A new `app/services/notion/` package ports the reference repo's sync Notion client (rate-limited `notion_client` + raw `httpx` 2-step file upload) and a pure markdown→blocks builder. `app/services/notion_archive.py` is the async orchestrator: it resolves the Notion **subject page** from a config map keyed by `{subject, grade}`, finds-or-creates the lesson + `Homework` sub-page, writes content, and stamps idempotency columns. The sync Notion I/O runs inside `asyncio.to_thread`. The pipeline calls `archive_job(job_id)` after marking the job `done`, wrapped so any exception is logged and swallowed.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0 (async), Alembic, Pydantic-Settings, `notion-client`, `httpx`, pytest + pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-06-02-notion-archive-design.md` (Phase 1 sections). Phase 2 (pull textbook from Notion) is a separate later plan and out of scope here.

**Scope decisions (locked in spec §2–§3):**
- Phase 1 (push) only.
- Subject anchor = config map `{subject}|{grade}` → subject-page-ID.
- Grade = new real `books.grade` column (nullable string, e.g. `"8"`).
- Body = rendered Notion blocks **AND** attached `homework.md` + `content.json`.
- Lesson title = `"{section_number} {section_title}"` (app-owned, only needs self-consistency for dedup).
- Idempotency: stamp `toc_entries.notion_homework_page_id` + `homework_jobs.notion_archived_at`; skip writing if the Homework page already has content blocks (no deletion machinery in Phase 1).

---

## Prerequisite (operational, no code) — do this before Task 1

- [ ] **Create a Notion internal integration** at https://www.notion.so/my-integrations → copy the token (starts with `ntn_` or `secret_`).
- [ ] **Share the lesson tree with the integration:** in Notion, open the top page (`Class A Creative`) → `•••` → Connections → add the integration. Sharing the root cascades to descendants.
- [ ] **Record the subject-page IDs** you will write under (already captured for our subjects, e.g. `Geometriya/8-sinf` = `2c4998381c7680a099fcfa8277758da9`). These go in `NOTION_SUBJECT_PAGES`.
- [ ] Keep `NOTION_ENABLED=false` until Task 11 (live smoke). With it false, `archive_job` is a no-op so the rest of the pipeline is unaffected in dev/CI.

---

## File Structure

**New files:**
- `app/services/notion/__init__.py` — package marker.
- `app/services/notion/blocks.py` — **pure** block builders + markdown→Notion-blocks (no I/O; fully unit-testable).
- `app/services/notion/client.py` — `NotionClientWrapper` (sync: rate-limited `notion_client.Client` + `httpx` 2-step upload).
- `app/services/notion/page_creator.py` — `find_or_create(parent_id, title)` (idempotent create-by-normalized-title).
- `app/services/notion_archive.py` — async `archive_job(job_id)` orchestrator (anchor resolution + push + stamps; best-effort).
- `app/services/job_artifacts.py` — `structured_artifacts(job)` + `build_content_json(job, *, generated_at)`; reused by the download endpoint and the Notion archive.
- Tests under `tests/services/` mirroring each module.

**Modified files:**
- `pyproject.toml` — add `notion-client`, `httpx`.
- `app/config.py` — add `notion_enabled`, `notion_api_key`, `notion_subject_pages`.
- `.env` / `.env.example` — add the three Notion vars.
- `app/models/book.py` — add `grade`.
- `app/models/homework_job.py` — add `notion_archived_at`.
- `app/models/toc_entry.py` — add `notion_homework_page_id`.
- `alembic/versions/0016_notion_archive.py` — new migration (3 columns).
- `app/repositories/books.py` — `create(...)` accepts `grade`.
- `app/repositories/jobs.py` — `set_notion_archived(...)`.
- `app/repositories/toc_entries.py` — `set_notion_homework_page_id(...)`.
- `app/api/v1/books.py` — upload accepts optional `grade` form field.
- `app/api/v1/jobs.py` — download endpoint reuses `structured_artifacts(job)`.
- `app/services/pipeline.py` — call `archive_job(job_id)` after job marked `done`.

---

## Task 1: Dependencies + config

**Files:**
- Modify: `pyproject.toml`
- Modify: `app/config.py:1-90`
- Modify: `.env`, `.env.example`
- Test: `tests/services/test_config_notion.py`

- [ ] **Step 1: Add dependencies**

In `pyproject.toml`, add to the `[project]` `dependencies` array (keep alphabetical with neighbours):

```toml
    "notion-client>=2.2.1",
    "httpx>=0.27",
```

Then run: `uv sync` — Expected: resolves and installs `notion-client` + `httpx`.

- [ ] **Step 2: Write the failing config test**

```python
# tests/services/test_config_notion.py
from app.config import Settings


def test_notion_defaults_disabled():
    s = Settings(database_url="postgresql+asyncpg://x/y")
    assert s.notion_enabled is False
    assert s.notion_api_key == ""
    assert s.notion_subject_pages == {}


def test_notion_subject_pages_parses_json_env(monkeypatch):
    monkeypatch.setenv("NOTION_ENABLED", "true")
    monkeypatch.setenv("NOTION_API_KEY", "ntn_test")
    monkeypatch.setenv(
        "NOTION_SUBJECT_PAGES", '{"geometriya-g7-11|8": "2c4998381c7680a099fcfa8277758da9"}'
    )
    s = Settings(database_url="postgresql+asyncpg://x/y")
    assert s.notion_enabled is True
    assert s.notion_api_key == "ntn_test"
    assert s.notion_subject_pages["geometriya-g7-11|8"] == "2c4998381c7680a099fcfa8277758da9"
```

- [ ] **Step 3: Run to verify it fails**

Run: `uv run python -m pytest tests/services/test_config_notion.py -q`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'notion_enabled'`.

- [ ] **Step 4: Add the config fields**

In `app/config.py`, add the import near the top (if `Field` is not already imported):

```python
from pydantic import Field
```

Then inside `class Settings(BaseSettings):`, after the existing `extract_model` field, add:

```python
    # ─── Notion archive (Phase 1 push) ───
    notion_enabled: bool = False
    notion_api_key: str = ""
    # Keyed "{subject}|{grade}" → Notion subject-page ID. Parsed from JSON in env.
    notion_subject_pages: dict[str, str] = Field(default_factory=dict)
```

- [ ] **Step 5: Run to verify it passes**

Run: `uv run python -m pytest tests/services/test_config_notion.py -q`
Expected: PASS (2 passed).

- [ ] **Step 6: Document the env vars**

Append to `.env.example` (and `.env` for local use — do NOT commit `.env`):

```dotenv
# Notion archive (Phase 1 push). Leave NOTION_ENABLED=false to no-op.
NOTION_ENABLED=false
NOTION_API_KEY=
# JSON: { "<subject>|<grade>": "<subject-page-id>" }
NOTION_SUBJECT_PAGES={}
```

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock app/config.py tests/services/test_config_notion.py .env.example
git commit -m "feat(notion): add notion-client dep + notion_* config fields"
```

---

## Task 2: Migration + models (grade, notion_archived_at, notion_homework_page_id)

**Files:**
- Modify: `app/models/book.py:11-38`
- Modify: `app/models/homework_job.py:12-109`
- Modify: `app/models/toc_entry.py:10-30`
- Create: `alembic/versions/0016_notion_archive.py`
- Test: `tests/services/test_notion_columns.py`

- [ ] **Step 1: Add the model columns**

In `app/models/book.py`, after the `subject` column add:

```python
    grade: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
```

In `app/models/homework_job.py`, after the `completed_at` column add:

```python
    notion_archived_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
```

In `app/models/toc_entry.py`, after the `order_index` column add:

```python
    notion_homework_page_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
```

(Confirm `Optional`, `String`, `DateTime`, `mapped_column`, `Mapped` are already imported in each file; they are used by existing columns, so they are.)

- [ ] **Step 2: Confirm the current head revision id**

Run: `uv run alembic heads`
Expected: prints `b6d2f8a4c3e9 (head)` — the `revision:` id inside `0015_homework_practice_arc.py` (Alembic revision ids are **hashes**, not the `0015_...` filename). If your tree shows a different hash, substitute it for `down_revision` below.

- [ ] **Step 3: Create the migration**

```python
# alembic/versions/0016_notion_archive.py
"""notion archive phase 1: books.grade, homework_jobs.notion_archived_at,
toc_entries.notion_homework_page_id

Revision ID: c9e3f1a07b62
Revises: b6d2f8a4c3e9
Create Date: 2026-06-02
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c9e3f1a07b62"
down_revision: Union[str, Sequence[str], None] = "b6d2f8a4c3e9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("books", sa.Column("grade", sa.String(length=32), nullable=True))
    op.add_column(
        "homework_jobs",
        sa.Column("notion_archived_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "toc_entries",
        sa.Column("notion_homework_page_id", sa.String(length=128), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("toc_entries", "notion_homework_page_id")
    op.drop_column("homework_jobs", "notion_archived_at")
    op.drop_column("books", "grade")
```

> If Step 2 showed a different head, set `down_revision` to that value and rename the `Revises:` docstring line accordingly.

- [ ] **Step 4: Write the failing test**

```python
# tests/services/test_notion_columns.py
from app.models.book import Book
from app.models.homework_job import HomeworkJob
from app.models.toc_entry import TOCEntry


def test_new_columns_exist_on_models():
    assert "grade" in Book.__table__.columns
    assert "notion_archived_at" in HomeworkJob.__table__.columns
    assert "notion_homework_page_id" in TOCEntry.__table__.columns
```

- [ ] **Step 5: Run the test**

Run: `uv run python -m pytest tests/services/test_notion_columns.py -q`
Expected: PASS (the model edits in Step 1 satisfy it). If FAIL, the column edits are missing.

- [ ] **Step 6: Apply the migration against the dev DB**

Run: `uv run alembic upgrade head`
Expected: applies `0016_notion_archive` with no error. Verify:
`docker exec edu-postgres psql -U edu -d edu_homework -c "\d books" | findstr grade`
Expected: shows a `grade | character varying(32)` row.

- [ ] **Step 7: Commit**

```bash
git add app/models/book.py app/models/homework_job.py app/models/toc_entry.py alembic/versions/0016_notion_archive.py tests/services/test_notion_columns.py
git commit -m "feat(notion): migration + model columns (grade, notion archive ids)"
```

---

## Task 3: Repo methods (set_notion_archived, set_notion_homework_page_id)

**Files:**
- Modify: `app/repositories/jobs.py:101-135`
- Modify: `app/repositories/toc_entries.py`
- Test: `tests/repositories/test_notion_repo_methods.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/repositories/test_notion_repo_methods.py
import inspect
from app.repositories import jobs as jobs_repo
from app.repositories import toc_entries as toc_repo


def test_repo_methods_exist_with_expected_signature():
    assert hasattr(jobs_repo, "set_notion_archived")
    assert hasattr(toc_repo, "set_notion_homework_page_id")
    jp = inspect.signature(jobs_repo.set_notion_archived).parameters
    assert "job_id" in jp and "notion_archived_at" in jp
    tp = inspect.signature(toc_repo.set_notion_homework_page_id).parameters
    assert "toc_entry_id" in tp and "page_id" in tp
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run python -m pytest tests/repositories/test_notion_repo_methods.py -q`
Expected: FAIL — `AttributeError: module 'app.repositories.jobs' has no attribute 'set_notion_archived'`.

- [ ] **Step 3: Add the jobs repo method**

In `app/repositories/jobs.py`, after `set_status` add (mirror its `session.get` pattern):

```python
async def set_notion_archived(
    session: AsyncSession, job_id: UUID, notion_archived_at: datetime
) -> None:
    job = await session.get(HomeworkJob, job_id)
    if job is None:
        return
    job.notion_archived_at = notion_archived_at
```

(Confirm `AsyncSession`, `UUID`, `datetime`, `HomeworkJob` are already imported in this file — `set_status` uses all of them.)

- [ ] **Step 4: Add the toc repo method**

In `app/repositories/toc_entries.py`, add (matching the file's existing import style; `TOCEntry` and `AsyncSession`/`UUID` are already imported because `get` uses them):

```python
async def set_notion_homework_page_id(
    session: AsyncSession, toc_entry_id: UUID, page_id: str
) -> None:
    entry = await session.get(TOCEntry, toc_entry_id)
    if entry is None:
        return
    entry.notion_homework_page_id = page_id
```

- [ ] **Step 5: Run to verify it passes**

Run: `uv run python -m pytest tests/repositories/test_notion_repo_methods.py -q`
Expected: PASS (1 passed).

- [ ] **Step 6: Commit**

```bash
git add app/repositories/jobs.py app/repositories/toc_entries.py tests/repositories/test_notion_repo_methods.py
git commit -m "feat(notion): repo setters for archive stamps"
```

---

## Task 4: Books upload accepts optional grade

**Files:**
- Modify: `app/repositories/books.py:11-19`
- Modify: `app/api/v1/books.py:44-88`
- Test: `tests/repositories/test_books_grade.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/repositories/test_books_grade.py
import inspect
from app.repositories import books as books_repo


def test_books_create_accepts_grade():
    params = inspect.signature(books_repo.create).parameters
    assert "grade" in params
    assert params["grade"].default is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run python -m pytest tests/repositories/test_books_grade.py -q`
Expected: FAIL — `assert 'grade' in params` is False.

- [ ] **Step 3: Add `grade` to the books repo create**

In `app/repositories/books.py`, update `create` to accept and set `grade` (keep the existing keyword-only style):

```python
async def create(
    session: AsyncSession,
    *,
    subject: str,
    original_filename: str,
    content_sha256: str,
    file_size_bytes: int,
    status: str = "uploading",
    grade: Optional[str] = None,
) -> Book:
    book = Book(
        subject=subject,
        grade=grade,
        original_filename=original_filename,
        content_sha256=content_sha256,
        file_size_bytes=file_size_bytes,
        status=status,
    )
    session.add(book)
    await session.flush()
    return book
```

> This reproduces the current `create` body verbatim (verified against `app/repositories/books.py:10-29`) — the ONLY additions are the `grade: Optional[str] = None` keyword param and `grade=grade` in the `Book(...)` call. Keep `status: str = "uploading"` (it has that default today). `from typing import Optional` is already imported at the top of the file (line 1) — no new import needed.

- [ ] **Step 4: Accept `grade` at the upload endpoint**

In `app/api/v1/books.py`, add a `grade` form field to the upload handler signature (alongside the existing `subject` parameter):

```python
    grade: str | None = Form(default=None),
```

and pass it into the `books_repo.create(...)` call:

```python
    book = await books_repo.create(
        session,
        subject=subject,
        grade=grade,
        original_filename=file.filename or "book.pdf",
        content_sha256=sha,
        file_size_bytes=len(body),
        status="uploading",
    )
```

(Confirm `Form` is imported from `fastapi`; the existing `subject` field already uses `Form`, so it is.)

- [ ] **Step 5: Run to verify it passes**

Run: `uv run python -m pytest tests/repositories/test_books_grade.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/repositories/books.py app/api/v1/books.py tests/repositories/test_books_grade.py
git commit -m "feat(notion): books upload accepts optional grade"
```

> **Frontend follow-on (note, not in this backend plan):** add a `grade` text/select input to the upload form in `web/` so new uploads populate `books.grade`. Existing books with NULL grade will simply be skipped by the archive (warn-once) until grade is set.

---

## Task 5: Pure block builders + markdown→Notion blocks

**Files:**
- Create: `app/services/notion/__init__.py`
- Create: `app/services/notion/blocks.py`
- Test: `tests/services/test_notion_blocks.py`

- [ ] **Step 1: Create the package marker**

```python
# app/services/notion/__init__.py
```

(empty file)

- [ ] **Step 2: Write the failing tests**

```python
# tests/services/test_notion_blocks.py
from app.services.notion import blocks


def test_heading_levels():
    b = blocks.make_heading("Title", level=2)
    assert b["type"] == "heading_2"
    assert b["heading_2"]["rich_text"][0]["text"]["content"] == "Title"


def test_paragraph_chunks_over_2000_chars():
    long = "x" * 4500
    b = blocks.make_paragraph(long)
    segs = b["paragraph"]["rich_text"]
    assert len(segs) == 3  # 2000 + 2000 + 500
    assert all(len(s["text"]["content"]) <= 2000 for s in segs)


def test_parse_rich_text_bold_and_plain():
    segs = blocks.parse_rich_text("plain **bold** end")
    contents = [s["text"]["content"] for s in segs]
    assert "bold" in contents
    bold_seg = next(s for s in segs if s["text"]["content"] == "bold")
    assert bold_seg["annotations"]["bold"] is True


def test_parse_rich_text_preserves_lone_asterisk_multiplication():
    segs = blocks.parse_rich_text("5 * 3 = 15")
    joined = "".join(s["text"]["content"] for s in segs)
    assert joined == "5 * 3 = 15"


def test_markdown_to_blocks_headings_bullets_divider():
    md = "# Heading\n\n- item one\n- item two\n\n---\n\nA paragraph."
    out = blocks.markdown_to_notion_blocks(md)
    types = [b["type"] for b in out]
    assert types[0] == "heading_1"
    assert "bulleted_list_item" in types
    assert "divider" in types
    assert types[-1] == "paragraph"


def test_file_upload_block_shape():
    b = blocks.make_file_upload_block("upl_123", "homework.md")
    assert b["type"] == "file"
    assert b["file"]["type"] == "file_upload"
    assert b["file"]["file_upload"]["id"] == "upl_123"
    assert b["file"]["name"] == "homework.md"
```

- [ ] **Step 3: Run to verify it fails**

Run: `uv run python -m pytest tests/services/test_notion_blocks.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.notion.blocks'`.

- [ ] **Step 4: Implement `blocks.py`** (ported from the reference, pure, no I/O)

```python
# app/services/notion/blocks.py
"""Pure Notion block builders + markdown→block conversion. No network I/O.

Ported from the s1gmamale1/Notion---Video-Lesson reference (tools/notion).
Notion limits respected: ≤2000 chars per rich_text segment.
"""

from __future__ import annotations

import re

_MAX_SEG = 2000


def _chunk(text: str, annotations: dict | None = None) -> list[dict]:
    segs: list[dict] = []
    for i in range(0, len(text), _MAX_SEG):
        seg: dict = {"type": "text", "text": {"content": text[i : i + _MAX_SEG]}}
        if annotations:
            seg["annotations"] = annotations
        segs.append(seg)
    return segs


def make_heading(text: str, level: int = 2) -> dict:
    htype = f"heading_{level}"
    return {
        "object": "block",
        "type": htype,
        htype: {"rich_text": [{"type": "text", "text": {"content": text[:_MAX_SEG]}}]},
    }


def make_paragraph(text: str, bold: bool = False) -> dict:
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": _chunk(text, {"bold": True} if bold else None)},
    }


def make_divider() -> dict:
    return {"object": "block", "type": "divider", "divider": {}}


def make_file_upload_block(upload_id: str, name: str = "") -> dict:
    block: dict = {
        "object": "block",
        "type": "file",
        "file": {"type": "file_upload", "file_upload": {"id": upload_id}},
    }
    if name:
        block["file"]["name"] = name
    return block


def parse_rich_text(text: str) -> list[dict]:
    """Parse markdown **bold**/*italic*/***both*** into Notion rich_text.

    A lone '*' flanked by spaces/digits (multiplication) stays plain text.
    """
    segments: list[dict] = []
    pattern = (
        r"\*\*\*(.+?)\*\*\*"
        r"|\*\*(.+?)\*\*"
        r"|\*(?=[^\s*])(.+?)(?<=[^\s*])\*"
        r"|([^*]+|\*)"
    )
    for match in re.finditer(pattern, text):
        if match.group(1):
            content, annotations = match.group(1), {"bold": True, "italic": True}
        elif match.group(2):
            content, annotations = match.group(2), {"bold": True}
        elif match.group(3):
            content, annotations = match.group(3), {"italic": True}
        else:
            content, annotations = match.group(4), {}
        if not content:
            continue
        segments.extend(_chunk(content, annotations or None))
    if not segments:
        segments = [{"type": "text", "text": {"content": text[:_MAX_SEG]}}]
    return segments


def _rich_paragraph(text: str) -> dict:
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": parse_rich_text(text)}}


def markdown_to_notion_blocks(text: str) -> list[dict]:
    """Convert markdown to Notion blocks: #/##/### headings, --- dividers,
    -/* bullet lists, **bold**/*italic* inline, paragraphs."""
    out: list[dict] = []
    para: list[str] = []

    def _flush() -> None:
        if para:
            out.append(_rich_paragraph(" ".join(para)))
            para.clear()

    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            _flush()
            continue
        if re.match(r"^-{3,}\s*$", stripped):
            _flush()
            out.append(make_divider())
            continue
        h = re.match(r"^(#{1,3})\s+(.+)$", stripped)
        if h:
            _flush()
            out.append(make_heading(h.group(2), level=len(h.group(1))))
            continue
        b = re.match(r"^[-*]\s+(.+)$", stripped)
        if b:
            _flush()
            out.append(
                {
                    "object": "block",
                    "type": "bulleted_list_item",
                    "bulleted_list_item": {"rich_text": parse_rich_text(b.group(1))},
                }
            )
            continue
        para.append(stripped)
    _flush()
    return out
```

- [ ] **Step 5: Run to verify it passes**

Run: `uv run python -m pytest tests/services/test_notion_blocks.py -q`
Expected: PASS (6 passed).

- [ ] **Step 6: Commit**

```bash
git add app/services/notion/__init__.py app/services/notion/blocks.py tests/services/test_notion_blocks.py
git commit -m "feat(notion): pure block builders + markdown->blocks"
```

---

## Task 6: Notion client wrapper (sync, rate-limited, 2-step upload)

**Files:**
- Create: `app/services/notion/client.py`
- Test: `tests/services/test_notion_client.py`

- [ ] **Step 1: Write the failing tests** (mock the SDK + httpx — no live calls)

```python
# tests/services/test_notion_client.py
from unittest.mock import MagicMock, patch
import pytest
from app.services.notion.client import NotionClientWrapper


def _wrapper():
    with patch("app.services.notion.client.Client") as sdk:
        w = NotionClientWrapper(api_key="ntn_test")
        w._min_interval = 0.0  # no sleeping in tests
        return w, sdk.return_value


def test_rejects_bad_key():
    with pytest.raises(ValueError):
        NotionClientWrapper(api_key="bad_key")


def test_create_page_calls_sdk():
    w, sdk = _wrapper()
    sdk.pages.create.return_value = {"id": "page_1"}
    out = w.create_page("parent_1", "1.1 Burchaklar")
    assert out["id"] == "page_1"
    kwargs = sdk.pages.create.call_args.kwargs
    assert kwargs["parent"] == {"page_id": "parent_1"}
    assert kwargs["properties"]["title"][0]["text"]["content"] == "1.1 Burchaklar"


def test_get_child_pages_filters_child_page_blocks():
    w, sdk = _wrapper()
    sdk.blocks.children.list.return_value = {
        "results": [
            {"id": "a", "type": "child_page", "child_page": {"title": "Homework"}},
            {"id": "b", "type": "paragraph", "paragraph": {}},
        ],
        "has_more": False,
    }
    pages = w.get_child_pages("parent_1")
    assert pages == [{"id": "a", "title": "Homework", "type": "child_page"}]


def test_append_block_children_chunks_at_100():
    w, sdk = _wrapper()
    sdk.blocks.children.append.return_value = {"results": []}
    children = [{"object": "block", "type": "divider", "divider": {}} for _ in range(250)]
    w.append_block_children("page_1", children)
    assert sdk.blocks.children.append.call_count == 3  # 100 + 100 + 50


def test_upload_bytes_two_step(monkeypatch):
    w, _ = _wrapper()

    class _Resp:
        def __init__(self, code, payload=None):
            self.status_code = code
            self._payload = payload or {}
            self.text = ""

        def json(self):
            return self._payload

    posts = []

    class _HttpClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, **kwargs):
            posts.append(url)
            if url.endswith("/file_uploads"):
                return _Resp(200, {"id": "upl_9"})
            return _Resp(200, {"id": "upl_9", "status": "uploaded"})

    monkeypatch.setattr("app.services.notion.client.httpx.Client", _HttpClient)
    upload_id = w.upload_bytes(b"hello", "homework.md", "text/markdown")
    assert upload_id == "upl_9"
    assert posts[0].endswith("/v1/file_uploads")
    assert posts[1].endswith("/v1/file_uploads/upl_9/send")
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run python -m pytest tests/services/test_notion_client.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.notion.client'`.

- [ ] **Step 3: Implement `client.py`** (trimmed port — Phase 1 needs create/append/list/upload only; deletion + parallel download are deferred to Phase 2)

```python
# app/services/notion/client.py
"""Sync Notion API wrapper: rate-limited notion_client.Client + raw httpx
2-step file upload. Ported (trimmed) from the s1gmamale1 reference.

This is synchronous on purpose; the async caller runs it via asyncio.to_thread.
"""

from __future__ import annotations

import time
import logging
from typing import Optional

import httpx
from notion_client import Client

logger = logging.getLogger("notion.client")

_NOTION_VERSION = "2022-06-28"
_MIN_INTERVAL = 0.35  # ~3 req/s


class NotionClientWrapper:
    def __init__(self, api_key: str):
        key = (api_key or "").strip().strip('"').strip("'")
        if not key or not key.startswith(("ntn_", "secret_")):
            raise ValueError(
                "NOTION_API_KEY missing or invalid (must start with 'ntn_' or 'secret_')."
            )
        self.api_key = key
        self.client = Client(auth=self.api_key)
        self._min_interval = _MIN_INTERVAL
        self._last_request_time = 0.0
        self._request_count = 0

    def _rate_limit(self) -> None:
        now = time.time()
        elapsed = now - self._last_request_time
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_request_time = time.time()
        self._request_count += 1

    # ─── reads ───
    def get_block_children(self, block_id: str) -> list[dict]:
        results: list[dict] = []
        cursor = None
        while True:
            self._rate_limit()
            kwargs: dict = {"block_id": block_id}
            if cursor:
                kwargs["start_cursor"] = cursor
            resp = self.client.blocks.children.list(**kwargs)
            results.extend(resp["results"])
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")
        return results

    def get_child_pages(self, parent_id: str) -> list[dict]:
        pages = []
        for block in self.get_block_children(parent_id):
            if block.get("type") == "child_page":
                pages.append(
                    {
                        "id": block["id"],
                        "title": block.get("child_page", {}).get("title", ""),
                        "type": "child_page",
                    }
                )
        return pages

    def page_has_content(self, page_id: str) -> bool:
        """True if the page already has any non-child_page block (idempotency guard)."""
        for block in self.get_block_children(page_id):
            if block.get("type") != "child_page":
                return True
        return False

    # ─── writes ───
    def create_page(self, parent_id: str, title: str, children: Optional[list[dict]] = None) -> dict:
        self._rate_limit()
        kwargs: dict = {
            "parent": {"page_id": parent_id},
            "properties": {"title": [{"text": {"content": title}}]},
        }
        if children:
            kwargs["children"] = children
        return self.client.pages.create(**kwargs)

    def append_block_children(self, block_id: str, children: list[dict]) -> dict:
        results = []
        for i in range(0, len(children), 100):
            self._rate_limit()
            res = self.client.blocks.children.append(block_id=block_id, children=children[i : i + 100])
            results.extend(res.get("results", []))
        return {"results": results}

    # ─── file upload (2-step) ───
    def upload_bytes(self, data: bytes, file_name: str, content_type: str) -> str:
        auth = {"Authorization": f"Bearer {self.api_key}", "Notion-Version": _NOTION_VERSION}
        self._rate_limit()
        with httpx.Client(timeout=30.0) as http:
            r1 = http.post(
                "https://api.notion.com/v1/file_uploads",
                headers={**auth, "Content-Type": "application/json"},
                json={"filename": file_name, "content_type": content_type},
            )
        if r1.status_code not in (200, 201):
            raise RuntimeError(f"Notion file upload init failed: {r1.status_code} — {r1.text}")
        upload_id = r1.json()["id"]

        self._rate_limit()
        with httpx.Client(timeout=120.0) as http:
            r2 = http.post(
                f"https://api.notion.com/v1/file_uploads/{upload_id}/send",
                headers=auth,
                files={"file": (file_name, data, content_type)},
            )
        if r2.status_code not in (200, 201):
            raise RuntimeError(f"Notion file upload send failed: {r2.status_code} — {r2.text}")
        return upload_id
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run python -m pytest tests/services/test_notion_client.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add app/services/notion/client.py tests/services/test_notion_client.py
git commit -m "feat(notion): sync client wrapper (rate limit + 2-step upload)"
```

---

## Task 7: Page creator (idempotent find-or-create)

**Files:**
- Create: `app/services/notion/page_creator.py`
- Test: `tests/services/test_notion_page_creator.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/services/test_notion_page_creator.py
from unittest.mock import MagicMock
from app.services.notion.page_creator import find_or_create


def test_returns_existing_when_title_matches_normalized():
    client = MagicMock()
    client.get_child_pages.return_value = [{"id": "h1", "title": "Homework (2)", "type": "child_page"}]
    page_id, created = find_or_create(client, "lesson_1", "Homework")
    assert page_id == "h1"
    assert created is False
    client.create_page.assert_not_called()


def test_creates_when_missing():
    client = MagicMock()
    client.get_child_pages.return_value = []
    client.create_page.return_value = {"id": "new_1"}
    page_id, created = find_or_create(client, "subject_1", "1.1 Burchaklar")
    assert page_id == "new_1"
    assert created is True
    client.create_page.assert_called_once_with("subject_1", "1.1 Burchaklar")
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run python -m pytest tests/services/test_notion_page_creator.py -q`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement `page_creator.py`**

```python
# app/services/notion/page_creator.py
"""Idempotent find-or-create of a child page by normalized title.

Ported (simplified) from the reference page_creator: we only ever create a
lesson page and a single `Homework` sub-page, never the full 12-sub-page template.
"""

from __future__ import annotations

import re

from .client import NotionClientWrapper


def _normalize(title: str) -> str:
    # strip trailing "(N)" dedup suffixes Notion appends, lowercase, trim
    return re.sub(r"\s*\(\d+\)\s*$", "", title.strip()).strip().lower()


def find_or_create(client: NotionClientWrapper, parent_id: str, title: str) -> tuple[str, bool]:
    """Return (page_id, created). Reuses an existing child whose normalized
    title matches; otherwise creates a new child page."""
    existing = {_normalize(c["title"]): c["id"] for c in client.get_child_pages(parent_id)}
    norm = _normalize(title)
    if norm in existing:
        return existing[norm], False
    page = client.create_page(parent_id, title.strip())
    return page["id"], True
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run python -m pytest tests/services/test_notion_page_creator.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add app/services/notion/page_creator.py tests/services/test_notion_page_creator.py
git commit -m "feat(notion): idempotent find-or-create page helper"
```

---

## Task 8: Job artifacts helper (content.json) + download refactor

**Files:**
- Create: `app/services/job_artifacts.py`
- Modify: `app/api/v1/jobs.py:280-332`
- Test: `tests/services/test_job_artifacts.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/services/test_job_artifacts.py
from types import SimpleNamespace
from app.services.job_artifacts import structured_artifacts, build_content_json


def _job():
    # Only the *_json + identity fields the helper reads; all default to None.
    fields = dict.fromkeys(
        [
            "games_json", "flashcards_json", "final_challenge_json", "memory_sprint_json",
            "reading_json", "cbp_json", "memory_check_json", "boss_arena_json",
            "source_map_json", "practice_rlc_json", "practice_error_detection_json",
            "practice_memory_match_json", "practice_tictactoe_json", "practice_jigsaw_json",
            "practice_sentence_json",
        ],
        None,
    )
    return SimpleNamespace(
        id="job-uuid", subject="geometriya-g7-11", provider="claude", model="claude-sonnet-4-6",
        assembled_md="# hw", **fields,
    )


def test_structured_artifacts_has_all_phase_files_with_defaults():
    arts = structured_artifacts(_job())
    assert arts["boss-arena.json"] == {"questions": []}
    assert arts["source-map.json"] == {"concepts": []}
    assert arts["case-based-preview.json"] == {}
    assert "memory-check.json" in arts


def test_build_content_json_wraps_metadata_and_phases():
    doc = build_content_json(_job(), generated_at="2026-06-02T00:00:00Z")
    assert doc["metadata"]["job_id"] == "job-uuid"
    assert doc["metadata"]["subject"] == "geometriya-g7-11"
    assert doc["metadata"]["generated_at"] == "2026-06-02T00:00:00Z"
    assert "boss-arena.json" in doc["phases"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run python -m pytest tests/services/test_job_artifacts.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.job_artifacts'`.

- [ ] **Step 3: Implement `job_artifacts.py`** (lift the exact dict from `jobs.py:303-326`)

```python
# app/services/job_artifacts.py
"""Serialize a HomeworkJob's structured-phase columns into downloadable
artifacts. Single source of truth shared by the /download endpoint and the
Notion archive."""

from __future__ import annotations

from typing import Any


def structured_artifacts(job: Any) -> dict[str, dict]:
    """Map filename → structured payload, with the same empty-defaults the
    download endpoint uses so every key is always present."""
    return {
        "games.json": job.games_json or {"games": []},
        "flashcards.json": job.flashcards_json or {"cards": []},
        "final-challenge.json": job.final_challenge_json or {"questions": []},
        "memory-sprint.json": job.memory_sprint_json or {"items": []},
        "reading.json": job.reading_json or {"passage_md": "", "checkpoints": []},
        "case-based-preview.json": job.cbp_json or {},
        "memory-check.json": job.memory_check_json or {"items": [], "pass_threshold": 0.60},
        "boss-arena.json": job.boss_arena_json or {"questions": []},
        "source-map.json": job.source_map_json or {"concepts": []},
        "practice-rlc.json": job.practice_rlc_json or {},
        "practice-error-detection.json": job.practice_error_detection_json or {},
        "practice-memory-match.json": job.practice_memory_match_json or {},
        "practice-tictactoe.json": job.practice_tictactoe_json or {},
        "practice-jigsaw.json": job.practice_jigsaw_json or {},
        "practice-sentence.json": job.practice_sentence_json or {},
    }


def build_content_json(job: Any, *, generated_at: str) -> dict:
    """One combined document for the Notion `content.json` attachment."""
    return {
        "metadata": {
            "job_id": str(job.id),
            "subject": job.subject,
            "provider": getattr(job, "provider", None),
            "model": getattr(job, "model", None),
            "generated_at": generated_at,
        },
        "phases": structured_artifacts(job),
    }
```

- [ ] **Step 4: Refactor the download endpoint to reuse it**

In `app/api/v1/jobs.py`, replace the inline `structured_files = {...}` literal (lines ~303-326) with:

```python
    from app.services.job_artifacts import structured_artifacts
    structured_files = structured_artifacts(job)
```

Leave the surrounding `zipfile` writing loop unchanged. (Move the import to the top of the file with the other imports if the project style prefers that.)

- [ ] **Step 5: Run to verify it passes + no regression**

Run: `uv run python -m pytest tests/services/test_job_artifacts.py -q`
Expected: PASS (2 passed).
Run: `uv run python -m pytest tests/ -q`
Expected: full suite still green (download endpoint behaviour unchanged).

- [ ] **Step 6: Commit**

```bash
git add app/services/job_artifacts.py app/api/v1/jobs.py tests/services/test_job_artifacts.py
git commit -m "refactor(jobs): extract structured_artifacts + content.json builder"
```

---

## Task 9: archive_job orchestrator (anchor resolve + push + stamps, best-effort)

**Files:**
- Create: `app/services/notion_archive.py`
- Test: `tests/services/test_notion_archive.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/services/test_notion_archive.py
from types import SimpleNamespace
from unittest.mock import MagicMock
import app.services.notion_archive as na


def test_resolve_subject_page_id_uses_subject_grade_key():
    mapping = {"geometriya-g7-11|8": "page_geo_8"}
    assert na._resolve_subject_page_id(mapping, "geometriya-g7-11", "8") == "page_geo_8"
    assert na._resolve_subject_page_id(mapping, "geometriya-g7-11", None) is None
    assert na._resolve_subject_page_id(mapping, "biology-g7-11", "8") is None


def test_lesson_title_from_section():
    assert na._lesson_title("1.1", "Burchaklar") == "1.1 Burchaklar"
    assert na._lesson_title(None, "Kirish") == "Kirish"


def test_push_skips_write_when_page_already_populated():
    client = MagicMock()
    client.page_has_content.return_value = True  # already populated
    # find_or_create returns (id, created) — patched below
    na_find = MagicMock(return_value=("hw_1", False))
    homework_id = na._push_to_notion(
        client=client,
        subject_page_id="subj_1",
        lesson_title="1.1 Burchaklar",
        assembled_md="# hw",
        content_json_bytes=b"{}",
        find_or_create=na_find,
    )
    assert homework_id == "hw_1"
    client.append_block_children.assert_not_called()
    client.upload_bytes.assert_not_called()


def test_push_writes_blocks_and_attachments_when_empty():
    client = MagicMock()
    client.page_has_content.return_value = False
    client.upload_bytes.return_value = "upl_x"
    na_find = MagicMock(side_effect=[("lesson_1", True), ("hw_1", True)])
    homework_id = na._push_to_notion(
        client=client,
        subject_page_id="subj_1",
        lesson_title="1.1 Burchaklar",
        assembled_md="# Heading\n\nbody",
        content_json_bytes=b"{}",
        find_or_create=na_find,
    )
    assert homework_id == "hw_1"
    # uploaded homework.md AND content.json
    assert client.upload_bytes.call_count == 2
    client.append_block_children.assert_called_once()
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run python -m pytest tests/services/test_notion_archive.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.notion_archive'`.

- [ ] **Step 3: Implement `notion_archive.py`**

```python
# app/services/notion_archive.py
"""Phase-1 Notion push. Best-effort: archive_job never raises into the pipeline.

Flow: resolve subject page from config map ({subject}|{grade}) → find-or-create
lesson page → find-or-create `Homework` sub-page → if empty, write rendered blocks
+ attach homework.md + content.json → stamp toc_entry + job."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Callable, Optional
from uuid import UUID

from app.config import settings
from app.db import SessionLocal
from app.repositories import jobs as jobs_repo
from app.repositories import books as books_repo
from app.repositories import toc_entries as toc_repo
from app.services.job_artifacts import build_content_json
from app.services.notion import blocks
from app.services.notion.client import NotionClientWrapper
from app.services.notion.page_creator import find_or_create

log = logging.getLogger("notion.archive")

_warned_unconfigured = False


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _resolve_subject_page_id(
    mapping: dict[str, str], subject: str, grade: Optional[str]
) -> Optional[str]:
    if not grade:
        return None
    return mapping.get(f"{subject}|{grade}")


def _lesson_title(section_number: Optional[str], section_title: str) -> str:
    return f"{section_number} {section_title}".strip() if section_number else section_title.strip()


def _push_to_notion(
    *,
    client: NotionClientWrapper,
    subject_page_id: str,
    lesson_title: str,
    assembled_md: str,
    content_json_bytes: bytes,
    find_or_create: Callable = find_or_create,
) -> str:
    """Synchronous Notion I/O. Returns the Homework page id. Idempotent:
    if the Homework page already has content, writes nothing."""
    lesson_id, _ = find_or_create(client, subject_page_id, lesson_title)
    homework_id, _ = find_or_create(client, lesson_id, "Homework")

    if client.page_has_content(homework_id):
        log.info("notion: Homework page %s already populated — skipping write", homework_id)
        return homework_id

    body = blocks.markdown_to_notion_blocks(assembled_md)
    md_upload = client.upload_bytes(assembled_md.encode("utf-8"), "homework.md", "text/markdown")
    json_upload = client.upload_bytes(content_json_bytes, "content.json", "application/json")
    body.append(blocks.make_divider())
    body.append(blocks.make_file_upload_block(md_upload, "homework.md"))
    body.append(blocks.make_file_upload_block(json_upload, "content.json"))
    client.append_block_children(homework_id, body)
    return homework_id


async def archive_job(job_id: UUID) -> None:
    """Best-effort entry point called from the pipeline after job is `done`."""
    global _warned_unconfigured
    if not settings.notion_enabled:
        return
    if not settings.notion_api_key:
        if not _warned_unconfigured:
            log.warning("notion_enabled but notion_api_key missing — skipping archive")
            _warned_unconfigured = True
        return

    try:
        async with SessionLocal() as session:
            job = await jobs_repo.get(session, job_id)
            if job is None or job.notion_archived_at is not None:
                return  # gone or already archived (idempotent on retry)
            book = await books_repo.get(session, job.book_id)
            section = await toc_repo.get(session, job.toc_entry_id)
            if book is None or section is None:
                return
            subject_page_id = _resolve_subject_page_id(
                settings.notion_subject_pages, job.subject, book.grade
            )
            if not subject_page_id:
                log.warning(
                    "notion: no subject-page mapping for subject=%s grade=%s — skipping",
                    job.subject, book.grade,
                )
                return

            lesson_title = _lesson_title(section.section_number, section.section_title)
            content_json_bytes = json.dumps(
                build_content_json(job, generated_at=_utcnow().isoformat()),
                ensure_ascii=False, indent=2,
            ).encode("utf-8")
            assembled_md = job.assembled_md or ""

            client = NotionClientWrapper(api_key=settings.notion_api_key)
            homework_id = await asyncio.to_thread(
                _push_to_notion,
                client=client,
                subject_page_id=subject_page_id,
                lesson_title=lesson_title,
                assembled_md=assembled_md,
                content_json_bytes=content_json_bytes,
            )

            await toc_repo.set_notion_homework_page_id(session, section.id, homework_id)
            await jobs_repo.set_notion_archived(session, job_id, _utcnow())
            await session.commit()
            log.info("notion: archived job %s → Homework page %s", job_id, homework_id)
    except Exception:
        log.warning("notion: archive failed for job %s (non-fatal)", job_id, exc_info=True)
```

> **Import paths (verified against the repo, 2026-06-02):** `from app.config import settings`, `from app.db import SessionLocal`, and `from app.repositories import toc_entries as toc_repo` are all correct as written — these match `pipeline.py:12-16`. `jobs_repo.get`, `books_repo.get`, `toc_repo.get` all exist (pipeline.py uses them around line 408).

- [ ] **Step 4: Run to verify it passes**

Run: `uv run python -m pytest tests/services/test_notion_archive.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add app/services/notion_archive.py tests/services/test_notion_archive.py
git commit -m "feat(notion): archive_job orchestrator (best-effort push)"
```

---

## Task 10: Pipeline hook

**Files:**
- Modify: `app/services/pipeline.py:587-606`
- Test: `tests/services/test_pipeline_notion_hook.py`

- [ ] **Step 1: Write the failing test** (the hook is called, and a raising archive does not break the pipeline)

```python
# tests/services/test_pipeline_notion_hook.py
import ast
from pathlib import Path


def test_pipeline_calls_archive_job_after_done():
    src = Path("app/services/pipeline.py").read_text(encoding="utf-8")
    assert "notion_archive" in src, "pipeline must import/call notion_archive"
    assert "archive_job" in src
    # The call must be guarded so it never re-raises into the pipeline.
    tree = ast.parse(src)
    found_guarded = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            call_names = {
                getattr(n.func, "attr", "")
                for n in ast.walk(node)
                if isinstance(n, ast.Call) and hasattr(n, "func")
            }
            if "archive_job" in call_names:
                found_guarded = True
    assert found_guarded, "archive_job must be inside a try/except"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run python -m pytest tests/services/test_pipeline_notion_hook.py -q`
Expected: FAIL — `assert "notion_archive" in src` is False.

- [ ] **Step 3: Add the import** at the top of `app/services/pipeline.py` (with the other `app.services` imports):

```python
from app.services import notion_archive
```

- [ ] **Step 4: Add the hook** in `pipeline.py`, immediately after the `job_completed` event publish (after line ~599, inside the success path, before the token-summary log):

```python
        try:
            await notion_archive.archive_job(job_id)
        except Exception:
            log.warning(f"[job {job_id}] notion archive hook failed (non-fatal)", exc_info=True)
```

> `archive_job` already swallows its own exceptions; this try/except is belt-and-suspenders so a future signature change can never fail a completed job.

- [ ] **Step 5: Run to verify it passes**

Run: `uv run python -m pytest tests/services/test_pipeline_notion_hook.py -q`
Expected: PASS.

- [ ] **Step 6: Run the full suite**

Run: `uv run python -m pytest tests/ -q`
Expected: all green (the hook is a no-op while `NOTION_ENABLED=false`).

- [ ] **Step 7: Commit**

```bash
git add app/services/pipeline.py tests/services/test_pipeline_notion_hook.py
git commit -m "feat(notion): call archive_job after job marked done (non-fatal)"
```

---

## Task 11: Manual live smoke + acceptance

**Files:**
- Create: `docs/notion-archive-smoke.md` (runbook)

> No live Notion in CI. This task is a manual runbook plus the acceptance checklist.

- [ ] **Step 1: Write the smoke runbook**

```markdown
# Notion Archive — Live Smoke (Phase 1)

Prereqs: integration token created + tree shared (see plan Prerequisite).

1. In `.env`: set `NOTION_ENABLED=true`, `NOTION_API_KEY=ntn_...`,
   `NOTION_SUBJECT_PAGES={"geometriya-g7-11|8":"<scratch-subject-page-id>"}`.
   Use a SCRATCH subject page you control for the first run.
2. `uv run alembic upgrade head` (applies 0016 if not yet).
3. Upload a Geometriya 8 book via the web UI with grade="8" (or set books.grade=8 by SQL on an existing book).
4. Generate homework for one section (provider=claude).
5. On job done, open the scratch subject page in Notion. CONFIRM:
   - a lesson page "{section_number} {section_title}" was created,
   - a "Homework" sub-page under it,
   - the Homework page shows rendered content + homework.md + content.json attachments.
6. Re-run generation for the SAME section. CONFIRM:
   - NO duplicate lesson/Homework page (find-or-create reused them),
   - the Homework page content was NOT written twice (page_has_content guard).
7. Negative: set NOTION_SUBJECT_PAGES={} and run a job → job still completes `done`,
   log shows "no subject-page mapping ... skipping". (archive non-fatal.)
8. Set NOTION_ENABLED=false again when done.
```

- [ ] **Step 2: Execute the runbook manually** and record pass/fail inline. Fix any real defects found (re-run the relevant task's tests).

- [ ] **Step 3: Final full suite + typecheck**

Run: `uv run python -m pytest tests/ -q`
Expected: all green.

- [ ] **Step 4: Commit**

```bash
git add docs/notion-archive-smoke.md
git commit -m "docs(notion): live smoke runbook for phase 1 push"
```

---

## Self-Review (completed during planning)

**Spec coverage (Phase 1 sections):**
- §2.1 not-a-database / write into tree → Tasks 7, 9. ✅
- §2.2 find-or-create lesson + Homework only → Task 7 (`find_or_create`), Task 9 (calls it twice). ✅
- §2.4 app-owned deterministic lesson title → Task 9 `_lesson_title`. ✅
- §2.6 body = rendered blocks AND attached files → Task 9 `_push_to_notion`. ✅
- §3 anchor = config map `{subject}|{grade}` → Task 1 (config), Task 9 `_resolve_subject_page_id`. ✅
- §3 idempotency: store page id + stamp archived_at + skip-if-populated → Task 9 + Task 2 columns + Task 3 setters. ✅
- §4 new module `notion_archive.py` best-effort, never raises → Task 9 + Task 10. ✅
- §4 config master switch (`notion_enabled` no-op) → Task 1 + Task 9 early return. ✅
- §4 migrations (3 columns) → Task 2. ✅
- §4 pipeline hook after done → Task 10. ✅
- §2.6 Notion limits (≤100 blocks/req, ≤2000 chars/seg) → Task 5 (`_chunk`), Task 6 (`append_block_children` chunks 100). ✅
- §6 tests: anchor resolution, find-or-create, skip-if-stamped, non-fatal, body mapping, limit chunking → Tasks 5–10. ✅
- content.json shape = reuse download serializer → Task 8. ✅

**Placeholder scan:** No TBD/TODO; every code step has complete code. Two flagged "confirm import path / confirm existing body" notes in Tasks 4 and 9 are verification reminders against real files, not placeholders — the code given is complete and correct against the anchor report.

**Type consistency:** `find_or_create(client, parent_id, title)` signature identical in Tasks 7 and 9. `structured_artifacts(job)` / `build_content_json(job, *, generated_at)` identical in Tasks 8 and 9. `NotionClientWrapper(api_key=...)` identical in Tasks 6 and 9. `set_notion_archived(session, job_id, notion_archived_at)` / `set_notion_homework_page_id(session, toc_entry_id, page_id)` identical in Tasks 3 and 9.

**Out of scope (Phase 2, not in this plan):** pulling the textbook from Notion, file download (`download_file`/parallel), deletion machinery, ingestion trigger policy.

**Known Phase-1 fidelity limitation (accepted):** `markdown_to_notion_blocks` handles headings, bullets, dividers, and inline bold/italic — but NOT markdown tables or inline SVG. The assembled packet can contain CBP `visual_svg` and teacher-note tables; those degrade to long paragraph chunks in the rendered blocks. This is acceptable because the lossless `homework.md` + `content.json` are also attached to the page. Follow-up (deferred): add table + code-fence handling to the converter.

**Plan corrections applied (2026-06-02 review):** (1) `toc` → `toc_entries` repo path (4 sites); (2) `app.db.session` → `app.db` for `SessionLocal`; (3) migration `down_revision` → real head hash `b6d2f8a4c3e9` (Alembic ids are hashes, not the `0015_...` filename) + new `revision` id `c9e3f1a07b62` to match convention. All three verified against the live tree — the migration one was a runtime breaker (`alembic upgrade head` would fail to locate the revision).
