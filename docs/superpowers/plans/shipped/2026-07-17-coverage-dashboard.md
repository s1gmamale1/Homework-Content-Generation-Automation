# Subject Coverage Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A standalone, non-technical-friendly `/dashboard` page showing, per grade, how homework generation is going for every subject — in plain language, at a glance.

**Architecture:** One new aggregate endpoint `GET /api/v1/dashboard/coverage?output_language=uz` (3 set-based queries + `classify_entries` in Python for the real lesson denominator), a pure builder in `app/services/subject_coverage.py`, and a read-only React page at `/dashboard` built from a pure status mapper (`web/src/lib/subject-coverage.ts`) plus three small presentational components. **The `/monitor` page is not touched.**

**Tech Stack:** FastAPI + SQLAlchemy async, Pydantic schemas, pytest; React 19 + react-router + @tanstack/react-query + Tailwind, node built-in test runner for pure FE logic.

## Approach & key decisions

- **User-locked (2026-07-17):** (a) **grade-first cards** — pick a grade, see its subjects as readable rows with a progress bar and a plain-English status line (a 11×26 matrix was rejected as unreadable); (b) **show the full curriculum including gaps** so missing textbooks are visible; (c) **read-only + drill-in links** — no launch/resume buttons on a page aimed at non-technical viewers.
- **A new endpoint is required, not reuse.** Verified: `GET /books` returns no TOC, no counts, no `toc_ready_at`, and caps at 100; `GET /jobs/batches` only lists *launched* books (so "ready, not started" is invisible) and is already 3N+1 (`batches.list_with_rollups` runs 3 queries per batch). Client-side assembly would need one `GET /books/{id}` per book.
- **The denominator must be launchable lessons, not `toc_total`.** `toc_total_for_batch` counts every TOC row including headers/tests/answer-keys, so "12 of 40" would be wrong. The real count needs `toc_classifier.classify_entries` (pure, no DB) run per book in Python — there is no SQL path and no existing helper that returns it.
- **Gaps are shown but collapsed.** The registry has **no per-grade curriculum map** (grade lives only on `Book.grade`), so rendering all 26 subjects expanded for every grade would assert nonsense (Geometry in Grade 1). Each grade card lists subjects **that have a textbook** first (sorted by urgency), then a collapsed `No textbook yet (N)` disclosure naming the rest. A real per-grade curriculum map is a follow-up wishlist item, not this plan.
- **Language is a first-class filter, not an aggregate.** Batches fork per `output_language` (`UNIQUE(book_id, transport, output_language)`), so mixing languages misreports. One language at a time, defaulting to `uz`, mirroring Monitor's existing language tab bar.
- **Job counts are transport-agnostic; the drill-in batch link prefers the newest batch.** A viewer asking "is homework generated?" does not care whether a legacy `cli` job produced it. Jobs are counted as the **latest job per (book, toc_entry)** for the chosen language, matching how `rollup_for_batch` defines its scope.
- **Pure logic is split out and unit-tested** on both sides — `app/services/subject_coverage.build_coverage` (no DB, no I/O) and `web/src/lib/subject-coverage.ts` (no React) — following the house pattern (`batch-status.ts` + `batch-status.test.ts`, `monitor-grouping.ts`).
- **Cross-plan collision check:** clean. PR #101 (worklog 0147) and PR #102 (teaching-audit, worklog 0148) are both **merged**; this branch is cut from `origin/Nggaev-v2` @ `6933d9d` which contains both. This plan takes worklog **0149**. Shared files touched: `docs/memory/INDEX.md`, `MASTER_MEMORY.md`, `CODE_MAP.md`, `HOW_IT_WORKS.md` (append-only, different sections), `web/src/App.tsx`, `web/src/components/layout.tsx`, `web/src/lib/api.ts`, `web/src/lib/types.ts` (all additive). No other lane is in flight.
- **Load-bearing facts verified against code:** router registration pattern in `app/api/v1/__init__.py` (sub-router + `Depends(get_current_user)`); `classify_entries(entries) -> list[str]` is pure and duck-typed on `.section_title/.page_start/.page_end` (`app/services/toc_classifier.py`), `"lesson"` is the residual class; `HomeworkJob` statuses are `pending|running|done|failed|cancelling|cancelled` (CHECK `ck_homework_jobs_status`), `output_language` ∈ `uz|en|ru`; `Book.status` ∈ `uploading|toc_extracting|toc_review|toc_ready|failed` (no CHECK), `Book.grade` is nullable `String(32)`; `Batch` has `paused_at`; `jobs.count_by_book_ids` (`app/repositories/jobs.py:329`) is the grouped-COUNT pattern to model; FE routes in `web/src/App.tsx`, nav in `web/src/components/layout.tsx:35-51` plus the `wide` list at `:15-19`; FE pure tests run under node's built-in runner (`web/package.json` → `node --import tsx --test src/lib/*.test.ts`) using top-level `node:assert`, NOT vitest; api tests are real-DB and skipped unless `RUN_DB_INTEGRATION=1` (`tests/api/conftest.py` pattern).

## Global Constraints

- **Do not modify `web/src/routes/monitor.tsx`** or any `components/fleet/*` file. Reuse-by-import is fine; editing is out of scope.
- **Read-only feature:** no new mutations, no writes, no migration. The endpoint is GET-only.
- Reuse existing shared modules rather than reimplementing: `subjectLabel`/`accentOf` (`web/src/lib/subjects.ts`), `SUBJECTS` (`web/src/lib/types.ts`), `LANG_LABEL` (`web/src/lib/language.ts`), `cn` (`web/src/lib/utils.ts`), `CARD`/`PRESSABLE`/`FRAME_ON`/`FRAME_OFF` (`web/src/lib/ui.ts`), `Card`/`Badge` (`web/src/components/ui/`), `SpaceBackdrop`.
- Stage only the files each task lists — never `git add -A`.
- Commit trailer: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- Suite bar: `uv run python -m pytest tests/ -q` green (canonical bar is WITHOUT `RUN_DB_INTEGRATION`); `cd web && npm run test`, `npx tsc -p tsconfig.app.json --noEmit`, `npm run build` all clean.

## File structure

- Create `app/services/subject_coverage.py` — pure builder: rows in → coverage entries out. No DB, no I/O.
- Create `app/repositories/subject_coverage.py` — the three set-based queries (books, TOC rows, per-TOC-entry job status).
- Create `app/api/v1/dashboard.py` — the GET route.
- Create `app/schemas/dashboard.py` — response models.
- Create `tests/services/test_subject_coverage.py`, `tests/api/test_dashboard_coverage.py`.
- Create `web/src/lib/subject-coverage.ts` + `web/src/lib/subject-coverage.test.ts` — pure state mapper + grade rollup.
- Create `web/src/routes/dashboard.tsx` — thin page shell.
- Create `web/src/components/dashboard/grade-card.tsx`, `subject-row.tsx`, `coverage-summary.tsx`.
- Modify `app/api/v1/__init__.py`, `web/src/App.tsx`, `web/src/components/layout.tsx`, `web/src/lib/api.ts`, `web/src/lib/types.ts`.

---

### Task 1: Pure coverage builder (backend)

**Files:**
- Create: `app/services/subject_coverage.py`
- Test: `tests/services/test_subject_coverage.py`

**Interfaces:**
- Produces: `CoverageEntry` (frozen dataclass), `build_coverage(books, toc_by_book, job_counts, batch_by_book) -> list[CoverageEntry]`, `entry_to_dict(entry) -> dict`.
- Consumes: `app.services.toc_classifier.classify_entries` (pure, duck-typed).

- [ ] **Step 1: Write the failing tests**

```python
# tests/services/test_subject_coverage.py
"""Unit tests for the pure subject-coverage builder (no DB, no I/O)."""
from dataclasses import dataclass
from typing import Optional

from app.services import subject_coverage as sc


@dataclass
class _Book:
    id: str
    subject: str
    grade: Optional[str]
    status: str
    source_language: str
    original_filename: str
    toc_validation: Optional[str] = None


@dataclass
class _Toc:
    id: str
    section_title: str
    page_start: Optional[int] = None
    page_end: Optional[int] = None


def _lesson_rows(n, prefix="t"):
    # plain titles with no keyword hits and no page containment -> classify as "lesson"
    return [_Toc(f"{prefix}{i}", f"Mavzu {i}", page_start=i, page_end=i) for i in range(1, n + 1)]


def test_lessons_total_counts_only_lesson_class_rows():
    book = _Book("b1", "biology", "9", "toc_ready", "uz", "bio9.pdf")
    toc = _lesson_rows(3) + [_Toc("x1", "Nazorat ishi", 9, 9), _Toc("x2", "Takrorlash", 10, 10)]
    out = sc.build_coverage([book], {"b1": toc}, {}, {})
    assert len(out) == 1
    # the test/revision rows are excluded by classify_entries
    assert out[0].lessons_total == 3
    assert out[0].done == 0 and out[0].running == 0


def test_job_counts_are_mapped_per_status():
    book = _Book("b1", "biology", "9", "toc_ready", "uz", "bio9.pdf")
    toc = _lesson_rows(12)
    # latest status per TOC entry: 5 done, 2 failed, 1 running, 3 pending, 1 cancelled
    statuses = (["done"] * 5 + ["failed"] * 2 + ["running"] + ["pending"] * 3 + ["cancelled"])
    jobs = {"b1": {row.id: st for row, st in zip(toc, statuses)}}
    out = sc.build_coverage([book], {"b1": toc}, jobs, {})
    e = out[0]
    assert (e.done, e.failed, e.running, e.pending, e.cancelled) == (5, 2, 1, 3, 1)
    assert e.lessons_total == 12


def test_jobs_on_non_lesson_rows_do_not_count_toward_done():
    # gate-1 finding: pre-#89 launches were UNFILTERED, so legacy books carry real
    # `done` jobs on test/revision rows. If those counted, a book whose only failed
    # lesson is masked by done non-lesson jobs would falsely report "Finished".
    book = _Book("b1", "biology", "9", "toc_ready", "uz", "bio9.pdf")
    lessons = _lesson_rows(3)                       # t1, t2, t3 -> lesson
    extra = [_Toc("x1", "Nazorat ishi", 9, 9), _Toc("x2", "Takrorlash", 10, 10)]
    jobs = {"b1": {
        "t1": "done", "t2": "failed", "t3": "done",  # the real lesson picture
        "x1": "done", "x2": "done",                   # legacy non-lesson jobs
    }}
    e = sc.build_coverage([book], {"b1": lessons + extra}, jobs, {})[0]
    assert e.lessons_total == 3
    assert e.done == 2          # NOT 4 — the test/revision jobs are excluded
    assert e.failed == 1
    # done < lessons_total, so the failed lesson can never be masked as "Finished"
    assert e.done < e.lessons_total


def test_cancelling_is_folded_into_running():
    book = _Book("b1", "biology", "9", "toc_ready", "uz", "bio9.pdf")
    toc = _lesson_rows(2)
    jobs = {"b1": {"t1": "cancelling", "t2": "running"}}
    e = sc.build_coverage([book], {"b1": toc}, jobs, {})[0]
    assert e.running == 2


def test_missing_toc_and_missing_jobs_default_to_zero():
    book = _Book("b1", "musiqa", "5", "toc_extracting", "uz", "mus5.pdf")
    out = sc.build_coverage([book], {}, {}, {})
    e = out[0]
    assert e.lessons_total == 0
    assert (e.done, e.failed, e.running, e.pending, e.cancelled) == (0, 0, 0, 0, 0)
    assert e.book_status == "toc_extracting"


def test_batch_link_and_paused_flag_are_threaded():
    book = _Book("b1", "biology", "9", "toc_ready", "uz", "bio9.pdf")
    out = sc.build_coverage([book], {"b1": _lesson_rows(2)}, {}, {"b1": ("batch-7", True)})
    assert out[0].batch_id == "batch-7" and out[0].paused is True

    out2 = sc.build_coverage([book], {"b1": _lesson_rows(2)}, {}, {})
    assert out2[0].batch_id is None and out2[0].paused is False


def test_null_grade_is_preserved_not_defaulted():
    book = _Book("b1", "biology", None, "toc_ready", "uz", "bio.pdf")
    out = sc.build_coverage([book], {"b1": _lesson_rows(1)}, {}, {})
    assert out[0].grade is None


def test_multiple_books_for_same_grade_subject_are_both_returned():
    # a grade+subject can legitimately have a uz AND a ru textbook — never collapse
    a = _Book("b1", "biology", "9", "toc_ready", "uz", "bio9-uz.pdf")
    b = _Book("b2", "biology", "9", "toc_ready", "ru", "bio9-ru.pdf")
    out = sc.build_coverage([a, b], {"b1": _lesson_rows(4), "b2": _lesson_rows(6)}, {}, {})
    assert {e.book_id for e in out} == {"b1", "b2"}
    assert {e.lessons_total for e in out} == {4, 6}


def test_entry_to_dict_is_json_shaped():
    book = _Book("b1", "biology", "9", "toc_ready", "uz", "bio9.pdf", toc_validation="verified")
    d = sc.entry_to_dict(sc.build_coverage([book], {"b1": _lesson_rows(2)}, {}, {})[0])
    assert d["subject"] == "biology" and d["grade"] == "9"
    assert d["lessons_total"] == 2 and d["toc_validation"] == "verified"
    import json
    json.dumps(d)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/services/test_subject_coverage.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.subject_coverage'`.

- [ ] **Step 3: Write the implementation**

```python
# app/services/subject_coverage.py
"""Pure builder for the subject-coverage dashboard.

Turns already-fetched rows (books, their TOC entries, per-book job-status
counts, per-book batch links) into one `CoverageEntry` per BOOK. No DB, no
I/O — the repository layer does the fetching, this module does the shaping,
so the interesting logic is unit-testable without Postgres.

The lesson denominator is deliberately `classify_entries(...) == "lesson"`,
NOT the raw TOC row count: `toc_total` includes headers, tests, revision and
answer-key rows, so a raw count would overstate the work ("12 of 40" against
a book with only 28 real lessons). There is no SQL path for this — the
classifier is pure Python and must run per book.

The job tally is scoped to the SAME lesson rows, which is why this module
receives per-TOC-entry job statuses rather than pre-summed counts: legacy
(pre-#89, unfiltered) launches left real `done` jobs on test/revision rows,
and counting those against a lesson-only denominator would mask a failed
lesson as "Finished".

One entry per book (never per grade+subject): a grade+subject can legitimately
hold two textbooks (e.g. a uz and a ru edition), and silently picking one would
hide the other. The frontend groups these under a single subject row.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Optional

from app.services.toc_classifier import LESSON, classify_entries


@dataclass(frozen=True)
class CoverageEntry:
    grade: Optional[str]
    subject: str
    book_id: str
    book_status: str
    source_language: str
    original_filename: str
    toc_validation: Optional[str]
    lessons_total: int  # launchable lessons (classify_entries == "lesson")
    done: int
    running: int
    pending: int
    failed: int
    cancelled: int
    batch_id: Optional[str]
    paused: bool


_COUNTED_STATUSES = ("done", "running", "pending", "failed", "cancelled")


def _lesson_tally(
    toc_rows: list, job_status: dict[str, str]
) -> tuple[int, dict[str, int]]:
    """(launchable-lesson count, per-status tally) for ONE book.

    Both numbers are scoped to the SAME set of TOC rows — those the classifier
    calls `lesson`. This scoping is load-bearing, not tidiness: pre-#89 batch
    launches were unfiltered, so legacy books carry real `done` jobs on
    test/revision/header rows. Tallying those against a lesson-only denominator
    would let non-lesson work mask a failed lesson and report "Finished"
    (a book with 3 lessons, one failed, plus 2 done test-row jobs → done=3 of 3).
    With one shared scope, `done == lessons_total` means every LESSON is done —
    correct by construction.

    `cancelling` folds into `running`: it is an in-flight state a non-technical
    viewer should not have to reason about.
    """
    tally = {s: 0 for s in _COUNTED_STATUSES}
    if not toc_rows:
        return 0, tally
    classes = classify_entries(toc_rows)
    lesson_ids = {str(row.id) for row, cls in zip(toc_rows, classes) if cls == LESSON}
    for toc_id, status in job_status.items():
        if toc_id not in lesson_ids:
            continue  # legacy job on a test/revision/header row — not lesson work
        key = "running" if status == "cancelling" else status
        if key in tally:
            tally[key] += 1
    return len(lesson_ids), tally


def build_coverage(
    books: list,
    toc_by_book: dict[str, list],
    job_status_by_book: dict[str, dict[str, str]],
    batch_by_book: dict[str, tuple[str, bool]],
) -> list[CoverageEntry]:
    """One `CoverageEntry` per book.

    `books` are row-likes with `.id/.subject/.grade/.status/.source_language/
    .original_filename/.toc_validation`. `toc_by_book` maps book id → its TOC
    rows (row-likes with `.id/.section_title/.page_start/.page_end`).
    `job_status_by_book` maps book id → {toc_entry_id: latest job status}.
    `batch_by_book` maps book id → (batch_id, is_paused). A missing key means
    zero/absent (a book with no TOC yet, or no jobs launched).
    """
    entries: list[CoverageEntry] = []
    for book in books:
        bid = str(book.id)
        batch = batch_by_book.get(bid)
        lessons_total, tally = _lesson_tally(
            toc_by_book.get(bid, []), job_status_by_book.get(bid, {})
        )
        entries.append(
            CoverageEntry(
                grade=book.grade,
                subject=book.subject,
                book_id=bid,
                book_status=book.status,
                source_language=book.source_language,
                original_filename=book.original_filename,
                toc_validation=getattr(book, "toc_validation", None),
                lessons_total=lessons_total,
                **tally,
                batch_id=batch[0] if batch else None,
                paused=bool(batch[1]) if batch else False,
            )
        )
    return entries


def entry_to_dict(entry: CoverageEntry) -> dict[str, Any]:
    return asdict(entry)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/services/test_subject_coverage.py -q`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add app/services/subject_coverage.py tests/services/test_subject_coverage.py
git commit -m "feat(dashboard): pure subject-coverage builder (lesson-class denominator)"
```

---

### Task 2: Repository queries + API endpoint

**Files:**
- Create: `app/repositories/subject_coverage.py`
- Create: `app/schemas/dashboard.py`
- Create: `app/api/v1/dashboard.py`
- Modify: `app/api/v1/__init__.py`
- Test: `tests/api/test_dashboard_coverage.py`

**Interfaces:**
- Consumes: `build_coverage`/`entry_to_dict` (Task 1).
- Produces: `GET /api/v1/dashboard/coverage?output_language=uz` → `{"output_language": "uz", "entries": [CoverageEntryOut, ...]}`.

- [ ] **Step 1: Write the failing test** (real-DB integration, skipped by default — matches `tests/api/` convention)

```python
# tests/api/test_dashboard_coverage.py
"""Real-DB proof: GET /api/v1/dashboard/coverage returns per-book coverage.

Skipped unless RUN_DB_INTEGRATION=1 + DATABASE_URL (same guard as every other
api integration test). Seeds a book + TOC rows + jobs, hits the endpoint via
AsyncClient, asserts the aggregate shape and the lesson-class denominator.
"""
from __future__ import annotations

import os
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)


async def test_coverage_lesson_scoped_denominator_and_counts(monkeypatch):
    from app import config
    from app.db import SessionLocal
    from app.models import Book, HomeworkJob, TOCEntry
    from main import app

    # House pattern (tests/api/test_sa_keys_assign_api.py:23): neutralize auth.
    # Required in a worktree — the outer /Users/macmini5/Documents/.env sets
    # AUTH_TOKEN, which defeats tests/conftest.py's os.environ.setdefault, so
    # without this the auth-protected dashboard router 401s.
    monkeypatch.setattr(config.settings, "auth_token", "")

    book_id = uuid.uuid4()
    async with SessionLocal() as s:
        s.add(Book(
            id=book_id, subject="biology", grade="9", original_filename="cov.pdf",
            content_sha256=uuid.uuid4().hex, file_size_bytes=1, status="toc_ready",
            source_language="uz",
        ))
        await s.flush()
        toc_ids = []
        for i in range(1, 4):  # 3 plain rows -> lesson
            t = TOCEntry(book_id=book_id, section_title=f"Mavzu {i}",
                         page_start=i, page_end=i, order_index=i)
            s.add(t)
            await s.flush()
            toc_ids.append(t.id)
        test_row = TOCEntry(book_id=book_id, section_title="Nazorat ishi",
                            page_start=9, page_end=9, order_index=9)  # -> test class
        s.add(test_row)
        await s.flush()

        def job(toc_entry_id, status):
            return HomeworkJob(book_id=book_id, toc_entry_id=toc_entry_id,
                               subject="biology", status=status, provider="gemini",
                               transport="api", output_language="uz")

        s.add(job(toc_ids[0], "done"))
        s.add(job(toc_ids[1], "failed"))
        # gate-1: a legacy unfiltered launch left a DONE job on the test row.
        # It must not count — otherwise done would reach lessons_total and the
        # failed lesson above would be masked as "Finished".
        s.add(job(test_row.id, "done"))
        await s.commit()

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.get("/api/v1/dashboard/coverage?output_language=uz")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["output_language"] == "uz"
        mine = [e for e in body["entries"] if e["book_id"] == str(book_id)]
        assert len(mine) == 1
        e = mine[0]
        assert e["grade"] == "9" and e["subject"] == "biology"
        assert e["lessons_total"] == 3          # the "Nazorat ishi" test row is excluded
        assert e["done"] == 1                   # NOT 2 — the test-row job is excluded
        assert e["failed"] == 1
        assert e["running"] == 0 and e["pending"] == 0
        assert e["done"] < e["lessons_total"]   # the failed lesson cannot be masked
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(HomeworkJob).where(HomeworkJob.book_id == book_id))
            await s.execute(delete(TOCEntry).where(TOCEntry.book_id == book_id))
            await s.execute(delete(Book).where(Book.id == book_id))
            await s.commit()


async def test_coverage_language_filter_excludes_other_language_jobs(monkeypatch):
    from app import config
    from app.db import SessionLocal
    from app.models import Book, HomeworkJob, TOCEntry
    from main import app

    monkeypatch.setattr(config.settings, "auth_token", "")  # see note above

    book_id = uuid.uuid4()
    async with SessionLocal() as s:
        s.add(Book(id=book_id, subject="physics", grade="8", original_filename="lang.pdf",
                   content_sha256=uuid.uuid4().hex, file_size_bytes=1,
                   status="toc_ready", source_language="uz"))
        await s.flush()
        t = TOCEntry(book_id=book_id, section_title="Mavzu 1", page_start=1,
                     page_end=1, order_index=1)
        s.add(t)
        await s.flush()
        s.add(HomeworkJob(book_id=book_id, toc_entry_id=t.id, subject="physics",
                          status="done", provider="gemini", transport="api",
                          output_language="ru"))
        await s.commit()

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            uz = (await c.get("/api/v1/dashboard/coverage?output_language=uz")).json()
            ru = (await c.get("/api/v1/dashboard/coverage?output_language=ru")).json()
        uz_e = [e for e in uz["entries"] if e["book_id"] == str(book_id)][0]
        ru_e = [e for e in ru["entries"] if e["book_id"] == str(book_id)][0]
        assert uz_e["done"] == 0   # the ru job must not leak into the uz view
        assert ru_e["done"] == 1
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(HomeworkJob).where(HomeworkJob.book_id == book_id))
            await s.execute(delete(TOCEntry).where(TOCEntry.book_id == book_id))
            await s.execute(delete(Book).where(Book.id == book_id))
            await s.commit()
```

- [ ] **Step 2: Run to verify it fails**

Run: `RUN_DB_INTEGRATION=1 DATABASE_URL=<scratch> uv run python -m pytest tests/api/test_dashboard_coverage.py -q`
Expected: FAIL with 404 (route not registered).
Also confirm the default bar skips it: `uv run python -m pytest tests/api/test_dashboard_coverage.py -q` → 2 skipped.

- [ ] **Step 3: Write the implementation**

```python
# app/repositories/subject_coverage.py
"""Set-based reads for the coverage dashboard. Three queries total, none N+1.

Modeled on `jobs.count_by_book_ids` (one grouped COUNT) rather than
`batches.list_with_rollups` (3 queries per batch), which must not be used here.
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Batch, Book, HomeworkJob, TOCEntry


async def all_books(session: AsyncSession) -> list[Book]:
    """Every book, newest first. No limit — the dashboard is a whole-fleet view
    (unlike `GET /books`, which paginates at 100)."""
    stmt = select(Book).order_by(Book.created_at.desc())
    return list((await session.execute(stmt)).scalars().all())


async def toc_rows_by_book(
    session: AsyncSession, book_ids: list[UUID]
) -> dict[str, list[TOCEntry]]:
    """All TOC rows for the given books in one query, grouped in Python and kept
    in `order_index` order (the classifier's page-containment rule reads
    neighbouring rows, so order matters)."""
    if not book_ids:
        return {}
    stmt = (
        select(TOCEntry)
        .where(TOCEntry.book_id.in_(book_ids))
        .order_by(TOCEntry.book_id, TOCEntry.order_index)
    )
    out: dict[str, list[TOCEntry]] = {}
    for row in (await session.execute(stmt)).scalars().all():
        out.setdefault(str(row.book_id), []).append(row)
    return out


async def job_status_by_book(
    session: AsyncSession, output_language: str
) -> dict[str, dict[str, str]]:
    """`{book_id: {toc_entry_id: latest_status}}` for this output language.

    Returns per-TOC-entry statuses rather than a pre-summed per-book tally: the
    builder must scope the tally to LESSON-class rows, and it can only do that
    if it can see which TOC entry each job belongs to (gate-1 finding — legacy
    unfiltered launches left `done` jobs on test/revision rows).

    "Latest job per (book, toc_entry)" is the same scope `rollup_for_batch`
    uses, so a retried lesson counts once. Still ONE query.
    """
    latest = (
        select(
            HomeworkJob.book_id.label("book_id"),
            HomeworkJob.toc_entry_id.label("toc_entry_id"),
            HomeworkJob.status.label("status"),
        )
        .where(HomeworkJob.output_language == output_language)
        .order_by(
            HomeworkJob.book_id,
            HomeworkJob.toc_entry_id,
            HomeworkJob.created_at.desc(),
        )
        .distinct(HomeworkJob.book_id, HomeworkJob.toc_entry_id)
        .subquery()
    )
    stmt = select(latest.c.book_id, latest.c.toc_entry_id, latest.c.status)
    out: dict[str, dict[str, str]] = {}
    for book_id, toc_entry_id, status in (await session.execute(stmt)).all():
        out.setdefault(str(book_id), {})[str(toc_entry_id)] = status
    return out


async def batch_by_book(
    session: AsyncSession, output_language: str
) -> dict[str, tuple[str, bool]]:
    """Newest batch per book for this language → (batch_id, is_paused), for the
    drill-in link. Transport-agnostic: a viewer asking "is homework generated?"
    does not care which transport produced it."""
    stmt = (
        select(Batch.book_id, Batch.id, Batch.paused_at)
        .where(Batch.output_language == output_language)
        .order_by(Batch.book_id, Batch.created_at.desc())
        .distinct(Batch.book_id)
    )
    return {
        str(book_id): (str(batch_id), paused_at is not None)
        for book_id, batch_id, paused_at in (await session.execute(stmt)).all()
    }
```

```python
# app/schemas/dashboard.py
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class CoverageEntryOut(BaseModel):
    grade: Optional[str]
    subject: str
    book_id: str
    book_status: str
    source_language: str
    original_filename: str
    toc_validation: Optional[str]
    lessons_total: int
    done: int
    running: int
    pending: int
    failed: int
    cancelled: int
    batch_id: Optional[str]
    paused: bool


class CoverageOut(BaseModel):
    output_language: str
    entries: list[CoverageEntryOut]
```

```python
# app/api/v1/dashboard.py
"""Read-only aggregate feeding the /dashboard page.

Deliberately NOT built on `GET /jobs/batches`: that route only sees launched
books (so "ready, not started" is invisible) and is 3N+1. This is three
set-based queries + one pure classify pass per book.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.repositories import subject_coverage as cov_repo
from app.schemas.dashboard import CoverageOut
from app.services.subject_coverage import build_coverage, entry_to_dict

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/coverage", response_model=CoverageOut)
async def coverage(
    output_language: Literal["uz", "en", "ru"] = Query("uz"),
    session: AsyncSession = Depends(get_session),
) -> CoverageOut:
    books = await cov_repo.all_books(session)
    toc_by_book = await cov_repo.toc_rows_by_book(session, [b.id for b in books])
    job_status = await cov_repo.job_status_by_book(session, output_language)
    batches = await cov_repo.batch_by_book(session, output_language)
    entries = build_coverage(books, toc_by_book, job_status, batches)
    return CoverageOut(
        output_language=output_language,
        entries=[entry_to_dict(e) for e in entries],
    )
```

Modify `app/api/v1/__init__.py` — add `dashboard` to the import list and register it (auth-protected, like every non-health router):

```python
from app.api.v1 import batch, books, dashboard, health, jobs, notion, sa_keys, settings, workers
```
```python
api_v1_router.include_router(dashboard.router, dependencies=[Depends(get_current_user)])
```

Place the `include_router` line after `sa_keys`. (`get_session` in `app/db.py:33` is the verified dependency used by the other routers; `LESSON = "lesson"` at `toc_classifier.py:18` is a real export.)

- [ ] **Step 4: Run to verify it passes**

Run: `RUN_DB_INTEGRATION=1 DATABASE_URL=<scratch> uv run python -m pytest tests/api/test_dashboard_coverage.py -q` → 2 passed.
Run: `uv run python -m pytest tests/ -q` → green, same skip count as base + 2.

- [ ] **Step 5: Commit**

```bash
git add app/repositories/subject_coverage.py app/schemas/dashboard.py app/api/v1/dashboard.py app/api/v1/__init__.py tests/api/test_dashboard_coverage.py
git commit -m "feat(dashboard): GET /dashboard/coverage aggregate (3 set-based queries)"
```

---

### Task 3: Frontend types + API client

**Files:**
- Modify: `web/src/lib/types.ts`
- Modify: `web/src/lib/api.ts`

**Interfaces:**
- Produces: `CoverageEntry`, `CoverageResponse` types; `api.getCoverage(outputLanguage: OutputLanguage): Promise<CoverageResponse>`.

- [ ] **Step 1: Add the types** (append near the other response interfaces in `web/src/lib/types.ts`)

```ts
/** One book's generation coverage, from GET /api/v1/dashboard/coverage. */
export interface CoverageEntry {
  grade: string | null;
  subject: string;
  book_id: string;
  book_status: BookStatus;
  source_language: string;
  original_filename: string;
  toc_validation: "verified" | "mismatch" | "skipped" | null;
  /** launchable lessons (TOC rows classified "lesson"), NOT raw TOC row count */
  lessons_total: number;
  done: number;
  running: number;
  pending: number;
  failed: number;
  cancelled: number;
  batch_id: string | null;
  paused: boolean;
}

export interface CoverageResponse {
  output_language: OutputLanguage;
  entries: CoverageEntry[];
}
```

- [ ] **Step 2: Add the client method** (in the `api` object literal in `web/src/lib/api.ts`, next to `listBooks`; verified idiom — `authFetch` then `unwrap<T>(res)`)

```ts
  async getCoverage(outputLanguage: OutputLanguage): Promise<CoverageResponse> {
    const res = await authFetch(
      `/api/v1/dashboard/coverage?output_language=${encodeURIComponent(outputLanguage)}`,
    );
    return unwrap<CoverageResponse>(res);
  },
```

Add `CoverageResponse` (and `OutputLanguage` if not already there) to the existing type import at the top of the file.

- [ ] **Step 3: Typecheck**

Run: `cd web && npx tsc -p tsconfig.app.json --noEmit`
Expected: clean.

- [ ] **Step 4: Commit**

```bash
git add web/src/lib/types.ts web/src/lib/api.ts
git commit -m "feat(dashboard): coverage types + api client method"
```

---

### Task 4: Pure status mapper (frontend)

**Files:**
- Create: `web/src/lib/subject-coverage.ts`
- Test: `web/src/lib/subject-coverage.test.ts`

**Interfaces:**
- Consumes: `CoverageEntry` (Task 3).
- Produces: `CoverageState`, `coverageState(e)`, `STATE_LABEL`, `STATE_TONE`, `progressOf(e)`, `stuckCount(e)`, `groupByGrade(entries)`, `GradeCoverage`, `SubjectCoverage`, `summarizeGrade(g)`, `GradeSummary`, `STATE_ORDER`.

- [ ] **Step 1: Write the failing tests**

```ts
// web/src/lib/subject-coverage.test.ts
import assert from "node:assert";
import {
  coverageState,
  groupByGrade,
  progressOf,
  stuckCount,
  summarizeGrade,
  STATE_LABEL,
} from "./subject-coverage";
import type { CoverageEntry } from "./types";

const base: CoverageEntry = {
  grade: "9", subject: "biology", book_id: "b1", book_status: "toc_ready",
  source_language: "uz", original_filename: "bio.pdf", toc_validation: "verified",
  lessons_total: 10, done: 0, running: 0, pending: 0, failed: 0, cancelled: 0,
  batch_id: null, paused: false,
};
const e = (o: Partial<CoverageEntry>): CoverageEntry => ({ ...base, ...o });

// --- state machine ---
assert.strictEqual(coverageState(null), "no_textbook");
assert.strictEqual(coverageState(e({ book_status: "uploading" })), "preparing");
assert.strictEqual(coverageState(e({ book_status: "toc_extracting" })), "preparing");
assert.strictEqual(coverageState(e({ book_status: "toc_review" })), "needs_review");
assert.strictEqual(coverageState(e({ book_status: "failed" })), "textbook_problem");
// ready: textbook prepared, nothing launched
assert.strictEqual(coverageState(e({})), "ready");
// finished: every launchable lesson done
assert.strictEqual(coverageState(e({ done: 10 })), "finished");
// in progress: anything in flight
assert.strictEqual(coverageState(e({ done: 3, running: 1 })), "in_progress");
assert.strictEqual(coverageState(e({ pending: 4 })), "in_progress");
// paused beats in-flight (a paused batch is not progressing)
assert.strictEqual(coverageState(e({ pending: 4, paused: true })), "paused");
// needs attention: failures with nothing in flight
assert.strictEqual(coverageState(e({ done: 6, failed: 4 })), "needs_attention");
// partial: some done, nothing in flight, nothing failed
assert.strictEqual(coverageState(e({ done: 6 })), "partial");
// a book with no classified lessons is "ready", never "finished" (0/0 must not read as complete)
assert.strictEqual(coverageState(e({ lessons_total: 0 })), "ready");

// --- progress ---
assert.deepStrictEqual(progressOf(e({ done: 5 })), { done: 5, total: 10, pct: 50 });
assert.deepStrictEqual(progressOf(e({ lessons_total: 0 })), { done: 0, total: 0, pct: 0 });
// done can exceed the classified total (a lesson launched from a since-reclassified row) -> clamp
assert.strictEqual(progressOf(e({ done: 12 })).pct, 100);

// --- stuck ---
assert.strictEqual(stuckCount(e({ failed: 3, cancelled: 2 })), 5);

// --- every state has a human label ---
for (const s of ["no_textbook","preparing","needs_review","textbook_problem","ready",
                 "in_progress","paused","needs_attention","partial","finished"] as const) {
  assert.ok(STATE_LABEL[s] && STATE_LABEL[s].length > 0, `missing label: ${s}`);
}

// --- grouping ---
const grouped = groupByGrade([
  e({ grade: "9", subject: "biology" }),
  e({ grade: "9", subject: "physics", book_id: "b2" }),
  e({ grade: "5", subject: "biology", book_id: "b3" }),
  e({ grade: null, subject: "musiqa", book_id: "b4" }),
]);
// numeric ascending, ungraded last
assert.deepStrictEqual(grouped.map((g) => g.grade), ["5", "9", null]);
const g9 = grouped.find((g) => g.grade === "9")!;
assert.strictEqual(g9.subjects.length, 2);
// two books for one grade+subject collapse into ONE subject entry holding both
const two = groupByGrade([
  e({ grade: "7", subject: "biology", book_id: "u", source_language: "uz" }),
  e({ grade: "7", subject: "biology", book_id: "r", source_language: "ru" }),
]);
assert.strictEqual(two[0].subjects.length, 1);
assert.strictEqual(two[0].subjects[0].books.length, 2);

// --- grade summary ---
const sum = summarizeGrade({
  grade: "9",
  subjects: [
    { subject: "biology", books: [e({ done: 10 })] },                  // finished
    { subject: "physics", books: [e({ running: 1, book_id: "b2" })] }, // in progress
    { subject: "kimyo-g7-11", books: [e({ failed: 2, done: 1, book_id: "b3" })] }, // attention
    { subject: "musiqa", books: [] },                                   // no textbook
  ],
});
assert.strictEqual(sum.withTextbook, 3);
assert.strictEqual(sum.finished, 1);
assert.strictEqual(sum.inProgress, 1);
assert.strictEqual(sum.attention, 1);
assert.strictEqual(sum.missing, 1);

console.log("subject-coverage: ok");
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd web && npm run test`
Expected: FAIL — cannot resolve `./subject-coverage`.

- [ ] **Step 3: Write the implementation**

```ts
// web/src/lib/subject-coverage.ts
/**
 * Pure status mapping for the /dashboard page (subject-coverage-1). No React —
 * typecheck-clean and unit-testable, following the batch-status.ts convention.
 *
 * The vocabulary here is deliberately plain-language: this page is read by
 * non-technical people, so states are things like "Ready to start" and
 * "Needs attention", never job statuses or transport names.
 */
import type { CoverageEntry } from "./types";

export type CoverageState =
  | "no_textbook"
  | "preparing"
  | "needs_review"
  | "textbook_problem"
  | "ready"
  | "in_progress"
  | "paused"
  | "needs_attention"
  | "partial"
  | "finished";

/** Plain-language labels. These are the words a non-technical viewer reads. */
export const STATE_LABEL: Record<CoverageState, string> = {
  no_textbook: "No textbook yet",
  preparing: "Preparing textbook",
  needs_review: "Textbook needs review",
  textbook_problem: "Textbook problem",
  ready: "Ready to start",
  in_progress: "In progress",
  paused: "Paused",
  needs_attention: "Needs attention",
  partial: "Started, not running",
  finished: "Finished",
};

/** Visual tone per state — consumed by the row component for chip colour. */
export const STATE_TONE: Record<CoverageState, "good" | "busy" | "warn" | "idle"> = {
  no_textbook: "idle",
  preparing: "busy",
  needs_review: "warn",
  textbook_problem: "warn",
  ready: "idle",
  in_progress: "busy",
  paused: "warn",
  needs_attention: "warn",
  partial: "warn",
  finished: "good",
};

/** Sort order: the things needing a human come first, finished work last. */
export const STATE_ORDER: CoverageState[] = [
  "needs_attention",
  "textbook_problem",
  "needs_review",
  "paused",
  "partial",
  "in_progress",
  "preparing",
  "ready",
  "finished",
  "no_textbook",
];

export function coverageState(entry: CoverageEntry | null): CoverageState {
  if (!entry) return "no_textbook";
  switch (entry.book_status) {
    case "uploading":
    case "toc_extracting":
      return "preparing";
    case "toc_review":
      return "needs_review";
    case "failed":
      return "textbook_problem";
  }
  const inFlight = entry.running + entry.pending;
  // A book whose TOC yielded no classified lessons has nothing to finish —
  // report it as ready, never as "finished" off a 0/0 division.
  if (entry.lessons_total > 0 && entry.done >= entry.lessons_total) return "finished";
  if (entry.paused) return "paused";
  if (inFlight > 0) return "in_progress";
  if (entry.failed > 0) return "needs_attention";
  if (entry.done > 0) return "partial";
  return "ready";
}

export function progressOf(entry: CoverageEntry): {
  done: number;
  total: number;
  pct: number;
} {
  const total = entry.lessons_total;
  const done = entry.done;
  if (total <= 0) return { done: 0, total: 0, pct: 0 };
  return { done, total, pct: Math.min(100, Math.round((done / total) * 100)) };
}

/** Lessons a human may need to look at (failed + cancelled). */
export function stuckCount(entry: CoverageEntry): number {
  return entry.failed + entry.cancelled;
}

export interface SubjectCoverage {
  subject: string;
  /** usually 1; >1 when a grade+subject has several textbook editions */
  books: CoverageEntry[];
}

export interface GradeCoverage {
  grade: string | null; // null = ungraded bucket
  subjects: SubjectCoverage[];
}

/** Numeric grade ascending; the ungraded bucket always last. */
export function groupByGrade(entries: CoverageEntry[]): GradeCoverage[] {
  const byGrade = new Map<string, Map<string, CoverageEntry[]>>();
  for (const e of entries) {
    const gk = e.grade ?? " ungraded";
    const subjects = byGrade.get(gk) ?? new Map<string, CoverageEntry[]>();
    subjects.set(e.subject, [...(subjects.get(e.subject) ?? []), e]);
    byGrade.set(gk, subjects);
  }
  const out: GradeCoverage[] = [...byGrade.entries()].map(([gk, subjects]) => ({
    grade: gk === " ungraded" ? null : gk,
    subjects: [...subjects.entries()].map(([subject, books]) => ({ subject, books })),
  }));
  return out.sort((a, b) => {
    if (a.grade === null) return 1;
    if (b.grade === null) return -1;
    const na = Number(a.grade);
    const nb = Number(b.grade);
    if (Number.isNaN(na) || Number.isNaN(nb)) return a.grade.localeCompare(b.grade);
    return na - nb;
  });
}

export interface GradeSummary {
  withTextbook: number;
  finished: number;
  inProgress: number;
  attention: number;
  missing: number;
}

const ATTENTION: CoverageState[] = [
  "needs_attention",
  "textbook_problem",
  "needs_review",
  "paused",
];

/** Headline counts for a grade card. A subject counts once, by its worst book. */
export function summarizeGrade(grade: GradeCoverage): GradeSummary {
  let withTextbook = 0;
  let finished = 0;
  let inProgress = 0;
  let attention = 0;
  let missing = 0;
  for (const s of grade.subjects) {
    if (s.books.length === 0) {
      missing += 1;
      continue;
    }
    withTextbook += 1;
    const states = s.books.map(coverageState);
    const worst = STATE_ORDER.find((st) => states.includes(st)) ?? "ready";
    if (ATTENTION.includes(worst)) attention += 1;
    else if (worst === "finished") finished += 1;
    else if (worst === "in_progress" || worst === "preparing" || worst === "partial")
      inProgress += 1;
  }
  return { withTextbook, finished, inProgress, attention, missing };
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd web && npm run test` → prints `subject-coverage: ok`, exit 0 (and the pre-existing lib tests still pass).

- [ ] **Step 5: Commit**

```bash
git add web/src/lib/subject-coverage.ts web/src/lib/subject-coverage.test.ts
git commit -m "feat(dashboard): pure coverage state mapper + grade rollup"
```

---

### Task 5: The dashboard page, components, route and nav

**Files:**
- Create: `web/src/components/dashboard/subject-row.tsx`
- Create: `web/src/components/dashboard/grade-card.tsx`
- Create: `web/src/components/dashboard/coverage-summary.tsx`
- Create: `web/src/routes/dashboard.tsx`
- Modify: `web/src/App.tsx`, `web/src/components/layout.tsx`

**Interfaces:**
- Consumes: `api.getCoverage` (Task 3); everything from `web/src/lib/subject-coverage.ts` (Task 4); `subjectLabel`/`accentOf` from `lib/subjects.ts`; `SUBJECTS` from `lib/types.ts`; `LANG_LABEL` from `lib/language.ts`; `cn` from `lib/utils.ts`; `CARD`/`PRESSABLE`/`FRAME_ON`/`FRAME_OFF` from `lib/ui.ts`.

- [ ] **Step 1: Write `subject-row.tsx`** — one subject line: name, progress bar, plain-language status, optional stuck count, drill-in link.

```tsx
// web/src/components/dashboard/subject-row.tsx
import { Link } from "react-router-dom";
import type { CoverageEntry } from "@/lib/types";
import {
  coverageState,
  progressOf,
  stuckCount,
  STATE_LABEL,
  STATE_TONE,
  type SubjectCoverage,
} from "@/lib/subject-coverage";
import { subjectLabel } from "@/lib/subjects";
import { cn } from "@/lib/utils";

const TONE_CHIP: Record<string, string> = {
  good: "bg-emerald-400/12 text-emerald-200 border-emerald-300/25",
  busy: "bg-sky-400/12 text-sky-200 border-sky-300/25",
  warn: "bg-amber-400/12 text-amber-200 border-amber-300/25",
  idle: "bg-white/[0.06] text-white/55 border-white/10",
};

const TONE_BAR: Record<string, string> = {
  good: "bg-emerald-400/70",
  busy: "bg-sky-400/70",
  warn: "bg-amber-400/70",
  idle: "bg-white/25",
};

function BookLine({ entry }: { entry: CoverageEntry }) {
  const state = coverageState(entry);
  const tone = STATE_TONE[state];
  const { done, total, pct } = progressOf(entry);
  const stuck = stuckCount(entry);
  return (
    <Link
      to={`/book/${entry.book_id}`}
      className="block rounded-xl px-3 py-2.5 transition-colors hover:bg-white/[0.04]"
    >
      <div className="flex items-center gap-3">
        <div className="h-2 min-w-24 flex-1 overflow-hidden rounded-full bg-white/[0.07]">
          <div
            className={cn("h-full rounded-full transition-[width]", TONE_BAR[tone])}
            style={{ width: `${pct}%` }}
          />
        </div>
        <span className="w-24 shrink-0 text-right text-sm tabular-nums text-white/70">
          {total > 0 ? `${done} of ${total}` : "—"}
        </span>
        <span
          className={cn(
            "shrink-0 rounded-lg border px-2 py-0.5 text-xs font-medium",
            TONE_CHIP[tone],
          )}
        >
          {STATE_LABEL[state]}
        </span>
      </div>
      {(stuck > 0 || entry.source_language !== "uz") && (
        <p className="mt-1 text-xs text-white/45">
          {stuck > 0 && `${stuck} lesson${stuck === 1 ? "" : "s"} need a look`}
          {stuck > 0 && entry.source_language !== "uz" && " · "}
          {entry.source_language !== "uz" && `${entry.source_language.toUpperCase()} textbook`}
        </p>
      )}
    </Link>
  );
}

export function SubjectRow({ item }: { item: SubjectCoverage }) {
  return (
    <div className="border-t border-white/[0.06] py-2 first:border-t-0">
      <p className="px-3 text-sm font-medium text-white/85">{subjectLabel(item.subject)}</p>
      {item.books.length === 0 ? (
        <p className="px-3 py-2 text-sm text-white/40">No textbook yet</p>
      ) : (
        item.books.map((b) => <BookLine key={b.book_id} entry={b} />)
      )}
    </div>
  );
}
```

- [ ] **Step 2: Write `grade-card.tsx`** — one grade: headline counts, subjects that have a textbook (sorted by urgency), then a collapsed gap list.

```tsx
// web/src/components/dashboard/grade-card.tsx
import { useState } from "react";
import { ChevronDown } from "lucide-react";
import {
  coverageState,
  summarizeGrade,
  STATE_ORDER,
  type GradeCoverage,
} from "@/lib/subject-coverage";
import { subjectLabel } from "@/lib/subjects";
import { CARD } from "@/lib/ui";
import { cn } from "@/lib/utils";
import { SubjectRow } from "./subject-row";

function worstRank(item: GradeCoverage["subjects"][number]): number {
  if (item.books.length === 0) return STATE_ORDER.length;
  const states = item.books.map(coverageState);
  const idx = STATE_ORDER.findIndex((s) => states.includes(s));
  return idx === -1 ? STATE_ORDER.length : idx;
}

export function GradeCard({ grade }: { grade: GradeCoverage }) {
  const [showGaps, setShowGaps] = useState(false);
  const summary = summarizeGrade(grade);
  const present = grade.subjects
    .filter((s) => s.books.length > 0)
    .sort((a, b) => worstRank(a) - worstRank(b) || subjectLabel(a.subject).localeCompare(subjectLabel(b.subject)));
  const missing = grade.subjects
    .filter((s) => s.books.length === 0)
    .map((s) => subjectLabel(s.subject))
    .sort((a, b) => a.localeCompare(b));

  return (
    <section className={cn(CARD, "overflow-hidden")}>
      <header className="border-b border-white/[0.07] px-4 py-3">
        <h2 className="text-lg font-semibold tracking-tight">
          {grade.grade === null ? "Ungraded" : `Grade ${grade.grade}`}
        </h2>
        <p className="mt-0.5 text-sm text-white/50">
          {summary.withTextbook} subject{summary.withTextbook === 1 ? "" : "s"} with a textbook
          {summary.finished > 0 && ` · ${summary.finished} finished`}
          {summary.inProgress > 0 && ` · ${summary.inProgress} in progress`}
          {summary.attention > 0 && ` · ${summary.attention} need attention`}
        </p>
      </header>

      <div className="px-1 py-1">
        {present.length === 0 ? (
          <p className="px-4 py-6 text-sm text-white/40">
            No textbooks for this grade yet.
          </p>
        ) : (
          present.map((s) => <SubjectRow key={s.subject} item={s} />)
        )}
      </div>

      {missing.length > 0 && (
        <div className="border-t border-white/[0.07]">
          <button
            type="button"
            onClick={() => setShowGaps((v) => !v)}
            aria-expanded={showGaps}
            className="flex w-full items-center justify-between px-4 py-2.5 text-sm text-white/50 transition-colors hover:text-white/75"
          >
            <span>No textbook yet ({missing.length})</span>
            <ChevronDown className={cn("size-4 transition-transform", showGaps && "rotate-180")} />
          </button>
          {showGaps && (
            <p className="px-4 pb-3 text-sm leading-relaxed text-white/40">
              {missing.join(" · ")}
            </p>
          )}
        </div>
      )}
    </section>
  );
}
```

- [ ] **Step 3: Write `coverage-summary.tsx`** — the top overview strip (4 plain-language tiles across all grades).

```tsx
// web/src/components/dashboard/coverage-summary.tsx
import { summarizeGrade, type GradeCoverage } from "@/lib/subject-coverage";
import { CARD } from "@/lib/ui";
import { cn } from "@/lib/utils";

function Tile({ label, value, tone }: { label: string; value: number; tone: string }) {
  return (
    <div className={cn(CARD, "px-4 py-3")}>
      <p className={cn("text-2xl font-semibold tabular-nums", tone)}>{value}</p>
      <p className="mt-0.5 text-xs text-white/50">{label}</p>
    </div>
  );
}

export function CoverageSummary({ grades }: { grades: GradeCoverage[] }) {
  const totals = grades.reduce(
    (acc, g) => {
      const s = summarizeGrade(g);
      return {
        finished: acc.finished + s.finished,
        inProgress: acc.inProgress + s.inProgress,
        attention: acc.attention + s.attention,
        missing: acc.missing + s.missing,
      };
    },
    { finished: 0, inProgress: 0, attention: 0, missing: 0 },
  );
  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      <Tile label="Finished" value={totals.finished} tone="text-emerald-300" />
      <Tile label="In progress" value={totals.inProgress} tone="text-sky-300" />
      <Tile label="Need attention" value={totals.attention} tone="text-amber-300" />
      <Tile label="No textbook yet" value={totals.missing} tone="text-white/70" />
    </div>
  );
}
```

- [ ] **Step 4: Write the page** — thin shell: language tabs, grade tabs, summary, the selected grade's card. Every grade in the fixed 1–11 axis appears even with no books, and every registry subject appears within a grade (missing ones land in the collapsed gap list).

```tsx
// web/src/routes/dashboard.tsx
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { SpaceBackdrop } from "@/components/space-backdrop";
import { CoverageSummary } from "@/components/dashboard/coverage-summary";
import { GradeCard } from "@/components/dashboard/grade-card";
import { api } from "@/lib/api";
import { LANG_LABEL } from "@/lib/language";
import { groupByGrade, type GradeCoverage } from "@/lib/subject-coverage";
import { SUBJECTS, type OutputLanguage } from "@/lib/types";
import { FRAME_OFF, FRAME_ON, PRESSABLE } from "@/lib/ui";
import { cn } from "@/lib/utils";

const LANGS: OutputLanguage[] = ["uz", "en", "ru"];
const GRADES = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11"];

/** Fill the fixed grade axis and, within each grade, every registry subject —
 *  so a missing textbook is visibly a gap rather than an absent row. */
function withFullCurriculum(groups: GradeCoverage[]): GradeCoverage[] {
  const byGrade = new Map(groups.map((g) => [g.grade, g]));
  const ungraded = byGrade.get(null);
  const axis: (string | null)[] = [...GRADES, ...(ungraded ? [null] : [])];
  return axis.map((grade) => {
    const found = byGrade.get(grade);
    const bySubject = new Map((found?.subjects ?? []).map((s) => [s.subject, s]));
    return {
      grade,
      subjects: SUBJECTS.map((subject) => bySubject.get(subject) ?? { subject, books: [] }),
    };
  });
}

export function DashboardPage() {
  const [lang, setLang] = useState<OutputLanguage>("uz");
  const [grade, setGrade] = useState<string | null>("9");

  const q = useQuery({
    queryKey: ["coverage", lang],
    queryFn: () => api.getCoverage(lang),
    refetchInterval: 10_000,
  });

  const grades = useMemo(
    () => withFullCurriculum(groupByGrade(q.data?.entries ?? [])),
    [q.data],
  );
  const selected = grades.find((g) => g.grade === grade) ?? grades[0];

  return (
    <>
      <SpaceBackdrop />
      <div className="relative z-10 space-y-5">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Subject dashboard</h1>
          <p className="mt-1 text-white/55">
            How homework generation is going, grade by grade.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {LANGS.map((l) => (
            <button
              key={l}
              type="button"
              onClick={() => setLang(l)}
              className={cn(
                "rounded-xl px-3 py-1.5 text-xs font-medium",
                PRESSABLE,
                l === lang ? FRAME_ON : FRAME_OFF,
              )}
            >
              {LANG_LABEL[l]}
            </button>
          ))}
        </div>

        {q.isLoading ? (
          <p className="text-white/50">Loading…</p>
        ) : q.isError ? (
          <p className="text-amber-200">Could not load the dashboard. Retrying…</p>
        ) : (
          <>
            <CoverageSummary grades={grades} />

            <div className="flex flex-wrap items-center gap-2">
              {grades.map((g) => (
                <button
                  key={g.grade ?? "ungraded"}
                  type="button"
                  onClick={() => setGrade(g.grade)}
                  className={cn(
                    "rounded-xl px-3 py-1.5 text-xs font-medium",
                    PRESSABLE,
                    g.grade === selected?.grade ? FRAME_ON : FRAME_OFF,
                  )}
                >
                  {g.grade === null ? "Ungraded" : `Grade ${g.grade}`}
                </button>
              ))}
            </div>

            {selected && <GradeCard key={selected.grade ?? "ungraded"} grade={selected} />}
          </>
        )}
      </div>
    </>
  );
}
```

- [ ] **Step 5: Register the route and nav**

In `web/src/App.tsx`: add `import { DashboardPage } from "@/routes/dashboard";` alongside the other route imports, and `<Route path="/dashboard" element={<DashboardPage />} />` inside the protected group (next to the `/monitor` line).

In `web/src/components/layout.tsx`: add `LayoutDashboard` to the existing `lucide-react` import, add the nav entry after the Monitor one —
```tsx
              <NavItem to="/dashboard" icon={<LayoutDashboard className="size-4" />}>
                Dashboard
              </NavItem>
```
— and add `pathname.startsWith("/dashboard") ||` to the `wide` expression (`layout.tsx:15-19`) so the page gets the 1200px content width.

- [ ] **Step 6: Verify**

Run: `cd web && npx tsc -p tsconfig.app.json --noEmit` → clean.
Run: `cd web && npm run test` → all lib tests pass.
Run: `cd web && npm run build` → clean.

- [ ] **Step 7: Commit**

```bash
git add web/src/components/dashboard/subject-row.tsx web/src/components/dashboard/grade-card.tsx web/src/components/dashboard/coverage-summary.tsx web/src/routes/dashboard.tsx web/src/App.tsx web/src/components/layout.tsx
git commit -m "feat(dashboard): /dashboard page — grade cards, plain-language status, gap list"
```

---

### Task 6: Acceptance — real data, read-only ($0)

**Controller-run.** This feature makes **no model calls**, so acceptance costs nothing; the proof is that the endpoint returns correct numbers against the production DB and the page renders them.

- [ ] **Step 1:** Start the API against the real DB (the user starts servers; ask if not already running) and hit the endpoint:

```bash
curl -s "http://localhost:8000/api/v1/dashboard/coverage?output_language=uz" \
  -H "Authorization: Bearer $AUTH_TOKEN" | python3 -m json.tool | head -40
```

- [ ] **Step 2: Cross-check the denominator AND the lesson-scoping against a known book.** Pick one entry, then verify `lessons_total` equals its `lesson`-classified TOC rows and that `done` counts only jobs on those rows. Prefer a **legacy book** (one launched before #89's lesson filter) — that is where non-lesson jobs actually exist and where the scoping bug would have shown:

```bash
uv run python - <<'PY'
import asyncio, uuid
from app.db import SessionLocal
from app.repositories import subject_coverage as cov
from app.services.toc_classifier import classify_entries
BOOK = "<book-id-from-step-1>"
async def main():
    async with SessionLocal() as s:
        toc = (await cov.toc_rows_by_book(s, [uuid.UUID(BOOK)]))[BOOK]
        classes = classify_entries(toc)
        lesson_ids = {str(r.id) for r, c in zip(toc, classes) if c == "lesson"}
        status = (await cov.job_status_by_book(s, "uz")).get(BOOK, {})
        on_lesson = {t: st for t, st in status.items() if t in lesson_ids}
        off_lesson = {t: st for t, st in status.items() if t not in lesson_ids}
        print("toc rows:", len(toc), "lesson-class:", len(lesson_ids))
        print("done on lesson rows:", sum(1 for st in on_lesson.values() if st == "done"))
        print("jobs on NON-lesson rows (must be excluded):", len(off_lesson),
              "of which done:", sum(1 for st in off_lesson.values() if st == "done"))
asyncio.run(main())
PY
```

The endpoint's `lessons_total` must equal `lesson-class`, and its `done` must equal **done on lesson rows** — NOT that plus the non-lesson dones. If the script reports non-lesson done jobs and the endpoint's `done` includes them, the scoping regressed. **A mismatch means the aggregate is wrong — stop and fix before shipping.**

- [ ] **Step 3: Render check.** With the SPA built (`cd web && npm run build`) and served by the API, open `/dashboard`: confirm the grade tabs, that a known in-progress subject shows a partial bar with a sensible "N of M", that a subject with no textbook appears only in the collapsed gap list, and that clicking a subject opens its book page. Confirm `/monitor` is unchanged.

- [ ] **Step 4:** Note any wording that reads as jargon to a non-technical eye and fix it in `STATE_LABEL` (with the test updated) — this page's whole purpose is plain language.

---

### Task 7: Finish (docs + worklog + PR)

- [ ] **Step 1:** De-stale reference docs: add the endpoint + page to `docs/CODE_MAP.md` (service, repo, route, FE lib/page) and a short "Subject dashboard" subsection to `docs/HOW_IT_WORKS.md` near the monitor/quality material, stating explicitly that the denominator is lesson-class TOC rows and that the page is read-only.
- [ ] **Step 2:** Worklog **0149** in `docs/memory/MASTER_MEMORY.md` + an `INDEX.md` row (**re-check the INDEX tail at write time** — numbers go stale mid-lane; 0147 = #101, 0148 = #102, both merged).
- [ ] **Step 3:** Add the deferred idea to `docs/memory/WISHLIST.md`: `per-grade-curriculum-map-1 — the subject registry has no grade mapping, so the dashboard shows all 26 subjects as potential gaps for every grade; a real per-grade expected-subject map would make the gap list accurate.`
- [ ] **Step 4:** `git mv docs/superpowers/plans/2026-07-17-coverage-dashboard.md docs/superpowers/plans/shipped/`.
- [ ] **Step 5:** Rebase check (`git fetch origin && git log HEAD..origin/Nggaev-v2`) → rebase + re-run both suites if the base moved. Then push and open the PR for the external gate (GK2). **Implementer does not self-merge.**
- [ ] **Step 6: Commit**

```bash
git add docs/CODE_MAP.md docs/HOW_IT_WORKS.md docs/memory/MASTER_MEMORY.md docs/memory/INDEX.md docs/memory/WISHLIST.md docs/superpowers/plans/shipped/2026-07-17-coverage-dashboard.md
git commit -m "docs(dashboard): worklog 0149 + code-map + how-it-works; archive plan"
```

---

## Out of scope (explicitly)

- **Any change to `/monitor` or `components/fleet/*`** — the user asked for a separate page; Monitor keeps its batch-centric view.
- **Actions (launch / resume / cancel)** — read-only by decision; the launcher already owns those flows.
- **A real per-grade curriculum map** — deferred to `per-grade-curriculum-map-1` (see Task 7 Step 3).
- **Per-transport breakdown** — a viewer asking "is homework generated?" doesn't care; the language filter is the only axis.
- **Caching / materialized rollups** — three set-based queries at a 10s poll is fine at fleet scale; revisit only if measured slow.
