# Book Grade Derivation + Silent-Skip Visibility — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop producing gradeless books (which silently defeat Notion archiving), heal existing ones, and make any remaining archive-skip visible.

**Architecture:** Derive grade from the filename at the shared `ingest_pdf` chokepoint when the caller omits it (explicit wins); backfill existing NULL-grade rows via an Alembic data migration; add a `notion_skip_reason` column that `archive_job` stamps on resolvable skip branches (and clears on success); surface it in `JobOut` + the job page; re-archive the existing backlog with a one-off sweep.

**Tech Stack:** FastAPI, SQLAlchemy (async), Alembic, Pydantic v2, pytest, React/TS/Vite.

**Spec:** `docs/superpowers/specs/2026-06-06-book-grade-derivation-design.md`

**Standing rules:** Stage only the files each task lists (never `git add -A`). Run pytest via `.\.venv\Scripts\python.exe -m pytest` (uv not on PATH). Use the PowerShell tool for `&`-prefixed calls. Postgres is `edu-postgres` on `-p 5433:5432`.

**⚠ Migration head check (do once before Task 3):** the current Alembic head is `a7c1e9d2b4f8` (0019_phase_provider). Another session may have added a migration since. Run `.\.venv\Scripts\python.exe -m alembic heads` and use the ACTUAL single head as `down_revision` for Task 3's migration; Task 4 chains off Task 3.

---

## File Structure

- **Create** `app/services/grade.py` — pure `derive_grade_from_filename` helper (one responsibility: filename → grade string or None).
- **Modify** `app/api/v1/books.py:70` — one line in `ingest_pdf`.
- **Create** `alembic/versions/0020_backfill_book_grade.py` — one-off data backfill (regex inlined).
- **Modify** `app/models/homework_job.py:33` — new `notion_skip_reason` column.
- **Create** `alembic/versions/0021_notion_skip_reason.py` — add the column.
- **Modify** `app/repositories/jobs.py` — `set_notion_skip_reason` + clear-on-archive.
- **Modify** `app/services/notion_archive.py` — stamp skip reason on resolvable branches.
- **Modify** `app/schemas/job.py:36` — optional `notion_skip_reason` on `JobOut`.
- **Modify** `web/src/lib/types.ts` + `web/src/routes/job.tsx` — type + indicator.
- **Create** `scripts/rearchive_unarchived.py` — one-off sweep.
- **Tests:** `tests/services/test_grade.py`, `tests/api/test_ingest_grade.py`, `tests/services/test_notion_archive_skip.py`.

---

## Task 1: `derive_grade_from_filename` helper

**Files:**
- Create: `app/services/grade.py`
- Test: `tests/services/test_grade.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/services/test_grade.py
import pytest
from app.services.grade import derive_grade_from_filename


@pytest.mark.parametrize("name,expected", [
    ("7-sinf_Algebra_2022_(elekton_darslikbot).pdf", "7"),
    ("8-sinf_Ingliz_tili_darslik_2022.pdf", "8"),
    ("9 sinf fizika.pdf", "9"),
    ("5-klass_russkiy.pdf", "5"),
    ("7-класс_история.pdf", "7"),
    ("11-SINF_GEOMETRIYA.PDF", "11"),
    ("algebra_final.pdf", None),
    ("12-sinf_too_high.pdf", None),
    ("0-sinf.pdf", None),
    ("", None),
    (None, None),
])
def test_derive_grade_from_filename(name, expected):
    assert derive_grade_from_filename(name) == expected
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/services/test_grade.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'app.services.grade'`).

- [ ] **Step 3: Write minimal implementation**

```python
# app/services/grade.py
"""Best-effort grade extraction from a textbook filename.

The book.grade column is the Notion-archive key ({subject}|{grade}); a NULL
grade silently defeats archiving. Uzbek textbook filenames almost always state
the grade ("7-sinf_Algebra…"), so when the uploader omits it we derive it here.
Best-effort only: an unparseable name returns None (and the archive then
surfaces the skip rather than failing silently)."""
from __future__ import annotations

import re
from typing import Optional

# "<n> sinf|klass|класс" with optional separator; case-insensitive.
_GRADE_RE = re.compile(r"(\d{1,2})\s*[-_ ]?\s*(?:sinf|klass|класс)", re.IGNORECASE)


def derive_grade_from_filename(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    m = _GRADE_RE.search(name)
    if not m:
        return None
    n = int(m.group(1))
    if 1 <= n <= 11:          # supported band; reject 0, 12+
        return str(n)
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.\.venv\Scripts\python.exe -m pytest tests/services/test_grade.py -q`
Expected: PASS (11 cases).

- [ ] **Step 5: Commit**

```bash
git add app/services/grade.py tests/services/test_grade.py
git commit -m "feat(grade): derive_grade_from_filename helper"
```

---

## Task 2: Wire derivation into `ingest_pdf`

**Files:**
- Modify: `app/api/v1/books.py` (import + line 70 area)
- Test: `tests/api/test_ingest_grade.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_ingest_grade.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/api/test_ingest_grade.py -q`
Expected: FAIL on `test_ingest_derives_grade_when_absent` (`captured["grade"]` is `None`, not `"7"`).

- [ ] **Step 3: Add the import and the one-line derivation**

In `app/api/v1/books.py`, add the import near the other `app.services` imports at the top of the file:

```python
from app.services.grade import derive_grade_from_filename
```

Then in `ingest_pdf`, immediately before the `book = await books_repo.create(` call (currently `books.py:70`), insert:

```python
    # Derive grade from the filename when the caller didn't supply one — a NULL
    # grade silently defeats Notion archiving ({subject}|{grade} key). Explicit
    # grade always wins; the dedup hit above already returned, so this only runs
    # for genuinely new books.
    grade = grade or derive_grade_from_filename(filename)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.\.venv\Scripts\python.exe -m pytest tests/api/test_ingest_grade.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add app/api/v1/books.py tests/api/test_ingest_grade.py
git commit -m "feat(ingest): derive book grade from filename when not supplied"
```

---

## Task 3: Backfill existing gradeless books (Alembic data migration)

**Files:**
- Create: `alembic/versions/0020_backfill_book_grade.py`

- [ ] **Step 1: Confirm the current head**

Run: `.\.venv\Scripts\python.exe -m alembic heads`
Expected: a single head `a7c1e9d2b4f8 (head)`. If it differs, use that value as `down_revision` below.

- [ ] **Step 2: Write the data migration**

```python
# alembic/versions/0020_backfill_book_grade.py
"""Backfill books.grade from the filename for NULL/empty rows.

A NULL grade silently defeats Notion archiving. New books are fixed at ingest;
this one-off heals existing rows. The grade regex is INLINED (not imported from
app code) because migrations are frozen snapshots and must not depend on
evolving application logic.
"""
import re
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b3f6a1c2d4e5"
down_revision: Union[str, Sequence[str], None] = "a7c1e9d2b4f8"
branch_labels = None
depends_on = None

_GRADE_RE = re.compile(r"(\d{1,2})\s*[-_ ]?\s*(?:sinf|klass|класс)", re.IGNORECASE)


def _derive(name: str | None) -> str | None:
    if not name:
        return None
    m = _GRADE_RE.search(name)
    if not m:
        return None
    n = int(m.group(1))
    return str(n) if 1 <= n <= 11 else None


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT id, original_filename FROM books "
            "WHERE grade IS NULL OR grade = ''"
        )
    ).fetchall()
    for row in rows:
        grade = _derive(row.original_filename)
        if grade is not None:
            conn.execute(
                sa.text("UPDATE books SET grade = :g WHERE id = :id"),
                {"g": grade, "id": row.id},
            )


def downgrade() -> None:
    # No-op: cannot know which rows were NULL before this backfill.
    pass
```

- [ ] **Step 3: Apply and verify on the dev DB**

Run: `.\.venv\Scripts\python.exe -m alembic upgrade head`
Then verify both known books got grades:
Run: `docker exec edu-postgres psql -U edu -d edu_homework -c "SELECT subject, grade, original_filename FROM books WHERE original_filename LIKE '%sinf%';"`
Expected: `math-algebra | 7 | 7-sinf_Algebra…` and `english | 8 | 8-sinf_Ingliz_tili…` (both now non-NULL).

- [ ] **Step 4: Confirm the suite still imports/migrates cleanly**

Run: `.\.venv\Scripts\python.exe -m pytest tests/ -q`
Expected: same baseline as before (the one pre-existing `test_notion_defaults_disabled` red; everything else green).

- [ ] **Step 5: Commit**

```bash
git add alembic/versions/0020_backfill_book_grade.py
git commit -m "feat(db): backfill books.grade from filename for NULL rows"
```

---

## Task 4: Add `notion_skip_reason` column (model + migration)

**Files:**
- Modify: `app/models/homework_job.py` (after line 33)
- Create: `alembic/versions/0021_notion_skip_reason.py`

- [ ] **Step 1: Add the column to the model**

In `app/models/homework_job.py`, immediately after the `notion_archived_at` mapped_column (line 31-33), add:

```python
    # Why a `done` job was NOT pushed to Notion (resolvable causes only:
    # no subject-page mapping, no completed phases, missing book/section).
    # NULL = archived, not-yet-attempted, or archiving disabled. Cleared on a
    # successful archive. Makes the silent-skip failure mode visible.
    notion_skip_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
```

(`Text` and `Optional` are already imported in this file.)

- [ ] **Step 2: Write the schema migration**

```python
# alembic/versions/0021_notion_skip_reason.py
"""Add homework_jobs.notion_skip_reason (silent-skip visibility)."""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c4a7b2d3e6f0"
down_revision: Union[str, Sequence[str], None] = "b3f6a1c2d4e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "homework_jobs",
        sa.Column("notion_skip_reason", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("homework_jobs", "notion_skip_reason")
```

- [ ] **Step 3: Apply the migration**

Run: `.\.venv\Scripts\python.exe -m alembic upgrade head`
Then verify the column exists:
Run: `docker exec edu-postgres psql -U edu -d edu_homework -c "\d homework_jobs" | findstr notion_skip_reason`
Expected: a `notion_skip_reason | text` line.

- [ ] **Step 4: Commit**

```bash
git add app/models/homework_job.py alembic/versions/0021_notion_skip_reason.py
git commit -m "feat(db): add homework_jobs.notion_skip_reason column"
```

---

## Task 5: Repo helpers — `set_notion_skip_reason` + clear on archive

**Files:**
- Modify: `app/repositories/jobs.py` (the `set_notion_archived` region, ~line 125-131)

- [ ] **Step 1: Write the failing test**

```python
# tests/services/test_notion_archive_skip.py  (first half — repo behavior)
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from app.repositories import jobs as jobs_repo


def test_set_notion_skip_reason_sets_field():
    job = SimpleNamespace(notion_skip_reason=None)
    session = SimpleNamespace(get=AsyncMock(return_value=job))
    asyncio.run(jobs_repo.set_notion_skip_reason(session, uuid4(), "no Notion page for x|5"))
    assert job.notion_skip_reason == "no Notion page for x|5"


def test_set_notion_archived_clears_skip_reason():
    from datetime import datetime, timezone
    job = SimpleNamespace(notion_archived_at=None, notion_skip_reason="stale")
    session = SimpleNamespace(get=AsyncMock(return_value=job))
    asyncio.run(jobs_repo.set_notion_archived(session, uuid4(), datetime.now(timezone.utc)))
    assert job.notion_archived_at is not None
    assert job.notion_skip_reason is None   # cleared on success
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/services/test_notion_archive_skip.py -q`
Expected: FAIL (`set_notion_skip_reason` doesn't exist; `set_notion_archived` doesn't clear).

- [ ] **Step 3: Add the helper and the clear**

In `app/repositories/jobs.py`, change `set_notion_archived` to also clear the reason, and add the new helper right after it:

```python
async def set_notion_archived(
    session: AsyncSession, job_id: UUID, notion_archived_at: datetime
) -> None:
    job = await session.get(HomeworkJob, job_id)
    if job is None:
        return
    job.notion_archived_at = notion_archived_at
    job.notion_skip_reason = None   # success clears any prior skip marker


async def set_notion_skip_reason(
    session: AsyncSession, job_id: UUID, reason: Optional[str]
) -> None:
    job = await session.get(HomeworkJob, job_id)
    if job is None:
        return
    job.notion_skip_reason = reason
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.\.venv\Scripts\python.exe -m pytest tests/services/test_notion_archive_skip.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add app/repositories/jobs.py tests/services/test_notion_archive_skip.py
git commit -m "feat(repo): set_notion_skip_reason + clear on archive"
```

---

## Task 6: Stamp skip reason in `archive_job`

**Files:**
- Modify: `app/services/notion_archive.py` (the `archive_job` body, ~line 163-218)
- Test: `tests/services/test_notion_archive_skip.py` (append)

- [ ] **Step 1: Write the failing test (append to the file from Task 5)**

```python
# tests/services/test_notion_archive_skip.py  (second half — archive_job branches)
import app.services.notion_archive as na


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def commit(self):
        return None


def test_archive_marks_skip_on_no_mapping():
    jid = uuid4()
    job = SimpleNamespace(id=jid, notion_archived_at=None, subject="math-algebra",
                          book_id=uuid4(), toc_entry_id=uuid4())
    book = SimpleNamespace(grade="5", original_filename="x.pdf")
    section = SimpleNamespace(id=uuid4(), section_number="1", section_title="T")
    set_skip = AsyncMock()
    with patch.object(na.settings, "notion_enabled", True), \
         patch.object(na.settings, "notion_api_key", "k"), \
         patch.object(na.settings, "notion_subject_pages", {}), \
         patch.object(na, "SessionLocal", lambda: _FakeSession()), \
         patch.object(na.jobs_repo, "get", AsyncMock(return_value=job)), \
         patch.object(na.books_repo, "get", AsyncMock(return_value=book)), \
         patch.object(na.toc_repo, "get", AsyncMock(return_value=section)), \
         patch.object(na.jobs_repo, "set_notion_skip_reason", set_skip):
        asyncio.run(na.archive_job(jid))
    set_skip.assert_awaited_once()
    assert "math-algebra|5" in set_skip.await_args.args[2]


def test_archive_no_skip_mark_when_disabled():
    set_skip = AsyncMock()
    with patch.object(na.settings, "notion_enabled", False), \
         patch.object(na.jobs_repo, "set_notion_skip_reason", set_skip):
        asyncio.run(na.archive_job(uuid4()))
    set_skip.assert_not_awaited()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/services/test_notion_archive_skip.py -q`
Expected: FAIL on `test_archive_marks_skip_on_no_mapping` (`set_skip` not awaited — archive_job doesn't stamp yet).

- [ ] **Step 3: Add the stamps in `archive_job`**

In `app/services/notion_archive.py`, modify the three resolvable skip branches. The two inside the open session block (`book/section None`, `no subject_page_id`) MUST commit before returning — that first session block is otherwise read-only and the write would roll back. The no-phases branch runs after the block closes, so it uses a fresh session.

Replace the `book/section None` branch:
```python
            if book is None or section is None:
                await jobs_repo.set_notion_skip_reason(
                    session, job_id, "book/section row missing")
                await session.commit()
                return
```

Replace the no-mapping branch:
```python
            if not subject_page_id:
                log.warning(
                    "notion: no subject-page mapping for subject=%s grade=%s — skipping",
                    job.subject, book.grade,
                )
                await jobs_repo.set_notion_skip_reason(
                    session, job_id, f"no Notion page for {job.subject}|{book.grade}")
                await session.commit()
                return
```

Replace the no-phases branch (this is AFTER `# session closed`, so open a fresh session):
```python
        if not phase_md:
            log.info("notion: job %s has no completed phase outputs — skipping", job_id)
            async with SessionLocal() as session:
                await jobs_repo.set_notion_skip_reason(
                    session, job_id, "no completed phase outputs")
                await session.commit()
            return
```

Do NOT touch the `notion_enabled` (`:166`), `notion_api_key` (`:168`), or already-archived/gone (`:177`) early returns. The success path already clears the reason via `set_notion_archived` (Task 5).

- [ ] **Step 4: Run test to verify it passes (and the full suite)**

Run: `.\.venv\Scripts\python.exe -m pytest tests/services/test_notion_archive_skip.py tests/services/test_notion_archive.py -q`
Expected: PASS (4 new + existing archive tests green).

- [ ] **Step 5: Commit**

```bash
git add app/services/notion_archive.py tests/services/test_notion_archive_skip.py
git commit -m "feat(notion): stamp notion_skip_reason on resolvable archive skips"
```

---

## Task 7: Surface `notion_skip_reason` in API + job page

**Files:**
- Modify: `app/schemas/job.py` (JobOut, after line 36)
- Modify: `web/src/lib/types.ts`
- Modify: `web/src/routes/job.tsx`

- [ ] **Step 1: Add the optional field to `JobOut`**

In `app/schemas/job.py`, add to `JobOut` (after `phases: list[PhaseOut] = []`):

```python
    notion_skip_reason: Optional[str] = None
```

(Optional with default → no existing `JobOut.model_validate` call site or test stub breaks.)

- [ ] **Step 2: Mirror in the FE type**

In `web/src/lib/types.ts`, find the `Job` interface (the shape returned by `getJob`) and add:

```typescript
  notion_skip_reason?: string | null;
```

- [ ] **Step 3: Capture and render it in the job page**

In `web/src/routes/job.tsx`:

(a) Add state near the other `useState` declarations (after `const [status, setStatus] = useState<JobStatus | null>(null);`):

```typescript
  const [notionSkip, setNotionSkip] = useState<string | null>(null);
```

(b) In the mount `getJob(id).then((j) => { … })` block, after `setStatus(j.status);`, add:

```typescript
        setNotionSkip(j.notion_skip_reason ?? null);
```

(c) Render a neutral note — place it just before the closing `</>` of the component's return, after the `status === "cancelled"` block:

```tsx
      {status === "done" && notionSkip && (
        <div className="mt-4 inline-flex items-center gap-2 rounded-(--radius-md) border border-(--color-border) bg-(--color-elevated) px-3 py-2 text-sm text-(--color-ink-muted)">
          Not archived to Notion: {notionSkip}
        </div>
      )}
```

- [ ] **Step 4: Typecheck**

Run (PowerShell tool): `Set-Location 'C:\Users\Recruiter\Desktop\Homework-Content-Generation-Automation\web'; npx tsc -p tsconfig.app.json --noEmit`
Expected: no output (clean).

Then confirm the backend still imports:
Run: `.\.venv\Scripts\python.exe -m pytest tests/api -q`
Expected: green (no JobOut regressions).

- [ ] **Step 5: Commit**

```bash
git add app/schemas/job.py web/src/lib/types.ts web/src/routes/job.tsx
git commit -m "feat(web): surface notion_skip_reason on the job page"
```

---

## Task 8: One-off re-archive sweep script

**Files:**
- Create: `scripts/rearchive_unarchived.py`

- [ ] **Step 1: Write the script**

```python
# scripts/rearchive_unarchived.py
"""Re-run the Notion archive for every `done` job that was never archived.

One-off operational tool. `archive_job` is idempotent (it no-ops on jobs that
already have notion_archived_at) and best-effort, so this is safe to re-run:
resolvable jobs get archived, the rest get a notion_skip_reason. Not wired into
startup. Run: .\\.venv\\Scripts\\python.exe -m scripts.rearchive_unarchived
"""
import asyncio

from sqlalchemy import select

from app.db import SessionLocal
from app.models import HomeworkJob
from app.services.notion_archive import archive_job


async def main() -> None:
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(HomeworkJob.id).where(
                    HomeworkJob.status == "done",
                    HomeworkJob.notion_archived_at.is_(None),
                )
            )
        ).scalars().all()
    print(f"re-archiving {len(rows)} done+unarchived job(s)")
    for job_id in rows:
        await archive_job(job_id)
        print(f"  processed {job_id}")
    print("done")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Run it (requires the server NOT to be needed — it's standalone)**

Run: `.\.venv\Scripts\python.exe -m scripts.rearchive_unarchived`
Expected: `re-archiving 6 done+unarchived job(s)` then one line per job, then `done`.

- [ ] **Step 3: Verify outcomes in the DB**

Run: `docker exec edu-postgres psql -U edu -d edu_homework -c "SELECT subject, notion_archived_at IS NOT NULL AS archived, notion_skip_reason FROM homework_jobs WHERE status='done' ORDER BY archived;"`
Expected: the 2 english + 1 algebra + the kimyo/history jobs now show `archived = t` (where the `{subject}|{grade}` key resolves); any that legitimately can't resolve show a non-NULL `notion_skip_reason` instead of being invisible.

- [ ] **Step 4: Commit**

```bash
git add scripts/rearchive_unarchived.py
git commit -m "chore(notion): one-off re-archive sweep for unarchived done jobs"
```

---

## Finish

- [ ] Full suite: `.\.venv\Scripts\python.exe -m pytest tests/ -q` — expect green except the one pre-existing `test_notion_defaults_disabled` red.
- [ ] Frontend build sanity: `Set-Location web; npx tsc -p tsconfig.app.json --noEmit` clean.
- [ ] `superpowers:finishing-a-development-branch` (user decides push; usually push to Nggaev-v2).
- [ ] Worklog entry in `docs/memory/MASTER_MEMORY.md` + INDEX row; note the gradeless-book arm of the `notion-subject-page-map` silent-skip is now closed.
- [ ] ⚠ Restart the server to load the new archive/JobOut code (same restart pending for 0038/0040/0041).
