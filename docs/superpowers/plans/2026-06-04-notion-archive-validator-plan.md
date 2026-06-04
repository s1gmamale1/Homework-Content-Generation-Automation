# Notion Archive Validator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After a job archives to Notion, automatically verify the live Notion tree matches the structure the archive should have built (expected pages under `Homework`, the right game child, each leaf non-empty with its file attachment(s)), and record a per-job result — best-effort, never breaking the pipeline.

**Architecture:** A pure `expected_tree(phase_keys)` reuses `_HOMEWORK_LAYOUT` + `PHASE_TITLES` from `notion_archive.py` (DRY) to derive the expected page set and per-leaf attachment count. A `_compare(expected, client, homework_page_id)` reads the live tree via `NotionClientWrapper` and returns a list of human-readable issues. `validate_archive(job_id)` runs the DB gate, runs the comparison in `asyncio.to_thread`, and persists `{status, checked_at, issues}` to a new `homework_jobs.notion_validation` JSONB column. It is wired into `pipeline.run` AFTER the archive `try/except`.

**Tech Stack:** FastAPI, SQLAlchemy + Alembic (Postgres/JSONB), asyncio, pytest (**DB-free suite** — pure-function + fake-client unit tests; `inspect` for I/O functions; migration proven by `alembic upgrade`; live behaviour by the Task 5 smoke).

**Spec:** `docs/superpowers/specs/2026-06-04-notion-archive-validator-design.md`

**Commands:**
- Backend tests: `& ".\.venv\Scripts\python.exe" -m pytest tests/ -q`
- Migration: `uv run alembic upgrade head` (if `uv` is not on PATH, use `& ".\.venv\Scripts\python.exe" -m alembic upgrade head`) · check head: `uv run alembic heads`

**Test-harness note (critical):** the suite is **DB-free** (`tests/conftest.py` injects sentinel env only). `expected_tree`/`_compare` get real assertions (pure / fake-client); `validate_archive` and the pipeline wiring are verified by `inspect.getsource`/`inspect.signature`; the migration is proven by `alembic upgrade`, not pytest; live Notion behaviour is the Task 5 smoke.

**Ordering:** T1 (column) → T2 (expected_tree) → T3 (_compare) → T4 (validate_archive + wiring) → T5 (acceptance). T2/T3 are pure and could be done in either order; T4 depends on T1–T3.

---

## File structure

| File | Responsibility | Task |
|---|---|---|
| `app/models/homework_job.py` · `app/repositories/jobs.py` · `alembic/versions/0020_*` | `homework_jobs.notion_validation` JSONB (additive) + setter | T1 |
| `app/services/notion/archive_validator.py` (new) | `expected_tree` (pure) | T2 |
| `app/services/notion/archive_validator.py` | `_compare` (live tree vs expected → issues) | T3 |
| `app/services/notion/archive_validator.py` · `app/services/pipeline.py` | `validate_archive` orchestrator + pipeline wiring | T4 |
| acceptance + worklog | live verified/mismatch/skipped smokes | T5 |

---

## Task 1: `homework_jobs.notion_validation` column + setter

**Files:**
- Modify: `app/models/homework_job.py` (add JSONB import + column after `notion_archived_at`, `:31-33`)
- Modify: `app/repositories/jobs.py` (add `set_notion_validation`, mirror `set_notion_archived` `:125-131`)
- Create: `alembic/versions/0020_notion_validation.py`
- Test: `tests/repositories/test_notion_validation_column.py` (new)

- [ ] **Step 1: Write the failing test** (DB-free — model attribute + signature/source)

```python
# tests/repositories/test_notion_validation_column.py
import inspect

from app.models import HomeworkJob
from app.repositories import jobs as jobs_repo


def test_model_has_notion_validation_attribute():
    j = HomeworkJob(subject="biology", status="pending")
    assert j.notion_validation is None


def test_set_notion_validation_exists_and_assigns():
    assert hasattr(jobs_repo, "set_notion_validation")
    src = inspect.getsource(jobs_repo.set_notion_validation)
    assert "notion_validation" in src
    sig = inspect.signature(jobs_repo.set_notion_validation)
    assert "result" in sig.parameters
```

- [ ] **Step 2: Run to verify it fails**

Run: `& ".\.venv\Scripts\python.exe" -m pytest tests/repositories/test_notion_validation_column.py -q`
Expected: FAIL — `notion_validation` attribute missing; `set_notion_validation` missing.

- [ ] **Step 3: Add the model column**

In `app/models/homework_job.py`, add the JSONB import under the existing sqlalchemy imports (after line 5):

```python
from sqlalchemy.dialects.postgresql import JSONB
```

Then, after the `notion_archived_at` column (ends line 33), add:

```python
    # Per-job Notion archive-validation result: {status, checked_at, issues}.
    # status ∈ verified | mismatch | archive-incomplete | check-failed | skipped.
    # NULL = the validator never ran (historical / pre-feature); distinct from
    # "skipped" (ran, intentional no-op because Notion is disabled).
    notion_validation: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
```

- [ ] **Step 4: Add the repo setter**

In `app/repositories/jobs.py`, after `set_notion_archived` (ends line 131) add:

```python
async def set_notion_validation(
    session: AsyncSession, job_id: UUID, result: dict
) -> None:
    job = await session.get(HomeworkJob, job_id)
    if job is None:
        return
    job.notion_validation = result
```

- [ ] **Step 5: Create the additive migration**

First confirm the head: `uv run alembic heads` → expect `a7c1e9d2b4f8` (0019 phase_provider). Then:

```python
# alembic/versions/0020_notion_validation.py
"""homework_jobs.notion_validation (per-job archive-validation result)

Revision ID: b3d6f1a8c2e5
Revises: a7c1e9d2b4f8
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b3d6f1a8c2e5"
down_revision: Union[str, Sequence[str], None] = "a7c1e9d2b4f8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "homework_jobs",
        sa.Column("notion_validation", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("homework_jobs", "notion_validation")
```

- [ ] **Step 6: Apply migration + run tests**

Run: `uv run alembic upgrade head` then
`& ".\.venv\Scripts\python.exe" -m pytest tests/repositories/test_notion_validation_column.py -q`
Expected: migration OK; 2 tests PASS.

- [ ] **Step 7: Commit**

```bash
git add app/models/homework_job.py app/repositories/jobs.py alembic/versions/0020_notion_validation.py tests/repositories/test_notion_validation_column.py
git commit -m "feat(notion): homework_jobs.notion_validation column + repo setter"
```

---

## Task 2: `expected_tree` — derive the expected page tree (pure)

**Files:**
- Create: `app/services/notion/archive_validator.py`
- Test: `tests/services/test_archive_validator_expected.py` (new)

> Reuses `_HOMEWORK_LAYOUT`, `PHASE_TITLES`, `_LEAF`, `_CONTAINER` from `notion_archive.py` — the single source of truth for the layout. The per-leaf attachment count is **derived** (`len(present)`), never hardcoded, so a `done` job whose `memory-check` emitted empty output (excluded by the archive filter) correctly expects a 1-attachment Flashcards leaf.

- [ ] **Step 1: Write the failing test**

```python
# tests/services/test_archive_validator_expected.py
from app.services.notion.archive_validator import expected_tree


_FULL = {
    "case-based-preview", "flashcards", "memory-check",
    "practice-rlc", "practice-error-detection",
    "practice-memory-match", "boss-arena", "reflection",
}


def test_full_history_job_tree():
    t = expected_tree(_FULL)
    titles = {l.title for l in t.leaves}
    assert titles == {"Case-Based Preview", "Flashcards", "Boss Arena", "Reflection"}
    assert t.top_titles == titles | {"Gamified Practices"}
    # Flashcards leaf holds flashcards + memory-check → 2 attachments
    fc = next(l for l in t.leaves if l.title == "Flashcards")
    assert fc.attachments == 2
    assert t.container is not None
    assert set(t.container.children) == {"Real-Life Challenge", "Error Detection", "Memory Matching"}


def test_flashcards_attachments_drop_to_one_when_memory_check_absent():
    keys = _FULL - {"memory-check"}
    fc = next(l for l in expected_tree(keys).leaves if l.title == "Flashcards")
    assert fc.attachments == 1


def test_subject_game_variants_pick_one_child():
    base = {"case-based-preview", "flashcards", "practice-rlc", "boss-arena", "reflection"}
    for phase, title in [
        ("practice-tictactoe", "TicTacToe"),
        ("practice-jigsaw", "Jigsaw Matching"),
        ("practice-sentence", "Sentence Filling"),
    ]:
        t = expected_tree(base | {phase})
        assert t.container is not None
        assert title in t.container.children


def test_group_omitted_when_no_present_phase():
    # only CBP present → no Gamified Practices, no Boss Arena/Reflection/Flashcards
    t = expected_tree({"case-based-preview"})
    assert t.top_titles == {"Case-Based Preview"}
    assert t.container is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `& ".\.venv\Scripts\python.exe" -m pytest tests/services/test_archive_validator_expected.py -q`
Expected: FAIL — `ModuleNotFoundError: app.services.notion.archive_validator`.

- [ ] **Step 3: Implement `expected_tree`**

```python
# app/services/notion/archive_validator.py
"""Auto, best-effort structural validation of a job's Notion archive.

Runs after `notion_archive.archive_job` in the pipeline. Derives the expected
`Homework` page tree from the job's done-phase keys (reusing the archive's own
layout maps), reads the live tree via NotionClientWrapper, and records a per-job
`notion_validation` result. Never raises into the pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.services.notion_archive import (
    _CONTAINER,
    _HOMEWORK_LAYOUT,
    _LEAF,
    PHASE_TITLES,
)


@dataclass
class ExpectedLeaf:
    title: str
    attachments: int   # number of file-attachment blocks at the top of the leaf


@dataclass
class ExpectedContainer:
    title: str
    children: list[str]   # expected child leaf titles (the present game page(s))


@dataclass
class ExpectedTree:
    leaves: list[ExpectedLeaf]
    container: Optional[ExpectedContainer]

    @property
    def top_titles(self) -> set[str]:
        titles = {leaf.title for leaf in self.leaves}
        if self.container is not None:
            titles.add(self.container.title)
        return titles


def expected_tree(phase_keys: set[str]) -> ExpectedTree:
    """Expected `Homework` children for a job, derived exactly like
    `_push_to_notion`: present phases per layout group, empty groups skipped,
    per-leaf attachment count = number of present phases on that leaf."""
    leaves: list[ExpectedLeaf] = []
    container: Optional[ExpectedContainer] = None
    for entry in _HOMEWORK_LAYOUT:
        present = [p for p in entry["phases"] if p in phase_keys]
        if not present:
            continue
        if entry["kind"] == _LEAF:
            leaves.append(ExpectedLeaf(title=entry["title"], attachments=len(present)))
        elif entry["kind"] == _CONTAINER:
            container = ExpectedContainer(
                title=entry["title"],
                children=[PHASE_TITLES.get(p, p) for p in present],
            )
    return ExpectedTree(leaves=leaves, container=container)
```

- [ ] **Step 4: Run to verify it passes**

Run: `& ".\.venv\Scripts\python.exe" -m pytest tests/services/test_archive_validator_expected.py -q`
Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/notion/archive_validator.py tests/services/test_archive_validator_expected.py
git commit -m "feat(notion): expected_tree — derive expected archive structure from phase keys"
```

---

## Task 3: `_compare` — live tree vs expected → issues

**Files:**
- Modify: `app/services/notion/archive_validator.py` (add `_compare`)
- Test: `tests/services/test_archive_validator_compare.py` (new)

> `_compare` does Notion reads through an injected client, so it's tested with a **fake client** (DB-free, no network). Titles are normalized with `page_creator._normalize` (strip trailing `(N)`, lowercase, trim) so correctly-archived pages never read as mismatches. Attachments are at the **top** of a leaf, so we count leading `type == "file"` blocks (the type `make_file_upload_block` emits).

- [ ] **Step 1: Write the failing test**

```python
# tests/services/test_archive_validator_compare.py
from app.services.notion.archive_validator import _compare, expected_tree


class _FakeClient:
    """tree: {page_id: {"children": [{"id","title"}], "blocks": [{"type"}]}}"""
    def __init__(self, tree):
        self.tree = tree

    def get_child_pages(self, parent_id):
        return self.tree.get(parent_id, {}).get("children", [])

    def get_block_children(self, page_id):
        return self.tree.get(page_id, {}).get("blocks", [])


_KEYS = {
    "case-based-preview", "flashcards", "memory-check",
    "practice-rlc", "practice-error-detection",
    "practice-memory-match", "boss-arena", "reflection",
}


def _file(): return {"type": "file"}
def _para(): return {"type": "paragraph"}
def _child(pid, title): return {"id": pid, "title": title}


def _good_tree():
    return {
        "hw": {"children": [
            _child("cbp", "Case-Based Preview"),
            _child("fc", "Flashcards"),
            _child("gp", "Gamified Practices"),
            _child("ba", "Boss Arena"),
            _child("rf", "Reflection"),
        ]},
        "cbp": {"blocks": [_file(), _para()]},
        "fc": {"blocks": [_file(), _file(), _para()]},   # 2 attachments
        "gp": {"children": [
            _child("g1", "Real-Life Challenge"),
            _child("g2", "Error Detection"),
            _child("g3", "Memory Matching"),
        ]},
        "g1": {"blocks": [_file(), _para()]},
        "g2": {"blocks": [_file(), _para()]},
        "g3": {"blocks": [_file(), _para()]},
        "ba": {"blocks": [_file(), _para()]},
        "rf": {"blocks": [_file(), _para()]},
    }


def test_good_tree_has_no_issues():
    issues = _compare(expected_tree(_KEYS), _FakeClient(_good_tree()), "hw")
    assert issues == []


def test_missing_top_level_page_is_flagged():
    t = _good_tree()
    t["hw"]["children"] = [c for c in t["hw"]["children"] if c["title"] != "Boss Arena"]
    issues = _compare(expected_tree(_KEYS), _FakeClient(t), "hw")
    assert any("Boss Arena" in i for i in issues)


def test_missing_game_child_is_flagged():
    t = _good_tree()
    t["gp"]["children"] = [c for c in t["gp"]["children"] if c["title"] != "Memory Matching"]
    issues = _compare(expected_tree(_KEYS), _FakeClient(t), "hw")
    assert any("Memory Matching" in i for i in issues)


def test_too_few_attachments_is_flagged():
    t = _good_tree()
    t["fc"]["blocks"] = [_file(), _para()]   # only 1, expected 2
    issues = _compare(expected_tree(_KEYS), _FakeClient(t), "hw")
    assert any("Flashcards" in i and "attachment" in i for i in issues)


def test_empty_leaf_is_flagged():
    t = _good_tree()
    t["rf"]["blocks"] = [_file()]   # attachment but no content after it
    issues = _compare(expected_tree(_KEYS), _FakeClient(t), "hw")
    assert any("Reflection" in i and "content" in i for i in issues)


def test_normalized_titles_with_dedup_suffix_still_match():
    t = _good_tree()
    # Notion appended a "(1)" dedup suffix — must still match via _normalize
    for c in t["hw"]["children"]:
        if c["title"] == "Boss Arena":
            c["title"] = "Boss Arena (1)"
    issues = _compare(expected_tree(_KEYS), _FakeClient(t), "hw")
    assert issues == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `& ".\.venv\Scripts\python.exe" -m pytest tests/services/test_archive_validator_compare.py -q`
Expected: FAIL — `cannot import name '_compare'`.

- [ ] **Step 3: Implement `_compare`**

Add to `app/services/notion/archive_validator.py` (after `expected_tree`; add the import at the top of the file):

```python
from app.services.notion.page_creator import _normalize
```

```python
def _compare(exp: ExpectedTree, client, homework_page_id: str) -> list[str]:
    """Read the live Homework tree via `client` and return human-readable issue
    strings (empty list = verified). I/O via the injected client only."""
    issues: list[str] = []

    children = client.get_child_pages(homework_page_id)
    by_norm = {_normalize(c["title"]): c for c in children}
    exp_top = {_normalize(t): t for t in exp.top_titles}

    for norm, title in exp_top.items():
        if norm not in by_norm:
            issues.append(f"Homework missing child page: {title!r}")
    for norm, child in by_norm.items():
        if norm not in exp_top:
            issues.append(f"Homework has unexpected child page: {child['title']!r}")

    if exp.container is not None:
        cnorm = _normalize(exp.container.title)
        if cnorm in by_norm:
            cont_children = client.get_child_pages(by_norm[cnorm]["id"])
            cont_norm = {_normalize(c["title"]) for c in cont_children}
            exp_children = {_normalize(c) for c in exp.container.children}
            for child_title in exp.container.children:
                if _normalize(child_title) not in cont_norm:
                    issues.append(f"Gamified Practices missing child: {child_title!r}")
            for c in cont_children:
                if _normalize(c["title"]) not in exp_children:
                    issues.append(f"Gamified Practices unexpected child: {c['title']!r}")

    for leaf in exp.leaves:
        norm = _normalize(leaf.title)
        if norm not in by_norm:
            continue  # already reported as missing above
        blocks = client.get_block_children(by_norm[norm]["id"])
        leading_files = 0
        for b in blocks:
            if b.get("type") == "file":
                leading_files += 1
            else:
                break  # attachments are at the TOP; first non-file ends the run
        if leading_files < leaf.attachments:
            issues.append(
                f"{leaf.title}: expected {leaf.attachments} attachment(s), found {leading_files}"
            )
        if not any(b.get("type") != "file" for b in blocks):
            issues.append(f"{leaf.title}: page has no content after attachments")

    return issues
```

- [ ] **Step 4: Run to verify it passes**

Run: `& ".\.venv\Scripts\python.exe" -m pytest tests/services/test_archive_validator_compare.py -q`
Expected: 6 PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/notion/archive_validator.py tests/services/test_archive_validator_compare.py
git commit -m "feat(notion): _compare — live archive tree vs expected (normalized titles, attachments, non-empty)"
```

---

## Task 4: `validate_archive` orchestrator + pipeline wiring

**Files:**
- Modify: `app/services/notion/archive_validator.py` (add `validate_archive`, `_record`, imports)
- Modify: `app/services/pipeline.py` (import + call after the archive `try/except` `:196-199`)
- Test: `tests/services/test_validate_archive_wiring.py` (new)

> `validate_archive` does DB + Notion I/O, so it's verified by `inspect.getsource`/`signature`; its real behaviour is the Task 5 smoke. It is best-effort internally (records `check-failed` on any error) AND is wrapped in the pipeline as belt-and-suspenders, so it can never break a `done` job.

- [ ] **Step 1: Write the failing test** (DB-free — source/signature)

```python
# tests/services/test_validate_archive_wiring.py
import inspect

from app.services.notion import archive_validator
from app.services import pipeline


def test_validate_archive_is_async_and_guards_disabled_notion():
    assert inspect.iscoroutinefunction(archive_validator.validate_archive)
    src = inspect.getsource(archive_validator.validate_archive)
    assert "notion_enabled" in src
    assert '"skipped"' in src
    assert '"archive-incomplete"' in src
    assert "expected_tree" in src and "_compare" in src
    assert "asyncio.to_thread" in src        # client reads off the event loop
    assert '"check-failed"' in src           # graceful degradation


def test_pipeline_calls_validate_archive_after_archive():
    src = inspect.getsource(pipeline.run)
    assert "archive_validator.validate_archive" in src
    # must come AFTER the archive_job call (and its except), not replace it
    assert src.index("archive_job") < src.index("validate_archive")
```

- [ ] **Step 2: Run to verify it fails**

Run: `& ".\.venv\Scripts\python.exe" -m pytest tests/services/test_validate_archive_wiring.py -q`
Expected: FAIL — `validate_archive` missing; not referenced in `pipeline.run`.

- [ ] **Step 3: Add `validate_archive` + `_record`**

At the top of `app/services/notion/archive_validator.py`, extend the imports:

```python
import asyncio
import logging
from datetime import datetime, timezone
from uuid import UUID

from app.config import settings
from app.db import SessionLocal
from app.repositories import jobs as jobs_repo
from app.repositories import phase_outputs as phase_repo
from app.repositories import toc_entries as toc_repo
from app.services.notion.client import NotionClientWrapper

log = logging.getLogger("notion.archive_validator")
```

Then append:

```python
def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def _record(job_id: UUID, status: str, issues: list[str]) -> None:
    result = {"status": status, "checked_at": _utcnow().isoformat(), "issues": issues}
    try:
        async with SessionLocal() as session:
            await jobs_repo.set_notion_validation(session, job_id, result)
            await session.commit()
    except Exception:
        log.warning("notion validate: failed to record result for job %s", job_id, exc_info=True)


async def validate_archive(job_id: UUID) -> None:
    """Best-effort: confirm the live Notion tree matches the expected archive
    structure and persist the result. Never raises into the pipeline."""
    if not settings.notion_enabled or not settings.notion_api_key:
        await _record(job_id, "skipped", [])
        return
    try:
        async with SessionLocal() as session:
            job = await jobs_repo.get(session, job_id)
            if job is None:
                return
            archived_at = job.notion_archived_at
            section = await toc_repo.get(session, job.toc_entry_id)
            homework_page_id = section.notion_homework_page_id if section else None
            phase_keys = {
                p.phase_name
                for p in await phase_repo.list_for_job(session, job_id)
                if p.status == "done" and p.phase_name != "extract" and (p.output_md or "").strip()
            }

        if archived_at is None or not homework_page_id:
            await _record(job_id, "archive-incomplete", [])
            return

        exp = expected_tree(phase_keys)
        client = NotionClientWrapper(api_key=settings.notion_api_key)
        issues = await asyncio.to_thread(_compare, exp, client, homework_page_id)
        status = "verified" if not issues else "mismatch"
        if issues:
            log.warning("notion validate job %s: %s", job_id, issues)
        await _record(job_id, status, issues)
    except Exception:
        log.warning("notion validate failed for job %s (non-fatal)", job_id, exc_info=True)
        await _record(job_id, "check-failed", [])
```

- [ ] **Step 4: Wire it into `pipeline.run`**

In `app/services/pipeline.py`, add the import near the other service imports (after line 18):

```python
from app.services.notion import archive_validator
```

Then, in `run`, the archive hook currently reads (`:196-199`):

```python
        try:
            await notion_archive.archive_job(job_id)
        except Exception:
            log.warning(f"[job {job_id}] notion archive hook failed (non-fatal)", exc_info=True)
```

Add immediately after it:

```python
        try:
            await archive_validator.validate_archive(job_id)
        except Exception:
            log.warning(f"[job {job_id}] notion archive validation hook failed (non-fatal)", exc_info=True)
```

- [ ] **Step 5: Run tests + import + full suite**

Run: `& ".\.venv\Scripts\python.exe" -m pytest tests/services/test_validate_archive_wiring.py -q`,
then `& ".\.venv\Scripts\python.exe" -c "import app.services.pipeline; import app.services.notion.archive_validator"`,
then `& ".\.venv\Scripts\python.exe" -m pytest tests/ -q`.
Expected: new tests PASS; imports clean; full suite green except the documented pre-existing red (`test_notion_defaults_disabled`).

- [ ] **Step 6: Commit**

```bash
git add app/services/notion/archive_validator.py app/services/pipeline.py tests/services/test_validate_archive_wiring.py
git commit -m "feat(notion): validate_archive orchestrator + pipeline wiring (after archive, best-effort)"
```

---

## Task 5: Acceptance smoke + worklog

**No code.** Archive behaviour is proven by a real run (CLAUDE.md gate). Requires Notion enabled + configured (`NOTION_ENABLED=1`, `NOTION_API_KEY`, a subject-page mapping) and the four CLIs on PATH.

- [ ] **Step 1: Suites green**

Run: `& ".\.venv\Scripts\python.exe" -m pytest tests/ -q`
Expected: green except the documented pre-existing red.

- [ ] **Step 2: Verified smoke**

Generate (or reuse) a job that archives successfully. Confirm in the DB:
```sql
SELECT id, status, notion_archived_at, notion_validation FROM homework_jobs WHERE id = :job_id;
```
`notion_validation->>'status'` is `verified` and `notion_validation->'issues'` is `[]`. Cross-check the log shows no validation warning.

- [ ] **Step 3: Mismatch smoke**

In Notion, delete one child page under that job's `Homework` (e.g. Boss Arena), then re-run `validate_archive(job_id)` in a REPL/scratch (`await archive_validator.validate_archive(<job_id>)`). Confirm `notion_validation->>'status'` becomes `mismatch` and `issues` names the missing page. (Re-archiving would re-create it; this is just to exercise the detector.)

- [ ] **Step 4: Skipped / archive-incomplete smokes**

- With `NOTION_ENABLED=0`, run a job → `notion_validation->>'status'` is `skipped`.
- Force an archive failure (e.g. temporarily bad `NOTION_API_KEY` so `archive_job` raises but the job still reaches `done`) → confirm `validate_archive` records `archive-incomplete` (proving placement AFTER the archive `try/except`).

- [ ] **Step 5: Worklog**

Add a worklog entry to `docs/memory/MASTER_MEMORY.md` + a row in `docs/memory/INDEX.md`: the auto Notion archive validator (DB gate + live structural compare, best-effort, `notion_validation` column); note it's the sibling of the per-phase LLM judge (job-level/deterministic vs per-phase/LLM), that console surfacing of the result is in WISHLIST, and that placement (Generated Lessons/adoption) stays OUT pending the `lesson-matching` merge.

---

## Self-review

**Spec coverage:** auto in-pipeline after `archive_job` (T4 wiring, after the except) ✓ · DB gate (T4 `archived_at`/`homework_page_id` → `archive-incomplete`) ✓ · live structure compare via NotionClientWrapper (T3 `_compare`) ✓ · expected tree derived from `phase_keys` reusing `_HOMEWORK_LAYOUT`/`PHASE_TITLES` (T2) ✓ · **derived** per-leaf attachment count, #1 fix (T2 `len(present)`, tested by the memory-check-absent case) ✓ · placement AFTER the archive try/except, #2 fix (T4 Step 4 + wiring test) ✓ · `skipped` recorded explicitly, NULL=never-ran (T4 guard + T1 column comment) ✓ · `mismatch`/`check-failed`/`verified` states (T4) ✓ · best-effort never raises (T4 internal try/except + pipeline wrap) ✓ · `notion_validation` JSONB column + setter (T1) ✓ · migration head a7c1e9d2b4f8 → 0020 (T1 Step 5, `alembic heads` check) ✓ · normalized-title comparison via `page_creator._normalize` (T3) ✓ · file-block type `"file"` (T3, confirmed against `make_file_upload_block`) ✓ · content-fidelity / placement(D) / re-archive / console surfacing OUT (none planned; console in WISHLIST) ✓.

**Placeholder scan:** no TBD/TODO; every code step shows complete code; I/O functions (`validate_archive`) verified by `inspect` + the T5 live smoke (DB-free harness, per CLAUDE.md).

**Type consistency:** `expected_tree(phase_keys: set[str]) -> ExpectedTree` matches T2 tests and the T3/T4 call sites. `ExpectedTree(leaves: list[ExpectedLeaf], container: Optional[ExpectedContainer])` + `.top_titles` + `ExpectedLeaf(title, attachments)` + `ExpectedContainer(title, children)` are consistent across T2/T3. `_compare(exp, client, homework_page_id) -> list[str]` matches its T3 tests (fake client with `get_child_pages`/`get_block_children`) and the T4 `asyncio.to_thread(_compare, exp, client, homework_page_id)` call. `set_notion_validation(session, job_id, result)` (T1) matches the `_record` call (T4). Migration chain `a7c1e9d2b4f8 → b3d6f1a8c2e5` (T1).
