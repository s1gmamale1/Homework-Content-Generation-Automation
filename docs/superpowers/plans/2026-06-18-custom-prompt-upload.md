# Per-phase Custom Prompts + Phase Subset — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On the section (and batch) launch page, let a user (a) upload a `.md` per phase that **replaces** that phase's built-in prompt and (b) generate only a chosen subset of phases — handling the four gate conditions: dedup force-fresh, judge contract override, dependency-closure expansion, and real-text provenance hash.

**Architecture:** Two nullable JSONB columns (`custom_prompts` = `{phase: md}`, `selected_phases` = ordered closure) on `homework_jobs` and `batches` carry intent from request → worker. The browser reads each `.md` inline. The endpoint validates, expands the dependency closure (returning what it auto-added), and force-creates a fresh job whenever custom/subset is present. The pipeline runs only the closure phases, swaps each phase's prompt for its custom override (and stamps a `sha256` provenance hash), and feeds the same override to the judge so it grades against the right contract.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 async + Alembic, Pydantic v2, pytest/pytest-asyncio; React + TypeScript + Vite (web/).

> **Naming note (pre-flight correction):** the phase-subset concept is named **`selected_phases`** everywhere (DB column, request schema, repo param, pipeline, FE body) — NOT `phases`. `JobOut` already has a `phases: list[PhaseOut]` field and `_job_out` calls `JobOut.model_validate(job)`; an ORM column literally named `phases` (list of strings) would break that validation for every job response. The closure the endpoint computes is returned to the FE as **`added_phases`** (a separate response field added to `JobOut`).

## Global Constraints

- **Replace, not append** — a custom phase prompt fully replaces `get_prompt(subject, phase)` for both generation and judging.
- **Never written to `prompts/`** — custom text lives only on the job/batch row and in-memory during the run.
- **`extract` is off-limits** — never a `custom_prompts` key, never in `selected_phases`; it is the always-on head every content phase depends on.
- **Per-prompt length cap: 20 000 characters** — 400 if any phase's custom md exceeds it.
- **`selected_phases` semantics:** `null` = run all (default flow); `[]` = 400 (must pick ≥1); a non-empty list is dependency-closure-expanded server-side and stored as the ordered closure.
- **Force-fresh:** any launch carrying `custom_prompts` (non-empty) OR `selected_phases` (non-null) skips the natural-key reuse check (Gate 1). Advisory lock + header idempotency still apply.
- **Backwards compatible** — both fields optional everywhere; absent ⇒ today's behavior exactly (NULL columns, full flow, built-in prompts).
- **Stage only the files each task lists** — never `git add -A`.
- Commit footer (every commit):
  ```
  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  ```

## File Structure

- `app/services/flows.py` — owns the pure closure logic (`expand_phase_selection`) next to `PHASE_DEPS`/`resolve_phase_deps`.
- `app/services/pipeline.py` — owns the per-phase prompt/hash resolution (`_custom_for` accessor) and the subset sequence build.
- `app/services/phase_judge.py` — owns the `contract_override` hook.
- Models / migration / repos / schemas / endpoints — thin plumbing mirroring the existing `transport` field.
- `web/src/routes/section.tsx` — the phase picker + per-phase uploader (local component state, inline like the existing `AgentPicker`).

---

### Task 1: DB columns + models (job + batch)

**Files:**
- Create: `alembic/versions/0027_add_custom_prompts_selected_phases.py`
- Modify: `app/models/homework_job.py` (after `judge_transport`, ~line 30), `app/models/batch.py` (after `judge_transport`, ~line 29)
- Test: `tests/repositories/test_custom_prompt_columns.py`

**Interfaces:**
- Produces: `HomeworkJob.custom_prompts: Optional[dict]`, `HomeworkJob.selected_phases: Optional[list]`, `Batch.custom_prompts`, `Batch.selected_phases` — all nullable JSONB.

- [ ] **Step 1: Write the failing test**

```python
# tests/repositories/test_custom_prompt_columns.py
"""custom_prompts + selected_phases JSONB columns exist and are nullable (no DB)."""
from app.models.batch import Batch
from app.models.homework_job import HomeworkJob


def test_homework_job_has_custom_columns():
    for name in ("custom_prompts", "selected_phases"):
        col = HomeworkJob.__table__.columns[name]
        assert col.nullable is True
        assert col.server_default is None


def test_batch_has_custom_columns():
    for name in ("custom_prompts", "selected_phases"):
        col = Batch.__table__.columns[name]
        assert col.nullable is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/repositories/test_custom_prompt_columns.py -q`
Expected: FAIL — `KeyError: 'custom_prompts'`

- [ ] **Step 3: Add the model columns**

In `app/models/homework_job.py`, ensure `from sqlalchemy.dialects.postgresql import JSONB` is imported (add if missing). After the `judge_transport` line (~30):

```python
    # Per-phase custom prompt overrides {phase_name: markdown} for this job.
    # NULL/{} = all built-in. Never written to prompts/.
    custom_prompts: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    # Ordered content-phase subset to run (dependency-closure-expanded at launch).
    # NULL = run the full subject flow. Named selected_phases (not `phases`) to
    # avoid colliding with JobOut.phases (the phase-outputs list).
    selected_phases: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
```

In `app/models/batch.py`, ensure the JSONB import is present and, after `judge_transport` (~line 29):

```python
    # Mirror of the launch's per-phase overrides + phase subset (provenance label).
    custom_prompts: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    selected_phases: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
```

(Both files already import `Optional` and `Mapped`/`mapped_column`.)

- [ ] **Step 4: Write the migration**

```python
# alembic/versions/0027_add_custom_prompts_selected_phases.py
"""Add nullable custom_prompts + selected_phases JSONB to homework_jobs and batches.

Carry per-phase custom prompt overrides and the phase subset (closure) from the
launch request to the worker. Nullable, no server default. Backwards compatible."""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "c1d2e3f4a5b6"
down_revision: Union[str, Sequence[str], None] = "a8c7e6d5f4b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("homework_jobs", "batches"):
        op.add_column(table, sa.Column("custom_prompts", JSONB(), nullable=True))
        op.add_column(table, sa.Column("selected_phases", JSONB(), nullable=True))


def downgrade() -> None:
    for table in ("homework_jobs", "batches"):
        op.drop_column(table, "selected_phases")
        op.drop_column(table, "custom_prompts")
```

- [ ] **Step 5: Run test + confirm single head**

Run: `uv run python -m pytest tests/repositories/test_custom_prompt_columns.py -q`
Expected: PASS
Run: `uv run alembic heads`
Expected: single head `c1d2e3f4a5b6 (head)`

- [ ] **Step 6: Commit**

```bash
git add alembic/versions/0027_add_custom_prompts_selected_phases.py app/models/homework_job.py app/models/batch.py tests/repositories/test_custom_prompt_columns.py
git commit -m "feat(db): add custom_prompts + selected_phases JSONB to jobs and batches"
```

---

### Task 2: `flows.expand_phase_selection` (dependency closure)

**Files:**
- Modify: `app/services/flows.py` (add after `resolve_phase_deps`, ~line 153)
- Test: `tests/services/test_expand_phase_selection.py`

**Interfaces:**
- Consumes: `flow_for(subject)`, `resolve_phase_deps(phase, flow)` (existing).
- Produces: `expand_phase_selection(subject: str, selected: list[str]) -> tuple[list[str], list[str]]` → `(ordered_closure, added_phases)`. Raises `ValueError` for unknown phase names or empty selection.

- [ ] **Step 1: Write the failing test**

```python
# tests/services/test_expand_phase_selection.py
import pytest

from app.services.flows import expand_phase_selection, flow_for

SUBJECT = "math-algebra"


def test_bossarena_pulls_in_its_deps():
    ordered, added = expand_phase_selection(SUBJECT, ["boss-arena"])
    # boss-arena needs case-based-preview + flashcards + memory-check
    for dep in ("case-based-preview", "flashcards", "memory-check"):
        assert dep in ordered
        assert dep in added
    assert "boss-arena" in ordered
    assert "boss-arena" not in added  # user-selected, not auto-added
    # ordering matches the subject's canonical flow
    flow = flow_for(SUBJECT)
    assert ordered == [p for p in flow if p in set(ordered)]


def test_full_selection_adds_nothing():
    flow = flow_for(SUBJECT)
    ordered, added = expand_phase_selection(SUBJECT, list(flow))
    assert set(ordered) == set(flow)
    assert added == []


def test_unknown_phase_raises():
    with pytest.raises(ValueError):
        expand_phase_selection(SUBJECT, ["not-a-phase"])


def test_empty_selection_raises():
    with pytest.raises(ValueError):
        expand_phase_selection(SUBJECT, [])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/services/test_expand_phase_selection.py -q`
Expected: FAIL — `ImportError: cannot import name 'expand_phase_selection'`

- [ ] **Step 3: Implement the helper**

In `app/services/flows.py`, after `resolve_phase_deps` (~line 153):

```python
def expand_phase_selection(
    subject: str, selected: list[str]
) -> tuple[list[str], list[str]]:
    """Expand a user's phase selection to its full dependency closure.

    Returns (ordered_closure, added_phases): the closure ordered by the
    subject's canonical flow, plus the phases that were auto-added (deps the
    user did not select). Raises ValueError on an empty selection or any phase
    not in the subject's flow. `extract` is never selectable (it is the head).
    """
    if not selected:
        raise ValueError("phase selection is empty — pick at least one phase")
    flow = flow_for(subject)
    flow_set = set(flow)
    unknown = [p for p in selected if p not in flow_set]
    if unknown:
        raise ValueError(f"phases not in {subject} flow: {unknown}")

    chosen = set(selected)
    changed = True
    while changed:                       # fixpoint: deps-of-deps included
        changed = False
        for p in list(chosen):
            for dep in resolve_phase_deps(p, flow):
                if dep not in chosen:
                    chosen.add(dep)
                    changed = True

    ordered = [p for p in flow if p in chosen]
    selected_set = set(selected)
    added = [p for p in ordered if p not in selected_set]
    return ordered, added
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/services/test_expand_phase_selection.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/flows.py tests/services/test_expand_phase_selection.py
git commit -m "feat(flows): expand_phase_selection computes dependency closure"
```

---

### Task 3: Request schemas (generate + batch) + JobOut.added_phases

**Files:**
- Modify: `app/schemas/job.py` (`GenerateRequest` ~line 42-48; `JobOut` ~line 23-39)
- Modify: `app/api/v1/batch.py` (`BatchLaunchRequest`, ~line 22-30)
- Test: `tests/services/test_custom_prompt_schema.py`

**Interfaces:**
- Produces: `GenerateRequest.custom_prompts: dict[str,str] | None = None`, `GenerateRequest.selected_phases: list[str] | None = None`; same two on `BatchLaunchRequest`; `JobOut.added_phases: list[str] = []`.

- [ ] **Step 1: Write the failing test**

```python
# tests/services/test_custom_prompt_schema.py
from app.api.v1.batch import BatchLaunchRequest
from app.schemas.job import GenerateRequest, JobOut


def test_generate_defaults_none():
    req = GenerateRequest()
    assert req.custom_prompts is None
    assert req.selected_phases is None


def test_generate_round_trips():
    req = GenerateRequest(custom_prompts={"flashcards": "RULES"}, selected_phases=["flashcards"])
    assert req.custom_prompts == {"flashcards": "RULES"}
    assert req.selected_phases == ["flashcards"]


def test_batch_round_trips():
    req = BatchLaunchRequest(
        book_id="00000000-0000-0000-0000-000000000001",
        custom_prompts={"reflection": "X"}, selected_phases=["reflection"],
    )
    assert req.custom_prompts == {"reflection": "X"}
    assert req.selected_phases == ["reflection"]


def test_jobout_added_phases_defaults_empty():
    # from_attributes build from a stub that lacks added_phases → default []
    class _J:
        id = "00000000-0000-0000-0000-000000000001"
        book_id = "00000000-0000-0000-0000-000000000002"
        toc_entry_id = "00000000-0000-0000-0000-000000000003"
        subject = "math-algebra"
        status = "pending"
        current_phase = None
        error_message = None
        provider = "claude"
        model = None
        transport = "cli"
        extract_transport = "inherit"
        judge_transport = "inherit"
        notion_skip_reason = None
    out = JobOut.model_validate(_J())
    assert out.added_phases == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/services/test_custom_prompt_schema.py -q`
Expected: FAIL — `AttributeError`/`ValidationError` for the missing fields.

- [ ] **Step 3: Add fields**

In `app/schemas/job.py`, inside `GenerateRequest`, after `judge_transport` (~line 48):

```python
    custom_prompts: dict[str, str] | None = None   # {phase: markdown}; replaces built-in. Not persisted to prompts/.
    selected_phases: list[str] | None = None        # subset to run; None = full flow. Dependency-closure-expanded server-side.
```

In `app/schemas/job.py`, inside `JobOut`, after `phases: list[PhaseOut] = []` (~line 38):

```python
    added_phases: list[str] = []   # deps the closure auto-added beyond the user's selection (response only)
```

In `app/api/v1/batch.py`, inside `BatchLaunchRequest`, after `force: bool = False` (~line 30):

```python
    custom_prompts: dict[str, str] | None = None
    selected_phases: list[str] | None = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/services/test_custom_prompt_schema.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/schemas/job.py app/api/v1/batch.py tests/services/test_custom_prompt_schema.py
git commit -m "feat(schema): add custom_prompts + selected_phases requests, JobOut.added_phases"
```

---

### Task 4: Repository plumbing (jobs + batches)

**Files:**
- Modify: `app/repositories/jobs.py` (`create`, lines 12-44)
- Modify: `app/repositories/batches.py` (`get_or_create_for_book`, lines 12-53)
- Test: `tests/repositories/test_custom_prompt_persist.py`

**Interfaces:**
- Consumes: the JSONB columns (Task 1).
- Produces: `jobs_repo.create(..., custom_prompts=None, selected_phases=None)`; `batches_repo.get_or_create_for_book(..., custom_prompts=None, selected_phases=None)`.

- [ ] **Step 1: Write the failing test** (real-DB, standard marker)

```python
# tests/repositories/test_custom_prompt_persist.py
import os

import pytest

_DB = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)


async def _seed_book(sha: str):
    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.toc_entry import TOCEntry
    async with SessionLocal() as s:
        book = Book(subject="math-algebra", original_filename="x.pdf",
                    content_sha256=sha * 64, file_size_bytes=1, status="toc_ready")
        s.add(book)
        await s.flush()
        t = TOCEntry(book_id=book.id, section_title="L0", order_index=0)
        s.add(t)
        await s.flush()
        await s.commit()
        return book.id, t.id


async def _cleanup(book_id):
    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.homework_job import HomeworkJob
    from app.models.toc_entry import TOCEntry
    from sqlalchemy import delete
    async with SessionLocal() as s:
        await s.execute(delete(HomeworkJob).where(HomeworkJob.book_id == book_id))
        await s.execute(delete(TOCEntry).where(TOCEntry.book_id == book_id))
        await s.execute(delete(Book).where(Book.id == book_id))
        await s.commit()


@_DB
@pytest.mark.asyncio
async def test_create_persists_custom_fields():
    from app.db import SessionLocal
    from app.repositories import jobs as jobs_repo
    book_id, sid = await _seed_book("C")
    try:
        async with SessionLocal() as s:
            job = await jobs_repo.create(
                s, book_id=book_id, toc_entry_id=sid, subject="math-algebra",
                custom_prompts={"flashcards": "RULES"}, selected_phases=["flashcards"],
            )
            await s.commit()
            assert job.custom_prompts == {"flashcards": "RULES"}
            assert job.selected_phases == ["flashcards"]
    finally:
        await _cleanup(book_id)


@_DB
@pytest.mark.asyncio
async def test_create_without_custom_is_null():
    from app.db import SessionLocal
    from app.repositories import jobs as jobs_repo
    book_id, sid = await _seed_book("D")
    try:
        async with SessionLocal() as s:
            job = await jobs_repo.create(s, book_id=book_id, toc_entry_id=sid,
                                         subject="math-algebra")
            await s.commit()
            assert job.custom_prompts is None
            assert job.selected_phases is None
    finally:
        await _cleanup(book_id)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `RUN_DB_INTEGRATION=1 uv run python -m pytest tests/repositories/test_custom_prompt_persist.py -q`
Expected: FAIL — `TypeError: create() got an unexpected keyword argument 'custom_prompts'` (or SKIP without DB — proven at the acceptance gate)

- [ ] **Step 3: Extend `jobs_repo.create`**

In `app/repositories/jobs.py`, add to the signature after `judge_transport` (~line 24):

```python
    custom_prompts: Optional[dict] = None,
    selected_phases: Optional[list] = None,
```

After the `if batch_id is not None:` block (~line 40):

```python
    if custom_prompts is not None:
        kwargs["custom_prompts"] = custom_prompts
    if selected_phases is not None:
        kwargs["selected_phases"] = selected_phases
```

- [ ] **Step 4: Extend `batches_repo.get_or_create_for_book`**

In `app/repositories/batches.py`, add to the signature after `notion_source` (~line 23):

```python
    custom_prompts: Optional[dict] = None,
    selected_phases: Optional[list] = None,
```

Add to the `pg_insert(Batch).values(...)` block (after `notion_source=notion_source,`, ~line 42):

```python
            custom_prompts=custom_prompts,
            selected_phases=selected_phases,
```

And to the conflict `set_` (so a re-launch records the latest intent; ~line 48):

```python
            set_={"updated_at": func.now(),
                  "custom_prompts": custom_prompts, "selected_phases": selected_phases},
```

- [ ] **Step 5: Run test to verify it passes**

Run: `RUN_DB_INTEGRATION=1 uv run python -m pytest tests/repositories/test_custom_prompt_persist.py -q`
Expected: PASS (or SKIP without DB)

- [ ] **Step 6: Commit**

```bash
git add app/repositories/jobs.py app/repositories/batches.py tests/repositories/test_custom_prompt_persist.py
git commit -m "feat(repo): persist custom_prompts + selected_phases on jobs and batches"
```

---

### Task 5: Generate endpoint — validate, expand, force-fresh, return added_phases

**Files:**
- Modify: `app/api/v1/jobs.py` (`generate`, validation after ~line 144; reuse check ~line 153; create ~line 179; fresh-job return)
- Test: `tests/api/test_custom_prompt_endpoint.py`

**Interfaces:**
- Consumes: `expand_phase_selection` (Task 2), schema fields + `JobOut.added_phases` (Task 3), `jobs_repo.create(custom_prompts=, selected_phases=)` (Task 4).
- Produces: 400 on unknown phase / oversize prompt / empty `selected_phases`; force-fresh when custom/subset; the freshly-created job's `JobOut` carries `added_phases`.

- [ ] **Step 1: Write the failing tests** (no-DB rejections; mirrors `test_transport_validation`)

```python
# tests/api/test_custom_prompt_endpoint.py
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient

_HDR = {"Authorization": "Bearer 123"}
_BOOK_ID = "00000000-0000-0000-0000-000000000001"
_SECTION_ID = "00000000-0000-0000-0000-000000000002"


def _client():
    from main import app
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


def _ready_book_patch(monkeypatch):
    from app.api.v1 import jobs as jobs_mod

    class _Book:
        status = "toc_ready"
        subject = "math-algebra"

        def __init__(self, book_id):
            self.id = book_id

    class _Section:
        def __init__(self, book_id, sid):
            self.id = sid
            self.book_id = book_id

    async def _fake_book(session, book_id):
        return _Book(book_id)

    async def _fake_toc(session, toc_entry_id):
        return _Section(UUID(_BOOK_ID), toc_entry_id)

    monkeypatch.setattr(jobs_mod.books_repo, "get", _fake_book)
    monkeypatch.setattr(jobs_mod.toc_repo, "get", _fake_toc)


def _post(c, body):
    return c.post(f"/api/v1/books/{_BOOK_ID}/sections/{_SECTION_ID}/generate",
                  headers=_HDR, json={"provider": "claude", **body})


@pytest.mark.asyncio
async def test_unknown_phase_rejected(monkeypatch):
    _ready_book_patch(monkeypatch)
    async with _client() as c:
        r = await _post(c, {"selected_phases": ["not-a-phase"]})
    assert r.status_code == 400, r.text
    assert "phase" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_empty_phases_rejected(monkeypatch):
    _ready_book_patch(monkeypatch)
    async with _client() as c:
        r = await _post(c, {"selected_phases": []})
    assert r.status_code == 400, r.text


@pytest.mark.asyncio
async def test_oversize_custom_prompt_rejected(monkeypatch):
    _ready_book_patch(monkeypatch)
    async with _client() as c:
        r = await _post(c, {"custom_prompts": {"flashcards": "x" * 20_001}})
    assert r.status_code == 400, r.text
    assert "flashcards" in r.json()["detail"]


@pytest.mark.asyncio
async def test_custom_prompt_for_extract_rejected(monkeypatch):
    _ready_book_patch(monkeypatch)
    async with _client() as c:
        r = await _post(c, {"custom_prompts": {"extract": "no"}})
    assert r.status_code == 400, r.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/api/test_custom_prompt_endpoint.py -q`
Expected: FAIL — requests currently pass validation (no 400).

- [ ] **Step 3: Add validation + closure expansion**

In `app/api/v1/jobs.py`, add the import near the other `app.services` imports:

```python
from app.services.flows import expand_phase_selection, flow_for
```

After the role-transport validation loop (after ~line 144, before the advisory-lock comment ~line 146), add:

```python
    # ── custom prompts + phase subset validation (Gate 2/3, fail before DB) ──
    custom_prompts = body.custom_prompts or None
    if custom_prompts:
        valid_phases = set(flow_for(book.subject))
        for phase, md in custom_prompts.items():
            if phase == "extract" or phase not in valid_phases:
                raise HTTPException(400, f"custom_prompts: unknown phase {phase!r}")
            if len(md) > 20_000:
                raise HTTPException(
                    400, f"custom_prompts[{phase}] too long ({len(md)} chars; max 20000).")

    selected_closure: Optional[list[str]] = None
    added_phases: list[str] = []
    if body.selected_phases is not None:
        try:
            selected_closure, added_phases = expand_phase_selection(book.subject, body.selected_phases)
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    # Gate 1: a custom/subset launch must never reuse a plain job.
    force_fresh = body.force or bool(custom_prompts) or selected_closure is not None
```

- [ ] **Step 4: Use force_fresh in the reuse check**

Replace the `if not body.force:` guard (line 153) with:

```python
    if not force_fresh:
```

- [ ] **Step 5: Thread into create**

In the `jobs_repo.create(...)` call (lines 179-190), add after `judge_transport=body.judge_transport,`:

```python
        custom_prompts=custom_prompts,
        selected_phases=selected_closure,
```

- [ ] **Step 6: Return `added_phases` on the fresh job**

The fresh-create path ends with `return await _job_out(...)` for the new job (the early idempotent-return paths above are unchanged — they carry the default `added_phases=[]`). Replace that fresh-job return with:

```python
    out = await _job_out(session, job.id)
    out.added_phases = added_phases
    response.status_code = 201
    return out
```

(`JobOut.added_phases` exists from Task 3; the handler keeps its `-> JobOut` annotation so FastAPI serializes the field. No dict / `response_model` change.)

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run python -m pytest tests/api/test_custom_prompt_endpoint.py -q`
Expected: PASS
Run (regression): `uv run python -m pytest tests/api/test_transport_validation.py -q`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add app/api/v1/jobs.py tests/api/test_custom_prompt_endpoint.py
git commit -m "feat(api): validate/expand custom prompts + subset, force-fresh on generate"
```

---

### Task 6: Batch endpoint — mirror validate/expand/force-fresh

**Files:**
- Modify: `app/api/v1/batch.py` (`launch_batch`, ~lines 80-130)
- Test: `tests/api/test_custom_prompt_batch.py`

**Interfaces:**
- Consumes: same helpers as Task 5; `batches_repo.get_or_create_for_book(custom_prompts=, selected_phases=)` and `jobs_repo.create(custom_prompts=, selected_phases=)` (Task 4).
- Produces: 400 on the same invalid inputs; per-target force-fresh; batch + jobs carry the fields.

- [ ] **Step 1: Write the failing tests** (no-DB, mirrors `_ready_batch_patch`)

```python
# tests/api/test_custom_prompt_batch.py
import pytest
from httpx import ASGITransport, AsyncClient

_HDR = {"Authorization": "Bearer 123"}
_BOOK_ID = "00000000-0000-0000-0000-000000000001"


def _client():
    from main import app
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


def _ready_batch_patch(monkeypatch):
    from app.api.v1 import batch as batch_mod

    class _Book:
        status = "toc_ready"
        subject = "math-algebra"
        grade = None
        error_message = None

    class _TOC:
        def __init__(self, i):
            self.id = i
            self.section_title = f"L{i}"
            self.order_index = i

    async def _fake_book(session, book_id):
        return _Book()

    async def _fake_list(session, book_id):
        return [_TOC(0), _TOC(1)]

    monkeypatch.setattr(batch_mod.books_repo, "get", _fake_book)
    monkeypatch.setattr(batch_mod.toc_repo, "list_for_book", _fake_list)


@pytest.mark.asyncio
async def test_batch_unknown_phase_rejected(monkeypatch):
    _ready_batch_patch(monkeypatch)
    async with _client() as c:
        r = await c.post("/api/v1/jobs/batch", headers=_HDR,
                         json={"book_id": _BOOK_ID, "selected_phases": ["not-a-phase"]})
    assert r.status_code == 400, r.text


@pytest.mark.asyncio
async def test_batch_oversize_custom_rejected(monkeypatch):
    _ready_batch_patch(monkeypatch)
    async with _client() as c:
        r = await c.post("/api/v1/jobs/batch", headers=_HDR,
                         json={"book_id": _BOOK_ID,
                               "custom_prompts": {"flashcards": "x" * 20_001}})
    assert r.status_code == 400, r.text
    assert "flashcards" in r.json()["detail"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/api/test_custom_prompt_batch.py -q`
Expected: FAIL — no 400 yet.

- [ ] **Step 3: Add validation + closure**

In `app/api/v1/batch.py`, add the import:

```python
from app.services.flows import expand_phase_selection, flow_for
```

After the role-transport validation loop (after ~line 94), add:

```python
    custom_prompts = body.custom_prompts or None
    if custom_prompts:
        valid_phases = set(flow_for(book.subject))
        for phase, md in custom_prompts.items():
            if phase == "extract" or phase not in valid_phases:
                raise HTTPException(400, f"custom_prompts: unknown phase {phase!r}")
            if len(md) > 20_000:
                raise HTTPException(
                    400, f"custom_prompts[{phase}] too long ({len(md)} chars; max 20000).")

    selected_closure = None
    if body.selected_phases is not None:
        try:
            selected_closure, _added = expand_phase_selection(book.subject, body.selected_phases)
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    batch_force = body.force or bool(custom_prompts) or selected_closure is not None
```

- [ ] **Step 4: Store on the batch + use force per target + thread into job create**

In the `batches_repo.get_or_create_for_book(...)` call (~line 96), add:

```python
        custom_prompts=custom_prompts, selected_phases=selected_closure,
```

In the per-target loop, change the reuse guard (line 108) from `None if body.force else ...` to:

```python
        existing = None if batch_force else await jobs_repo.find_active_for_section(
            session, body.book_id, t.id, transport=body.transport)
```

In the `jobs_repo.create(...)` call inside the loop (~line 124), add after `judge_transport=body.judge_transport`:

```python
                               custom_prompts=custom_prompts, selected_phases=selected_closure,
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run python -m pytest tests/api/test_custom_prompt_batch.py -q`
Expected: PASS
Run (regression): `uv run python -m pytest tests/api/test_transport_validation.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/api/v1/batch.py tests/api/test_custom_prompt_batch.py
git commit -m "feat(api): mirror custom prompts + subset on the batch endpoint"
```

---

### Task 7: Judge contract override (Gate 2)

**Files:**
- Modify: `app/services/phase_judge.py` (`judge`, ~line 128-149)
- Test: `tests/services/test_judge_contract_override.py`

**Interfaces:**
- Produces: `judge(..., contract_override: Optional[str] = None)` — uses `contract_override` instead of `get_prompt(...)` when set.

- [ ] **Step 1: Write the failing test**

```python
# tests/services/test_judge_contract_override.py
import inspect

from app.services import phase_judge


def test_judge_accepts_contract_override():
    sig = inspect.signature(phase_judge.judge)
    assert "contract_override" in sig.parameters


def test_judge_uses_override_in_source():
    src = inspect.getsource(phase_judge.judge)
    # the contract must come from the override when present, else get_prompt
    assert "contract_override or get_prompt(" in src
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/services/test_judge_contract_override.py -q`
Expected: FAIL — `contract_override` not a parameter.

- [ ] **Step 3: Add the param + use it**

In `app/services/phase_judge.py`, add to the `judge` signature after `transport: str = "cli",` (~line 139):

```python
    contract_override: Optional[str] = None,
```

Replace line 148:

```python
        contract = get_prompt(subject, phase_name)
```

with:

```python
        contract = contract_override or get_prompt(subject, phase_name)
```

Update the docstring's first sentence to note the override (optional but tidy):

```python
    """Grade `output_md` against its phase contract (the custom override when
    supplied, else the built-in prompt). ..."""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/services/test_judge_contract_override.py -q`
Expected: PASS
Run (regression): `uv run python -m pytest tests/services/test_execute_phase_judge.py tests/services/test_judge_auth.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/phase_judge.py tests/services/test_judge_contract_override.py
git commit -m "feat(judge): accept contract_override so judge grades custom prompts"
```

---

### Task 8: Pipeline — subset sequence, per-phase prompt swap, provenance hash, judge override

**Files:**
- Modify: `app/services/pipeline.py` (imports; `run()` ~lines 81-227; `_execute_one_phase` ~283; `_run_content_phases_parallel` ~378; `_execute_phase` ~569; the judge calls ~750/781)
- Test: `tests/services/test_pipeline_custom_prompt.py`

**Interfaces:**
- Consumes: `HomeworkJob.custom_prompts`/`.selected_phases` (Task 1), `judge(contract_override=)` (Task 7).
- Produces: module helper `_custom_for(phase_name: str, custom_prompts: Optional[dict]) -> Optional[str]` (stripped custom text or None).

- [ ] **Step 1: Write the failing test** (pure helper + source-inspection guards)

```python
# tests/services/test_pipeline_custom_prompt.py
import inspect

from app.services import pipeline
from app.services.pipeline import _custom_for


def test_custom_for_returns_text():
    assert _custom_for("flashcards", {"flashcards": "RULES"}) == "RULES"


def test_custom_for_none_and_blank():
    assert _custom_for("flashcards", None) is None
    assert _custom_for("flashcards", {}) is None
    assert _custom_for("flashcards", {"flashcards": "   "}) is None
    assert _custom_for("flashcards", {"memory-check": "X"}) is None


def test_execute_phase_uses_custom_prompt_and_hash():
    src = inspect.getsource(pipeline._execute_phase)
    # generator prompt: custom replaces built-in
    assert "_custom_for(phase_name, custom_prompts)" in src
    # provenance: sha256 of the custom text when custom is used
    assert "sha256" in src
    # judge: the override is threaded into BOTH judge() calls
    assert src.count("contract_override=") == 2


def test_run_builds_sequence_from_selected_phases():
    src = inspect.getsource(pipeline.run)
    assert "custom_prompts" in src
    assert "selected_phases" in src
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/services/test_pipeline_custom_prompt.py -q`
Expected: FAIL — `ImportError: cannot import name '_custom_for'`

- [ ] **Step 3: Add the helper + `hashlib` import**

At the top of `app/services/pipeline.py`, ensure `import hashlib` is present (add if missing). Add the helper just above `_execute_phase` (~line 568):

```python
def _custom_for(phase_name: str, custom_prompts: Optional[dict]) -> Optional[str]:
    """The stripped custom prompt for this phase, or None (blank/missing).
    `extract` is never overridden — callers pass it through harmlessly."""
    c = (custom_prompts or {}).get(phase_name)
    return c if (c and c.strip()) else None
```

- [ ] **Step 4: Wire into `_execute_phase`**

Add to the `_execute_phase` signature after `judge_transport: str = "cli",` (~line 586):

```python
    custom_prompts: Optional[dict] = None,
```

Replace the hash block (lines 588-591):

```python
    if phase_name == "extract":
        prompt_hash = "builtin:extract:v2"
    else:
        prompt_hash = get_prompt_hash(subject, phase_name)
```

with:

```python
    _custom_md = _custom_for(phase_name, custom_prompts)
    if phase_name == "extract":
        prompt_hash = "builtin:extract:v2"
    elif _custom_md is not None:
        prompt_hash = "custom:sha256:" + hashlib.sha256(_custom_md.encode("utf-8")).hexdigest()
    else:
        prompt_hash = get_prompt_hash(subject, phase_name)
```

Replace the content-branch prompt build (line 702):

```python
            base_phase_prompt = get_prompt(subject, phase_name)
```

with:

```python
            base_phase_prompt = _custom_md if _custom_md is not None else get_prompt(subject, phase_name)
```

In the FIRST judge call (~line 750), add after `transport=judge_transport,`:

```python
            contract_override=_custom_md,
```

In the POST-REGEN judge call (~line 781), add after `transport=judge_transport,`:

```python
                contract_override=_custom_md,
```

(The regen prompt at line 772 is `base_phase_prompt + outcome.feedback`; `base_phase_prompt` is already the custom text, so the regen regenerates against the custom contract automatically.)

- [ ] **Step 5: Thread `custom_prompts` through the intermediate callers**

In `_execute_one_phase` signature (~line 303) add after `judge_transport: str = "cli",`:

```python
    custom_prompts: Optional[dict] = None,
```

In its `_execute_phase(...)` call (~line 339) add after `judge_transport=judge_transport,`:

```python
            custom_prompts=custom_prompts,
```

In `_run_content_phases_parallel` signature (~line 398) add after `judge_transport: str = "cli",`:

```python
    custom_prompts: Optional[dict] = None,
```

In its `_execute_one_phase(...)` call (~line 451) add after `judge_transport=judge_transport,`:

```python
                            custom_prompts=custom_prompts,
```

- [ ] **Step 6: Read job fields in `run()` + build the subset sequence**

In `run()`, after the `judge_transport = resolve_role_transport(...)` block (~line 90), add (still inside the `async with` that has `job`):

```python
            custom_prompts = getattr(job, "custom_prompts", None)
            selected_phases = getattr(job, "selected_phases", None)
```

Replace the sequence build (line 113):

```python
        sequence: list[str] = ["extract", *flow_for(subject)]
```

with:

```python
        # Subset: job.selected_phases is the dependency-closure the endpoint stored.
        # Defensive re-order/filter against the live flow; None ⇒ full flow.
        full_flow = flow_for(subject)
        if selected_phases:
            chosen = set(selected_phases)
            content_planned = [p for p in full_flow if p in chosen]
        else:
            content_planned = full_flow
        sequence: list[str] = ["extract", *content_planned]
```

In the head `_execute_one_phase(...)` call (~line 175) add after `judge_transport=judge_transport,`:

```python
                    custom_prompts=custom_prompts,
```

In the `_run_content_phases_parallel(...)` call (~line 226) add after `judge_transport=judge_transport,`:

```python
                    custom_prompts=custom_prompts,
```

- [ ] **Step 7: Run test to verify it passes**

Run: `uv run python -m pytest tests/services/test_pipeline_custom_prompt.py -q`
Expected: PASS

- [ ] **Step 8: Pipeline regression**

Run: `uv run python -m pytest tests/services/test_execute_phase_judge.py tests/services/test_general_flow.py tests/services/test_learning_flow.py -q`
Expected: PASS (threading is additive; default None ⇒ unchanged behavior)

- [ ] **Step 9: Commit**

```bash
git add app/services/pipeline.py tests/services/test_pipeline_custom_prompt.py
git commit -m "feat(pipeline): per-phase custom prompts, subset sequence, provenance hash"
```

---

### Task 9: Frontend — phase picker + per-phase upload + added-deps notice

**Files:**
- Modify: `web/src/lib/api.ts` (`generate` opts/body + return, lines 147-187)
- Modify: `web/src/lib/subjects.ts` (export the user-facing content-phase list + labels)
- Modify: `web/src/routes/section.tsx` (phase picker card + per-phase uploaders; surface `added_phases`)
- Test: `cd web && npx tsc -p tsconfig.app.json --noEmit` + `npm run build`

**Interfaces:**
- Consumes: the generate endpoint's `custom_prompts` + `selected_phases` body and `added_phases` response (Task 5).
- Produces: a "Phases & custom prompts" card; `api.generate(..., { custom_prompts, selected_phases })`.

- [ ] **Step 1: Add the phase list to subjects.ts**

In `web/src/lib/subjects.ts`, add (the user-selectable content phases — `extract` excluded; labels display-only; order matches the backend flow):

```typescript
/** User-selectable content phases (extract is the always-on head, not listed).
 *  The subject-specific game phase is intentionally omitted from the picker for
 *  MVP — selecting other phases still runs the full server-side closure. */
export const CONTENT_PHASES: { key: string; label: string }[] = [
  { key: "case-based-preview", label: "Preview" },
  { key: "flashcards", label: "Flashcards" },
  { key: "memory-check", label: "Memory sprint" },
  { key: "practice-rlc", label: "Practice — real-life context" },
  { key: "practice-error-detection", label: "Practice — error detection" },
  { key: "boss-arena", label: "Boss fight" },
  { key: "reflection", label: "Reflection" },
];
```

- [ ] **Step 2: Extend the api client**

In `web/src/lib/api.ts`, in the `generate` opts type (after `judge_transport?`, line 157):

```typescript
      custom_prompts?: Record<string, string> | null;
      selected_phases?: string[] | null;
```

In the destructure defaults (after `judge_transport = "inherit",`, line 167):

```typescript
      custom_prompts = null,
      selected_phases = null,
```

In the JSON body (after `judge_transport,`, line 182):

```typescript
          custom_prompts,
          selected_phases,
```

Widen the return type so callers can read the added deps (the field is optional, existing call sites unaffected):

```typescript
  ): Promise<Job & { added_phases?: string[] }> {
```

- [ ] **Step 3: Add picker + upload state to the section page**

In `web/src/routes/section.tsx`, import the phase list:

```tsx
import { subjectLabel, CONTENT_PHASES } from "@/lib/subjects";
```

After the existing `useState` hooks (~line 39), add:

```tsx
  // Phase subset: empty set = "all phases" (send null). Otherwise the chosen keys.
  const [selectedPhases, setSelectedPhases] = useState<Set<string>>(new Set());
  // Per-phase custom prompt text, keyed by phase. Read in-browser, never uploaded as a file.
  const [customPrompts, setCustomPrompts] = useState<Record<string, string>>({});

  function togglePhase(key: string) {
    setSelectedPhases((prev) => {
      const next = new Set(prev);
      next.has(key) ? next.delete(key) : next.add(key);
      return next;
    });
  }

  function handlePhasePromptFile(key: string, e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      const text = String(reader.result ?? "");
      if (!text.trim()) {
        toast.error(`${file.name} is empty`);
        return;
      }
      setCustomPrompts((prev) => ({ ...prev, [key]: text }));
    };
    reader.onerror = () => toast.error("Couldn't read that file");
    reader.readAsText(file);
  }
```

- [ ] **Step 4: Send the fields + surface added_phases in `handleGenerate`**

In `handleGenerate`, replace the `api.generate(...)` block (lines 85-94) with:

```tsx
      const selected_phases = selectedPhases.size > 0 ? [...selectedPhases] : null;
      const custom_prompts = Object.keys(customPrompts).length > 0 ? customPrompts : null;
      const job = await api.generate(bookId, sectionId, {
        force,
        idempotencyKey,
        provider,
        model,
        transport,
        extract_transport: extractTransport,
        judge_transport: judgeTransport,
        custom_prompts,
        selected_phases,
      });
      if (job.added_phases && job.added_phases.length > 0) {
        toast.info(`Also generating dependencies: ${job.added_phases.join(", ")}`);
      }
      navigate(`/job/${job.id}`);
```

- [ ] **Step 5: Render the picker card**

Between `<AgentPicker ... />` and `<ActionPanel ... />` (~line 187), insert:

```tsx
        {/* Phases & per-phase custom prompts. Leave all phases unchecked to run
            the full packet. A custom .md replaces that phase's built-in prompt
            for this run only — never saved to the prompt library. Picking a
            subset auto-adds any phases it depends on (shown after launch). */}
        <section className={CARD}>
          <p className="font-mono text-[0.7rem] uppercase tracking-[0.16em] text-white/45">
            Phases & custom prompts
          </p>
          <h2 className="mt-1 text-base font-semibold tracking-tight text-white">
            Choose phases and override prompts (optional)
          </h2>
          <p className="mt-1.5 text-sm leading-relaxed text-white/55">
            Leave all unchecked to generate the full packet. Checking a subset runs only those
            phases (plus any they depend on). Upload a <span className="font-mono">.md</span> to
            replace a phase's built-in prompt — for this run only, never saved.
          </p>

          <div className="mt-4 space-y-2">
            {CONTENT_PHASES.map(({ key, label }) => (
              <div
                key={key}
                className="flex items-center justify-between gap-3 rounded-xl border border-white/[0.1] bg-white/[0.04] px-3 py-2.5"
              >
                <label className="flex items-center gap-2.5 text-sm text-white/80">
                  <input
                    type="checkbox"
                    checked={selectedPhases.has(key)}
                    onChange={() => togglePhase(key)}
                    className="size-4 accent-[#7c5cff]"
                  />
                  {label}
                </label>
                <div className="flex items-center gap-2">
                  {customPrompts[key] ? (
                    <>
                      <span className="font-mono text-xs text-emerald-300">custom ✓</span>
                      <button
                        type="button"
                        onClick={() =>
                          setCustomPrompts((prev) => {
                            const next = { ...prev };
                            delete next[key];
                            return next;
                          })
                        }
                        className="text-xs font-medium text-white/55 transition-colors hover:text-white"
                      >
                        Remove
                      </button>
                    </>
                  ) : (
                    <label className="cursor-pointer text-xs font-medium text-white/55 transition-colors hover:text-white">
                      Upload .md
                      <input
                        type="file"
                        accept=".md,.markdown,text/markdown"
                        onChange={(e) => handlePhasePromptFile(key, e)}
                        className="hidden"
                      />
                    </label>
                  )}
                </div>
              </div>
            ))}
          </div>
        </section>
```

(`CARD`, `cn`, `toast` already imported. `toast.info` is part of sonner's API.)

- [ ] **Step 6: Typecheck + build**

Run: `cd web && npx tsc -p tsconfig.app.json --noEmit`
Expected: no errors.
Run: `cd web && npm run build`
Expected: build succeeds.

- [ ] **Step 7: Commit**

```bash
git add web/src/lib/api.ts web/src/lib/subjects.ts web/src/routes/section.tsx
git commit -m "feat(web): per-phase prompt upload + phase subset picker on section page"
```

---

## Acceptance Gate (controller runs after all tasks)

Per CLAUDE.md §4 — generation-affecting work needs a real CLI smoke. After the suite is green:

- [ ] Full backend suite: `uv run python -m pytest tests/ -q` (DB tests need `RUN_DB_INTEGRATION=1` + `DATABASE_URL`).
- [ ] **Real generate smoke #1 (custom prompt changes output + judge grades against it):** launch a section with `custom_prompts = {"flashcards": "<built-in flashcards prompt> ... and append the literal token CUSTOMMARKER as the last line of the deck."}` via a real `gemini`/`claude` run. Confirm: the flashcards output contains `CUSTOMMARKER`; the job reaches `done`; the flashcards judge did NOT spuriously regen (check logs — it saw the custom contract). Confirm `extract` output is a plain factual summary (untouched).
- [ ] **Real generate smoke #2 (subset + dependency closure):** launch with `selected_phases = ["boss-arena"]`. Confirm the response `added_phases` lists `case-based-preview`, `flashcards`, `memory-check`; those phases plus `boss-arena` (and `extract`) ran; `reflection`/practice phases did not.
- [ ] **Gate 1 manual check:** re-launch the same section (done) with a custom prompt → a NEW job is created (not the old one returned).
- [ ] Browser spot-check: the picker renders, checking phases + uploading a `.md` works, `added_phases` toast appears, and nothing is written under `prompts/`.

## Self-Review (completed during planning)

- **Spec coverage:** Gate 1 → Task 5/6 (`force_fresh`); Gate 2 → Task 7 + Task 8 (`contract_override` in both judge calls); Gate 3 → Task 2 (`expand_phase_selection`) + Task 5 (`added_phases`) + Task 9 (toast); Gate 4 → Task 8 (`sha256` on `phase_outputs.prompt_hash`, joinable from `agent_usages.phase_output_id` — no `agent.py` change needed); storage (JSONB job+batch) → Task 1/4; batch mirror → Task 6; per-phase replace → Task 8; subset sequence → Task 8; FE → Task 9; batch-rollup subset-safety → verified in spec, no task. All spec sections map to a task or a verified no-op.
- **Placeholder scan:** none — every code step shows real code and exact commands.
- **Type consistency:** `_custom_for(phase_name: str, custom_prompts: Optional[dict]) -> Optional[str]` defined in Task 8 Step 3, called identically in Step 4 and the tests; the subset concept is `selected_phases` consistently across model, migration, schema, repo, endpoint, pipeline, and FE body — distinct from `JobOut.phases` (phase outputs) and the `added_phases` response field; `expand_phase_selection(subject, selected) -> (ordered, added)` defined in Task 2 and consumed identically in Tasks 5/6; `judge(contract_override=)` defined in Task 7 and called in Task 8.
- **Pre-flight corrections baked in:** (1) renamed the subset column/field to `selected_phases` to avoid colliding with `JobOut.phases` under `JobOut.model_validate(job)`; (2) `added_phases` returned via a real `JobOut` field (not a dict that the `-> JobOut` response model would strip).
- **Note on Gate 4 vs. spec:** the spec mentioned tagging `agent_usages` via `extra_envelope`; the plan instead relies on the existing `agent_usages.phase_output_id` → `phase_outputs.prompt_hash` FK join, which already distinguishes custom runs without threading a hash through `run_phase`'s recording paths. Lower-risk, same outcome.
