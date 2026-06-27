# Global Launch Defaults — UI-managed model selection (retire `.env` model vars) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move judge/extract/TOC model selection out of `.env`/`settings` into a DB-backed singleton (`launch_defaults`) edited from a new `/settings` page; jobs are stamped with concrete provider/model at launch; `.env` model vars are deleted entirely (credentials/infra stay).

**Architecture:** A singleton table `launch_defaults` (id=1, mirrors `budget_state`) holds the judge/extract provider·model·transport + TOC transport defaults, seeded with literal values by the migration. Launch endpoints resolve each role (explicit pick → else global default) and persist **concrete** provider/model onto the job + batch rows, so jobs are self-describing and `agent_usages` attribution is honest. A migration backfill stamps pre-existing NULL-column queued jobs. Runtime readers (`pipeline`, `model_tiers`, `toc_extractor`, claim gate) stop reading `settings.*`; the few defensive null-paths read the DB row instead. The `settings.judge_*/extract_*/extract_toc_transport` fields are then removed from `config.py` and every `.env`.

**Tech Stack:** FastAPI, SQLAlchemy (async), Alembic, Postgres, Pydantic; React + Vite + TanStack Query (web/).

## Global Constraints

- **Spec:** `docs/superpowers/specs/2026-06-27-global-launch-defaults-design.md` is the source of truth. Every task's requirements implicitly include it.
- **Seed values (migration, literal — no `.env`/`settings` read anywhere in the migration):** judge = `gemini` / `gemini-2.5-flash`; extract = `gemini` / `gemini-2.5-flash`; `judge_transport` = `inherit`; `extract_transport` = `inherit`; `toc_transport` = `cli`. These are the implementer's to confirm against the locked model strategy at build time, but use exactly these unless told otherwise.
- **Singleton:** `launch_defaults` has exactly one row, `id = 1`, enforced by `CHECK (id = 1)` named `ck_launch_defaults_singleton`. Mirror `budget_state` (`app/models/budget_state.py`, `app/repositories/budget.py`, `alembic/versions/0032_budget_state.py`) exactly.
- **Migration revision id ≤ 32 chars:** use `0037_launch_defaults` (20 chars). `down_revision = "0036_session_limit_strategy"` (current head).
- **Resolve-at-launch / future-launches-only:** at launch, persist the concrete resolved provider/model onto the job AND batch rows; never null going forward. Changing a default later must NOT mutate already-launched jobs.
- **Model resolution rule (load-bearing):** if a role's provider is an explicit pick, its model resolves to `body.<role>_model or default_model(provider)` (the **picked provider's** default) — NOT the global default's model. The global default *pair* is used only when the provider pick is Auto (NULL).
- **Transport resolution rule:** `resolved = explicit if explicit != "inherit" else launch_defaults.<role>_transport`. The stored value may itself be `"inherit"`; the pipeline resolves it via the existing `resolve_role_transport` at run, unchanged.
- **No `.env`/`settings` model authority after this ships:** `settings.judge_provider`, `settings.judge_model`, `settings.extract_provider`, `settings.extract_model`, `settings.extract_toc_transport` are deleted from `config.py`. Workers carry credentials + infra only. `settings.max_judge_regens` is NOT deleted (unrelated).
- **Validation:** every persisted/PUT (provider, model) pair must pass `agent_models.is_valid`; transports via `agent_models.validate_transport` (or `validate_role_transport` for `cli|api|inherit`). PUT rejects off-manifest with HTTP 422.
- **Stage only the files each task lists.** Other sessions commit to this branch — never `git add -A`.
- **Tests must BITE** — RED-prove the singleton CHECK and the resolution/claim-gate guards (revert the guard → test fails). No source-grep (`"x" in src`) assertions.
- **Real-DB tasks** (migration, repo, claim gate, resolution persistence) use the scratch-DB recipe: `createdb -U macmini5 edu_gld_test` → `DATABASE_URL=postgresql+asyncpg://macmini5@localhost:5432/edu_gld_test RUN_DB_INTEGRATION=1 uv run --extra dev alembic upgrade head` → pytest → `dropdb edu_gld_test`. NEVER point tests at a shared/live DB.
- **FE acceptance** (no JS test runner): `cd web && npx tsc -p tsconfig.app.json --noEmit` + `npm run build`; pure helpers may be checked with `npx tsx`.

---

## File Structure

**New (backend):**
- `app/models/launch_defaults.py` — `LaunchDefaults` singleton model.
- `app/repositories/launch_defaults.py` — `get(session)` / `update(session, fields)`.
- `alembic/versions/0037_launch_defaults.py` — create table + seed row + backfill NULL job columns.
- `app/api/v1/settings.py` — `GET`/`PUT /settings/launch-defaults`.

**New (frontend):**
- `web/src/routes/settings.tsx` — the `/settings` page.

**Modified (backend):**
- `app/services/agent_models.py` — `resolve_role_selection`, `resolve_role_transport_default` helpers.
- `app/api/v1/batch.py` + `app/api/v1/jobs.py` — resolve roles at launch, stamp concrete onto batch + job.
- `app/api/v1/__init__.py` — register settings router.
- `app/services/pipeline.py` — `_resolve_extract` + judge-override reads take the DB default defensively.
- `app/services/model_tiers.py` — `judge_model_for` no longer reads `settings.judge_*`.
- `app/services/toc_extractor.py` — read `launch_defaults` for provider/model/toc_transport.
- `app/repositories/jobs.py` — `claim_next_job` gate becomes job-column-based (drop settings hints).
- `app/services/worker.py` — `_compute_capabilities` → credential-only `{can_claude_api, can_gemini_api}`; remove `settings.judge_*` reads + the `JUDGE_MODEL == default_model` warn.
- `app/config.py` — delete the 5 fields + 2 validators.
- `app/schemas/job.py`, `app/models/homework_job.py` — comment fixes.

**Modified (frontend):**
- `web/src/lib/api.ts` — `getLaunchDefaults` / `updateLaunchDefaults`.
- `web/src/lib/types.ts` — `LaunchDefaults` type.
- `web/src/App.tsx` — `/settings` route; `web/src/components/layout.tsx` — nav item.
- `web/src/components/fleet/launcher.tsx` + `web/src/components/fleet/RoleAgentControls.tsx` — render `Auto → <resolved>` from the fetched defaults.

**Modified (ops/docs):** `.env`, `.env.example`, `README.md`, `docs/HOW_IT_WORKS.md`, `docs/CODE_MAP.md`, `docs/DATABASE.md`, `docs/DEPLOY.md`, worker setup runbook.

---

## Task 1: Storage — model, migration (table + seed + job backfill), repo

**Files:**
- Create: `app/models/launch_defaults.py`
- Modify: `app/models/__init__.py` (export `LaunchDefaults`, mirror the `BudgetState` export line)
- Create: `alembic/versions/0037_launch_defaults.py`
- Create: `app/repositories/launch_defaults.py`
- Test: `tests/repositories/test_launch_defaults.py`

**Interfaces:**
- Produces: `LaunchDefaults` (columns: `id`, `judge_provider`, `judge_model`, `judge_transport`, `extract_provider`, `extract_model`, `extract_transport`, `toc_transport`, `updated_at`); `launch_defaults_repo.get(session) -> LaunchDefaults`; `launch_defaults_repo.update(session, fields: dict) -> LaunchDefaults`.

- [ ] **Step 1: Write the model** (`app/models/launch_defaults.py`) — mirror `app/models/budget_state.py`:

```python
from datetime import datetime
from typing import Optional

from sqlalchemy import CheckConstraint, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class LaunchDefaults(Base):
    """Singleton (exactly one row, id=1) holding the UI-managed global launch
    defaults for the judge/extract roles + upload-time TOC transport. The
    launch endpoints resolve each role (explicit pick -> else this row) and
    stamp the concrete value onto every job. CHECK(id=1) enforces the singleton.
    Columns are nullable so a partial PUT can touch one field; the migration
    seeds the row with concrete values so reads always see a populated default.
    """

    __tablename__ = "launch_defaults"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    judge_provider: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    judge_model: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    judge_transport: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    extract_provider: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    extract_model: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    extract_transport: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    toc_transport: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    updated_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)

    __table_args__ = (
        CheckConstraint("id = 1", name="ck_launch_defaults_singleton"),
    )
```

Add the export to `app/models/__init__.py` next to the existing `BudgetState` line (match its exact import/`__all__` style).

- [ ] **Step 2: Write the migration** (`alembic/versions/0037_launch_defaults.py`):

```python
"""launch_defaults singleton — UI-managed judge/extract/TOC defaults; backfill jobs

Revision ID: 0037_launch_defaults
Revises: 0036_session_limit_strategy
"""
from alembic import op
import sqlalchemy as sa

revision = "0037_launch_defaults"
down_revision = "0036_session_limit_strategy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "launch_defaults",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("judge_provider", sa.String(length=32), nullable=True),
        sa.Column("judge_model", sa.String(length=128), nullable=True),
        sa.Column("judge_transport", sa.String(length=16), nullable=True),
        sa.Column("extract_provider", sa.String(length=32), nullable=True),
        sa.Column("extract_model", sa.String(length=128), nullable=True),
        sa.Column("extract_transport", sa.String(length=16), nullable=True),
        sa.Column("toc_transport", sa.String(length=16), nullable=True),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint("id = 1", name="ck_launch_defaults_singleton"),
        sa.PrimaryKeyConstraint("id"),
    )
    # Seed the singleton with literal default values (no .env/settings read).
    op.execute(
        """
        INSERT INTO launch_defaults
            (id, judge_provider, judge_model, judge_transport,
             extract_provider, extract_model, extract_transport,
             toc_transport, updated_at)
        VALUES
            (1, 'gemini', 'gemini-2.5-flash', 'inherit',
             'gemini', 'gemini-2.5-flash', 'inherit',
             'cli', now())
        """
    )
    # Backfill pre-existing queued jobs launched with "Auto" (NULL judge/extract
    # provider+model) so every job is self-describing — the claim gate's settings
    # hint is dropped in this release, and a NULL-provider api-judge job would
    # otherwise strand unclaimable. Literal values match the seed above.
    op.execute(
        """
        UPDATE homework_jobs
           SET judge_provider   = COALESCE(judge_provider,   'gemini'),
               judge_model      = COALESCE(judge_model,      'gemini-2.5-flash'),
               extract_provider = COALESCE(extract_provider, 'gemini'),
               extract_model    = COALESCE(extract_model,    'gemini-2.5-flash')
         WHERE status IN ('pending', 'running')
        """
    )


def downgrade() -> None:
    op.drop_table("launch_defaults")
```

- [ ] **Step 3: Write the repo** (`app/repositories/launch_defaults.py`) — mirror `app/repositories/budget.py`:

```python
"""Repository for the launch_defaults singleton (id=1, seeded by migration
0037_launch_defaults). Read once per launch/upload — not a hot path."""
from __future__ import annotations

from sqlalchemy import func, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.launch_defaults import LaunchDefaults

_MUTABLE = (
    "judge_provider", "judge_model", "judge_transport",
    "extract_provider", "extract_model", "extract_transport",
    "toc_transport",
)


async def get(session: AsyncSession) -> LaunchDefaults:
    """Return the singleton (id=1). Raises if missing (broken migration state)."""
    row = await session.get(LaunchDefaults, 1)
    if row is None:
        raise RuntimeError(
            "launch_defaults singleton (id=1) is missing — run 'alembic upgrade head'"
        )
    return row


async def update(session: AsyncSession, fields: dict) -> LaunchDefaults:
    """Partial update of the singleton; touches updated_at. Ignores unknown keys."""
    values = {k: v for k, v in fields.items() if k in _MUTABLE}
    if values:
        values["updated_at"] = func.now()
        await session.execute(
            update(LaunchDefaults).where(LaunchDefaults.id == 1).values(**values)
        )
    return await get(session)
```

- [ ] **Step 4: Write the failing test** (`tests/repositories/test_launch_defaults.py`). Mirror `tests/repositories/test_fleet_pause_gate.py`'s scratch-DB fixture style (real Postgres, `RUN_DB_INTEGRATION`):

```python
import os
import pytest
from sqlalchemy.exc import IntegrityError

from app.models.launch_defaults import LaunchDefaults
from app.repositories import launch_defaults as repo

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION") != "1", reason="needs real Postgres"
)


async def test_migration_seeds_singleton(db_session):
    row = await repo.get(db_session)
    assert row.id == 1
    assert (row.judge_provider, row.judge_model) == ("gemini", "gemini-2.5-flash")
    assert (row.extract_provider, row.extract_model) == ("gemini", "gemini-2.5-flash")
    assert row.judge_transport == "inherit"
    assert row.extract_transport == "inherit"
    assert row.toc_transport == "cli"


async def test_update_partial_roundtrip(db_session):
    await repo.update(db_session, {"judge_provider": "claude", "judge_model": "claude-opus-4-7"})
    row = await repo.get(db_session)
    assert (row.judge_provider, row.judge_model) == ("claude", "claude-opus-4-7")
    # untouched fields remain
    assert row.extract_provider == "gemini"
    assert row.updated_at is not None


async def test_singleton_invariant_rejects_second_row(db_session):
    # RED-prove the CHECK(id=1): inserting id=2 must violate the constraint.
    db_session.add(LaunchDefaults(id=2, judge_provider="x"))
    with pytest.raises(IntegrityError):
        await db_session.flush()
```

Provide a `db_session` fixture in this file (or reuse the existing real-DB conftest fixture if `tests/repositories/conftest.py` defines one — check first and reuse rather than duplicate).

- [ ] **Step 5: Run on a scratch DB to verify RED then GREEN.**

```bash
createdb -U macmini5 edu_gld_test
DATABASE_URL=postgresql+asyncpg://macmini5@localhost:5432/edu_gld_test \
  RUN_DB_INTEGRATION=1 uv run --extra dev alembic upgrade head
DATABASE_URL=postgresql+asyncpg://macmini5@localhost:5432/edu_gld_test \
  RUN_DB_INTEGRATION=1 uv run --extra dev python -m pytest tests/repositories/test_launch_defaults.py -q
dropdb edu_gld_test
```
Expected: all three pass; `test_singleton_invariant_rejects_second_row` fails if the CHECK is removed (bite-verify by temporarily dropping it).

- [ ] **Step 6: Commit.**

```bash
git add app/models/launch_defaults.py app/models/__init__.py \
        alembic/versions/0037_launch_defaults.py \
        app/repositories/launch_defaults.py \
        tests/repositories/test_launch_defaults.py
git commit -m "feat(launch-defaults): singleton table + migration (seed + job backfill) + repo"
```

---

## Task 2: Resolve roles at launch — stamp concrete provider/model onto job + batch

**Files:**
- Modify: `app/services/agent_models.py` (add two resolver helpers)
- Modify: `app/api/v1/batch.py:170-205` (resolve before preview/batch-create; pass resolved to `get_or_create_for_book` + `jobs_repo.create`)
- Modify: `app/api/v1/jobs.py` (resolve before `jobs_repo.create`)
- Test: `tests/services/test_role_resolution.py` (pure helper), `tests/api/test_launch_stamps_defaults.py` (persistence, real-DB)

**Interfaces:**
- Consumes: `launch_defaults_repo.get` (Task 1); `agent_models.is_valid`, `default_model`.
- Produces: `agent_models.resolve_role_selection(explicit_provider, explicit_model, default_provider, default_model_) -> tuple[str, str]`; `agent_models.resolve_role_transport_default(explicit_transport, default_transport) -> str`.

- [ ] **Step 1: Write the failing helper test** (`tests/services/test_role_resolution.py`):

```python
from app.services.agent_models import (
    resolve_role_selection,
    resolve_role_transport_default,
)


def test_auto_provider_uses_global_default_pair():
    assert resolve_role_selection(None, None, "gemini", "gemini-2.5-flash") == (
        "gemini", "gemini-2.5-flash",
    )


def test_explicit_provider_and_model_passthrough():
    assert resolve_role_selection("claude", "claude-opus-4-7", "gemini", "gemini-2.5-flash") == (
        "claude", "claude-opus-4-7",
    )


def test_explicit_provider_auto_model_uses_that_providers_default_not_global():
    # claude picked, model Auto -> claude's own default, NOT gemini-2.5-flash
    p, m = resolve_role_selection("claude", None, "gemini", "gemini-2.5-flash")
    assert p == "claude"
    assert m == "claude-sonnet-4-6"  # default_model("claude") == first manifest entry


def test_transport_inherit_falls_to_default_explicit_wins():
    assert resolve_role_transport_default("inherit", "api") == "api"
    assert resolve_role_transport_default("cli", "api") == "cli"
    assert resolve_role_transport_default("inherit", "inherit") == "inherit"
```

- [ ] **Step 2: Run — expect ImportError / fail.** `uv run python -m pytest tests/services/test_role_resolution.py -q`

- [ ] **Step 3: Add the helpers** to `app/services/agent_models.py` (after `resolve_role_transport`):

```python
def resolve_role_selection(
    explicit_provider: str | None,
    explicit_model: str | None,
    default_provider: str,
    default_model_: str | None,
) -> tuple[str, str | None]:
    """Resolve a role's (provider, model) at launch time.

    Explicit provider wins: its model is the explicit pick or THAT provider's
    own default (never the global default's model, which belongs to a different
    provider). Auto provider -> the global default pair verbatim.
    """
    if explicit_provider is not None:
        return explicit_provider, (explicit_model or default_model(explicit_provider))
    return default_provider, default_model_


def resolve_role_transport_default(explicit_transport: str, default_transport: str) -> str:
    """'inherit' (the launcher 'Auto') -> the global default transport (which may
    itself be 'inherit'); an explicit 'cli'/'api' wins."""
    return default_transport if explicit_transport == "inherit" else explicit_transport
```

- [ ] **Step 4: Run helper test — GREEN.**

- [ ] **Step 5: Wire into `batch.py`.** After the per-role validation loop (ends `app/api/v1/batch.py:169`) and BEFORE the preview block (`:171`), insert:

```python
    # Resolve roles against the UI-managed global defaults: explicit pick wins,
    # else the global default. Stamp CONCRETE provider/model onto job + batch so
    # jobs are self-describing (future-launches-only; agent_usages stays honest).
    ld = await launch_defaults_repo.get(session)
    res_judge_provider, res_judge_model = resolve_role_selection(
        body.judge_provider, body.judge_model, ld.judge_provider, ld.judge_model)
    res_extract_provider, res_extract_model = resolve_role_selection(
        body.extract_provider, body.extract_model, ld.extract_provider, ld.extract_model)
    res_judge_transport = resolve_role_transport_default(body.judge_transport, ld.judge_transport)
    res_extract_transport = resolve_role_transport_default(body.extract_transport, ld.extract_transport)
    # Defense-in-depth: the resolved pairs must be manifest-valid (the global
    # default could only be off-manifest via a buggy PUT — fail loud, not silent).
    for role, prov, mdl in (("judge", res_judge_provider, res_judge_model),
                            ("extract", res_extract_provider, res_extract_model)):
        if not is_valid(prov, mdl):
            raise HTTPException(500, f"{role}: resolved default off-manifest ({prov!r},{mdl!r})")
```

Then change the `batches_repo.get_or_create_for_book(...)` call (`:194-204`) and BOTH `jobs_repo.create(...)` call sites in this file to pass the **resolved** values instead of `body.*`:
`extract_transport=res_extract_transport, judge_transport=res_judge_transport, extract_provider=res_extract_provider, extract_model=res_extract_model, judge_provider=res_judge_provider, judge_model=res_judge_model`.

Add imports at the top of `batch.py`: `from app.repositories import launch_defaults as launch_defaults_repo` and extend the existing `from app.services.agent_models import (...)` to include `resolve_role_selection, resolve_role_transport_default`.

- [ ] **Step 6: Wire into `jobs.py`** identically — after its per-role validation loop and before `jobs_repo.create` (`app/api/v1/jobs.py:234`), insert the same resolution block (using `body.transport` context) and pass the resolved values into `jobs_repo.create`. Same imports.

- [ ] **Step 7: Write the persistence test** (`tests/api/test_launch_stamps_defaults.py`, real-DB). Assert: (a) launch with judge Auto → the created job row has `judge_provider="gemini"`, `judge_model="gemini-2.5-flash"`; (b) launch with `judge_provider="claude"` (model Auto) → job row has `judge_provider="claude"`, `judge_model="claude-sonnet-4-6"`; (c) after `launch_defaults_repo.update(judge_provider="claude", judge_model="claude-opus-4-7")`, an EARLIER job's row is unchanged (future-launches-only). RED-prove (c) by asserting the old job keeps its stamped value. Use the existing api-test client + real-DB fixtures (mirror `tests/api/test_role_provider_persist.py`).

- [ ] **Step 8: Run** the api + helper tests on the scratch DB (recipe as Task 1). GREEN.

- [ ] **Step 9: Commit.**

```bash
git add app/services/agent_models.py app/api/v1/batch.py app/api/v1/jobs.py \
        tests/services/test_role_resolution.py tests/api/test_launch_stamps_defaults.py
git commit -m "feat(launch-defaults): resolve judge/extract roles at launch, stamp concrete on job+batch"
```

---

## Task 3: Runtime defensive reads → DB default (pipeline + model_tiers), drop `settings.*`

**Files:**
- Modify: `app/services/pipeline.py:73-79` (`_resolve_extract`) + the role-override reads at `:135-143` + load `launch_defaults` in `run`
- Modify: `app/services/model_tiers.py:80-90` (`judge_model_for` stops reading `settings.judge_*`)
- Test: `tests/services/test_extract_resolution.py`, `tests/services/test_model_tiers.py`, `tests/services/test_judge_resolution.py` (update existing)

**Interfaces:**
- Consumes: `launch_defaults_repo.get`; `model_tiers._self_fallback` (unchanged hardcoded frontier peers).
- Produces: pipeline judge/extract override resolution sourced from the job row, defensively backstopped by the DB default (never `settings.*`).

- [ ] **Step 1: Update `model_tiers.judge_model_for`.** It is reached only as the self-grade fallback now (jobs are always stamped). Remove the `settings.judge_*` read; return the generator-aware self-fallback peer directly:

```python
def judge_model_for(gen_provider: str, gen_model: Optional[str]) -> tuple[str, str]:
    """Self-grade fallback judge (provider, model): a strong frontier peer
    guaranteed != the generator. Reached only when a job's explicit judge would
    grade its own output, or (defensively) when no judge is stamped — in both
    cases the safe answer is a non-self frontier peer, not any configured default
    (the configured default now lives in the launch_defaults DB row, applied at
    launch time, never here)."""
    resolved_gen = (gen_provider, gen_model or default_model(gen_provider))
    return _self_fallback(resolved_gen)
```

Remove the now-unused `from app.config import settings` import if nothing else in the file uses it (check: the `_MODEL_TIER` table and `tier_of` don't). Update the module docstring lines 1-11 + the `judge_model_for` docstring references to `settings.judge_model` to describe the DB-row source.

- [ ] **Step 2: Update the affected `model_tiers`/`judge_resolution` tests.** `tests/services/test_model_tiers.py` + `tests/services/test_judge_resolution.py` currently assert `judge_model_for` returns the `settings.judge` pair. Rewrite those assertions to the new contract: `judge_model_for("gemini","gemini-2.5-flash")` → `("claude","claude-opus-4-7")` (the primary peer, since the generator isn't the primary peer); `judge_model_for("claude","claude-opus-4-7")` → `("gemini","gemini-3.1-pro-preview")` (alternate peer). Keep/confirm the `resolve_judge` self-grade and explicit-override cases. The two known config-driven reds noted in worklog 0068 are resolved by this change — they should now pass deterministically (no `JUDGE_MODEL` env dependency).

- [ ] **Step 3: Load `launch_defaults` in `pipeline.run`** alongside the batch load (`app/services/pipeline.py:148-152`), inside the same `async with SessionLocal() as session:` block:

```python
            from app.repositories import launch_defaults as _ld_repo  # noqa: PLC0415
            _ld = await _ld_repo.get(session)
```

Then change the override reads (`:135-143`) so a NULL job column falls back to the DB default (defensive — jobs are stamped, so this is belt-and-suspenders, NOT a settings read):

```python
            judge_provider_ov = getattr(job, "judge_provider", None) or _ld.judge_provider
            judge_model_ov = getattr(job, "judge_model", None) or _ld.judge_model
            extract_provider, extract_model = _resolve_extract(
                getattr(job, "extract_provider", None),
                getattr(job, "extract_model", None),
                _ld,
            )
```

- [ ] **Step 4: Update `_resolve_extract`** (`pipeline.py:73-79`) to take the DB default instead of `settings`:

```python
def _resolve_extract(job_extract_provider, job_extract_model, ld):
    """Extract role provider/model: explicit job override, else the global
    default from the launch_defaults DB row (jobs are stamped at launch; this
    is the defensive null-path, no settings read)."""
    return (
        job_extract_provider or ld.extract_provider,
        job_extract_model or ld.extract_model,
    )
```

Remove the module-level `from app.config import settings` use for extract if it's now unused elsewhere in `pipeline.py` (it is still used for `extract_window_pages`, `extract_max_text_chars`, etc. — keep the import; just stop reading `settings.extract_provider/model`).

- [ ] **Step 5: Update `tests/services/test_extract_resolution.py`** to pass a fake `ld` object (e.g. `SimpleNamespace(extract_provider="gemini", extract_model="gemini-2.5-flash")`) and assert override-wins / default-fallback. RED-prove the default path (delete the `or ld.extract_provider` → test fails).

- [ ] **Step 6: Run** `uv run python -m pytest tests/services/test_extract_resolution.py tests/services/test_model_tiers.py tests/services/test_judge_resolution.py -q`. GREEN.

- [ ] **Step 7: Commit.**

```bash
git add app/services/pipeline.py app/services/model_tiers.py \
        tests/services/test_extract_resolution.py tests/services/test_model_tiers.py \
        tests/services/test_judge_resolution.py
git commit -m "refactor(launch-defaults): pipeline+model_tiers read DB default, drop settings.judge/extract reads"
```

---

## Task 4: Claim gate — job-column-based capability gate, drop settings hints

**Files:**
- Modify: `app/services/worker.py:61-116` (`_compute_capabilities` → credential-only) + `:230-241` (remove `JUDGE_MODEL == default_model` warn)
- Modify: `app/repositories/jobs.py:300-351` (`claim_next_job` judge/extract gate from job columns)
- Test: `tests/integration/test_claim_contention.py` (update), `tests/services/test_worker_capabilities.py` (update), new `tests/integration/test_claim_gate_self_grade.py`

**Interfaces:**
- Consumes: `model_tiers._PRIMARY_SELF_FALLBACK` (`("claude","claude-opus-4-7")`), `_ALT_SELF_FALLBACK` (`("gemini","gemini-3.1-pro-preview")`).
- Produces: `worker._compute_capabilities(env) -> {"can_claude_api": bool, "can_gemini_api": bool}`; `CAPABILITIES = _compute_capabilities(os.environ)`. `claim_next_job` gate references only `can_claude_api`/`can_gemini_api` + job columns.

- [ ] **Step 1: Simplify `_compute_capabilities`** to credential-only and drop the settings-derived keys (`judge_api_ok`, `judge_fallback_api_ok`, `extract_api_ok`, `judge_pair`, `settings_judge_provider`, `settings_extract_provider`):

```python
def _compute_capabilities(env) -> dict:
    """Credential-only api capability. The claim gate evaluates each job's own
    stamped provider x transport against these; no model/provider value lives on
    the worker anymore (those moved to the launch_defaults DB row)."""
    cap = _api_capable(env)
    return {"can_claude_api": cap["claude"], "can_gemini_api": cap["gemini"]}


CAPABILITIES: dict = _compute_capabilities(os.environ)
```

Update the call site (was `_compute_capabilities(os.environ, settings.judge_provider, settings.judge_model, settings.extract_provider)`). Remove the `if settings.judge_model == default_model(settings.judge_provider): logger.warning(...)` block (`worker.py:230-241`) and the now-unused `default_model`/`settings` imports if nothing else needs them (check: `settings` is still used elsewhere in `worker.py` — keep it; just remove the deleted reads).

- [ ] **Step 2: Rewrite the `claim_next_job` judge/extract gate** (`app/repositories/jobs.py:312-351`). Replace the `judge_pair` / `settings_*` COALESCE block with job-column logic. The self-grade case (a job whose generator equals its stamped judge) is judged by the hardcoded self-fallback peer, so gate on THAT peer's credential:

```python
    from app.services.model_tiers import _PRIMARY_SELF_FALLBACK  # ("claude","claude-opus-4-7")

    def _provider_api_ok(resolved):
        return or_(
            and_(resolved == "claude", literal(bool(caps.get("can_claude_api")))),
            and_(resolved == "gemini", literal(bool(caps.get("can_gemini_api")))),
        )

    judge_needs_api = or_(
        HomeworkJob.judge_transport == "api",
        and_(HomeworkJob.judge_transport == "inherit", HomeworkJob.transport == "api"),
    )
    # Self-grade: job's generator == its stamped judge -> judged by the
    # self-fallback peer (claude-opus-4-7, or gemini-3.1-pro-preview when the job
    # IS that primary peer). Gate on the peer's credential for exactly those jobs.
    job_is_self_grade = and_(
        HomeworkJob.provider == HomeworkJob.judge_provider,
        func.coalesce(HomeworkJob.model, "") == func.coalesce(HomeworkJob.judge_model, ""),
    )
    self_grade_judge_provider = case(
        (and_(HomeworkJob.provider == _PRIMARY_SELF_FALLBACK[0],
              HomeworkJob.model == _PRIMARY_SELF_FALLBACK[1]), "gemini"),
        else_="claude",
    )
    judge_ok = or_(
        not_(judge_needs_api),
        and_(job_is_self_grade, _provider_api_ok(self_grade_judge_provider)),
        and_(not_(job_is_self_grade), _provider_api_ok(HomeworkJob.judge_provider)),
    )
    extract_needs_api = or_(
        HomeworkJob.extract_transport == "api",
        and_(HomeworkJob.extract_transport == "inherit", HomeworkJob.transport == "api"),
    )
    extract_ok = or_(not_(extract_needs_api), _provider_api_ok(HomeworkJob.extract_provider))
```

Add `case` to the SQLAlchemy import in `jobs.py`. Keep `content_ok`, `not_in_paused_batch`, and the fleet-daily pause gate unchanged. Update the `claim_next_job` docstring (`:280-300`) to describe the credential-only/job-column gate.

- [ ] **Step 3: Update `_drain_check_and_beat` / heartbeat publish** if it references any removed CAPABILITIES key (it publishes `CAPABILITY_BLOB`, which is credential-based already — confirm no removed key is read). Update `tests/services/test_worker_capabilities.py` to the new `{can_claude_api, can_gemini_api}` shape; drop assertions on the removed keys.

- [ ] **Step 4: Update + extend the claim-contention tests.** In `tests/integration/test_claim_contention.py`, replace `capabilities` dicts that carried `settings_judge_provider`/`judge_pair`/etc. with the credential-only shape. Add `tests/integration/test_claim_gate_self_grade.py` (real-DB) covering: (a) a non-self-grade api-judge job with stamped `judge_provider=gemini` is claimable by a gemini-api worker, not a claude-only worker; (b) a self-grade job generated by `claude/claude-opus-4-7` with judge `claude/claude-opus-4-7` (judge→gemini peer) needs `can_gemini_api`; (c) a self-grade job generated by `gemini/gemini-2.5-flash` judged by same (judge→claude peer) needs `can_claude_api`. RED-prove each by flipping the worker's cap flag and asserting non-claim.

- [ ] **Step 5: Run on scratch DB.**

```bash
createdb -U macmini5 edu_gld_test
DATABASE_URL=postgresql+asyncpg://macmini5@localhost:5432/edu_gld_test \
  RUN_DB_INTEGRATION=1 uv run --extra dev alembic upgrade head
DATABASE_URL=postgresql+asyncpg://macmini5@localhost:5432/edu_gld_test \
  RUN_DB_INTEGRATION=1 uv run --extra dev python -m pytest \
  tests/integration/test_claim_contention.py tests/integration/test_claim_gate_self_grade.py \
  tests/services/test_worker_capabilities.py -q
dropdb edu_gld_test
```
GREEN; bite-verify each self-grade case.

- [ ] **Step 6: Commit.**

```bash
git add app/services/worker.py app/repositories/jobs.py \
        tests/integration/test_claim_contention.py tests/integration/test_claim_gate_self_grade.py \
        tests/services/test_worker_capabilities.py
git commit -m "refactor(launch-defaults): claim gate evaluates stamped job columns; worker capability is credential-only"
```

---

## Task 5: TOC extraction reads `launch_defaults` (provider + model + transport)

**Files:**
- Modify: `app/services/toc_extractor.py:34-61`
- Test: `tests/services/test_toc_extractor.py` (update)

**Interfaces:**
- Consumes: `launch_defaults_repo.get`. `toc_extractor.run` already opens `SessionLocal()` (`:38`) — read the row there and thread the three values down.

- [ ] **Step 1: Read `launch_defaults` in `run`.** In the existing `async with SessionLocal() as session:` block at `:38-40`, after `set_status`, fetch the row and capture the three values into locals (the session closes after the block; capture primitives, not the ORM object):

```python
        async with SessionLocal() as session:
            await books_repo.set_status(session, book_id, "toc_extracting")
            ld = await launch_defaults_repo.get(session)
            toc_provider = ld.extract_provider
            toc_model = ld.extract_model
            toc_transport = ld.toc_transport
            await session.commit()
```

Add `from app.repositories import launch_defaults as launch_defaults_repo` to the imports.

- [ ] **Step 2: Replace the `settings.*` reads** at `:48-61` with the captured locals:

```python
        log.info(
            f"[book {book_id}] extracting TOC via agent "
            f"({toc_provider} / {toc_model}) transport={toc_transport}"
        )
        t_extract = perf_counter()
        extracted = await agent.extract_toc(
            provider=toc_provider,
            model=toc_model,
            pdf_path=file_path,
            subject=subject,
            book_id=book_id,
            transport=toc_transport,
        )
```

Remove the `from app.config import settings` import if `settings` is now unused in this file (check the rest of `toc_extractor.py`).

- [ ] **Step 3: Update `tests/services/test_toc_extractor.py`** to stub `launch_defaults_repo.get` returning a fake row (`SimpleNamespace(extract_provider="gemini", extract_model="gemini-2.5-flash", toc_transport="cli")`) and assert `agent.extract_toc` is called with those values. RED-prove (change the fake → assertion must follow).

- [ ] **Step 4: Run** `uv run python -m pytest tests/services/test_toc_extractor.py -q`. GREEN.

- [ ] **Step 5: Commit.**

```bash
git add app/services/toc_extractor.py tests/services/test_toc_extractor.py
git commit -m "refactor(launch-defaults): TOC extraction sources provider/model/transport from DB defaults"
```

---

## Task 6: Settings API — `GET`/`PUT /settings/launch-defaults`

**Files:**
- Create: `app/api/v1/settings.py`
- Modify: `app/api/v1/__init__.py` (register router)
- Test: `tests/api/test_settings_launch_defaults.py`

**Interfaces:**
- Consumes: `launch_defaults_repo.get/update`; `agent_models.is_valid`, `validate_transport`, `validate_role_transport`.
- Produces: `GET /api/v1/settings/launch-defaults` → JSON of the row; `PUT` → partial update, 422 on off-manifest.

- [ ] **Step 1: Write the failing test** (`tests/api/test_settings_launch_defaults.py`, real-DB; mirror an existing api-test module's client fixture):

```python
async def test_get_returns_seeded_defaults(client):
    r = await client.get("/api/v1/settings/launch-defaults")
    assert r.status_code == 200
    body = r.json()
    assert body["judge_provider"] == "gemini"
    assert body["toc_transport"] == "cli"


async def test_put_partial_update(client):
    r = await client.put("/api/v1/settings/launch-defaults",
                         json={"judge_provider": "claude", "judge_model": "claude-opus-4-7"})
    assert r.status_code == 200
    assert r.json()["judge_provider"] == "claude"
    # untouched
    assert r.json()["extract_provider"] == "gemini"


async def test_put_rejects_off_manifest(client):
    r = await client.put("/api/v1/settings/launch-defaults",
                         json={"judge_provider": "claude", "judge_model": "not-a-model"})
    assert r.status_code == 422


async def test_put_rejects_bad_transport(client):
    r = await client.put("/api/v1/settings/launch-defaults", json={"toc_transport": "bogus"})
    assert r.status_code == 422
```

- [ ] **Step 2: Run — expect 404 (route absent).**

- [ ] **Step 3: Write `app/api/v1/settings.py`:**

```python
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.repositories import launch_defaults as launch_defaults_repo
from app.services.agent_models import is_valid, validate_role_transport, validate_transport

router = APIRouter(tags=["settings"])


class LaunchDefaultsOut(BaseModel):
    judge_provider: str | None
    judge_model: str | None
    judge_transport: str | None
    extract_provider: str | None
    extract_model: str | None
    extract_transport: str | None
    toc_transport: str | None


class LaunchDefaultsUpdate(BaseModel):
    judge_provider: str | None = None
    judge_model: str | None = None
    judge_transport: str | None = None
    extract_provider: str | None = None
    extract_model: str | None = None
    extract_transport: str | None = None
    toc_transport: str | None = None


def _serialize(row) -> LaunchDefaultsOut:
    return LaunchDefaultsOut(
        judge_provider=row.judge_provider, judge_model=row.judge_model,
        judge_transport=row.judge_transport, extract_provider=row.extract_provider,
        extract_model=row.extract_model, extract_transport=row.extract_transport,
        toc_transport=row.toc_transport)


@router.get("/settings/launch-defaults", response_model=LaunchDefaultsOut)
async def get_launch_defaults(session: AsyncSession = Depends(get_session)) -> LaunchDefaultsOut:
    return _serialize(await launch_defaults_repo.get(session))


@router.put("/settings/launch-defaults", response_model=LaunchDefaultsOut)
async def put_launch_defaults(
    body: LaunchDefaultsUpdate,
    session: AsyncSession = Depends(get_session),
) -> LaunchDefaultsOut:
    fields = body.model_dump(exclude_unset=True)
    current = await launch_defaults_repo.get(session)
    # Validate the merged (provider, model) per role + each transport.
    merged = {**_serialize(current).model_dump(), **fields}
    for role in ("judge", "extract"):
        prov, mdl = merged.get(f"{role}_provider"), merged.get(f"{role}_model")
        if prov is not None and not is_valid(prov, mdl):
            raise HTTPException(422, f"{role}: off-manifest (provider, model) ({prov!r}, {mdl!r})")
    for role in ("judge", "extract"):
        t = merged.get(f"{role}_transport")
        if t is not None and (err := validate_role_transport(f"{role}_transport", t)) is not None:
            raise HTTPException(422, err)
    toc = merged.get("toc_transport")
    if toc is not None and toc not in ("cli", "api"):
        raise HTTPException(422, "toc_transport must be 'cli' or 'api'")
    return _serialize(await launch_defaults_repo.update(session, fields))
```

(If `agent_models` exposes a cleaner transport validator that also rejects `inherit` for TOC, prefer it; the inline `cli|api` check above is the contract from §1.)

- [ ] **Step 4: Register the router** in `app/api/v1/__init__.py`: add `settings` to the `from app.api.v1 import ...` line and `api_v1_router.include_router(settings.router, dependencies=[Depends(get_current_user)])` (placement is order-independent — its paths are static `/settings/*`; add it after `workers`).

- [ ] **Step 5: Run** the settings api test on the scratch DB. GREEN.

- [ ] **Step 6: Commit.**

```bash
git add app/api/v1/settings.py app/api/v1/__init__.py tests/api/test_settings_launch_defaults.py
git commit -m "feat(launch-defaults): GET/PUT /settings/launch-defaults with manifest validation"
```

---

## Task 7: Delete `settings.*` model fields + `.env` model vars

**Files:**
- Modify: `app/config.py:145-146,194-228` (delete 5 fields + 2 validators; KEEP `max_judge_regens`)
- Modify: `.env`, `.env.example`
- Modify: `app/schemas/job.py:62`, `app/models/homework_job.py:40` (comment fixes)
- Delete: `tests/test_config_extract_provider.py`, `tests/test_config_extract_toc_transport.py`
- Test: full suite

**Interfaces:** none new. This task is the deletion — only safe AFTER Tasks 3, 4, 5 repointed every reader.

- [ ] **Step 1: Prove no readers remain.**

```bash
grep -rn "settings\.judge_provider\|settings\.judge_model\|settings\.extract_provider\|settings\.extract_model\|settings\.extract_toc_transport" app/
```
Expected: NO matches in `app/` (Tasks 3/4/5 removed them all). If any remain, fix them before deleting the fields.

- [ ] **Step 2: Delete the fields + validators** from `app/config.py`: remove `judge_provider`/`judge_model` (`:145-146`), `extract_provider`/`extract_model` (`:194-195`) + `_blank_extract_provider_to_default` (`:197-211`), `extract_toc_transport` (`:213`) + `_blank_toc_transport_to_default` (`:215-228`). Leave `max_judge_regens`, `failover_provider_order`, the extract-robustness numerics, etc. untouched.

- [ ] **Step 3: Delete the env lines.** Remove `EXTRACT_PROVIDER`, `EXTRACT_MODEL`, `EXTRACT_TOC_TRANSPORT`, `JUDGE_PROVIDER`, `JUDGE_MODEL` from `.env` (`:18-27` region) and from `.env.example` (`:50-64` region). Add a one-line comment in `.env.example` pointing to `/settings`: `# Model selection (judge/extract/TOC) lives in the DB, edited at /settings — not here.`

- [ ] **Step 4: Fix stale comments.** `app/schemas/job.py:62` (`None ⇒ settings.extract_provider`) → `None ⇒ global default (launch_defaults)`; `app/models/homework_job.py:40` similarly.

- [ ] **Step 5: Delete the two config-validator test files** (they test deleted validators): `tests/test_config_extract_provider.py`, `tests/test_config_extract_toc_transport.py`.

- [ ] **Step 6: Run the full suite** and fix any remaining references (tests that imported the deleted settings fields):

```bash
uv run python -m pytest tests/ -q
```
Expected: green (DB-integration ones skipped without `RUN_DB_INTEGRATION`). Grep `tests/` for the deleted field names and repoint/remove as needed.

- [ ] **Step 7: Commit.**

```bash
git add app/config.py .env .env.example app/schemas/job.py app/models/homework_job.py
git rm tests/test_config_extract_provider.py tests/test_config_extract_toc_transport.py
git commit -m "feat(launch-defaults): remove settings.judge/extract/toc model vars from config + .env"
```

---

## Task 8: Frontend — `/settings` page + api + types + nav

**Files:**
- Modify: `web/src/lib/types.ts` (add `LaunchDefaults`)
- Modify: `web/src/lib/api.ts` (`getLaunchDefaults`, `updateLaunchDefaults`)
- Create: `web/src/routes/settings.tsx`
- Modify: `web/src/App.tsx` (route), `web/src/components/layout.tsx` (nav item)
- Acceptance: `npx tsc -p tsconfig.app.json --noEmit` + `npm run build`

**Interfaces:**
- Consumes: `api.getAgentModels()` (manifest for dropdowns), Task 6 endpoints.

- [ ] **Step 1: Add the type** to `web/src/lib/types.ts`:

```ts
export interface LaunchDefaults {
  judge_provider: string | null;
  judge_model: string | null;
  judge_transport: RoleTransport | null;
  extract_provider: string | null;
  extract_model: string | null;
  extract_transport: RoleTransport | null;
  toc_transport: "cli" | "api" | null;
}
```

- [ ] **Step 2: Add the api calls** to `web/src/lib/api.ts` (mirror `getAgentModels` / `updateBook`):

```ts
async getLaunchDefaults(): Promise<LaunchDefaults> {
  return unwrap<LaunchDefaults>(await authFetch("/api/v1/settings/launch-defaults"));
},
async updateLaunchDefaults(patch: Partial<LaunchDefaults>): Promise<LaunchDefaults> {
  return unwrap<LaunchDefaults>(await authFetch("/api/v1/settings/launch-defaults", {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(patch),
  }));
},
```

- [ ] **Step 3: Build `web/src/routes/settings.tsx`** — a page (mirror `web/src/routes/usage.tsx` structure) with a `useQuery(["launch-defaults"], api.getLaunchDefaults)` and a `useQuery(["agent-models"], api.getAgentModels)`. Render one row per role (Judge / Extract / TOC) reusing the manifest provider/model dropdowns + a transport select (Judge/Extract: `inherit|cli|api`; TOC: `cli|api`). A "Save" `useMutation(api.updateLaunchDefaults)` that `qc.invalidateQueries({ queryKey: ["launch-defaults"] })` + `qc.invalidateQueries({ queryKey: ["agent-models"] })` on success (so the launcher's `Auto → X` transparency refreshes), with a toast. Surface a 422 error message from `ApiError`.

- [ ] **Step 4: Register route + nav.** `web/src/App.tsx`: import `SettingsPage` and add `<Route path="/settings" element={<SettingsPage />} />` inside the protected block. `web/src/components/layout.tsx`: add a `<NavItem to="/settings" icon={<Settings className="size-4" />}>Settings</NavItem>` (import `Settings` from `lucide-react`).

- [ ] **Step 5: Acceptance.**

```bash
cd web && npx tsc -p tsconfig.app.json --noEmit && npm run build
```
Expected: clean. (In-browser eyeball is part of the final acceptance, Task 10.)

- [ ] **Step 6: Commit.**

```bash
git add web/src/lib/types.ts web/src/lib/api.ts web/src/routes/settings.tsx \
        web/src/App.tsx web/src/components/layout.tsx
git commit -m "feat(launch-defaults): /settings page to edit judge/extract/TOC defaults"
```

---

## Task 9: Frontend — launcher `Auto → <resolved>` transparency

**Files:**
- Modify: `web/src/components/fleet/launcher.tsx` (fetch launch-defaults; pass resolved default into RoleAgentControls)
- Modify: `web/src/components/fleet/RoleAgentControls.tsx` (render the resolved default in the Auto placeholder)
- Acceptance: `npx tsc ... --noEmit` + `npm run build`

**Interfaces:**
- Consumes: `api.getLaunchDefaults` (Task 8); the `LaunchDefaults` type.

- [ ] **Step 1: Fetch defaults in the launcher.** Add `const defaultsQ = useQuery({ queryKey: ["launch-defaults"], queryFn: api.getLaunchDefaults });` to `launcher.tsx`. Derive, per role, the resolved-default label shown when the picker is on Auto (e.g. extract: `defaultsQ.data?.extract_provider` + `extract_model`).

- [ ] **Step 2: Extend `RoleAgentControls` props** with an optional `resolvedDefault?: { provider: string | null; model: string | null; transport: string | null }`. When `provider == null` (Auto), render the provider trigger placeholder as `Auto → {resolvedDefault.provider ?? "…"}` and, where space allows, the model as `Auto → {resolvedDefault.model}`. Keep the existing serveability greying. Do not change the value semantics — Auto still sends `null`; this is display-only.

- [ ] **Step 3: Pass `resolvedDefault`** from both RoleAgentControls render sites in `launcher.tsx:916-938` (Extract + Judge) from `defaultsQ.data`.

- [ ] **Step 4: Acceptance.** `cd web && npx tsc -p tsconfig.app.json --noEmit && npm run build`. Clean.

- [ ] **Step 5: Commit.**

```bash
git add web/src/components/fleet/launcher.tsx web/src/components/fleet/RoleAgentControls.tsx
git commit -m "feat(launch-defaults): launcher shows Auto -> resolved global default"
```

---

## Task 10: Acceptance smoke (generation-affecting gate) + finish

**Files:** none committed by the smoke itself; this is the CLAUDE.md acceptance gate + the finish checklist.

- [ ] **Step 1: Full suite + scratch-DB integration green.**

```bash
uv run python -m pytest tests/ -q
createdb -U macmini5 edu_gld_test
DATABASE_URL=postgresql+asyncpg://macmini5@localhost:5432/edu_gld_test \
  RUN_DB_INTEGRATION=1 uv run --extra dev alembic upgrade head
DATABASE_URL=postgresql+asyncpg://macmini5@localhost:5432/edu_gld_test \
  RUN_DB_INTEGRATION=1 uv run --extra dev python -m pytest tests/ -q
dropdb edu_gld_test
```

- [ ] **Step 2: Real acceptance smoke (fact over theory).** On a scratch DB at head: `PUT /settings/launch-defaults` setting the judge default to a known model; launch a job with Judge=Auto; run the pipeline (in-process, real CLI/API spawn per the acceptance-gate rule); assert the judge call's `agent_usages` row recorded **that** `model_name` — proving the UI default (not `.env`) drove the run. Write this as a scripted smoke under `scripts/` (module form `python -m scripts.<name>`), not a committed test, unless it generalizes cleanly into one.

- [ ] **Step 3: In-browser eyeball.** Start the head, open `/settings`, change the judge default, confirm the launcher's Extract/Judge pickers show `Auto → <new value>` after the query refetch.

- [ ] **Step 4: Finish (per CLAUDE.md).** Then, in order:
  1. `git fetch origin` + `git log HEAD..origin/Nggaev-v2` — if base moved, rebase onto `origin/Nggaev-v2`, re-run the suite.
  2. Worklog entry in `docs/memory/MASTER_MEMORY.md` + a row in `docs/memory/INDEX.md` (next free id — verify against the live tip at finish).
  3. Close the relevant `docs/memory/WISHLIST.md`/`ROADMAP.md` item.
  4. `git mv docs/superpowers/plans/2026-06-27-global-launch-defaults.md docs/superpowers/plans/shipped/`.
  5. De-stale `README.md`, `docs/HOW_IT_WORKS.md`, `docs/CODE_MAP.md`, `docs/DATABASE.md` (new `launch_defaults` table; `.env` model vars removed → DB row + `/settings` is the home), `docs/DEPLOY.md` (remove the `JUDGE_*`/`EXTRACT_*` env rows; note credentials/infra stay), and the worker setup runbook.
  6. Push the branch + open a PR; route to the gatekeeper (do NOT self-merge).

---

## Self-Review (completed)

**Spec coverage:** §1 storage → Task 1. §2 repo → Task 1. §3 resolve-at-launch + retire settings → Tasks 2, 3. §4 TOC → Task 5. §5 API + UI → Tasks 6, 8, 9. §6 worker/claim-gate → Task 4. §7 testing/acceptance → per-task tests + Task 10. "What gets deleted from `.env`" → Task 7. Finish checklist → Task 10 Step 4. Backfill (user-locked) → Task 1 migration.

**Type consistency:** `resolve_role_selection`/`resolve_role_transport_default` signatures match between agent_models (Task 2) and their callers (batch/jobs Task 2). `_compute_capabilities(env)` shape `{can_claude_api, can_gemini_api}` matches the claim-gate reads (Task 4). `_resolve_extract(job_p, job_m, ld)` 3-arg signature matches the pipeline caller (Task 3). `LaunchDefaults` columns match across model (Task 1), repo `_MUTABLE` (Task 1), settings API models (Task 6), and the FE type (Task 8).

**Ordering safety:** the `config.py` field deletion (Task 7) runs only after every reader is repointed (Tasks 3/4/5); Task 7 Step 1 grep-gates this. Each task ends green/independently testable.

**Placeholder scan:** no TBD/"handle errors"/"similar to" — every code/test step carries real code and exact commands.
