# Notion refresh-stale-archive-on-regen Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an operator force a stale Notion homework page to be cleared and rewritten after a regen, instead of the archive silently skipping any page that already has content.

**Architecture:** Add a Notion block-delete capability to `NotionClientWrapper`, thread a `replace` flag down the archive push so a populated leaf page is cleared-then-rewritten (instead of skipped), and expose that as an explicit `force` on the two manual re-archive endpoints. The automatic pipeline archive path is unchanged (still skip-if-populated) so nothing is ever auto-clobbered.

**Tech Stack:** FastAPI, SQLAlchemy async, `notion-client` (sync SDK, run via `asyncio.to_thread`), pytest + `fastapi.testclient`.

## Global Constraints

- **No migration.** No new columns. The trigger is an explicit operator `force`, not a stored content hash — deliberately, to stay migration-free (task constraint). If a task appears to need a migration, STOP and escalate.
- **Automatic archive never clobbers.** `archive_job(job_id)` with no `force` keeps today's exact behavior: skip-if-populated, idempotent, best-effort, never raises into the pipeline. Deletion happens ONLY on an explicit `force=True` call.
- **Idempotency preserved (RED-provable).** An unchanged, already-archived job re-archived WITHOUT force performs no Notion write beyond reads.
- **Preserve structure and sub-pages.** A clear deletes only non-`child_page` content blocks (file uploads, dividers, rendered markdown) on the leaf pages the archive owns. It never deletes the lesson / `Homework` page structure, the container pages, or any `child_page` (sub-page) — so a human-added sub-page survives.
- **Human content on a leaf, under force, is replaced.** Block provenance is NOT stored, so a manual annotation added as a block on a generated leaf page cannot be distinguished from a machine block and IS deleted when the operator forces a refresh of that leaf. This is the accepted conservative behavior (deletion only on explicit operator action); it must be documented, not silently done.
- **Surface discipline.** Touch only: `app/services/notion/client.py`, `app/services/notion_archive.py`, `app/api/v1/jobs.py`, `app/api/v1/batch.py`, `app/repositories/batches.py` (one additive read method), and their tests. Do NOT touch `pipeline.py`/`agent.py`/extract (parallel coverage-contract lane). Stage only the files each task lists; never `git add -A`.
- **Worklog ID: take the ACTUAL next-free at finish** (N1) — check BOTH `docs/memory/MASTER_MEMORY.md` and `docs/memory/INDEX.md`; 0114 appears free right now (INDEX ends at 0113; the re-audit session shipped research docs without a worklog row), so this is likely 0114, not 0115. Do NOT pre-assume. Hand-merge append-only doc conflicts on rebase.
- **External-writes rule.** Any Notion write in a test uses mocks only. The live-verify step (force-re-archiving the real Parallelogramm job) runs ONLY on the user's explicit go, against the real page — no automated test writes to Notion.

## Approach & key decisions

- **Chosen: operator-driven `force` flag, manual endpoints only** (provisional decision #1 — recommended; awaits GK2 confirmation). Rejected **automatic newer-wins** (compare `job.completed_at` vs Notion `last_edited_time`): a teacher editing a page bumps `last_edited_time`, so newer-wins would either auto-clobber the edit or permanently block a needed refresh — and "never lose human content" forbids an automatic delete. Rejected a **content-hash trigger**: it needs a new column = migration, explicitly out of scope.
- **Chosen: clear = delete non-`child_page` blocks on the leaf, then re-append** (provisional decision #3 — recommended). The archive's leaf pages (Case-Based Preview, Flashcards, Boss Arena, Reflection, and each game leaf under Gamified Practices) are wholly machine-generated; everything lives under our own "Generated Homeworks" container (the module never writes into human pages). Preserving `child_page` blocks keeps any human sub-page and the container's game leaves intact.
- **Chosen: force on BOTH job and batch endpoints** (provisional decision #2 — recommended). A regen wave is batch-scoped, so `POST /jobs/batch/{id}/retry-archive?force=true` is the real remediation lever; the job endpoint covers one-offs.
- **Load-bearing facts verified against code (tip `f79bee1`):** the primary gate is `_write_leaf`'s `page_has_content` skip (`notion_archive.py:163`); force-regen of a done section creates a **new** job row (`batch.py:308,349`) with `notion_archived_at=NULL`, so the job-level early-return (`notion_archive.py:229`) does not block — but the leaf skip does, which is why the page keeps June content while the new job stamps "archived". The `notion-client` SDK exposes `client.blocks.delete` (verified via `uv run`), but `NotionClientWrapper` has no delete wrapper today. Both manual endpoints independently gate: `retry_archive_job` 409s on already-archived (`jobs.py:386`); `retry_archive_batch` sweeps only `done_unarchived_job_ids` (`batch.py:492`).
- **FE is out of scope** (not in the task's surface): the `force` param is backend-only this PR. A "Re-archive (force)" button in Monitor is a flagged follow-up.

---

### Task 1: Notion block-delete + page-clear on the client wrapper

**Files:**
- Modify: `app/services/notion/client.py` (add `delete_block` + `clear_content_blocks`)
- Test: `tests/services/test_notion_client.py`

**Interfaces:**
- Produces: `NotionClientWrapper.delete_block(block_id: str) -> None` and `NotionClientWrapper.clear_content_blocks(page_id: str) -> int` (returns count of blocks deleted; deletes every non-`child_page` block, preserves `child_page` sub-pages).

- [ ] **Step 1: Write the failing test**

Add to `tests/services/test_notion_client.py`:

```python
from unittest.mock import MagicMock


def _wrapper_with_fake_sdk():
    from app.services.notion.client import NotionClientWrapper
    w = NotionClientWrapper(api_key="ntn_testtoken")
    w.client = MagicMock()          # replace the real notion_client.Client
    w._rate_limit = lambda: None    # no sleeping in tests
    return w


def test_clear_content_blocks_deletes_only_non_child_page_blocks():
    w = _wrapper_with_fake_sdk()
    # get_block_children returns a paragraph, a file, a child_page (sub-page), a divider
    w.get_block_children = MagicMock(return_value=[
        {"id": "b1", "type": "paragraph"},
        {"id": "b2", "type": "file"},
        {"id": "b3", "type": "child_page"},   # must be preserved
        {"id": "b4", "type": "divider"},
    ])
    deleted = w.clear_content_blocks("page1")
    assert deleted == 3
    deleted_ids = {c.kwargs["block_id"] for c in w.client.blocks.delete.call_args_list}
    assert deleted_ids == {"b1", "b2", "b4"}   # b3 (child_page) NOT deleted


def test_clear_content_blocks_empty_page_is_noop():
    w = _wrapper_with_fake_sdk()
    w.get_block_children = MagicMock(return_value=[])
    assert w.clear_content_blocks("page1") == 0
    w.client.blocks.delete.assert_not_called()


def test_delete_block_calls_sdk_blocks_delete():
    w = _wrapper_with_fake_sdk()
    w.delete_block("bX")
    w.client.blocks.delete.assert_called_once_with(block_id="bX")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/services/test_notion_client.py -k "clear_content_blocks or delete_block" -q`
Expected: FAIL with `AttributeError: 'NotionClientWrapper' object has no attribute 'clear_content_blocks'` / `delete_block`.

- [ ] **Step 3: Write minimal implementation**

In `app/services/notion/client.py`, add these methods to `NotionClientWrapper` (after `append_block_children`, in the `# ─── writes ───` section):

```python
    def delete_block(self, block_id: str) -> None:
        """Archive (soft-delete) a single block. Notion moves it to trash."""
        self._rate_limit()
        self.client.blocks.delete(block_id=block_id)

    def clear_content_blocks(self, page_id: str) -> int:
        """Delete every non-`child_page` block on a page — the archive's content
        blocks (file uploads, dividers, rendered markdown). `child_page` blocks
        (sub-pages, and the Gamified-Practices container's game leaves) are
        preserved so page structure and any human-added sub-page survive.
        Returns the number of blocks deleted."""
        deleted = 0
        for block in self.get_block_children(page_id):
            if block.get("type") != "child_page":
                self.delete_block(block["id"])
                deleted += 1
        return deleted
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/services/test_notion_client.py -q`
Expected: PASS (all, including the two new + existing).

- [ ] **Step 5: Commit**

```bash
git add app/services/notion/client.py tests/services/test_notion_client.py
git commit -m "notion: add block-delete + page-clear to NotionClientWrapper"
```

---

### Task 2: `_write_leaf` replace mode (clear-then-rewrite) threaded through the push

**Files:**
- Modify: `app/services/notion_archive.py` (`_write_leaf`, `_push_to_notion`, `_push_with_retry`)
- Test: `tests/services/test_notion_archive.py`

**Interfaces:**
- Consumes: `NotionClientWrapper.clear_content_blocks` (Task 1).
- Produces: `_push_to_notion(..., replace: bool = False)` and `_push_with_retry(..., replace: bool = False)`; when `replace` is True, a populated leaf is cleared then rewritten instead of skipped. Default `replace=False` keeps today's skip-if-populated behavior byte-for-byte.

- [ ] **Step 1: Write the failing test**

Add to `tests/services/test_notion_archive.py`:

```python
def test_push_replace_clears_then_rewrites_populated_page():
    client = MagicMock()
    client.page_has_content.return_value = True      # page already populated
    client.upload_bytes.side_effect = lambda data, name, ctype: f"upl::{name}"
    na_find = MagicMock(side_effect=lambda c, parent, title: (f"id::{title}", False))
    na._push_to_notion(
        client=client, subject_page_id="subj", lesson_title="L",
        phase_md={"case-based-preview": "# CBP"}, find_or_create=na_find,
        replace=True,
    )
    # replace=True → the stale content is cleared, then the fresh content written
    client.clear_content_blocks.assert_called_once_with("id::Case-Based Preview")
    client.append_block_children.assert_called_once()
    assert client.append_block_children.call_args.args[0] == "id::Case-Based Preview"


def test_push_replace_false_still_skips_populated_page():
    # Control: default (replace=False) must NOT clear — pure idempotent skip.
    client = MagicMock()
    client.page_has_content.return_value = True
    na_find = MagicMock(side_effect=lambda c, parent, title: (f"id::{title}", False))
    na._push_to_notion(
        client=client, subject_page_id="subj", lesson_title="L",
        phase_md={"case-based-preview": "# CBP"}, find_or_create=na_find,
    )
    client.clear_content_blocks.assert_not_called()
    client.append_block_children.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/services/test_notion_archive.py -k "replace" -q`
Expected: FAIL — `_push_to_notion()` got an unexpected keyword argument `replace` (first test), and `clear_content_blocks` not called.

- [ ] **Step 3: Write minimal implementation**

In `app/services/notion_archive.py`, change `_push_to_notion` signature and `_write_leaf`:

```python
def _push_to_notion(
    *,
    client: NotionClientWrapper,
    subject_page_id: str,
    lesson_title: str,
    phase_md: dict[str, str],  # phase_name -> markdown (only present/done phases)
    find_or_create: Callable = find_or_create,  # injectable for tests
    replace: bool = False,
) -> str:
```

(Extend the existing docstring's "Idempotent" sentence with: "When `replace` is True, a populated leaf page is cleared (`clear_content_blocks`) and rewritten instead of skipped — used by the operator force-refresh path.")

Then the closure:

```python
    def _write_leaf(parent_id: str, title: str, present: list[tuple[str, str]]) -> None:
        page_id, _ = find_or_create(client, parent_id, title)
        if client.page_has_content(page_id):
            if not replace:
                log.info("notion: page %s (%s) already populated — skipping", page_id, title)
                return
            log.info("notion: page %s (%s) already populated — clearing to rewrite (force)", page_id, title)
            client.clear_content_blocks(page_id)
        client.append_block_children(page_id, _leaf_blocks(client, present))
```

And thread `replace` through `_push_with_retry`:

```python
async def _push_with_retry(*, client, subject_page_id, lesson_title, phase_md, replace: bool = False) -> str:
```

and inside its `asyncio.to_thread(_push_to_notion, ...)` call add `replace=replace,`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/services/test_notion_archive.py -q`
Expected: PASS (new replace tests + all existing archive tests, incl. `test_push_skips_pages_already_populated`).

- [ ] **Step 5: Commit**

```bash
git add app/services/notion_archive.py tests/services/test_notion_archive.py
git commit -m "notion: _write_leaf replace mode — clear-then-rewrite a populated leaf"
```

---

### Task 3: `archive_job(force=)` — bypass the already-archived early-return and push with replace

**Files:**
- Modify: `app/services/notion_archive.py` (`archive_job`)
- Test: `tests/services/test_notion_archive_force.py` (new)

**Interfaces:**
- Consumes: `_push_with_retry(..., replace=)` (Task 2).
- Produces: `archive_job(job_id: UUID, *, force: bool = False) -> None`. With `force=True`, an already-archived done job is NOT short-circuited; the push runs with `replace=True`. With `force=False` (default), behavior is byte-identical to today.

- [ ] **Step 1: Write the failing test**

Create `tests/services/test_notion_archive_force.py`:

```python
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

import app.services.notion_archive as na


def _done_archived_job():
    return SimpleNamespace(
        id=uuid4(), book_id=uuid4(), toc_entry_id=uuid4(), subject="geometriya-g7-11",
        notion_archived_at=datetime.now(timezone.utc), output_language="uz",
    )


@pytest.mark.asyncio
async def test_archive_job_without_force_short_circuits_already_archived(monkeypatch):
    monkeypatch.setattr(na.settings, "notion_enabled", True)
    monkeypatch.setattr(na.settings, "notion_api_key", "ntn_x")
    job = _done_archived_job()

    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(na, "SessionLocal", MagicMock(return_value=session))
    with patch.object(na.jobs_repo, "get", AsyncMock(return_value=job)), \
         patch.object(na, "_push_with_retry", AsyncMock()) as push:
        await na.archive_job(job.id)          # no force
    push.assert_not_awaited()                  # early-return: no push at all


@pytest.mark.asyncio
async def test_archive_job_force_pushes_with_replace_on_already_archived(monkeypatch):
    monkeypatch.setattr(na.settings, "notion_enabled", True)
    monkeypatch.setattr(na.settings, "notion_api_key", "ntn_x")
    job = _done_archived_job()
    book = SimpleNamespace(grade="8", original_filename="8-sinf.pdf", id=job.book_id)
    section = SimpleNamespace(id=job.toc_entry_id, section_number="1", section_title="L")
    phase = SimpleNamespace(phase_name="case-based-preview", status="done", output_md="# CBP")

    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(na, "SessionLocal", MagicMock(return_value=session))
    monkeypatch.setattr(na.settings, "notion_subject_pages", {"geometriya-g7-11|8": "subj"})

    with patch.object(na.jobs_repo, "get", AsyncMock(return_value=job)), \
         patch.object(na.books_repo, "get", AsyncMock(return_value=book)), \
         patch.object(na.toc_repo, "get", AsyncMock(return_value=section)), \
         patch.object(na.phase_repo, "list_for_job", AsyncMock(return_value=[phase])), \
         patch.object(na.toc_repo, "set_notion_homework_page_id", AsyncMock()), \
         patch.object(na.jobs_repo, "set_notion_archived", AsyncMock()) as set_arch, \
         patch.object(na, "NotionClientWrapper", MagicMock()), \
         patch.object(na, "_push_with_retry", AsyncMock(return_value="hw_page")) as push:
        await na.archive_job(job.id, force=True)

    push.assert_awaited_once()
    assert push.await_args.kwargs["replace"] is True
    # N3: the force-success path runs the success write, which clears
    # notion_skip_reason (set_notion_archived sets notion_skip_reason=None) —
    # closes the loop with R15's skip-reason plumbing.
    set_arch.assert_awaited_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/services/test_notion_archive_force.py -q`
Expected: FAIL — `archive_job()` got an unexpected keyword argument `force` (second test) / `_push_with_retry` awaited when it should not be reachable (once force is added but replace not threaded).

- [ ] **Step 3: Write minimal implementation**

In `app/services/notion_archive.py`, change the `archive_job` signature and the early-return:

```python
async def archive_job(job_id: UUID, *, force: bool = False) -> None:
    """Best-effort entry point called from the pipeline after job is `done`.
    With `force=True` (operator re-archive), an already-archived job is NOT
    short-circuited and its leaf pages are cleared and rewritten (replace mode)."""
```

Change the job guard (currently `if job is None or job.notion_archived_at is not None: return`) to:

```python
            if job is None:
                return  # gone
            if job.notion_archived_at is not None and not force:
                return  # already archived (idempotent on retry) unless forced
```

And in the `_push_with_retry(...)` call, add `replace=force,`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/services/test_notion_archive_force.py tests/services/test_notion_archive.py tests/services/test_notion_archive_skip.py -q`
Expected: PASS (new force tests + existing archive + skip tests unaffected).

- [ ] **Step 5: Commit**

```bash
git add app/services/notion_archive.py tests/services/test_notion_archive_force.py
git commit -m "notion: archive_job(force=) — re-archive an already-archived job with replace"
```

---

### Task 4: `force` on `POST /jobs/{id}/retry-archive`

**Files:**
- Modify: `app/api/v1/jobs.py` (`retry_archive_job`)
- Test: `tests/api/test_retry_archive_endpoint.py`

**Interfaces:**
- Consumes: `archive_job(job_id, force=)` (Task 3).
- Produces: `POST /jobs/{job_id}/retry-archive?force=true` — when force, the already-archived 409 is skipped and `archive_job(job_id, force=True)` is called. Non-done still 409s. Default (no force) is unchanged.

- [ ] **Step 1: Write the failing test**

Add to `tests/api/test_retry_archive_endpoint.py`:

```python
def test_retry_archive_force_allows_already_archived():
    jid = uuid4()
    job = SimpleNamespace(id=jid, status="done",
                          notion_archived_at=datetime.now(timezone.utc))
    out = JobOut(id=jid, book_id=uuid4(), toc_entry_id=uuid4(),
                 subject="kimyo-g7-11", status="done")
    arch = AsyncMock()
    fake_session = MagicMock()
    fake_session.expire_all = MagicMock()
    app.dependency_overrides[get_session] = lambda: fake_session
    try:
        with patch("app.api.v1.jobs.jobs_repo.get", AsyncMock(return_value=job)), \
             patch("app.api.v1.jobs.notion_archive.archive_job", arch), \
             patch("app.api.v1.jobs._job_out", AsyncMock(return_value=out)):
            r = client.post(f"/api/v1/jobs/{jid}/retry-archive?force=true")
    finally:
        app.dependency_overrides.pop(get_session, None)
    assert r.status_code == 200
    arch.assert_awaited_once()
    assert arch.await_args.kwargs.get("force") is True


def test_retry_archive_force_still_rejects_non_done():
    jid = uuid4()
    job = SimpleNamespace(id=jid, status="running", notion_archived_at=None)
    with patch("app.api.v1.jobs.jobs_repo.get", AsyncMock(return_value=job)):
        r = client.post(f"/api/v1/jobs/{jid}/retry-archive?force=true")
    assert r.status_code == 409
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/api/test_retry_archive_endpoint.py -k force -q`
Expected: FAIL — the already-archived job still 409s (force not honored) and `archive_job` isn't awaited.

- [ ] **Step 3: Write minimal implementation**

In `app/api/v1/jobs.py`, update `retry_archive_job`:

```python
@router.post("/jobs/{job_id}/retry-archive")
async def retry_archive_job(
    job_id: UUID,
    force: bool = False,
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
) -> JobOut:
    """Re-attempt the best-effort Notion archive for a job. Normally for a job
    whose push previously failed (status=done, notion_archived_at IS NULL);
    `archive_job` is idempotent (skips already-populated pages) and clears
    `notion_skip_reason` on success. With `force=true` an already-archived job is
    re-pushed and its leaf pages are cleared and rewritten (replace mode) — use
    after a regen to refresh stale content. Refuses non-done jobs with 409."""
    job = await jobs_repo.get(session, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    if job.status != "done":
        raise HTTPException(
            409, f"only done jobs can be re-archived; current status={job.status!r}")
    if job.notion_archived_at is not None and not force:
        raise HTTPException(409, "job already archived to Notion")
    await notion_archive.archive_job(job_id, force=force)
    session.expire_all()
    return await _job_out(session, job_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/api/test_retry_archive_endpoint.py -q`
Expected: PASS (new force tests + existing, incl. `test_retry_archive_rejects_already_archived` which posts WITHOUT force).

- [ ] **Step 5: Commit**

```bash
git add app/api/v1/jobs.py tests/api/test_retry_archive_endpoint.py
git commit -m "notion: force param on POST /jobs/{id}/retry-archive"
```

---

### Task 5: `force` on `POST /jobs/batch/{id}/retry-archive` (sweep done+archived)

**Files:**
- Modify: `app/repositories/batches.py` (add `done_job_ids`), `app/api/v1/batch.py` (`_rearchive_sweep`, `retry_archive_batch`)
- Test: `tests/api/test_batch_rearchive.py`

**Interfaces:**
- Consumes: `archive_job(job_id, force=)` (Task 3).
- Produces: `batches_repo.done_job_ids(session, batch_id) -> list[UUID]` (latest job per toc_entry that is `done`, regardless of archived state — mirrors `done_unarchived_job_ids` minus the `notion_archived_at IS NULL` filter). `POST /jobs/batch/{id}/retry-archive?force=true` sweeps that worklist and calls `archive_job(force=True)` per job.

Note: these are DB-integration tests (`RUN_DB_INTEGRATION=1`), reusing the `_seed_batch_with_two_done_jobs` fixture already in `tests/api/test_batch_rearchive.py` (seeds one done+archived job `j1` and one done+unarchived job `j2`). The repo signature file `tests/repositories/test_notion_repo_methods.py` is signature-only (no DB) — not the home for these.

- [ ] **Step 1: Write the failing tests**

First, fix the existing sweep test's fake so it survives the new call shape — in `tests/api/test_batch_rearchive.py`, `_rearchive_sweep` will now call `archive_job(jid, force=...)`, so update the local fake in `test_retry_archive_endpoint_sweeps_unarchived`:

```python
    async def _fake_archive(job_id, *, force=False):   # was (job_id)
        called.append((job_id, force))
    ...
    assert called == [(j2.id, False)]   # non-force sweep: only the unarchived done job, force=False
```

Then add two new tests to the same file:

```python
@pytest.mark.asyncio
async def test_done_job_ids_includes_archived():
    async with SessionLocal() as s:
        batch, j1, j2 = await _seed_batch_with_two_done_jobs(s)
        ids = await batches_repo.done_job_ids(s, batch.id)
        assert set(ids) == {j1.id, j2.id}          # BOTH done jobs, incl. archived j1
        # control: the unarchived-only view still excludes the archived one
        assert await batches_repo.done_unarchived_job_ids(s, batch.id) == [j2.id]


@pytest.mark.asyncio
async def test_retry_archive_batch_force_sweeps_all_done(monkeypatch):
    from httpx import AsyncClient, ASGITransport
    from main import app
    from app.api.v1 import batch as batch_api
    from app.services import notion_archive

    called: list = []

    async def _fake_archive(job_id, *, force=False):
        called.append((job_id, force))

    monkeypatch.setattr(notion_archive, "archive_job", _fake_archive)

    async with SessionLocal() as s:
        batch, j1, j2 = await _seed_batch_with_two_done_jobs(s)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.post(f"/api/v1/jobs/batch/{batch.id}/retry-archive?force=true")
    assert r.status_code == 200
    assert r.json()["queued"] == 2                 # both done jobs, incl. the archived one

    task = batch_api._REARCHIVE_TASKS.get(batch.id)
    if task is not None:
        await task
    assert {jid for jid, _ in called} == {j1.id, j2.id}
    assert all(force is True for _, force in called)   # force threaded to every archive
```

(The file already imports `batches_repo`, `SessionLocal`, `uuid4`, `datetime`, and defines `_seed_batch_with_two_done_jobs`.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `RUN_DB_INTEGRATION=1 DATABASE_URL=<scratch-or-edu_copy> uv run python -m pytest tests/api/test_batch_rearchive.py -q`
Expected: FAIL — `done_job_ids` doesn't exist; the endpoint ignores `force` (queued==1, not 2); the updated `_fake_archive`/`called`-tuple assertions bite.

- [ ] **Step 3: Write minimal implementation**

In `app/repositories/batches.py`, add next to `done_unarchived_job_ids` (identical body minus the archived filter):

```python
async def done_job_ids(session: AsyncSession, batch_id: UUID) -> list[UUID]:
    """Latest job per toc_entry in the batch that is `done` — including
    already-archived jobs. The worklist for a FORCE re-archive sweep (refresh
    stale Notion content after a regen). Stable order."""
    latest = (
        select(
            HomeworkJob.id.label("job_id"),
            HomeworkJob.status.label("status"),
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
            .where(latest.c.status == "done")
            .order_by(latest.c.toc_entry_id)
        )
    ).all()
    return [r.job_id for r in rows]
```

In `app/api/v1/batch.py`, thread `force` through the sweep and endpoint:

```python
async def _rearchive_sweep(batch_id: UUID, job_ids: list[UUID], *, force: bool = False) -> None:
    """... (existing docstring) ... When `force`, each archive clears+rewrites
    stale leaf pages (replace mode)."""
    from app.services import notion_archive
    try:
        for jid in job_ids:
            try:
                await notion_archive.archive_job(jid, force=force)
            except Exception:
                log.warning("[batch %s] re-archive of job %s failed (non-fatal)",
                            batch_id, jid, exc_info=True)
    finally:
        _REARCHIVE_TASKS.pop(batch_id, None)
```

```python
@router.post("/jobs/batch/{batch_id}/retry-archive")
async def retry_archive_batch(batch_id: UUID, force: bool = False,
                              session: AsyncSession = Depends(get_session)):
    """Re-push every done-but-unarchived lesson of a batch to Notion from the
    HEAD process. With `force=true`, sweep ALL done lessons (incl. already
    archived) and clear+rewrite stale leaf pages — the regen-wave refresh lever.
    Backgrounded + idempotent; a second call while a sweep is in flight no-ops.

    Operational ordering: run force re-archive AFTER a regen wave has fully
    completed. The sweep takes the latest *done* job per lesson; if a replacement
    job is still running it isn't picked up, and once it later finishes its
    automatic archive skips-if-populated → the page goes stale again. Force once
    the wave is done."""
    from app.models.batch import Batch
    batch = await session.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(404, "batch not found")
    if batch_id in _REARCHIVE_TASKS:
        return {"batch_id": str(batch_id), "queued": 0, "already_running": True}
    job_ids = (await batches_repo.done_job_ids(session, batch_id) if force
               else await batches_repo.done_unarchived_job_ids(session, batch_id))
    if not job_ids:
        return {"batch_id": str(batch_id), "queued": 0, "already_running": False}
    _REARCHIVE_TASKS[batch_id] = asyncio.create_task(
        _rearchive_sweep(batch_id, job_ids, force=force))
    return {"batch_id": str(batch_id), "queued": len(job_ids), "already_running": False}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `RUN_DB_INTEGRATION=1 DATABASE_URL=<scratch-or-edu_copy> uv run python -m pytest tests/api/test_batch_rearchive.py -q`
Expected: PASS (new force tests + the existing sweep test with the updated `_fake_archive`).

- [ ] **Step 5: Commit**

```bash
git add app/repositories/batches.py app/api/v1/batch.py tests/api/test_batch_rearchive.py
git commit -m "notion: force param on batch retry-archive — sweep done+archived with replace"
```

---

### Task 6: Full suite + docs de-stale (finish-stage)

**Files:**
- Modify (docs, if they describe archive idempotency): `docs/HOW_IT_WORKS.md`, `docs/CODE_MAP.md`
- Modify: `docs/memory/WISHLIST.md` (close the `notion-archive-stale-on-regen-1` line), `docs/memory/MASTER_MEMORY.md` (worklog 0115), `docs/memory/INDEX.md` (0115 row)

- [ ] **Step 1: Full suite green**

Run: `uv run python -m pytest tests/ -q`
Expected: green (the canonical bar is WITHOUT `RUN_DB_INTEGRATION`; the real-DB repo test from Task 5 is skipped there and run separately once).

- [ ] **Step 2: De-stale reference docs**

Grep `docs/HOW_IT_WORKS.md` and `docs/CODE_MAP.md` for the archive idempotency / "skip-if-populated" description; if present, add the force-refresh (replace-mode) behavior **and the N2 operational-ordering note** (force re-archive only after a regen wave completes, else the sweep re-pushes the old done job and the running replacement re-goes-stale on its own skip-if-populated archive). If neither documents it, note that in the worklog and skip.

- [ ] **Step 3: Worklog + INDEX + close WISHLIST**

Take the actual next-free worklog ID (N1 — check both `MASTER_MEMORY.md` and `INDEX.md`; likely 0114). Write the worklog to `docs/memory/MASTER_MEMORY.md`, add its `INDEX.md` row, and remove the `notion-archive-stale-on-regen-1` line from `docs/memory/WISHLIST.md` (Open section).

- [ ] **Step 4: Commit**

```bash
git add docs/memory/WISHLIST.md docs/memory/MASTER_MEMORY.md docs/memory/INDEX.md docs/HOW_IT_WORKS.md docs/CODE_MAP.md
git commit -m "docs: worklog 0115 + close notion-archive-stale-on-regen-1"
```

---

## Acceptance gate

Not a generation change (no model calls) → the acceptance proof is the unit/API suite + a **user-gated live-Notion verify**: with the user's explicit go, force-re-archive the real stale Parallelogramm job (`POST /jobs/{id}/retry-archive?force=true` or the batch form) and confirm in Notion that the old June content is gone, the fresh content is present, and the lesson/Homework structure + any sibling pages are intact. This is fact-over-theory and runs only on the user's go — no automated test writes to Notion.

## Out of scope (flagged follow-ups)

- **FE affordance:** a "Re-archive (force)" button / confirm in Monitor passing `?force=true`. Backend-only this PR (FE not in the task surface).
- **Automatic newer-wins:** deliberately not built (see Approach). If the campaign shows manual force is too toilsome, revisit with a content-hash column (migration).
