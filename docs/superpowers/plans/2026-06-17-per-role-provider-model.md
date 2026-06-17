# Per-Role Provider/Model Selection + API-Default Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user choose provider + model + transport independently for the generator, extract, and judge roles, and default the generator transport to API.

**Architecture:** Extend the existing Phase-4.1 flat-column pattern — add nullable `extract_provider/model` and `judge_provider/model` to `homework_jobs` and `batches` (NULL = today's smart default). Pipeline resolves each role's provider/model once at job start (mirroring how it already resolves per-role transport). The judge gains a hard self-grade guard and a soft weaker-judge advisory. The cross-job extract-reuse lookup gains provider/model filters (a bug this feature activates). The two launch UIs pre-select API for the generator and CLI for the roles.

**Tech Stack:** FastAPI, SQLAlchemy + Alembic, Pydantic, React + TypeScript (Vite), pytest.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-06-17-per-role-provider-model-design.md`.
- Backend `server_default` for every transport column stays `"cli"`. Do NOT change it.
- The generator's provider/model pin across content phases is unchanged — only `extract` and `judge` read their own override.
- `transport=api` requires an api-supported provider (`claude`/`gemini`) AND an explicit model — enforced per role via the existing `validate_transport`.
- Stage ONLY the files each task lists. Never `git add -A` (other sessions commit to this branch, esp. `web/`).
- All commands run from repo root. Backend tests: `uv run python -m pytest`. FE typecheck: `cd web && npx tsc -p tsconfig.app.json --noEmit`.
- Branch: `feat/per-role-provider-model` (already cut off `origin/Nggaev-v2`; spec committed at `4060097`).

---

### Task 1: Migration + ORM columns (8 nullable columns, both tables)

**Files:**
- Modify: `app/models/homework_job.py:28-30` (after `judge_transport`)
- Modify: `app/models/batch.py:26` (after `transport`)
- Create: `alembic/versions/0027_per_role_provider_model.py` (revision id auto; see step)
- Test: `tests/test_migrations_per_role.py` (new)

**Interfaces:**
- Produces: `HomeworkJob.extract_provider/extract_model/judge_provider/judge_model` and the same four on `Batch`, all `Mapped[Optional[str]]`, nullable, no server_default. NULL = "fall back to settings/auto".

- [ ] **Step 1: Add columns to the two ORM models**

In `app/models/homework_job.py`, immediately after line 30 (`judge_transport`):

```python
    # Per-role provider/model overrides. NULL = fall back to the role default
    # (extract -> settings.extract_provider/model; judge -> model_tiers auto).
    extract_provider: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    extract_model: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    judge_provider: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    judge_model: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
```

In `app/models/batch.py`, immediately after line 26 (`transport`):

```python
    # Per-role provider/model launch-default labels (mirror homework_jobs).
    extract_provider: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    extract_model: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    judge_provider: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    judge_model: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
```

Confirm `Optional` is imported in both files (it is — `model` column already uses it).

- [ ] **Step 2: Generate the migration skeleton**

Run: `uv run alembic revision -m "per-role provider/model columns"`
Expected: prints a new file path under `alembic/versions/`. Rename it to `0027_per_role_provider_model.py` and set `down_revision` to the current head (find with `uv run alembic heads`).

- [ ] **Step 3: Fill in the migration body**

```python
"""per-role provider/model columns

Revision ID: 0027_per_role_provider_model
"""
from alembic import op
import sqlalchemy as sa

revision = "0027_per_role_provider_model"
down_revision = "<CURRENT_HEAD>"  # from `uv run alembic heads`
branch_labels = None
depends_on = None

_COLS = ("extract_provider", "extract_model", "judge_provider", "judge_model")
_LEN = {"extract_provider": 32, "extract_model": 128, "judge_provider": 32, "judge_model": 128}


def upgrade() -> None:
    for table in ("homework_jobs", "batches"):
        for col in _COLS:
            op.add_column(table, sa.Column(col, sa.String(_LEN[col]), nullable=True))


def downgrade() -> None:
    for table in ("homework_jobs", "batches"):
        for col in _COLS:
            op.drop_column(table, col)
```

- [ ] **Step 4: Write the migration roundtrip + default-NULL test**

`tests/test_migrations_per_role.py`:

```python
from app.models import HomeworkJob, Batch


def test_new_columns_present_and_default_null():
    # ORM-level: a freshly constructed row leaves the overrides None.
    job = HomeworkJob(book_id=None, toc_entry_id=None, subject="biology", status="pending")
    for attr in ("extract_provider", "extract_model", "judge_provider", "judge_model"):
        assert getattr(job, attr) is None
    batch = Batch(book_id=None, subject="biology", provider="gemini", transport="cli")
    for attr in ("extract_provider", "extract_model", "judge_provider", "judge_model"):
        assert getattr(batch, attr) is None
```

- [ ] **Step 5: Run the test + apply the migration locally**

Run: `uv run python -m pytest tests/test_migrations_per_role.py -q`
Expected: PASS.
Run: `uv run alembic upgrade head` (against the local dev DB) then `uv run alembic downgrade -1` then `uv run alembic upgrade head`.
Expected: all three succeed (roundtrip clean).

- [ ] **Step 6: Commit**

```bash
git add app/models/homework_job.py app/models/batch.py alembic/versions/0027_per_role_provider_model.py tests/test_migrations_per_role.py
git commit -m "feat(db): per-role provider/model columns on jobs+batches"
```

---

### Task 2: Repos accept + persist the role provider/model

**Files:**
- Modify: `app/repositories/jobs.py:12-44` (`create`)
- Modify: `app/repositories/batches.py:12-` (`get_or_create_for_book`)
- Test: `tests/repositories/test_role_provider_persist.py` (new; real-DB, guarded by `RUN_DB_INTEGRATION`)

**Interfaces:**
- Consumes: Task 1 columns.
- Produces: `jobs_repo.create(..., extract_provider=None, extract_model=None, judge_provider=None, judge_model=None)` and `batches_repo.get_or_create_for_book(..., extract_provider=None, extract_model=None, judge_provider=None, judge_model=None)`. All default `None`; `None` is persisted as NULL.

- [ ] **Step 1: Extend `jobs_repo.create`**

In `app/repositories/jobs.py`, add four params to the signature (after `judge_transport`):

```python
    judge_transport: str = "inherit",
    extract_provider: Optional[str] = None,
    extract_model: Optional[str] = None,
    judge_provider: Optional[str] = None,
    judge_model: Optional[str] = None,
) -> HomeworkJob:
```

Inside, after the `if model is not None:` block, persist the role overrides only when set (keep NULL otherwise — same idiom as `provider`/`model`):

```python
    for _k, _v in (
        ("extract_provider", extract_provider),
        ("extract_model", extract_model),
        ("judge_provider", judge_provider),
        ("judge_model", judge_model),
    ):
        if _v is not None:
            kwargs[_k] = _v
```

Place this immediately before `job = HomeworkJob(**kwargs)`.

- [ ] **Step 2: Extend `batches_repo.get_or_create_for_book`**

Add the same four params (after `judge_transport`) to the signature, and add them to the `.values(...)` of the `pg_insert(Batch)` statement. Read the existing `.values(...)` block first; insert the four keys alongside `transport`/`extract_transport`. They are NOT part of the conflict target (`book_id, transport` stays the unique key) and are only written on insert (existing batch keeps its launch-default label).

- [ ] **Step 3: Write the persistence test**

`tests/repositories/test_role_provider_persist.py` (mirror an existing `RUN_DB_INTEGRATION`-guarded repo test for the session fixture):

```python
import os
import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION") != "1", reason="needs a real DB"
)

# ... use the project's standard async session fixture ...

async def test_create_persists_role_overrides(session, sample_book, sample_toc):
    from app.repositories import jobs as jobs_repo
    job = await jobs_repo.create(
        session, book_id=sample_book.id, toc_entry_id=sample_toc.id,
        subject="biology", provider="claude", model="claude-opus-4-7",
        extract_provider="gemini", extract_model="gemini-2.5-flash",
        judge_provider=None, judge_model=None,
    )
    await session.flush()
    assert job.extract_provider == "gemini"
    assert job.extract_model == "gemini-2.5-flash"
    assert job.judge_provider is None and job.judge_model is None
```

- [ ] **Step 4: Run**

Run: `RUN_DB_INTEGRATION=1 DATABASE_URL=$DATABASE_URL uv run python -m pytest tests/repositories/test_role_provider_persist.py -q`
Expected: PASS (or SKIP if no DB — then run the full non-DB suite to confirm no import/signature regressions: `uv run python -m pytest tests/ -q`).

- [ ] **Step 5: Commit**

```bash
git add app/repositories/jobs.py app/repositories/batches.py tests/repositories/test_role_provider_persist.py
git commit -m "feat(repo): persist per-role provider/model on create"
```

---

### Task 3: API schemas + endpoints (accept, validate per role, pass through)

**Files:**
- Modify: `app/schemas/job.py:23-48` (`JobOut`, `GenerateRequest`)
- Modify: `app/api/v1/jobs.py:120-189` (validate + create)
- Modify: `app/api/v1/batch.py:24-129` (LaunchBody + validate + get_or_create)
- Test: `tests/api/test_role_validation.py` (new)

**Interfaces:**
- Consumes: Task 2 repo params; `validate_transport` (`agent_models.py:69`), `is_valid` (`agent_models.MODEL_MANIFEST`).
- Produces: request bodies carry `extract_provider/extract_model/judge_provider/judge_model: Optional[str] = None`; `JobOut`/`BatchOut` echo them; both endpoints validate each role and pass the values to the repo.

- [ ] **Step 1: Extend the request/response schemas**

In `app/schemas/job.py`, add to `GenerateRequest` (after `judge_transport`):

```python
    extract_provider: str | None = None   # None ⇒ settings.extract_provider
    extract_model: str | None = None
    judge_provider: str | None = None      # None ⇒ model_tiers auto-tier
    judge_model: str | None = None
```

Add the same four (`Optional[str] = None`) to `JobOut`. Do the same for the batch `LaunchBody` and the batch-out dict at `app/api/v1/batch.py:24-43` and `42-43`.

- [ ] **Step 2: Write the per-role validation test (failing)**

`tests/api/test_role_validation.py` — unit-test a small helper rather than spin the whole app:

```python
from app.services.agent_models import validate_transport


def test_api_role_requires_explicit_model():
    # judge role on api with no model is invalid.
    assert validate_transport("claude", None, "api") is not None
    # gemini role on api with a real model is valid.
    assert validate_transport("gemini", "gemini-2.5-flash", "api") is None
    # a non-api provider on api is invalid.
    assert validate_transport("kimi", "k2", "api") is not None
```

Run: `uv run python -m pytest tests/api/test_role_validation.py -q` → PASS (this asserts the existing validator behaves; the endpoint wiring below relies on it).

- [ ] **Step 3: Validate each role in the generate endpoint**

In `app/api/v1/jobs.py`, after the existing job-level `validate_transport` block (line ~135) and the role-transport loop (line ~139-145), add per-role provider/model validation. A role is validated only when its provider is explicitly set; the effective transport for the role is the resolved one:

```python
from app.services.agent_models import resolve_role_transport
from app.services.agent_models import is_valid  # already imported at top

# Per-role provider/model: validate only explicit picks. The role's effective
# transport decides whether an explicit model is mandatory.
for role, prov, mdl, role_tx in (
    ("extract", body.extract_provider, body.extract_model, body.extract_transport),
    ("judge", body.judge_provider, body.judge_model, body.judge_transport),
):
    if prov is None:
        continue  # Auto -> role default, nothing to validate
    if not is_valid(prov, mdl):
        raise HTTPException(400, f"{role}: unknown (provider, model) ({prov!r}, {mdl!r})")
    eff_tx = resolve_role_transport(role_tx, body.transport)
    err = validate_transport(prov, mdl, eff_tx)
    if err is not None:
        raise HTTPException(400, f"{role}: {err}")
```

Then pass the four fields into `jobs_repo.create(...)` (extend the existing call at line ~181-189):

```python
        extract_provider=body.extract_provider,
        extract_model=body.extract_model,
        judge_provider=body.judge_provider,
        judge_model=body.judge_model,
```

- [ ] **Step 4: Mirror the validation + pass-through in the batch endpoint**

In `app/api/v1/batch.py`, add the identical per-role validation loop after its existing transport validation, and pass the four fields into both `get_or_create_for_book(...)` (line ~99-100, 128-129) call sites.

- [ ] **Step 5: Run the full suite**

Run: `uv run python -m pytest tests/ -q`
Expected: PASS (no regressions; existing API tests still green).

- [ ] **Step 6: Commit**

```bash
git add app/schemas/job.py app/api/v1/jobs.py app/api/v1/batch.py tests/api/test_role_validation.py
git commit -m "feat(api): per-role provider/model fields + per-role validation"
```

---

### Task 4: Pipeline extract resolution (override → settings fallback)

**Files:**
- Modify: `app/services/pipeline.py:86-91` (role resolution block) and `:599-706` (extract phase) — line numbers per current main (`188c83c`, post-PR#18); anchor on the code strings below, not the numbers
- Test: `tests/services/test_extract_resolution.py` (new)

**Interfaces:**
- Consumes: `job.extract_provider/extract_model`; `settings.extract_provider/extract_model`.
- Produces: a pure helper `pipeline._resolve_extract(job_extract_provider, job_extract_model) -> tuple[str, str]` returning `(provider, model)` with the settings fallback. Used to pin the extract phase.

- [ ] **Step 1: Write the failing resolution test**

`tests/services/test_extract_resolution.py`:

```python
from app.services.pipeline import _resolve_extract
from app.config import settings


def test_resolve_extract_explicit_override():
    assert _resolve_extract("claude", "claude-opus-4-7") == ("claude", "claude-opus-4-7")


def test_resolve_extract_falls_back_to_settings():
    assert _resolve_extract(None, None) == (settings.extract_provider, settings.extract_model)


def test_resolve_extract_partial_override_uses_settings_for_missing():
    # provider given, model missing -> settings model
    assert _resolve_extract("gemini", None) == ("gemini", settings.extract_model)
```

Run: `uv run python -m pytest tests/services/test_extract_resolution.py -q`
Expected: FAIL (`_resolve_extract` not defined).

- [ ] **Step 2: Add the helper**

In `app/services/pipeline.py` near the top-level helpers:

```python
def _resolve_extract(job_extract_provider, job_extract_model):
    """Extract role provider/model: explicit job override, else settings."""
    return (
        job_extract_provider or settings.extract_provider,
        job_extract_model or settings.extract_model,
    )
```

- [ ] **Step 3: Resolve once at job start + use in the extract phase**

In the role-resolution block (`pipeline.py:86-91`, alongside `extract_transport = resolve_role_transport(...)` / `judge_transport = ...`), add:

```python
            extract_provider, extract_model = _resolve_extract(
                getattr(job, "extract_provider", None),
                getattr(job, "extract_model", None),
            )
```

Thread `extract_provider`/`extract_model` into `_execute_one_phase` (add two params; note `_execute_one_phase` already takes `extract_transport`/`judge_transport` at `pipeline.py:305-306`) and, in the extract branch (`pipeline.py:599-706`), replace the two `settings.extract_*` references:
- line ~608 `phase_model_label = settings.extract_model` → `phase_model_label = extract_model`
- line ~705-706 `requested_provider=settings.extract_provider, model=settings.extract_model` → `requested_provider=extract_provider, model=extract_model`

(The provider/model pin still holds across all content phases — they continue to use `provider`/`model`.)

- [ ] **Step 4: Run + full suite**

Run: `uv run python -m pytest tests/services/test_extract_resolution.py tests/ -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/pipeline.py tests/services/test_extract_resolution.py
git commit -m "feat(pipeline): resolve extract provider/model per-job (settings fallback)"
```

---

### Task 5: Cross-job extract-reuse correctness (provider/model filter)

**Files:**
- Modify: `app/repositories/phase_outputs.py:139-166` (`find_latest_extract`)
- Modify: `app/services/pipeline.py:646` (the `find_latest_extract(...)` call — pass the extract provider/model)
- Test: `tests/repositories/test_extract_reuse_key.py` (new)

**Interfaces:**
- Consumes: Task 4's resolved `extract_provider`/`extract_model`; `PhaseOutput.provider` (`:24`), `PhaseOutput.model_name` (`:21`).
- Produces: `find_latest_extract(session, *, toc_entry_id, prompt_hash, provider, model)` — adds two WHERE filters. A pre-existing row with `provider IS NULL` does NOT match (safe miss → fresh extract).

- [ ] **Step 1: Write the failing reuse-key test (real-DB guarded)**

`tests/repositories/test_extract_reuse_key.py`:

```python
import os
import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION") != "1", reason="needs a real DB"
)

async def test_reuse_matches_only_same_provider_model(session, make_extract_row):
    # make_extract_row: helper that inserts a done extract phase_output for a
    # given (toc_entry_id, prompt_hash, provider, model_name).
    toc, ph = ..., "builtin:extract:v2"
    await make_extract_row(toc, ph, provider="gemini", model_name="gemini-2.5-flash")
    from app.repositories import phase_outputs as repo
    # same provider+model -> hit
    hit = await repo.find_latest_extract(session, toc_entry_id=toc, prompt_hash=ph,
                                         provider="gemini", model="gemini-2.5-flash")
    assert hit is not None
    # different provider -> miss (would otherwise serve a gemini extract to claude)
    miss = await repo.find_latest_extract(session, toc_entry_id=toc, prompt_hash=ph,
                                          provider="claude", model="claude-opus-4-7")
    assert miss is None

async def test_legacy_null_provider_row_is_a_safe_miss(session, make_extract_row):
    toc, ph = ..., "builtin:extract:v2"
    await make_extract_row(toc, ph, provider=None, model_name="gemini-2.5-flash")
    from app.repositories import phase_outputs as repo
    assert await repo.find_latest_extract(session, toc_entry_id=toc, prompt_hash=ph,
                                          provider="gemini", model="gemini-2.5-flash") is None
```

Run: `RUN_DB_INTEGRATION=1 ... uv run python -m pytest tests/repositories/test_extract_reuse_key.py -q`
Expected: FAIL (signature mismatch — function doesn't accept provider/model yet).

- [ ] **Step 2: Add the filters**

In `app/repositories/phase_outputs.py`, change the signature and WHERE clause:

```python
async def find_latest_extract(
    session: AsyncSession,
    *,
    toc_entry_id: UUID,
    prompt_hash: str,
    provider: str,
    model: str,
) -> Optional[PhaseOutput]:
    ...
        .where(
            PhaseOutput.phase_name == "extract",
            PhaseOutput.status == "done",
            PhaseOutput.prompt_hash == prompt_hash,
            PhaseOutput.output_md.is_not(None),
            PhaseOutput.provider == provider,
            PhaseOutput.model_name == model,
            HomeworkJob.toc_entry_id == toc_entry_id,
        )
```

Update the docstring: reuse now requires same `(toc_entry_id, prompt_hash, provider, model)`; a legacy `provider IS NULL` row is a safe miss. Transport is deliberately NOT in the key (auth mode does not change output).

- [ ] **Step 3: Pass the resolved extract provider/model at the call site**

In `app/services/pipeline.py:646`, update the `find_latest_extract(...)` call to pass `provider=extract_provider, model=extract_model` (the values resolved in Task 4).

- [ ] **Step 4: Run**

Run: `RUN_DB_INTEGRATION=1 ... uv run python -m pytest tests/repositories/test_extract_reuse_key.py -q` → PASS.
Run: `uv run python -m pytest tests/ -q` → PASS (no regressions).

- [ ] **Step 5: Commit**

```bash
git add app/repositories/phase_outputs.py app/services/pipeline.py tests/repositories/test_extract_reuse_key.py
git commit -m "fix(reuse): key cross-job extract reuse on provider+model"
```

---

### Task 6: Judge resolution + guard (explicit override, hard self-grade, soft weaker-judge)

**Files:**
- Modify: `app/services/model_tiers.py` (add `resolve_judge`, `judge_advisory`)
- Modify: `app/services/phase_judge.py:128-147` (`judge` accepts resolved judge provider/model)
- Modify: `app/services/pipeline.py:86-91, 760-766, 791-` (resolve + thread) — current-main line numbers; anchor on `phase_judge.judge(` calls
- Test: `tests/services/test_judge_resolution.py` (new)

**Interfaces:**
- Consumes: `model_tiers.judge_model_for`, `_SELF_FALLBACK` (`:55`), `tier_of` (`:58`), `default_model`.
- Produces:
  - `model_tiers.resolve_judge(gen_provider, gen_model, judge_provider, judge_model) -> tuple[str, str]` — explicit override wins, EXCEPT exact self-grade → `_SELF_FALLBACK`; NULL override → `judge_model_for(...)`.
  - `model_tiers.judge_advisory(gen_provider, gen_model, judge_provider, judge_model) -> Optional[str]` — returns a warning string when the resolved judge tier is weaker than the generator, else None.
  - `phase_judge.judge(..., judge_provider: str, judge_model: Optional[str], ...)` — uses the passed values directly (no internal `judge_model_for`).

- [ ] **Step 1: Write the failing resolution + advisory tests**

`tests/services/test_judge_resolution.py`:

```python
from app.services import model_tiers as mt


def test_explicit_judge_wins():
    assert mt.resolve_judge("gemini", "gemini-2.5-flash", "claude", "claude-opus-4-7") \
        == ("claude", "claude-opus-4-7")


def test_null_override_uses_auto_tier():
    assert mt.resolve_judge("gemini", "gemini-2.5-flash", None, None) \
        == mt.judge_model_for("gemini", "gemini-2.5-flash")


def test_exact_self_grade_is_hard_swapped():
    # judge == generator -> never self-grade, swap to the non-self peer.
    assert mt.resolve_judge("gemini", "gemini-3.1-pro-preview",
                            "gemini", "gemini-3.1-pro-preview") == mt._SELF_FALLBACK


def test_advisory_warns_when_judge_weaker():
    # a tier-2 judge grading a tier-1 generator -> advisory string.
    weak = mt.judge_advisory("claude", "claude-opus-4-7", "gemini", "gemini-2.5-flash")
    assert weak is not None
    # a stronger-or-equal judge -> no advisory.
    assert mt.judge_advisory("gemini", "gemini-2.5-flash", "claude", "claude-opus-4-7") is None
```

Run: `uv run python -m pytest tests/services/test_judge_resolution.py -q`
Expected: FAIL (functions not defined). (Adjust the concrete model ids to ones present in `_MODEL_TIER`; read `model_tiers.py:21-55` first and pick a tier-1 and tier-2 example.)

- [ ] **Step 2: Implement the two functions**

In `app/services/model_tiers.py`:

```python
def resolve_judge(gen_provider, gen_model, judge_provider, judge_model):
    """Effective judge (provider, model). Explicit override wins, EXCEPT an
    exact self-grade (judge == generator) which is hard-swapped to a non-self
    peer. A NULL override falls back to the auto-tier judge."""
    if judge_provider is None:
        return judge_model_for(gen_provider, gen_model)
    resolved_gen = (gen_provider, gen_model or default_model(gen_provider))
    if (judge_provider, judge_model) == resolved_gen:
        return _SELF_FALLBACK
    return (judge_provider, judge_model)


def judge_advisory(gen_provider, gen_model, judge_provider, judge_model):
    """Non-blocking advisory: warn when the resolved judge is weaker (higher
    tier number) than the generator. None when judge >= generator."""
    j_prov, j_model = resolve_judge(gen_provider, gen_model, judge_provider, judge_model)
    if tier_of(j_prov, j_model) > tier_of(gen_provider, gen_model):
        return (f"judge ({j_prov}/{j_model}) is weaker than the generator "
                f"({gen_provider}/{gen_model}); grading may be unreliable")
    return None
```

- [ ] **Step 3: Make `phase_judge.judge` use the passed judge**

In `app/services/phase_judge.py`, add params `judge_provider: str` and `judge_model: Optional[str]` to `judge(...)`, and replace line 147 (`judge_provider, judge_model = model_tiers.judge_model_for(gen_provider, gen_model)`) — delete it; the values now arrive as arguments. The `run_phase(provider=judge_provider, model=judge_model, ...)` call is unchanged.

- [ ] **Step 4: Resolve + thread in the pipeline**

In `pipeline.py:86-91` add:

```python
            judge_provider_ov = getattr(job, "judge_provider", None)
            judge_model_ov = getattr(job, "judge_model", None)
```

At BOTH `phase_judge.judge(...)` call sites (`:760` and `:791`), compute the effective judge from the actual producer and pass it:

```python
        _jp, _jm = model_tiers.resolve_judge(
            produced_by, _gen_model_of(produced_by), judge_provider_ov, judge_model_ov,
        )
        outcome = await phase_judge.judge(
            subject=subject, phase_name=phase_name, output_md=output_md,
            lesson_context=lesson_context, prior_outputs=prior_outputs,
            gen_provider=produced_by, gen_model=_gen_model_of(produced_by),
            judge_provider=_jp, judge_model=_jm,
            homework_job_id=job_id, phase_output_id=po_id,
            transport=judge_transport,
        )
```

(`model_tiers` is already imported in `phase_judge`; add `from app.services import model_tiers` to `pipeline.py` if not present — check the imports first.)

- [ ] **Step 5: Run**

Run: `uv run python -m pytest tests/services/test_judge_resolution.py tests/services/test_phase_judge.py tests/ -q`
Expected: PASS. (If existing `test_phase_judge.py` calls `judge(...)` without the new params, update those calls to pass `judge_provider`/`judge_model` — they are now required.)

- [ ] **Step 6: Commit**

```bash
git add app/services/model_tiers.py app/services/phase_judge.py app/services/pipeline.py tests/services/test_judge_resolution.py tests/services/test_phase_judge.py
git commit -m "feat(judge): explicit judge override + hard self-grade guard + weak-judge advisory"
```

---

### Task 7: Expose model tiers via /agent/models (powers the FE advisory)

**Files:**
- Modify: the `/api/v1/agent/models` handler (find with `grep -rn "def.*models" app/api/v1/agent.py`)
- Test: `tests/api/test_agent_models_tiers.py` (new)

**Interfaces:**
- Consumes: `MODEL_MANIFEST`, `model_tiers.tier_of`.
- Produces: the `/agent/models` response gains `tiers: { [provider]: { [model]: int } }` (1 = strongest). FE compares `tiers[judgeProvider][judgeModel] > tiers[genProvider][genModel]` to show the inline weaker-judge warning.

- [ ] **Step 1: Read the current handler + its response shape.** `grep -rn "api_supported\|providers\|MODEL_MANIFEST" app/api/v1/agent.py`.

- [ ] **Step 2: Write the failing test**

`tests/api/test_agent_models_tiers.py`:

```python
from app.services.agent_models import MODEL_MANIFEST
from app.services.model_tiers import tier_of


def test_tiers_cover_every_manifest_pair():
    # The tier map the endpoint serves must have an int for every manifest model.
    for provider, models in MODEL_MANIFEST.items():
        for m in models:
            assert isinstance(tier_of(provider, m), int)
```

Run: `uv run python -m pytest tests/api/test_agent_models_tiers.py -q` → PASS (this guards the data; wire it into the response next).

- [ ] **Step 3: Add `tiers` to the response**

In the `/agent/models` handler, build and include:

```python
    tiers = {
        prov: {m: tier_of(prov, m) for m in models}
        for prov, models in MODEL_MANIFEST.items()
    }
    # ... add `"tiers": tiers` to the returned dict ...
```

Import `tier_of` from `app.services.model_tiers`.

- [ ] **Step 4: Run the suite**

Run: `uv run python -m pytest tests/ -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add app/api/v1/agent.py tests/api/test_agent_models_tiers.py
git commit -m "feat(api): expose per-model tier in /agent/models"
```

---

### Task 8: FE types + api.ts payloads

**Files:**
- Modify: `web/src/lib/types.ts` (request/response types; `AgentModels` type for `/agent/models`)
- Modify: `web/src/lib/api.ts:150-185, 260-270` (generate + launchBatch payloads)
- Test: `cd web && npx tsc -p tsconfig.app.json --noEmit`

**Interfaces:**
- Produces: `generate`/`launchBatch` accept `extract_provider?`, `extract_model?`, `judge_provider?`, `judge_model?` (all `string | null`). The `/agent/models` response type gains `tiers?: Record<string, Record<string, number>>`.

- [ ] **Step 1: Add the optional fields** to the generate-options and launch-batch param types in `web/src/lib/api.ts` (mirror the existing `extract_transport?`/`judge_transport?` lines at `:156-157, 266`), and thread them into the POST body objects (`:166-182`).

- [ ] **Step 2: Add `tiers`** to the `/agent/models` response type in `web/src/lib/types.ts` (wherever `api_supported`/`providers` are typed).

- [ ] **Step 3: Typecheck**

Run: `cd web && npx tsc -p tsconfig.app.json --noEmit`
Expected: exit 0.

- [ ] **Step 4: Commit**

```bash
git add web/src/lib/types.ts web/src/lib/api.ts
git commit -m "feat(web): role provider/model fields in api client + types"
```

---

### Task 9: FE controls + defaults + weak-judge warning

**Files:**
- Create: `web/src/components/fleet/RoleAgentControls.tsx` (shared provider+model+transport control for a role)
- Modify: `web/src/components/fleet/launcher.tsx:268-270, 330-344, 405-423` (state, payload, render)
- Modify: `web/src/routes/section.tsx:37-39, 85-94` and its sub-component (state, payload, render)
- Test: `cd web && npx tsc -p tsconfig.app.json --noEmit`

**Interfaces:**
- Consumes: `RoleTransport`, the `/agent/models` query (`modelsQ`), `model_tiers` data via `tiers`.
- Produces: `RoleAgentControls` props `{ label, provider, model, transport, onProvider, onModel, onTransport, models /* manifest */, apiSupportedMap, defaultTransport }`. Generator transport default flips to `"api"`; extract/judge transport default `"cli"`; role provider/model default `null` (Auto).

- [ ] **Step 1: Flip the generator transport default + toggle order**

In `launcher.tsx:268` and `section.tsx:37`: `useState<Transport>("cli")` → `useState<Transport>("api")`.
In `launcher.tsx:23` and `section.tsx:222-223`: reorder so API is first — `const ALL_TRANSPORTS: Transport[] = ["api", "cli"];` (launcher) and the `{value:"api"}` entry before `{value:"cli"}` (section).

- [ ] **Step 2: Build `RoleAgentControls.tsx`**

A self-contained control: a provider `<Select>` (options: "Auto" + api/cli-capable providers from the manifest), a model `<Select>` (enabled only when a provider is picked; "Auto" allowed for cli, forced concrete for api — reuse the launcher's `missingApiModel` rule), and the existing `RoleTransport` select (Auto/CLI/API). Default provider = `null` (renders "Auto"), default transport from the `defaultTransport` prop (`"cli"` for roles). Encode the same api→force-model `useEffect` the generator uses (`launcher.tsx:312-324`) scoped to this role. Keep it ~120 lines, one responsibility.

- [ ] **Step 3: Wire extract + judge state + render in the launcher**

Add `extractProvider/extractModel/judgeProvider/judgeModel` state (default `null`). Render two `<RoleAgentControls label="Extract" defaultTransport="cli" .../>` and `label="Judge"` near the existing role-transport selects (`:405-423`). Replace the standalone `RoleTransportSelect` usages with the transport inside `RoleAgentControls` (or keep transport separate and pass through — pick one; do not double-render transport).

- [ ] **Step 4: Add the inline weak-judge warning**

When `judgeProvider` and `model`/`judgeModel` are set and `tiers[judgeProvider]?.[judgeModel] > tiers[genProvider]?.[genModel]`, render a small amber note next to the Judge control: "Judge is weaker than the generator — grading may be unreliable." Non-blocking (does not disable launch). (Exact self-grade is already hard-handled server-side; no FE block needed.)

- [ ] **Step 5: Send the fields in both payloads**

In `launcher.tsx` launch mutation (`:332-344`) and `section.tsx` generate call (`:85-94`), add:

```ts
        extract_provider: extractProvider,
        extract_model: extractModel,
        judge_provider: judgeProvider,
        judge_model: judgeModel,
```

- [ ] **Step 6: Mirror state + render + payload in `section.tsx`** (its sub-component at `:213-237` gets the same two `RoleAgentControls`).

- [ ] **Step 7: Typecheck**

Run: `cd web && npx tsc -p tsconfig.app.json --noEmit`
Expected: exit 0. Spot-check in `npm run dev` that the launcher defaults show generator=API, Extract/Judge transport=CLI, provider=Auto.

- [ ] **Step 8: Commit**

```bash
git add web/src/components/fleet/RoleAgentControls.tsx web/src/components/fleet/launcher.tsx web/src/routes/section.tsx
git commit -m "feat(web): per-role provider/model controls + API-default generator + weak-judge warning"
```

---

### Task 10: Acceptance — real CLI smoke, three distinct roles

**Files:**
- Create: `scripts/smoke_per_role.py` (in-process, no server)

**Interfaces:**
- Consumes: the pipeline resolution + judge resolution from Tasks 4 & 6.

- [ ] **Step 1: Write an in-process smoke that proves independent routing**

`scripts/smoke_per_role.py`: construct a job-like object with `provider="claude"`, `extract_provider="gemini", extract_model="gemini-2.5-flash"`, `judge_provider=None` (auto), transports all `cli`, and assert via logs/`agent_usages` that the extract spawn used gemini and the generator used claude. Minimal token use (one short section). Document the exact run command at the top of the file.

- [ ] **Step 2: Run it**

Run: `uv run python scripts/smoke_per_role.py`
Expected: prints the per-role (provider, model) actually used; extract=gemini, generator=claude, judge=auto-tier — proving the three roles route independently. (Real CLI calls; keep the section tiny.)

- [ ] **Step 3: Commit**

```bash
git add scripts/smoke_per_role.py
git commit -m "test(smoke): in-process per-role routing acceptance"
```

---

## Self-Review

**Spec coverage:**
- Storage (8 columns, both tables) → Task 1. ✓
- Resolution (extract override→settings; judge override→auto) → Tasks 4, 6. ✓
- Defaults (generator=API, roles=CLI, provider/model=Auto) → Task 9. ✓
- Reuse-key fix → Task 5. ✓
- Per-role validation → Task 3. ✓
- Judge guard split (self-grade hard, tier-below soft) → Task 6 (`resolve_judge` hard, `judge_advisory` soft) + Task 9 (FE surface) + Task 7 (tier data). ✓
- UI both surfaces → Task 9. ✓
- Backend `server_default` stays cli → no task touches it (verified constraint). ✓
- Testing (resolution, reuse, validation, judge guard, migration, FE, acceptance) → Tasks 1–10 each carry their tests. ✓

**Placeholder scan:** Tasks 2 and 5 reference a project session fixture / `make_extract_row` helper without inlining them — the implementer must copy the fixture idiom from an existing `RUN_DB_INTEGRATION` repo test; called out explicitly, not a silent TODO. Task 6 step 1 instructs reading `_MODEL_TIER` to pick concrete tier-1/tier-2 model ids (the table is the source of truth; hardcoding here would risk drift). All code-change steps show real code.

**Type consistency:** `extract_provider/extract_model/judge_provider/judge_model` (snake_case) used consistently across model, repo, schema, endpoint, pipeline. `_resolve_extract -> (provider, model)`, `resolve_judge -> (provider, model)`, `judge_advisory -> Optional[str]`, `find_latest_extract(..., provider, model)`, `phase_judge.judge(..., judge_provider, judge_model)` — names match every call site referenced.
