# Custom Prompt Upload Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user upload a `.md` file on the section page whose text is appended to every content phase prompt for that one generation run, stored on the job row but never written to `prompts/`.

**Architecture:** A new nullable `custom_prompt` column on `homework_jobs` carries the uploaded text from the generate request through to the background worker. The browser reads the `.md` inline (File API) and sends it in the existing generate body. The pipeline appends it (under a clear delimiter) to each content phase's built-in prompt; `extract` and the judge are untouched. Every hop mirrors the existing `transport` field's plumbing.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 (async), Alembic, Pydantic v2, pytest/pytest-asyncio; React + TypeScript + Vite (web/).

## Global Constraints

- **Never written to `prompts/`** — the custom prompt lives only on the job row and in-memory during the run.
- **Content phases only** — append to the non-`extract` branch of `_execute_phase`; never to `extract`, never to the judge call.
- **Length cap: 20 000 characters** — server returns HTTP 400 if exceeded.
- **Backwards compatible** — the field is optional everywhere; existing generate requests with the field absent behave exactly as today (NULL column, byte-identical prompts).
- **Stage only the files each task lists** — other sessions may be committing to the same branch. Never `git add -A`.
- **Mirror `transport`** — for every hop, the existing `transport` field a few lines away is the working template.
- Commit message footer (every commit):
  ```
  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  ```

---

### Task 1: DB column + model field

**Files:**
- Create: `alembic/versions/0027_add_custom_prompt.py`
- Modify: `app/models/homework_job.py` (add column after `judge_transport`, line ~30)
- Test: `tests/repositories/test_custom_prompt_column.py`

**Interfaces:**
- Produces: `HomeworkJob.custom_prompt: Optional[str]` — nullable TEXT column, default NULL.

- [ ] **Step 1: Write the failing test**

```python
# tests/repositories/test_custom_prompt_column.py
"""The custom_prompt column exists on homework_jobs and is nullable (no DB)."""
from app.models.homework_job import HomeworkJob


def test_homework_job_has_custom_prompt_column():
    col = HomeworkJob.__table__.columns["custom_prompt"]
    assert col.nullable is True
    # No server default — absence means NULL, not empty string.
    assert col.server_default is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/repositories/test_custom_prompt_column.py -q`
Expected: FAIL with `KeyError: 'custom_prompt'`

- [ ] **Step 3: Add the model column**

In `app/models/homework_job.py`, immediately after the `judge_transport` line (~30):

```python
    # Optional user-supplied markdown appended to every content phase prompt
    # for this job. NULL = none. Never written to prompts/ (see worklog).
    custom_prompt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
```

`Text` and `Optional` are already imported in this file (used by `error_message`, etc.). Confirm both imports are present; if not, add `from sqlalchemy import Text` / `from typing import Optional`.

- [ ] **Step 4: Write the migration**

```python
# alembic/versions/0027_add_custom_prompt.py
"""Add nullable custom_prompt to homework_jobs.

Carries user-uploaded per-job markdown (appended to content phase prompts).
Nullable, no server default — NULL means "no custom prompt". Backwards
compatible with all existing rows."""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c1d2e3f4a5b6"
down_revision: Union[str, Sequence[str], None] = "a8c7e6d5f4b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "homework_jobs",
        sa.Column("custom_prompt", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("homework_jobs", "custom_prompt")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run python -m pytest tests/repositories/test_custom_prompt_column.py -q`
Expected: PASS

- [ ] **Step 6: Verify the migration chain is linear**

Run: `uv run alembic heads`
Expected: a single head — `c1d2e3f4a5b6 (head)`. (If a real DB is configured, optionally `uv run alembic upgrade head` then `downgrade -1` to confirm both directions; not required for the gate.)

- [ ] **Step 7: Commit**

```bash
git add alembic/versions/0027_add_custom_prompt.py app/models/homework_job.py tests/repositories/test_custom_prompt_column.py
git commit -m "feat(db): add nullable custom_prompt column to homework_jobs"
```

---

### Task 2: GenerateRequest schema field

**Files:**
- Modify: `app/schemas/job.py` (`GenerateRequest`, line ~42-48)
- Test: `tests/services/test_custom_prompt_schema.py`

**Interfaces:**
- Produces: `GenerateRequest.custom_prompt: str | None = None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/services/test_custom_prompt_schema.py
from app.schemas.job import GenerateRequest


def test_custom_prompt_defaults_to_none():
    req = GenerateRequest()
    assert req.custom_prompt is None


def test_custom_prompt_round_trips():
    req = GenerateRequest(custom_prompt="Focus on vocabulary.")
    assert req.custom_prompt == "Focus on vocabulary."
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/services/test_custom_prompt_schema.py -q`
Expected: FAIL — `AttributeError: 'GenerateRequest' object has no attribute 'custom_prompt'`

- [ ] **Step 3: Add the field**

In `app/schemas/job.py`, inside `GenerateRequest`, after `judge_transport` (line ~48):

```python
    custom_prompt: str | None = None   # appended to every content phase prompt; not persisted to prompts/
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/services/test_custom_prompt_schema.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/schemas/job.py tests/services/test_custom_prompt_schema.py
git commit -m "feat(schema): add custom_prompt to GenerateRequest"
```

---

### Task 3: jobs_repo.create accepts custom_prompt

**Files:**
- Modify: `app/repositories/jobs.py` (`create`, lines 12-44)
- Test: `tests/repositories/test_custom_prompt_persist.py`

**Interfaces:**
- Consumes: `HomeworkJob.custom_prompt` (Task 1), `GenerateRequest.custom_prompt` (Task 2).
- Produces: `jobs_repo.create(..., custom_prompt: str | None = None) -> HomeworkJob` — sets the column when non-None.

- [ ] **Step 1: Write the failing test** (real-DB, behind the standard marker)

```python
# tests/repositories/test_custom_prompt_persist.py
"""jobs_repo.create persists custom_prompt (real DB)."""
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
async def test_create_persists_custom_prompt():
    from app.db import SessionLocal
    from app.repositories import jobs as jobs_repo
    book_id, sid = await _seed_book("C")
    try:
        async with SessionLocal() as s:
            job = await jobs_repo.create(
                s, book_id=book_id, toc_entry_id=sid,
                subject="math-algebra", custom_prompt="Add one worked example.",
            )
            await s.commit()
            assert job.custom_prompt == "Add one worked example."
    finally:
        await _cleanup(book_id)


@_DB
@pytest.mark.asyncio
async def test_create_without_custom_prompt_is_null():
    from app.db import SessionLocal
    from app.repositories import jobs as jobs_repo
    book_id, sid = await _seed_book("D")
    try:
        async with SessionLocal() as s:
            job = await jobs_repo.create(
                s, book_id=book_id, toc_entry_id=sid, subject="math-algebra",
            )
            await s.commit()
            assert job.custom_prompt is None
    finally:
        await _cleanup(book_id)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `RUN_DB_INTEGRATION=1 uv run python -m pytest tests/repositories/test_custom_prompt_persist.py -q`
Expected: FAIL — `TypeError: create() got an unexpected keyword argument 'custom_prompt'`
(If no DB is available, the tests skip — in that case verify failure by signature inspection instead; the real proof runs in the acceptance gate where a DB is up.)

- [ ] **Step 3: Add the param**

In `app/repositories/jobs.py`, add to the `create` signature after `judge_transport` (line 24):

```python
    custom_prompt: Optional[str] = None,
```

And in the `kwargs` dict, after the `if batch_id is not None:` block (after line 40), add:

```python
    if custom_prompt is not None:
        kwargs["custom_prompt"] = custom_prompt
```

- [ ] **Step 4: Run test to verify it passes**

Run: `RUN_DB_INTEGRATION=1 uv run python -m pytest tests/repositories/test_custom_prompt_persist.py -q`
Expected: PASS (or SKIP if no DB — confirm in acceptance gate)

- [ ] **Step 5: Commit**

```bash
git add app/repositories/jobs.py tests/repositories/test_custom_prompt_persist.py
git commit -m "feat(repo): jobs_repo.create accepts custom_prompt"
```

---

### Task 4: Generate endpoint — length cap + pass-through

**Files:**
- Modify: `app/api/v1/jobs.py` (`generate`, length check after role validation ~line 144; pass to `create` ~line 189)
- Test: `tests/api/test_custom_prompt_endpoint.py`

**Interfaces:**
- Consumes: `GenerateRequest.custom_prompt` (Task 2), `jobs_repo.create(custom_prompt=...)` (Task 3).
- Produces: 400 when `len(custom_prompt) > 20_000`; otherwise threads the value into `jobs_repo.create`.

- [ ] **Step 1: Write the failing test** (no-DB rejection — mirrors `test_transport_validation._ready_book_patch`)

```python
# tests/api/test_custom_prompt_endpoint.py
"""custom_prompt length cap is enforced before any DB write (no real DB)."""
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


@pytest.mark.asyncio
async def test_oversize_custom_prompt_rejected(monkeypatch):
    _ready_book_patch(monkeypatch)
    async with _client() as c:
        r = await c.post(
            f"/api/v1/books/{_BOOK_ID}/sections/{_SECTION_ID}/generate",
            headers=_HDR,
            json={"provider": "claude", "custom_prompt": "x" * 20_001},
        )
    assert r.status_code == 400, r.text
    assert "custom_prompt" in r.json()["detail"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/api/test_custom_prompt_endpoint.py -q`
Expected: FAIL — the request proceeds past validation (no 400 / wrong status) because no length check exists yet.

- [ ] **Step 3: Add the length check**

In `app/api/v1/jobs.py`, immediately after the role-transport validation loop (after line 144, before the advisory-lock comment at line 146):

```python
    if body.custom_prompt is not None and len(body.custom_prompt) > 20_000:
        raise HTTPException(
            400,
            f"custom_prompt too long ({len(body.custom_prompt)} chars; max 20000).",
        )
```

- [ ] **Step 4: Thread the value into create**

In the `jobs_repo.create(...)` call (lines 179-190), add after `judge_transport=body.judge_transport,`:

```python
        custom_prompt=body.custom_prompt,
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run python -m pytest tests/api/test_custom_prompt_endpoint.py -q`
Expected: PASS

- [ ] **Step 6: Run the existing endpoint suite (regression)**

Run: `uv run python -m pytest tests/api/test_transport_validation.py -q`
Expected: PASS (no behavior change for the existing fields)

- [ ] **Step 7: Commit**

```bash
git add app/api/v1/jobs.py tests/api/test_custom_prompt_endpoint.py
git commit -m "feat(api): enforce custom_prompt length cap and persist on generate"
```

---

### Task 5: Pipeline — append custom_prompt to content phases

**Files:**
- Modify: `app/services/pipeline.py`
  - Add module-level helper `_append_custom_prompt` (near other small helpers, e.g. after `_strip_svgs`/top of file region — place it just above `_execute_phase`, ~line 568)
  - `run()`: read `job.custom_prompt` (~line 81 area) and pass into both call sites (head `_execute_one_phase` ~line 157 and `_run_content_phases_parallel` ~line 207)
  - `_execute_one_phase` (~line 283): add `custom_prompt` param, pass to `_execute_phase`
  - `_run_content_phases_parallel` (~line 378): add `custom_prompt` param, pass to `_execute_one_phase`
  - `_execute_phase` (~line 569): add `custom_prompt` param; in the non-extract branch (line ~702) wrap `get_prompt(...)` with the helper
- Test: `tests/services/test_custom_prompt_append.py`

**Interfaces:**
- Consumes: `HomeworkJob.custom_prompt` (Task 1).
- Produces: `_append_custom_prompt(base_phase_prompt: str, custom_prompt: Optional[str]) -> str`.

- [ ] **Step 1: Write the failing test** (pure helper + source-inspection guards — no DB)

```python
# tests/services/test_custom_prompt_append.py
import inspect

from app.services import pipeline
from app.services.pipeline import _append_custom_prompt


def test_append_adds_delimited_block():
    out = _append_custom_prompt("BASE PROMPT", "Add one worked example.")
    assert out.startswith("BASE PROMPT")
    assert "## Additional instructions (user-provided)" in out
    assert out.rstrip().endswith("Add one worked example.")


def test_append_noop_when_none():
    assert _append_custom_prompt("BASE", None) == "BASE"


def test_append_noop_when_blank():
    assert _append_custom_prompt("BASE", "   \n  ") == "BASE"


def test_execute_phase_appends_only_on_content_branch():
    src = inspect.getsource(pipeline._execute_phase)
    # The helper is applied where the content-phase prompt is built...
    assert "_append_custom_prompt(get_prompt(" in src
    # ...and the extract branch (prompt_hash builtin) never calls the helper:
    # the only _append_custom_prompt call sits in the else/content branch.
    assert src.count("_append_custom_prompt") == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/services/test_custom_prompt_append.py -q`
Expected: FAIL — `ImportError: cannot import name '_append_custom_prompt'`

- [ ] **Step 3: Add the helper**

In `app/services/pipeline.py`, just above `async def _execute_phase(` (~line 569):

```python
def _append_custom_prompt(base_phase_prompt: str, custom_prompt: Optional[str]) -> str:
    """Append user-supplied markdown to a content phase prompt under a clear
    delimiter heading, so the model treats it as supplementary guidance rather
    than blending it into the built-in policy. No-op for None / blank. Never
    called on the extract path (extract is a flat factual summary that every
    other phase depends on)."""
    if not custom_prompt or not custom_prompt.strip():
        return base_phase_prompt
    return (
        base_phase_prompt
        + "\n\n## Additional instructions (user-provided)\n"
        + custom_prompt.strip()
    )
```

- [ ] **Step 4: Wire the helper into the content branch**

In `_execute_phase`, the signature gains a param (add after `judge_transport: str = "cli",`, ~line 586):

```python
    custom_prompt: Optional[str] = None,
```

In the `else` (non-extract) branch, replace line 702:

```python
            base_phase_prompt = get_prompt(subject, phase_name)
```

with:

```python
            base_phase_prompt = _append_custom_prompt(
                get_prompt(subject, phase_name), custom_prompt
            )
```

(The extract branch starting at line 588 is untouched — it never builds `base_phase_prompt`.)

- [ ] **Step 5: Thread the param through the two intermediate callers**

In `_execute_one_phase` (signature ~line 304), add after `judge_transport: str = "cli",`:

```python
    custom_prompt: Optional[str] = None,
```

In its call to `_execute_phase` (~line 339), add after `judge_transport=judge_transport,`:

```python
            custom_prompt=custom_prompt,
```

In `_run_content_phases_parallel` (signature ~line 398), add after `judge_transport: str = "cli",`:

```python
    custom_prompt: Optional[str] = None,
```

In its `_execute_one_phase(...)` call (~line 451), add after `judge_transport=judge_transport,`:

```python
                            custom_prompt=custom_prompt,
```

- [ ] **Step 6: Read the job field in `run()` and pass to both call sites**

In `run()`, after the `judge_transport = resolve_role_transport(...)` block (~line 90), add:

```python
            custom_prompt = getattr(job, "custom_prompt", None)
```

In the head `_execute_one_phase(...)` call (~line 175, after `judge_transport=judge_transport,`):

```python
                    custom_prompt=custom_prompt,
```

In the `_run_content_phases_parallel(...)` call (~line 226, after `judge_transport=judge_transport,`):

```python
                    custom_prompt=custom_prompt,
```

- [ ] **Step 7: Run test to verify it passes**

Run: `uv run python -m pytest tests/services/test_custom_prompt_append.py -q`
Expected: PASS

- [ ] **Step 8: Run the pipeline regression suite**

Run: `uv run python -m pytest tests/services/test_execute_phase_judge.py tests/services/test_general_flow.py -q`
Expected: PASS (threading is additive; default None preserves today's behavior)

- [ ] **Step 9: Commit**

```bash
git add app/services/pipeline.py tests/services/test_custom_prompt_append.py
git commit -m "feat(pipeline): append job custom_prompt to content phase prompts"
```

---

### Task 6: Frontend — upload card + request wiring

**Files:**
- Modify: `web/src/lib/api.ts` (`generate` opts + body, lines 147-187)
- Modify: `web/src/routes/section.tsx` (state, file handler, upload card, pass into `api.generate`)
- Test: typecheck (`npx tsc -p tsconfig.app.json --noEmit`)

**Interfaces:**
- Consumes: the generate endpoint's new `custom_prompt` body field (Task 4).
- Produces: a "Custom prompt (optional)" card; `api.generate(..., { custom_prompt })`.

- [ ] **Step 1: Add `custom_prompt` to the api client**

In `web/src/lib/api.ts`, in the `generate` opts type (after `judge_transport?: RoleTransport;`, line 157):

```typescript
      custom_prompt?: string | null;
```

In the destructure defaults (after `judge_transport = "inherit",`, line 167):

```typescript
      custom_prompt = null,
```

In the JSON body (after `judge_transport,`, line 182):

```typescript
          custom_prompt,
```

- [ ] **Step 2: Add upload state + handler to the section page**

In `web/src/routes/section.tsx`, inside `SectionPage` after the existing `useState` hooks (~line 39), add:

```tsx
  const [customPrompt, setCustomPrompt] = useState<{ name: string; text: string } | null>(
    null,
  );

  function handleCustomPromptFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = ""; // allow re-selecting the same filename
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      const text = String(reader.result ?? "");
      if (!text.trim()) {
        setCustomPrompt(null);
        toast.error("That file is empty");
        return;
      }
      setCustomPrompt({ name: file.name, text });
    };
    reader.onerror = () => toast.error("Couldn't read that file");
    reader.readAsText(file);
  }
```

- [ ] **Step 3: Send the prompt in `handleGenerate`**

In `handleGenerate`'s `api.generate(...)` call (~line 85-93), add after `judge_transport: judgeTransport,`:

```tsx
        custom_prompt: customPrompt?.text ?? null,
```

- [ ] **Step 4: Render the upload card**

In the JSX, between `<AgentPicker ... />` and `<ActionPanel ... />` (~line 187), insert:

```tsx
        {/* Optional custom prompt — read in-browser, appended to every content
            phase for this run. Never uploaded to the server as a file; never
            written to prompts/. */}
        <section className={CARD}>
          <p className="font-mono text-[0.7rem] uppercase tracking-[0.16em] text-white/45">
            Custom prompt
          </p>
          <h2 className="mt-1 text-base font-semibold tracking-tight text-white">
            Add your own instructions (optional)
          </h2>
          <p className="mt-1.5 text-sm leading-relaxed text-white/55">
            Upload a <span className="font-mono">.md</span> file. Its text is appended to every
            content phase for this run only — it is not saved to the prompt library.
          </p>

          {customPrompt ? (
            <div className="mt-4 flex items-center justify-between gap-3 rounded-xl border border-white/[0.12] bg-white/[0.05] px-3 py-2.5">
              <span className="truncate font-mono text-sm text-white/80">
                {customPrompt.name}
              </span>
              <button
                type="button"
                onClick={() => setCustomPrompt(null)}
                className="shrink-0 text-sm font-medium text-white/55 transition-colors hover:text-white"
              >
                Remove
              </button>
            </div>
          ) : (
            <label className={cn(GLASS_BTN, "mt-4 cursor-pointer")}>
              Upload .md file
              <input
                type="file"
                accept=".md,.markdown,text/markdown"
                onChange={handleCustomPromptFile}
                className="hidden"
              />
            </label>
          )}
        </section>
```

(`CARD`, `GLASS_BTN`, `cn`, and `toast` are already imported in this file.)

- [ ] **Step 5: Typecheck**

Run: `cd web && npx tsc -p tsconfig.app.json --noEmit`
Expected: no errors.

- [ ] **Step 6: Build (catch anything tsc misses)**

Run: `cd web && npm run build`
Expected: build succeeds, writes `web/dist/`.

- [ ] **Step 7: Commit**

```bash
git add web/src/lib/api.ts web/src/routes/section.tsx
git commit -m "feat(web): custom prompt .md upload on the section page"
```

---

## Acceptance Gate (controller runs after all tasks)

Per CLAUDE.md §4 — anything affecting generation needs a real CLI smoke. After the suite is green:

- [ ] Full backend suite: `uv run python -m pytest tests/ -q` (DB-integration tests need `RUN_DB_INTEGRATION=1` + `DATABASE_URL`).
- [ ] **Real generate smoke:** create a job for a section with a short `custom_prompt` (e.g. `"Always include one extra worked example labelled EXTRA."`) via a real `gemini` or `claude` run. Confirm:
  1. a content phase output visibly reflects the instruction, and
  2. the `extract` phase output is unaffected (no appended block — it should read as a plain factual summary).
- [ ] Spot-check the rendered section page in the browser: upload a `.md`, see the filename chip + Remove, generate, and confirm no file was written under `prompts/`.

## Self-Review (completed during planning)

- **Spec coverage:** Decision 1 (append) → Task 5; Decision 2 (job-row persistence) → Tasks 1, 3, 4; Decision 3 (inline browser read) → Task 6; Decision 4 (content phases only, skip extract+judge) → Task 5 (helper on the non-extract branch only, judge call untouched); length cap → Task 4; FE card → Task 6; idempotency interaction → no code (documented in spec, surfaced via existing Regenerate affordance). All spec sections map to a task.
- **Placeholder scan:** none — every code step shows real code and exact commands.
- **Type consistency:** `_append_custom_prompt(base_phase_prompt: str, custom_prompt: Optional[str]) -> str` is defined in Task 5 Step 3 and called identically in Step 4 and the tests; `custom_prompt` param name is consistent across `run()`, `_execute_one_phase`, `_run_content_phases_parallel`, `_execute_phase`, `jobs_repo.create`, `GenerateRequest`, and the FE `api.generate` opts.
