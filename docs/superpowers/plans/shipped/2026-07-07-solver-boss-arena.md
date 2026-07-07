# Solver → Boss-Arena Coverage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the CQ-C answer-key solver to the boss-arena phase — catching objective errors it embeds (the live G8-GEO 140°-polygon escape) — without flagging its open reasoning questions, and make the boss-arena kill-switch **operator-editable on the live `/settings` page** (no `.env` edits).

**Architecture:** Give the solver a boss-arena-aware contract addendum; add `boss-arena` to `_SOLVER_PHASES` behind a `launch_defaults.solver_boss_arena_enabled` toggle that is read live from the singleton the pipeline already loads and surfaced on `/settings` (the R21.8 solver-config pattern). Auto-regen and `solver_status` are inherited unchanged.

**Tech Stack:** Python, Alembic, SQLAlchemy, Pydantic settings API, React/TS `/settings` page.

---

## Approach & key decisions

- **The defect (verified):** the 2026-07-06 4-packet audit's only FLAG was UZ-G8-GEO 1-mavzu `19f32884` Q3 — a boss question that grades *"140° regular polygon impossible"* as correct; truth: exterior angle 40° → n = 360/40 = **9**, a nonagon exists. Both post-CQ live escapes sit in boss-arena, which `_SOLVER_PHASES` (`pipeline.py:35`) does not cover.
- **Boss-aware solver contract (not a naive add-to-tuple):** `boss-arena.md` produces **open Why/How/What questions — no marked-correct option, no answer-key field** ("open reasoning"). The generic solver `_INSTRUCTIONS` are framed around a "wrong option marked correct / wrong expected answer", a shape boss-arena lacks. So the solver gets a boss addendum: for each question check its **embedded objectively-decidable claim** (computable value / mathematical possibility / lesson-settled fact stated in Scenario·What·Feedback), flag `high` only on an objective error (the 140° case), and **never flag genuinely open/interpretive/multi-answer questions**. The existing high-conf-only regen gate is the zero-FP backstop.
- **Live-editable toggle on `/settings` (user request, supersedes the earlier env-only plan):** editing `.env` per flip is friction. The toggle becomes `launch_defaults.solver_boss_arena_enabled` (BOOLEAN NOT NULL DEFAULT true) — the same operator-editable singleton the R21.8 solver-config editing uses (worklog 0118). **Read live**, not job-stamped: `pipeline.run` already loads the singleton once per job (`_ld` at `pipeline.py:179`); the gate reads `_ld.solver_boss_arena_enabled` (threaded into `_execute_phase`), so a flip at `/settings` takes effect for every job started after it — no redeploy, no per-job stamping, zero extra DB reads. This matches the *enable-flag* semantics of the global `settings.solver_enabled` (also read live), not the per-role provider/model stamping.
- **Gate:** `settings.solver_enabled` (global env master switch, unchanged) **AND** `phase in _SOLVER_PHASES` **AND** `(phase != "boss-arena" or _ld.solver_boss_arena_enabled)`. Disabling the `/settings` toggle stops only boss-arena solving; the three proven phases keep solving.
- **Auto-regen once (user-locked):** boss-arena inherits the existing solver-regen loop (`pipeline.py:1326`), so a flagged wrong answer is fixed before it ships. **Regen-adoption risk (GK2 C3, stated explicitly):** the inherited path adopts the regenerated output *without re-judging it*. For memory-check that surface is small (a key fix); for **boss-arena the regen rewrites the entire boss fight — all questions and their Correct/Partial/Wrong feedback ladders — a materially larger un-re-judged surface.** We deliberately extend the same policy on the basis of the solver's zero-false-positive history and the `high`-confidence-only regen gate; the accepted tradeoff is a rare regen that fixes one objective key while re-rolling the whole phase unjudged.
- **Recall is probabilistic, not guaranteed:** the solver is an independent second opinion, not an oracle (CQ-C characterized ~1/3 recall on the harder error classes). This lane adds *coverage* of boss-arena's objective claims and is expected to catch the 140°-class error in the acceptance smoke; it does **not** "reliably catch" every boss error. Missed classes remain CQ-E/human-audit territory. No wording in this plan or its worklog should claim otherwise.
- **Migration required** (one boolean column; server_default backfills the seeded singleton). No `homework_jobs` change (live-read, not stamped). No solver-schema or model-resolution change (`Discrepancy`/`SolveVerdict` are phase-generic; `resolve_solver` already guards self-grade).
- **Acceptance is model-behavior → real api smoke** (not asserted from prompt): re-solve the stored `19f32884` boss output (present in `edu_copy`, 7500 chars) → must flag Q3 `high`; re-solve ≥3 clean boss outputs → `agrees` (no false positives). Bounded, single-phase, cost-reported (~$0.05–0.15).

## File structure

- **Create** `alembic/versions/0044_solver_boss_toggle.py`.
- **Modify** `app/models/launch_defaults.py` — add the column.
- **Modify** `app/repositories/launch_defaults.py:11` — `_MUTABLE`.
- **Modify** `app/api/v1/settings.py` — `LaunchDefaultsOut`/`Update`/`_serialize` + null guard.
- **Modify** `app/services/solver.py` — boss addendum + thread `phase_name`.
- **Modify** `app/services/pipeline.py` — `_SOLVER_PHASES`, read `_ld` toggle, thread into `_execute_phase`, gate.
- **Modify** `web/src/lib/types.ts`, `web/src/routes/settings.tsx` — FE toggle.
- **Create/modify** tests per task; **finish** docs.

---

### Task 0: Plan commit

- [ ] **Step 1: Commit this plan.**

```bash
git add docs/superpowers/plans/2026-07-07-solver-boss-arena.md
git commit -m "solverboss: plan — boss-arena solver + live /settings toggle"
```

---

### Task 1: Migration 0044 + model column

**Files:**
- Create: `alembic/versions/0044_solver_boss_toggle.py`
- Modify: `app/models/launch_defaults.py`
- Create: `tests/integration/test_migration_0044_boss_toggle.py`

- [ ] **Step 1: Write the migration.**

Create `alembic/versions/0044_solver_boss_toggle.py`:

```python
"""launch_defaults.solver_boss_arena_enabled — operator toggle for boss-arena solving.

Revision ID: 0044_solver_boss_toggle
Revises: 0043_solver_role_columns
"""
import sqlalchemy as sa
from alembic import op

revision = "0044_solver_boss_toggle"
down_revision = "0043_solver_role_columns"  # re-verify current head at execution
branch_labels = None
depends_on = None


def upgrade() -> None:
    # NOT NULL + server_default true backfills the seeded singleton row.
    op.add_column(
        "launch_defaults",
        sa.Column("solver_boss_arena_enabled", sa.Boolean(),
                  nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("launch_defaults", "solver_boss_arena_enabled")
```

- [ ] **Step 2: Write the real-DB migration test (RED).**

Create `tests/integration/test_migration_0044_boss_toggle.py` (mirror `tests/integration/test_migration_0043_solver.py`'s harness — `RUN_DB_INTEGRATION` skip, `command.upgrade`/`downgrade` at sync level, `asyncpg.connect` for introspection):

```python
"""Real-DB: 0044 adds launch_defaults.solver_boss_arena_enabled (BOOL NOT NULL
default true) and drops it on downgrade. Skipped unless RUN_DB_INTEGRATION=1.

Recipe:
  createdb -U macmini5 edu_boss_test
  DATABASE_URL=postgresql+asyncpg://macmini5@127.0.0.1:5432/edu_boss_test \\
    RUN_DB_INTEGRATION=1 uv run --extra dev alembic upgrade head
  DATABASE_URL=... RUN_DB_INTEGRATION=1 uv run --extra dev python -m pytest \\
    tests/integration/test_migration_0044_boss_toggle.py -q
  dropdb -U macmini5 edu_boss_test
"""
from __future__ import annotations
import os
import asyncio
import asyncpg
import pytest
from alembic import command
from alembic.config import Config

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION") != "1", reason="needs real Postgres")
REV = "0044_solver_boss_toggle"
PREV = "0043_solver_role_columns"


def _cfg() -> Config:
    c = Config("alembic.ini")
    c.set_main_option("sqlalchemy.url", os.environ["DATABASE_URL"].replace("+asyncpg", ""))
    return c


async def _col(url):
    conn = await asyncpg.connect(url)
    try:
        return await conn.fetchrow(
            "select is_nullable, column_default from information_schema.columns "
            "where table_name='launch_defaults' and column_name='solver_boss_arena_enabled'")
    finally:
        await conn.close()


def test_0044_adds_and_drops_boss_toggle():
    url = os.environ["DATABASE_URL"].replace("+asyncpg", "")
    cfg = _cfg()
    command.upgrade(cfg, REV)
    row = asyncio.run(_col(url))
    assert row is not None, "column missing after upgrade"
    assert row["is_nullable"] == "NO"
    assert "true" in (row["column_default"] or "").lower()
    # the seeded singleton got the default
    assert asyncio.run(_singleton_val(url)) is True
    command.downgrade(cfg, PREV)
    assert asyncio.run(_col(url)) is None


async def _singleton_val(url):
    conn = await asyncpg.connect(url)
    try:
        return await conn.fetchval(
            "select solver_boss_arena_enabled from launch_defaults where id=1")
    finally:
        await conn.close()
```

- [ ] **Step 3: Run RED** (scratch DB per the docstring): fails — the model/migration column doesn't exist yet.

- [ ] **Step 4: Add the model column.**

In `app/models/launch_defaults.py`, add `Boolean` and `text` to the sqlalchemy import, and after the `solver_transport` column:

```python
    solver_boss_arena_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"))
```

- [ ] **Step 5: Run GREEN** on the scratch DB — test passes.

- [ ] **Step 6: Commit.**

```bash
git add alembic/versions/0044_solver_boss_toggle.py app/models/launch_defaults.py tests/integration/test_migration_0044_boss_toggle.py
git commit -m "solverboss: mig 0044 launch_defaults.solver_boss_arena_enabled (bool, default true)"
```

---

### Task 2: Repo `_MUTABLE` + settings API

**Files:**
- Modify: `app/repositories/launch_defaults.py:11` (`_MUTABLE`)
- Modify: `app/api/v1/settings.py`
- Create: `tests/api/test_settings_boss_toggle.py`

- [ ] **Step 1: Write the failing tests.**

Create `tests/api/test_settings_boss_toggle.py` (TestClient + mocked repo, mirroring `tests/api/test_toc_retry.py`'s mock style):

```python
"""GET returns solver_boss_arena_enabled; PUT persists a bool; PUT explicit null
is a no-op (the NOT NULL column is never written null)."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import pytest
from fastapi.testclient import TestClient
from main import app
from app.auth import get_current_user

client = TestClient(app)


@pytest.fixture(autouse=True)
def _auth():
    app.dependency_overrides[get_current_user] = lambda: {"user": "t"}
    yield
    app.dependency_overrides.pop(get_current_user, None)


def _row(boss=True):
    return SimpleNamespace(
        judge_provider="gemini", judge_model="gemini-2.5-flash", judge_transport="inherit",
        solver_provider="gemini", solver_model="gemini-3.1-pro-preview", solver_transport="inherit",
        extract_provider="gemini", extract_model="gemini-2.5-flash", extract_transport="inherit",
        content_provider="gemini", content_model="gemini-3-flash-preview", content_transport="api",
        toc_transport="cli", output_language="uz", solver_boss_arena_enabled=boss)


def test_get_exposes_boss_toggle():
    with patch("app.api.v1.settings.launch_defaults_repo.get", AsyncMock(return_value=_row(True))):
        r = client.get("/api/v1/settings/launch-defaults")
    assert r.status_code == 200
    assert r.json()["solver_boss_arena_enabled"] is True


def test_put_persists_false():
    upd = AsyncMock(return_value=_row(False))
    with patch("app.api.v1.settings.launch_defaults_repo.get", AsyncMock(return_value=_row(True))), \
         patch("app.api.v1.settings.launch_defaults_repo.update", upd):
        r = client.put("/api/v1/settings/launch-defaults",
                       json={"solver_boss_arena_enabled": False})
    assert r.status_code == 200
    # the write carried the toggle
    assert upd.await_args.args[1]["solver_boss_arena_enabled"] is False


def test_put_explicit_null_is_dropped():
    upd = AsyncMock(return_value=_row(True))
    with patch("app.api.v1.settings.launch_defaults_repo.get", AsyncMock(return_value=_row(True))), \
         patch("app.api.v1.settings.launch_defaults_repo.update", upd):
        r = client.put("/api/v1/settings/launch-defaults",
                       json={"solver_boss_arena_enabled": None})
    assert r.status_code == 200
    # null = no-op: the NOT NULL column is never written null
    assert "solver_boss_arena_enabled" not in upd.await_args.args[1]
```

- [ ] **Step 2: Run RED.**

Run: `uv run python -m pytest tests/api/test_settings_boss_toggle.py -q`
Expected: FAIL — the Out model has no such field / the guard doesn't drop null.

- [ ] **Step 3: Wire the repo + API.**

In `app/repositories/launch_defaults.py`, add to `_MUTABLE` (line 11):

```python
    "solver_boss_arena_enabled",
```

In `app/api/v1/settings.py`: add `solver_boss_arena_enabled: bool` to `LaunchDefaultsOut`, `solver_boss_arena_enabled: bool | None = None` to `LaunchDefaultsUpdate`, and `solver_boss_arena_enabled=row.solver_boss_arena_enabled` to the `_serialize(...)` call. Then in `put_launch_defaults`, right after `fields = body.model_dump(exclude_unset=True)`:

```python
    # A bool toggle sent as explicit null means "no change" — drop it so the
    # NOT NULL launch_defaults column is never written null.
    if fields.get("solver_boss_arena_enabled", False) is None:
        fields.pop("solver_boss_arena_enabled")
```

(The role null-check loop is provider/model-only; the bool is not a role and is unaffected.)

- [ ] **Step 4: Run GREEN.**

Run: `uv run python -m pytest tests/api/test_settings_boss_toggle.py -q`
Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add app/repositories/launch_defaults.py app/api/v1/settings.py tests/api/test_settings_boss_toggle.py
git commit -m "solverboss: expose solver_boss_arena_enabled on /settings API (null=no-op)"
```

---

### Task 3: Boss-arena-aware solver contract

**Files:**
- Modify: `app/services/solver.py`
- Modify: `tests/services/test_solver.py`

- [ ] **Step 1: Write the failing tests.** Append to `tests/services/test_solver.py`:

```python
def test_boss_arena_prompt_carries_objective_key_guidance():
    p = solver._build_solver_prompt(
        contract="CONTRACT", phase_output_md="OUTPUT", phase_name="boss-arena")
    assert "objectively" in p.lower()
    assert "open" in p.lower()
    assert "independently SOLVES each item" in p  # generic instructions still present


def test_non_boss_prompt_has_no_boss_addendum():
    p = solver._build_solver_prompt(
        contract="C", phase_output_md="O", phase_name="memory-check")
    assert "Boss Arena phase" not in p


def test_build_solver_prompt_phase_name_optional():
    p = solver._build_solver_prompt(contract="C", phase_output_md="O")
    assert "Boss Arena phase" not in p
```

- [ ] **Step 2: Run RED.**

Run: `uv run python -m pytest tests/services/test_solver.py -q -k "boss or phase_name"`
Expected: FAIL (`_build_solver_prompt` has no `phase_name` kwarg; no boss guidance).

- [ ] **Step 3: Add the addendum + thread `phase_name`.** In `app/services/solver.py`, after `_INSTRUCTIONS`:

```python
_BOSS_ARENA_ADDENDUM = (
    "## This is a Boss Arena phase — a different shape\n"
    "Boss Arena questions are OPEN Why/How/What reasoning prompts. There is NO "
    "marked-correct option and NO written answer-key field — do NOT expect one, "
    "and do NOT flag a question for 'missing a key'.\n\n"
    "Instead, check each question for an EMBEDDED, OBJECTIVELY-DECIDABLE claim — "
    "a computable value, a mathematical truth/possibility, or a fact the lesson's "
    "concepts settle unambiguously — that the question STATES or ASSUMES as "
    "correct anywhere in its Scenario, its What/counterfactual, or its three "
    "Feedback lines (Correct/Partial/Wrong). Independently derive that claim from "
    "the lesson's concepts. Flag a discrepancy ONLY when the question asserts or "
    "assumes an objectively WRONG answer (e.g. it treats a constructible figure "
    "as impossible, or states a wrong numeric result): set `generated_key` to the "
    "answer the question assumes, `solver_answer` to the correct one, and reserve "
    "`high` for an unambiguous objective error.\n\n"
    "If a question is genuinely OPEN — interpretive, evaluative, design/opinion, "
    "or admitting several defensible answers — it has NO objective key: treat it "
    "as agreeing and do NOT flag it. Never flag phrasing, difficulty, pedagogy, "
    "hint quality, or the Why/How/What structure."
)

# Per-phase solver-contract addenda appended to _INSTRUCTIONS for phases whose
# shape differs from the standard marked-key phases. Absent phase → no addendum.
_PHASE_SOLVE_ADDENDUM = {"boss-arena": _BOSS_ARENA_ADDENDUM}
```

Replace `_build_solver_prompt` with:

```python
def _build_solver_prompt(
    *, contract: str, phase_output_md: str, phase_name: Optional[str] = None
) -> str:
    instructions = _INSTRUCTIONS
    addendum = _PHASE_SOLVE_ADDENDUM.get(phase_name or "")
    if addendum:
        instructions = f"{instructions}\n\n{addendum}"
    parts = [
        instructions,
        "\n\n## CONTRACT (the authoring instructions the output was produced from)",
        contract.strip(),
        "\n\n## OUTPUT TO CHECK (the generated phase, including its answer key)",
        phase_output_md.strip(),
        "",
    ]
    return "\n".join(parts)
```

In `solve()`, pass the phase name (the `_build_solver_prompt` call at `solver.py:104`):

```python
        solver_prompt = _build_solver_prompt(
            contract=contract, phase_output_md=phase_output_md, phase_name=phase_name)
```

- [ ] **Step 4: Run GREEN.**

Run: `uv run python -m pytest tests/services/test_solver.py -q`
Expected: PASS (new + existing).

- [ ] **Step 5: Commit.**

```bash
git add app/services/solver.py tests/services/test_solver.py
git commit -m "solverboss: boss-arena-aware solver contract (objective claims, skip open Qs)"
```

---

### Task 4: Enable boss-arena in the pipeline (live-read toggle)

**Files:**
- Modify: `app/services/pipeline.py` — `:35`, `~191`, `:527` call site, `:930` signature, `:1309` gate
- Modify: `tests/services/test_pipeline_solver.py`

- [ ] **Step 1: Write the failing tests.** In `tests/services/test_pipeline_solver.py`, add the new kwarg to `_make_kwargs` (so `_execute_phase` receives it) and append tests. In `_make_kwargs`'s returned dict add:

```python
        solver_boss_arena_enabled=True,
```

Append:

```python
async def test_boss_arena_solved_when_toggle_on(patch_io):
    patch_io.failover_outputs = [
        ("# initial boss", 100, 50, "claude"),
        ("# regenned boss", 110, 55, "claude"),
    ]
    patch_io.solve_outputs = [_mismatch(), _agree()]
    kw = _make_kwargs(phase_name="boss-arena")
    await pipeline._execute_phase(**kw)
    assert len(patch_io.solve_calls) >= 1, "boss-arena must be solved when toggle on"
    assert patch_io.solver_status == "mismatch_regen", f"got {patch_io.solver_status!r}"


async def test_boss_arena_skipped_when_toggle_off(patch_io):
    patch_io.failover_outputs = [("# initial boss", 100, 50, "claude")]
    kw = _make_kwargs(phase_name="boss-arena")
    kw["solver_boss_arena_enabled"] = False
    await pipeline._execute_phase(**kw)
    assert len(patch_io.solve_calls) == 0, "boss-arena must NOT be solved when toggle off"
    assert patch_io.solver_status is None, f"got {patch_io.solver_status!r}"


async def test_non_boss_phase_ignores_boss_toggle(patch_io):
    patch_io.failover_outputs = [("# initial output", 100, 50, "claude")]
    patch_io.solve_outputs = [_agree()]
    kw = _make_kwargs(phase_name="memory-check")
    kw["solver_boss_arena_enabled"] = False   # off, but memory-check still solves
    await pipeline._execute_phase(**kw)
    assert len(patch_io.solve_calls) == 1
    assert patch_io.solver_status == "ok"
```

- [ ] **Step 2: Run RED.**

Run: `uv run python -m pytest tests/services/test_pipeline_solver.py -q -k "boss or ignores"`
Expected: `_execute_phase` rejects the unknown `solver_boss_arena_enabled` kwarg (TypeError) → fails.

- [ ] **Step 3: Wire the pipeline.**

(a) `_SOLVER_PHASES` (line 35):

```python
_SOLVER_PHASES = ("memory-check", "practice-error-detection", "practice-rlc", "boss-arena")
```

(b) In `run()`, right after the solver overrides are resolved (near `pipeline.py:192`, after `solver_model_ov = ...`):

```python
            # Live-read boss-arena kill-switch off the already-loaded singleton
            # (operator-editable at /settings). Read once per job like the rest of
            # _ld; threaded into _execute_phase below.
            solver_boss_arena_enabled = _ld.solver_boss_arena_enabled
```

(c) The `_execute_phase` call (line 527) — add after `solver_model_ov=solver_model_ov,`:

```python
            solver_boss_arena_enabled=solver_boss_arena_enabled,
```

(d) `_execute_phase` signature (line 930) — add after `solver_model_ov: Optional[str] = None,`:

```python
    solver_boss_arena_enabled: bool = True,
```

(e) The gate (line 1309) — replace `if settings.solver_enabled and phase_name in _SOLVER_PHASES:` with:

```python
        _solver_on = (
            settings.solver_enabled
            and phase_name in _SOLVER_PHASES
            and (phase_name != "boss-arena" or solver_boss_arena_enabled)
        )
        if _solver_on:
```

- [ ] **Step 4: Run GREEN.**

Run: `uv run python -m pytest tests/services/test_pipeline_solver.py -q`
Expected: PASS (new + all existing).

- [ ] **Step 5: Commit.**

```bash
git add app/services/pipeline.py tests/services/test_pipeline_solver.py
git commit -m "solverboss: enable boss-arena solving, live-gated by the /settings toggle"
```

---

### Task 5: FE — Settings toggle

**Files:**
- Modify: `web/src/lib/types.ts:477` (`LaunchDefaults`)
- Modify: `web/src/routes/settings.tsx`

- [ ] **Step 1: Add the field to the FE type.** In `web/src/lib/types.ts`, inside `interface LaunchDefaults`, add:

```ts
  solver_boss_arena_enabled: boolean;
```

- [ ] **Step 2: State + load + save + control.** In `web/src/routes/settings.tsx`:

State (near the other solver state, ~line 145):
```tsx
  const [solverBossArenaEnabled, setSolverBossArenaEnabled] = useState(true);
```
Load (in the effect that reads `data`, near line 167):
```tsx
    setSolverBossArenaEnabled(data.solver_boss_arena_enabled ?? true);
```
Save payload (in the `save(...)` object, next to `solver_transport`, ~line 237):
```tsx
      solver_boss_arena_enabled: solverBossArenaEnabled,
```
Control — a checkbox under the Solver row's helper `<p>` (after line 358), mirroring the native checkbox idiom already used in `batch-lesson-list.tsx`:
```tsx
              <label className="ml-16 -mt-1 flex items-center gap-2 text-[0.7rem] text-white/60">
                <input
                  type="checkbox"
                  checked={solverBossArenaEnabled}
                  onChange={(e) => setSolverBossArenaEnabled(e.target.checked)}
                />
                Also solve the Boss Arena answer keys (objective questions only).
                Turn off if it gets noisy — the three practice phases keep solving.
              </label>
```

- [ ] **Step 3: Typecheck.**

Run: `cd web && npx tsc -p tsconfig.app.json --noEmit`
Expected: no errors. (`api.updateLaunchDefaults` already takes `Partial<LaunchDefaults>` — no api-client change.)

- [ ] **Step 4: Commit.**

```bash
git add web/src/lib/types.ts web/src/routes/settings.tsx
git commit -m "solverboss: /settings toggle for boss-arena solving"
```

---

### Task 6: Finish — docs de-stale, worklog, WISHLIST, plan rename

**Files:**
- Modify: `docs/HOW_IT_WORKS.md:369-370`, `docs/CODE_MAP.md:42`
- Modify: `docs/memory/MASTER_MEMORY.md`, `INDEX.md`, `WISHLIST.md`
- Rename: plan → `shipped/`

- [ ] **Step 1: `HOW_IT_WORKS.md`** (369-370): solver now also covers `boss-arena` (checking embedded objective claims, skipping open questions), gated by the `/settings` **Boss Arena solving** toggle (`launch_defaults.solver_boss_arena_enabled`, default on). State the regen-adoption caveat (a boss regen re-rolls the whole phase unjudged) and keep recall wording probabilistic (no "reliably catches").
- [ ] **Step 2: `CODE_MAP.md:42`**: `_SOLVER_PHASES` includes `boss-arena`; note the boss-aware addendum + the live-read `/settings` toggle (mig 0044).
- [ ] **Step 3: Worklog **0126** (GK2 C1 — 0124/0125 already taken)** in `MASTER_MEMORY.md` + `INDEX.md` row (note **mig 0044**). The worklog MUST carry the regen-adoption risk (C3) and the actual measured acceptance cost, in words. **After rebasing, apply the standing INDEX rule: numeric-reorder the `| 01xx` rows so 0126 lands in order.**
- [ ] **Step 4: Close `solver-boss-arena-1`** in `WISHLIST.md` (`✅ CLOSED (worklog 0126, feat/solver-boss-arena):` prefix). WISHLIST Open + INDEX were edited today (0124/0125) — resolve textual conflicts from the rebase, don't clobber those edits.
- [ ] **Step 5: Rename:** `git mv docs/superpowers/plans/2026-07-07-solver-boss-arena.md docs/superpowers/plans/shipped/`
- [ ] **Step 6: Commit** the finish (stage the listed docs + the renamed plan).

---

## Acceptance gate (real-model smoke — IN-PROCESS ONLY, run by controller)

**GK2 C2 — must NOT go through the queue.** A stale Windows fleet worker (Oliver, 192.168.1.16) is running pre-#83/#84 code and outraces the Mac server on claims (worklog 0125, `fleet-worker-version-gate-1`); any enqueued job it claims would run code that predates this feature → a false-clean result. So the smoke calls **`solver.solve(...)` directly, in-process** (no `/generate`, no batch, no queue), re-solving phase outputs already stored in `edu_copy`. The acceptance record MUST state Oliver's status at run time (still stale / patched / offline) and confirm no job was enqueued.

Bounded, DB-read via `edu_copy`; **measure and report the ACTUAL cost delta** (sum `agent_usages`/usage tokens × price — do NOT restate the ~$0.03–0.05 estimate). On the boss-aware contract:
1. **Recall (this specific error):** re-solve the stored `19f32884` boss-arena output in-process → a `high` discrepancy on the Q3 140°-polygon claim (`solver_answer` ≈ n=9 / nonagon exists). This proves coverage of the objective class; it is not a general recall guarantee.
2. **Zero false positives:** re-solve ≥3 other clean, human-audited boss outputs in-process → `agrees=True` (no `high` flags on their open questions).

If (1) fails on the resolved solver model, STOP and surface it (a capability miss like CQ-C's ~1/3 → model decision, not a prompt paper-over). If (2) shows a false positive, tighten the addendum and re-run before finishing.

## Full-suite gate
`uv run python -m pytest tests/ -q` — canonical bar WITHOUT `RUN_DB_INTEGRATION`. As of the rebase onto `3d02c5b`, worklog 0124 (`9e57927`) made the two former `test_failover_api` reds hermetic, so **the suite is expected fully green** (verify at execution). The 0044 migration test runs separately on the scratch DB.

## PR body must state
- Closes WISHLIST `solver-boss-arena-1`.
- Boss-arena solving is **operator-toggleable on `/settings`** (`launch_defaults.solver_boss_arena_enabled`, default on, live-read) — recurring cost ~+$0.03–0.05/job when enabled.
- Auto-regen on a flagged boss objective error (inherits the existing loop).
- The acceptance smoke result (Q3 caught `high`, clean boss packets agree) + cost.
- **Migration 0044** (one boolean column on `launch_defaults`; no `homework_jobs` change).
