# Notion Archive Stale Detection + Auto-Replace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop stale Notion pages from silently reading as "archived" after a regen — stamp the producing job on each lesson page so the archiver auto-replaces its own older output, and surface any residual staleness as «N stale» in the Fleet UI with a one-click targeted refresh.

**Architecture:** Add `toc_entries.notion_archived_job_id` — the job whose content is actually on the lesson's Notion page. `archive_job` sets it whenever it writes (first archive or replace) and uses it to decide whether a populated page is its own older output (safe to clear+rewrite) vs. untouched. The batch archive rollup joins that stamp to split archived → fresh/stale; a targeted `done_stale_job_ids` worklist powers a force-refresh of only the stale pages.

**Tech Stack:** FastAPI, SQLAlchemy (async), Alembic, Postgres, React + TanStack Query + TypeScript.

---

## Approach & key decisions

- **Root cause (verified against code):** `pipeline.py:443` auto-archives a regen job (`job_B`, `notion_archived_at=NULL`); `_push_to_notion` (`notion_archive.py:166`) sees the leaf page already populated (prior `job_A`'s content) → **skips** but still returns the page id → `job_B` gets `notion_archived_at` stamped (`:296`) → `archive_rollup_for_batch` (`batches.py:147`) counts it archived. Page keeps `job_A`'s stale content. Nothing records *which* job produced a page, so the archiver keeps the safe skip-default. Confirmed twice live (G7-alg 4/47).
- **Chosen (user-approved: "Both — auto-fix + visibility"):** (1) `toc_entries.notion_archived_job_id` stamp; (2) `archive_job` auto-replaces its own older output on a regen (`stamp != this job` ⟹ `replace=True`, no operator action); (3) rollup `stale` count + Fleet «N stale» chip + a targeted "Refresh stale" button.
- **Ownership fact that makes auto-replace safe:** every homework is filed under our own `"Generated Homeworks"` container and human-page adoption is *not* performed (`notion_archive.py:10-11` docstring) — so a populated leaf page under it is *always our own output*. The only residual risk is a human hand-editing our generated page before a regen; the user accepted this (a regen is an explicit "give me fresh content" signal).
- **Stamp decision needs NO push-return-signature change (verified):** `archive_job` decides purely from columns it already loads — `first_archive = section.notion_homework_page_id is None` (we set that id only when we archive, so `None` ⟹ genuine first write); `auto_replace = section.notion_archived_job_id is not None and != job_id`. It stamps iff `first_archive or force or auto_replace` (all three imply the push wrote). This avoids threading `content_written` back through `_push_to_notion`/`_push_with_retry`, so their existing mocks stay valid.
- **Stale signal = our own stamp, not Notion `last_edited_time`** (rejected alt): comparing the stored producing-job id is deterministic, needs zero Notion API calls per rollup (last_edited would be N calls, rate-limited), and isn't fooled by unrelated edits bumping `last_edited`.
- **Strict stale definition** (`stamp IS NOT NULL AND stamp != latest_job.id`): precisely flags confirmed own-older-output mismatches; a **NULL stamp is NOT flagged**. Consequence: pre-feature husks (archived before this migration → stamp NULL) are invisible to the chip and not auto-healed — a bounded one-time set the operator clears with the existing `force=true` sweep (`done_job_ids`, rewrites all). Rejected the "loose" definition (NULL counts as stale) because it would scream «all-N stale» on old batches at deploy and trigger mass Notion rewrites. With auto-replace shipped, strict-stale stays ~0 in healthy operation and rises only during the transient window between a regen completing and its archive, or on a regression — exactly the health signal wanted.
- **Column is a plain nullable `UUID` (no FK):** it's a soft content-provenance stamp, not a relational-integrity requirement; a plain column avoids ON-DELETE ordering with the existing `homework_jobs.toc_entry_id` FK.

## Global Constraints (reviewer attention lens)

- `_resolve_subject_page_id`, the skip-if-populated default for **stamp-NULL** pages, and "no human-page adoption" behavior must be UNCHANGED. Auto-replace fires ONLY when `notion_archived_job_id` is non-NULL and differs from the archiving job (or `force=True`).
- `archive_rollup_for_batch` must keep `archived`/`unarchived` values byte-identical to today; `stale` is an ADDITIVE subset of `archived`.
- `stale` count is strict: `notion_archived_at IS NOT NULL AND notion_archived_job_id IS NOT NULL AND notion_archived_job_id != latest_job.id`. A NULL stamp is never stale.
- Real-DB tests: guard with `RUN_DB_INTEGRATION`, pin `127.0.0.1` (not `localhost`), never run without an explicit scratch `DATABASE_URL`.
- Migration revision id ≤ 32 chars; `down_revision = "0044_solver_boss_toggle"` (current head).
- Stage only the files each task lists. No `git add -A`.

---

## Task 1: Migration + model column

**Files:**
- Create: `alembic/versions/0045_notion_archived_job.py`
- Modify: `app/models/toc_entry.py`
- Test: `tests/integration/test_migration_0045_archived_job.py`

- [ ] **Step 1: Write the migration**

Create `alembic/versions/0045_notion_archived_job.py`:

```python
"""add toc_entries.notion_archived_job_id (which job's content is on the page)

Revision ID: 0045_notion_archived_job
Revises: 0044_solver_boss_toggle
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0045_notion_archived_job"
down_revision = "0044_solver_boss_toggle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "toc_entries",
        sa.Column("notion_archived_job_id", postgresql.UUID(as_uuid=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("toc_entries", "notion_archived_job_id")
```

- [ ] **Step 2: Add the model column**

In `app/models/toc_entry.py`, add the import and column. Change the import line:

```python
from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID as PgUUID
```

Add the column right after `notion_homework_page_id` (line 27):

```python
    notion_homework_page_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    # The homework_jobs.id whose content is currently on the Notion page. Set by
    # notion_archive.archive_job when it writes (first archive or replace); used
    # to auto-replace our own older output after a regen and to compute the
    # batch "stale" rollup. NULL = never archived by us, or a pre-stamp husk.
    notion_archived_job_id: Mapped[Optional[UUID]] = mapped_column(PgUUID(as_uuid=True), nullable=True)
```

- [ ] **Step 3: Write the failing real-DB migration test**

Create `tests/integration/test_migration_0045_archived_job.py`:

```python
"""Real-DB check that migration 0045 adds toc_entries.notion_archived_job_id
(nullable, defaults NULL). Run:

  createdb -U macmini5 edu_mig0045_test
  DATABASE_URL=postgresql+asyncpg://macmini5@127.0.0.1:5432/edu_mig0045_test \
    RUN_DB_INTEGRATION=1 uv run --extra dev python -m pytest \
    tests/integration/test_migration_0045_archived_job.py -q
  dropdb -U macmini5 edu_mig0045_test
"""
import asyncio
import os

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION") != "1", reason="needs real Postgres"
)

from sqlalchemy import text
from app.db import engine


def test_notion_archived_job_id_column_exists():
    async def _check():
        async with engine.connect() as conn:
            row = (await conn.execute(text(
                "SELECT is_nullable, data_type FROM information_schema.columns "
                "WHERE table_name='toc_entries' AND column_name='notion_archived_job_id'"
            ))).first()
        assert row is not None, "column missing — did alembic upgrade head run?"
        assert row.is_nullable == "YES"
        assert row.data_type == "uuid"

    asyncio.run(_check())
```

- [ ] **Step 4: Run it against a scratch DB — verify it fails without the migration, passes with it**

```bash
createdb -U macmini5 edu_mig0045_test
DATABASE_URL=postgresql+asyncpg://macmini5@127.0.0.1:5432/edu_mig0045_test RUN_DB_INTEGRATION=1 uv run --extra dev alembic upgrade head
DATABASE_URL=postgresql+asyncpg://macmini5@127.0.0.1:5432/edu_mig0045_test RUN_DB_INTEGRATION=1 uv run --extra dev python -m pytest tests/integration/test_migration_0045_archived_job.py -q
dropdb -U macmini5 edu_mig0045_test
```
Expected: PASS after `upgrade head`.

- [ ] **Step 5: Confirm no offline test regressions**

Run: `uv run python -m pytest tests/ -q -k "toc_entry or migration"`
Expected: PASS (offline tests skip the real-DB one).

- [ ] **Step 6: Commit**

```bash
git add alembic/versions/0045_notion_archived_job.py app/models/toc_entry.py tests/integration/test_migration_0045_archived_job.py
git commit -m "notionstale: add toc_entries.notion_archived_job_id (mig 0045)"
```

---

## Task 2: Repo setter + archive_job stamp & auto-replace

**Files:**
- Modify: `app/repositories/toc_entries.py`
- Modify: `app/services/notion_archive.py:263-297`
- Modify (fixtures): `tests/services/test_notion_archive_force.py`, `tests/services/test_notion_archive_language.py`, `tests/services/test_notion_archive_skip.py`
- Test: `tests/services/test_notion_archive_stamp.py` (new)

- [ ] **Step 1: Add the repo setter**

In `app/repositories/toc_entries.py`, right after `set_notion_homework_page_id`:

```python
async def set_notion_archived_job(
    session: AsyncSession, toc_entry_id: UUID, job_id: UUID
) -> None:
    """Stamp which homework_job's content is currently on the lesson's Notion
    page. Set only when archive_job actually writes (first archive or replace)."""
    entry = await session.get(TOCEntry, toc_entry_id)
    if entry is None:
        return
    entry.notion_archived_job_id = job_id
```

- [ ] **Step 2: Write the failing stamp/auto-replace unit tests**

Create `tests/services/test_notion_archive_stamp.py`:

```python
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

import app.services.notion_archive as na


def _job(archived=False):
    return SimpleNamespace(
        id=uuid4(), book_id=uuid4(), toc_entry_id=uuid4(),
        subject="geometriya-g7-11", output_language="uz",
        notion_archived_at=(datetime.now(timezone.utc) if archived else None),
    )


def _section(job, *, page_id=None, archived_job_id=None):
    return SimpleNamespace(
        id=job.toc_entry_id, section_number="1", section_title="L",
        notion_homework_page_id=page_id, notion_archived_job_id=archived_job_id,
    )


def _wire(monkeypatch, job, section):
    monkeypatch.setattr(na.settings, "notion_enabled", True)
    monkeypatch.setattr(na.settings, "notion_api_key", "ntn_x")
    monkeypatch.setattr(na.settings, "notion_subject_pages", {"geometriya-g7-11|8": "subj"})
    book = SimpleNamespace(grade="8", original_filename="g8.pdf", id=job.book_id)
    phase = SimpleNamespace(phase_name="case-based-preview", status="done", output_md="# CBP")
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(na, "SessionLocal", MagicMock(return_value=session))
    return book, phase


@pytest.mark.asyncio
async def test_first_archive_stamps_producing_job(monkeypatch):
    job = _job()
    section = _section(job, page_id=None, archived_job_id=None)   # never filed
    book, phase = _wire(monkeypatch, job, section)
    with patch.object(na.jobs_repo, "get", AsyncMock(return_value=job)), \
         patch.object(na.books_repo, "get", AsyncMock(return_value=book)), \
         patch.object(na.toc_repo, "get", AsyncMock(return_value=section)), \
         patch.object(na.phase_repo, "list_for_job", AsyncMock(return_value=[phase])), \
         patch.object(na.toc_repo, "set_notion_homework_page_id", AsyncMock()), \
         patch.object(na.toc_repo, "set_notion_archived_job", AsyncMock()) as stamp, \
         patch.object(na.jobs_repo, "set_notion_archived", AsyncMock()), \
         patch.object(na, "NotionClientWrapper", MagicMock()), \
         patch.object(na, "_push_with_retry", AsyncMock(return_value="hw")) as push:
        await na.archive_job(job.id)
    assert push.await_args.kwargs["replace"] is False       # empty page → plain write
    stamp.assert_awaited_once()
    assert stamp.await_args.args[2] == job.id


@pytest.mark.asyncio
async def test_regen_auto_replaces_own_older_output_and_restamps(monkeypatch):
    job = _job()                                             # the newer regen job
    older = uuid4()
    section = _section(job, page_id="hw", archived_job_id=older)  # page is OUR older output
    book, phase = _wire(monkeypatch, job, section)
    with patch.object(na.jobs_repo, "get", AsyncMock(return_value=job)), \
         patch.object(na.books_repo, "get", AsyncMock(return_value=book)), \
         patch.object(na.toc_repo, "get", AsyncMock(return_value=section)), \
         patch.object(na.phase_repo, "list_for_job", AsyncMock(return_value=[phase])), \
         patch.object(na.toc_repo, "set_notion_homework_page_id", AsyncMock()), \
         patch.object(na.toc_repo, "set_notion_archived_job", AsyncMock()) as stamp, \
         patch.object(na.jobs_repo, "set_notion_archived", AsyncMock()), \
         patch.object(na, "NotionClientWrapper", MagicMock()), \
         patch.object(na, "_push_with_retry", AsyncMock(return_value="hw")) as push:
        await na.archive_job(job.id)                          # NO force
    assert push.await_args.kwargs["replace"] is True          # auto-replace fired
    assert stamp.await_args.args[2] == job.id                 # re-stamped to the newer job


@pytest.mark.asyncio
async def test_husk_no_stamp_no_replace(monkeypatch):
    """Populated page with a NULL stamp (pre-feature husk / human-edited-ours):
    skip-default preserved — no replace, no (mis-)stamp."""
    job = _job()
    section = _section(job, page_id="hw", archived_job_id=None)  # populated but unstamped
    book, phase = _wire(monkeypatch, job, section)
    with patch.object(na.jobs_repo, "get", AsyncMock(return_value=job)), \
         patch.object(na.books_repo, "get", AsyncMock(return_value=book)), \
         patch.object(na.toc_repo, "get", AsyncMock(return_value=section)), \
         patch.object(na.phase_repo, "list_for_job", AsyncMock(return_value=[phase])), \
         patch.object(na.toc_repo, "set_notion_homework_page_id", AsyncMock()), \
         patch.object(na.toc_repo, "set_notion_archived_job", AsyncMock()) as stamp, \
         patch.object(na.jobs_repo, "set_notion_archived", AsyncMock()), \
         patch.object(na, "NotionClientWrapper", MagicMock()), \
         patch.object(na, "_push_with_retry", AsyncMock(return_value="hw")) as push:
        await na.archive_job(job.id)
    assert push.await_args.kwargs["replace"] is False
    stamp.assert_not_awaited()
```

- [ ] **Step 3: Run to verify they fail**

Run: `uv run python -m pytest tests/services/test_notion_archive_stamp.py -q`
Expected: FAIL (`archive_job` doesn't read `notion_archived_job_id` or call `set_notion_archived_job` yet).

- [ ] **Step 4: Implement stamp + auto-replace in `archive_job`**

In `app/services/notion_archive.py`, in the first session block, capture the two provenance fields alongside `section_id` (currently line 263):

```python
            section_id = section.id
            prior_page_id = section.notion_homework_page_id
            prior_job_id = section.notion_archived_job_id
            lesson_title = _lesson_title(section.section_number, section.section_title)
```

After the `phase_md` build / `if not phase_md` block, just before constructing the client, compute the replace decision:

```python
        # A leaf page under 'Generated Homeworks' is always our own output (no
        # human-page adoption — see module docstring), so a regen may safely
        # clear+rewrite it. first_archive: never filed this lesson (we set the
        # page id only when we archive). auto_replace: the page holds a DIFFERENT
        # (older) job's content. Both write → we (re)stamp the producing job.
        first_archive = prior_page_id is None
        auto_replace = prior_job_id is not None and prior_job_id != job_id
        do_replace = force or auto_replace

        client = NotionClientWrapper(api_key=settings.notion_api_key)
        try:
            homework_id = await _push_with_retry(
                client=client,
                subject_page_id=subject_page_id,
                lesson_title=lesson_title,
                phase_md=phase_md,
                replace=do_replace,
            )
```

(Delete the old `replace=force`.) Then in the success session block, stamp when we wrote:

```python
        async with SessionLocal() as session:
            await toc_repo.set_notion_homework_page_id(session, section_id, homework_id)
            if first_archive or do_replace:
                await toc_repo.set_notion_archived_job(session, section_id, job_id)
            await jobs_repo.set_notion_archived(session, job_id, _utcnow())
            await session.commit()
```

- [ ] **Step 5: Fix the existing SimpleNamespace section fixtures**

Existing tests build a `section = SimpleNamespace(...)` without the two new attributes; `archive_job` now reads them. Add `notion_homework_page_id=None, notion_archived_job_id=None` to every `SimpleNamespace(id=..., section_number=..., section_title=...)` section fixture in:
- `tests/services/test_notion_archive_force.py` (the `section` in `test_archive_job_force_pushes_with_replace_on_already_archived`)
- `tests/services/test_notion_archive_language.py`
- `tests/services/test_notion_archive_skip.py`

Also, wherever those tests `patch.object(na.toc_repo, "set_notion_homework_page_id", ...)`, add a sibling `patch.object(na.toc_repo, "set_notion_archived_job", AsyncMock())` so the new call is stubbed. (Grep each file for `set_notion_homework_page_id` and mirror it.)

- [ ] **Step 6: Run the full notion_archive suite**

Run: `uv run python -m pytest tests/services/ -q -k notion_archive`
Expected: PASS (new stamp tests + all existing, incl. the force test asserting `replace is True`).

- [ ] **Step 7: Commit**

```bash
git add app/repositories/toc_entries.py app/services/notion_archive.py tests/services/test_notion_archive_stamp.py tests/services/test_notion_archive_force.py tests/services/test_notion_archive_language.py tests/services/test_notion_archive_skip.py
git commit -m "notionstale: stamp producing job + auto-replace own stale output"
```

---

## Task 3: Rollup `stale` split + `done_stale_job_ids` worklist

**Files:**
- Modify: `app/repositories/batches.py`
- Modify: `tests/api/test_batch_rearchive.py` (existing assertion + new cases)

- [ ] **Step 1: Update the existing rollup assertion (it will break — the dict gains `stale`)**

In `tests/api/test_batch_rearchive.py`, `test_archive_rollup_splits_done_by_archived_state`:

```python
        assert counts == {"archived": 1, "unarchived": 1, "stale": 0}
```

- [ ] **Step 2: Write failing stale tests**

Append to `tests/api/test_batch_rearchive.py`:

```python
@pytest.mark.asyncio
async def test_rollup_counts_stale_when_page_holds_older_job():
    async with SessionLocal() as s:
        batch, j1, j2 = await _seed_batch_with_two_done_jobs(s)
        # j1 is archived; stamp its lesson page with a DIFFERENT (older) job id.
        from app.repositories import toc_entries as toc_repo
        await toc_repo.set_notion_archived_job(s, j1.toc_entry_id, uuid4())
        await s.commit()
        counts = await batches_repo.archive_rollup_for_batch(s, batch.id)
        assert counts == {"archived": 1, "unarchived": 1, "stale": 1}


@pytest.mark.asyncio
async def test_rollup_not_stale_when_stamp_matches_latest_job():
    async with SessionLocal() as s:
        batch, j1, j2 = await _seed_batch_with_two_done_jobs(s)
        from app.repositories import toc_entries as toc_repo
        await toc_repo.set_notion_archived_job(s, j1.toc_entry_id, j1.id)  # fresh
        await s.commit()
        counts = await batches_repo.archive_rollup_for_batch(s, batch.id)
        assert counts == {"archived": 1, "unarchived": 1, "stale": 0}


@pytest.mark.asyncio
async def test_done_stale_job_ids_returns_only_the_stale_job():
    async with SessionLocal() as s:
        batch, j1, j2 = await _seed_batch_with_two_done_jobs(s)
        from app.repositories import toc_entries as toc_repo
        await toc_repo.set_notion_archived_job(s, j1.toc_entry_id, uuid4())  # j1 stale
        await s.commit()
        ids = await batches_repo.done_stale_job_ids(s, batch.id)
        assert ids == [j1.id]
```

- [ ] **Step 3: Run to verify they fail**

Run (real DB): create `edu_stale_test`, `alembic upgrade head`, then
`DATABASE_URL=...edu_stale_test RUN_DB_INTEGRATION=1 uv run --extra dev python -m pytest tests/api/test_batch_rearchive.py -q`
Expected: FAIL — `archive_rollup_for_batch` has no `stale` key; `done_stale_job_ids` doesn't exist.

- [ ] **Step 4: Implement the rollup split + worklist**

In `app/repositories/batches.py`, add the import near the top:

```python
from app.models.toc_entry import TOCEntry
```

Replace `archive_rollup_for_batch` body's row-query + return with a `TOCEntry`-joined version:

```python
    rows = (
        await session.execute(
            select(
                latest.c.job_id,
                latest.c.notion_archived_at,
                TOCEntry.notion_archived_job_id,
            )
            .join(TOCEntry, TOCEntry.id == latest.c.toc_entry_id)
            .where(latest.c.status == "done")
        )
    ).all()
    archived = sum(1 for r in rows if r.notion_archived_at is not None)
    unarchived = sum(1 for r in rows if r.notion_archived_at is None)
    stale = sum(
        1 for r in rows
        if r.notion_archived_at is not None
        and r.notion_archived_job_id is not None
        and r.notion_archived_job_id != r.job_id
    )
    return {"archived": archived, "unarchived": unarchived, "stale": stale}
```

(Add `HomeworkJob.id.label("job_id")` and `HomeworkJob.toc_entry_id.label("toc_entry_id")` to the `latest` subquery's `select(...)` — mirror the columns in `done_unarchived_job_ids`.)

Add the worklist function after `done_job_ids`:

```python
async def done_stale_job_ids(session: AsyncSession, batch_id: UUID) -> list[UUID]:
    """Latest job per toc_entry that is `done` and archived, but whose page holds
    an OLDER job's output (toc_entries.notion_archived_job_id != this job). The
    targeted worklist for the operator 'refresh stale' sweep — a subset of
    done_job_ids, so a force-refresh rewrites only the husks, not all pages."""
    latest = (
        select(
            HomeworkJob.id.label("job_id"),
            HomeworkJob.status.label("status"),
            HomeworkJob.notion_archived_at.label("notion_archived_at"),
            HomeworkJob.toc_entry_id.label("toc_entry_id"),
        )
        .where(HomeworkJob.batch_id == batch_id)
        .order_by(HomeworkJob.toc_entry_id, HomeworkJob.created_at.desc())
        .distinct(HomeworkJob.toc_entry_id)
        .subquery()
    )
    rows = (
        await session.execute(
            select(latest.c.job_id)
            .join(TOCEntry, TOCEntry.id == latest.c.toc_entry_id)
            .where(latest.c.status == "done")
            .where(latest.c.notion_archived_at.is_not(None))
            .where(TOCEntry.notion_archived_job_id.is_not(None))
            .where(TOCEntry.notion_archived_job_id != latest.c.job_id)
            .order_by(latest.c.toc_entry_id)
        )
    ).all()
    return [r.job_id for r in rows]
```

- [ ] **Step 5: Run the real-DB tests — verify PASS**

Same command as Step 3. Expected: PASS (all rollup + worklist cases). Drop the scratch DB after.

- [ ] **Step 6: Confirm offline suite unaffected**

Run: `uv run python -m pytest tests/ -q -k "batch and not integration"`
Expected: PASS (real-DB cases skip without the flag).

- [ ] **Step 7: Commit**

```bash
git add app/repositories/batches.py tests/api/test_batch_rearchive.py
git commit -m "notionstale: rollup fresh/stale split + done_stale_job_ids worklist"
```

---

## Task 4: Batch payload `stale` + endpoint `stale` sweep mode

**Files:**
- Modify: `app/api/v1/batch.py`
- Test: `tests/api/test_batch_rearchive.py` (endpoint case)

- [ ] **Step 1: Write the failing endpoint test**

Append to `tests/api/test_batch_rearchive.py`:

```python
@pytest.mark.asyncio
async def test_retry_archive_stale_mode_sweeps_only_stale_with_force(monkeypatch):
    from httpx import AsyncClient, ASGITransport
    from main import app
    from app.services import notion_archive
    from app.repositories import toc_entries as toc_repo

    called: list = []

    async def _fake_archive(job_id, *, force=False):
        called.append((job_id, force))

    monkeypatch.setattr(notion_archive, "archive_job", _fake_archive)

    async with SessionLocal() as s:
        batch, j1, j2 = await _seed_batch_with_two_done_jobs(s)
        await toc_repo.set_notion_archived_job(s, j1.toc_entry_id, uuid4())  # j1 stale
        await s.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.post(f"/api/v1/jobs/batch/{batch.id}/retry-archive?stale=true")
    assert r.status_code == 200
    assert r.json()["queued"] == 1
    # allow the backgrounded sweep to run
    import asyncio as _a
    for _ in range(50):
        if called:
            break
        await _a.sleep(0.02)
    assert called and called[0] == (j1.id, True)   # stale job, force=True
```

- [ ] **Step 2: Run to verify it fails**

Run (real DB, as Task 3 Step 3 command but this test id). Expected: FAIL — endpoint has no `stale` param.

- [ ] **Step 3: Add `stale` to the payload**

In `app/api/v1/batch.py`, extend `_rollup_payload` signature and body:

```python
def _rollup_payload(batch, tally: dict[str, int], original_filename: str | None = None,
                    *, archived: int = 0, unarchived: int = 0, stale: int = 0) -> dict:
```

and add to the returned dict (next to `"unarchived": unarchived,`):

```python
        "unarchived": unarchived,
        "stale": stale,
```

Update the three callers to pass `stale`:
- line ~373: `archived=archive["archived"], unarchived=archive["unarchived"], stale=archive["stale"])`
- line ~384-385 (list): add `stale=r["archive"]["stale"],`
- line ~399 (get): `archived=archive["archived"], unarchived=archive["unarchived"], stale=archive["stale"])`

- [ ] **Step 4: Add the `stale` sweep mode to the endpoint**

Replace the `retry_archive_batch` signature and worklist selection:

```python
@router.post("/jobs/batch/{batch_id}/retry-archive")
async def retry_archive_batch(batch_id: UUID, force: bool = False, stale: bool = False,
                              session: AsyncSession = Depends(get_session)):
```

and the worklist block (currently lines 500-501):

```python
    if stale:
        job_ids = await batches_repo.done_stale_job_ids(session, batch_id)
        sweep_force = True   # bypass the already-archived early-return + rewrite
    elif force:
        job_ids = await batches_repo.done_job_ids(session, batch_id)
        sweep_force = True
    else:
        job_ids = await batches_repo.done_unarchived_job_ids(session, batch_id)
        sweep_force = False
    if not job_ids:
        return {"batch_id": str(batch_id), "queued": 0, "already_running": False}
    _REARCHIVE_TASKS[batch_id] = asyncio.create_task(
        _rearchive_sweep(batch_id, job_ids, force=sweep_force))
```

Also extend the docstring's first paragraph to mention: "With `stale=true`, sweep ONLY the lessons whose page holds an older job's output (targeted refresh) with force."

- [ ] **Step 5: Run the endpoint test — verify PASS**

Real-DB command for the new test id. Expected: PASS.

- [ ] **Step 6: Confirm offline suite green**

Run: `uv run python -m pytest tests/ -q`
Expected: PASS (real-DB cases skip).

- [ ] **Step 7: Commit**

```bash
git add app/api/v1/batch.py tests/api/test_batch_rearchive.py
git commit -m "notionstale: expose stale in batch rollup + stale sweep endpoint mode"
```

---

## Task 5: Frontend — stale chip + targeted refresh button

**Files:**
- Modify: `web/src/lib/types.ts:418`
- Modify: `web/src/lib/api.ts:280-287`
- Modify: `web/src/components/fleet/batch-funnel.tsx:66-70`
- Modify: `web/src/components/fleet/batch-actions.tsx`

- [ ] **Step 1: Add `stale` to the type**

In `web/src/lib/types.ts`, after `unarchived: number;` (line 418):

```ts
  unarchived: number;
  /** Archived lessons whose Notion page holds an OLDER job's output (regen husks). */
  stale: number;
```

- [ ] **Step 2: Thread `stale` through the API client**

In `web/src/lib/api.ts`, replace `retryArchiveBatch`:

```ts
  async retryArchiveBatch(batchId: string, opts?: { stale?: boolean }): Promise<BatchRearchiveResponse> {
    const qs = opts?.stale ? "?stale=true" : "";
    const res = await authFetch(
      `/api/v1/jobs/batch/${encodeURIComponent(batchId)}/retry-archive${qs}`,
      { method: "POST" },
    );
    return unwrap<BatchRearchiveResponse>(res);
  },
```

- [ ] **Step 3: Show «N stale» in the funnel chip**

In `web/src/components/fleet/batch-funnel.tsx`, replace the archive line (66-70):

```tsx
      {batch.archived + batch.unarchived > 0 && (
        <div className="text-[0.7rem] text-white/45">
          Notion archive · {batch.archived}/{batch.archived + batch.unarchived}
          {batch.stale > 0 && (
            <span className="text-amber-400"> · {batch.stale} stale</span>
          )}
        </div>
      )}
```

- [ ] **Step 4: Add the "Refresh stale" button**

In `web/src/components/fleet/batch-actions.tsx`:

Add a mutation next to `rearchiveMut`:

```tsx
  const refreshStaleMut = useMutation({
    mutationFn: () => api.retryArchiveBatch(batch.batch_id, { stale: true }),
    onSuccess: (res) => {
      toast.success(
        res.already_running
          ? "Re-archive already running"
          : `Refreshing ${res.queued} stale page(s) in Notion`,
      );
      qc.invalidateQueries({ queryKey: ["batches"] });
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "Action failed"),
  });
```

Add the gating flag and include it in the early-return guard:

```tsx
  const canRearchive = batch.unarchived > 0;
  const canRefreshStale = batch.stale > 0;
  if (!canPause && !isPaused && !canCancel && !canRetry && !canRearchive && !canRefreshStale) return null;
```

Add the button immediately after the `canRearchive` button block (mirror its markup, amber accent):

```tsx
      {canRefreshStale && (
        <button
          type="button"
          className={cn(GHOST_BTN, PRESSABLE, FRAME_OFF, "h-7 px-2 text-xs text-amber-300/80 hover:text-amber-200 border-amber-500/30 hover:border-amber-400/50 disabled:opacity-50")}
          disabled={refreshStaleMut.isPending}
          title={`Refresh ${batch.stale} stale Notion page(s) — clears + rewrites pages whose content is from an older regen (head PC)`}
          onClick={() => refreshStaleMut.mutate()}
        >
          {refreshStaleMut.isPending ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <CloudUpload className="size-3.5" />
          )}
          Refresh stale ({batch.stale})
        </button>
      )}
```

(`CloudUpload`, `Loader2`, `cn`, `GHOST_BTN`, `PRESSABLE`, `FRAME_OFF` are already imported for the existing buttons.)

- [ ] **Step 5: Typecheck + build**

```bash
cd web && npx tsc -p tsconfig.app.json --noEmit && npm run build
```
Expected: no type errors; build writes `web/dist/`.

- [ ] **Step 6: Commit**

```bash
git add web/src/lib/types.ts web/src/lib/api.ts web/src/components/fleet/batch-funnel.tsx web/src/components/fleet/batch-actions.tsx
git commit -m "notionstale: Fleet «N stale» chip + targeted refresh button"
```

---

## Acceptance gate (controller, after all tasks)

This change is archive-plumbing, not model generation — **no paid model call is needed**. Prove it with:

1. **Full offline suite:** `uv run python -m pytest tests/ -q` → green.
2. **Real-DB slice** on a scratch DB (create → `alembic upgrade head` → run → drop): `tests/api/test_batch_rearchive.py` + `tests/integration/test_migration_0045_archived_job.py` → green (rollup stale split, worklist, endpoint sweep, migration column).
3. **FE:** `npx tsc -p tsconfig.app.json --noEmit` + `npm run build` → clean.
4. **End-to-end stamp/auto-replace trace** (in-process, no queue, no Notion writes — a fake `NotionClientWrapper`): drive `archive_job` twice for two jobs on one toc_entry with a fake client that records `append_block_children` / `clear_content_blocks` calls; assert the second (regen) call clears+rewrites and the stamp lands on the newer job. (Covered by `test_notion_archive_stamp.py`; re-run and read it.)

## Finish (controller, do not defer)

- Worklog entry → `docs/memory/MASTER_MEMORY.md` + a row in `docs/memory/INDEX.md` (next worklog number; apply the minimal-INDEX-reorder rule).
- Close `notion-archive-stale-detect-1` in `docs/memory/WISHLIST.md` (✅) — note the pre-feature-husk caveat + one-time `force=true` remediation.
- `git mv` this plan into `docs/superpowers/plans/shipped/`.
- De-stale reference docs: `docs/HOW_IT_WORKS.md` (archive: own-output stamp + auto-replace + stale chip), `docs/CODE_MAP.md` (notion_archive stamp/auto-replace, `done_stale_job_ids`, mig 0045), `docs/DATABASE.md` (`toc_entries.notion_archived_job_id`).
- Rebase-check onto `origin/Nggaev-v2` before the PR; re-run the suite; then finishing-a-development-branch (push to the working branch / open PR for the GK2 gate).
