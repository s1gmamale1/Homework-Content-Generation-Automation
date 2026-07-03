# CQ-C — Answer-key solver pass (R21.2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **⚠️ PROVISIONAL DECISIONS — confirm at the single approval gate.** The user was asked the three open decisions (phase scope / solver model / on-mismatch action) but was away-from-keyboard. This plan is drafted against the **recommended** answer to each; every one is isolated so a change is cheap. Re-confirm before execution:
> 1. **Phase scope:** `memory-check` + `practice-error-detection` + `practice-rlc` (the key-bearing three). Boss-arena **dropped** (open-ended reasoning, no written key to diff against → its correctness belongs to the judge + CQ-A boundary rule, not a solver-diff); `practice-rlc` **added** (computable key; had a real wrong key in the audit — `8f734563` x=5 → packet said 21/100, truth 7/40). Isolated in the one constant `_SOLVER_PHASES` — swapping boss-arena in/out is a one-line change.
> 2. **Solver model:** a **dedicated frontier solver role + self-grade guard** (new `solver_provider/model/transport` columns mirroring the judge). A solver must out-reason the generator or it just reproduces the same wrong answer, so it defaults to a frontier reasoning model and is **never** the same model as the generator.
> 3. **On mismatch:** **auto-regen once** (mirror the judge's regen-once), driven by a **conservative** solver that flags only unambiguous, high-confidence key errors (the `validate_toc` false-positive lesson — do not over-escalate). Recorded in a new `phase_outputs.solver_status` column.
> If any answer changes: #1 → edit `_SOLVER_PHASES` (+ boss-arena needs a different verdict shape, see note in Approach); #2 → drop Tasks 4/2-partial if reusing judge tiers; #3 → warn-only drops the whole regen machinery + `solver_status` + `max_solve_regens` (Tasks collapse to solver.py + a warning-append).

**Goal:** Ship R21.2 — an independent LLM "solver" that re-solves each key-bearing phase's items, diffs its answer against the generated key, and regenerates the phase once on a clear mismatch. This is the only fix for the audit's most damaging defect class — *a correct student graded wrong* (3/5 packets: false symmetry-exclusivity `263d99c5`; denied second sign error `8f734563`; the textbook's own 4-step list marked wrong `8f734563`) — which the judge provably cannot see because it grades prompt-contract + fidelity and never solves the problems.

**Architecture:** A new role that **clones the judge**. New `app/services/solver.py` exposes `solve(...) -> SolveOutcome`, making ONE structured `agent.run_phase(schema=SolveVerdict, phase_name="__solver__", operation="solve:<phase>", transport=…)` call — the exact pattern `phase_judge.judge` uses. It is wired into `pipeline._execute_phase` right after the judge/regen block (so it solves the *final, judge-approved* markdown), gated on `phase_name in _SOLVER_PHASES and settings.solver_enabled`. On a high-confidence mismatch it runs the judge's own regen shape (`_run_with_failover(_make_run(regen_prompt))`) once, feeding the discrepancy as a correction addendum, then re-solves. Solver config lives in new `solver_*` per-role columns on `launch_defaults`/`homework_jobs`/`batches` (mirroring the judge role exactly), model resolved by a new `model_tiers.resolve_solver` with the same self-grade guard; outcome recorded in a new `phase_outputs.solver_status` column. Cost is recorded automatically by `agent.run_phase`'s `agent_usages` write (tagged `solve:<phase>`).

**Tech Stack:** FastAPI + SQLAlchemy async (Postgres), Alembic, pytest / pytest-asyncio, gemini/claude over the SDK (`transport=api`).

## Approach & key decisions

- **Solver = a judge-shaped second opinion, not new plumbing.** Everything reuses proven machinery: structured validated call via `agent.run_phase(schema=…)` (retries once on schema-parse fail); degrade-never-blocks error contract (any exception → `available=False`, job unaffected) **except** api-transport auth errors, which re-raise (job-level failure — mirrors `phase_judge.py:248`); per-role provider/model/transport columns identical in shape to the judge's; automatic `agent_usages` cost attribution via a distinct `operation="solve:<phase>"`. Rejected building a bespoke path — the judge template is battle-tested and keeps solver observability/cost identical to the judge's.
- **Conservative solver, high-confidence-only regen (the `validate_toc` lesson).** `SolveVerdict.discrepancies[].confidence ∈ {low, medium, high}`; **only a `high`-confidence discrepancy triggers a regen.** The solver prompt states explicitly: flag ONLY unambiguous, demonstrable key errors (a wrong marked-correct option, a numerically wrong expected answer, a wrong "correct version"); do NOT flag phrasing, ordering, accepted-alternative wording, or stylistic differences. This is a direct carry-over from the TOC-validator false positive (one missing auxiliary line wrongly escalated a clean 69-entry TOC) — a solver that over-escalates would burn paid regens on correct packets and could *replace a right key with a wrong one*.
- **Solver judges under CQ-A's boundary.** The solver receives the same `lesson_context` that now (post-CQ-A) carries the `CURRICULUM BOUNDARY:` note. A key that is "correct" only because it reaches into the next lesson (the audit's `asimptota` boss item, the parallelogram second-criterion) must be flagged — the solver prompt says: solve using ONLY the current lesson's concepts; if the key's correctness depends on next-lesson material, that is a discrepancy. **This is why CQ-C serializes after CQ-A** (REMEDIATION_CLUSTERS.md:247).
- **Model must out-reason the generator (self-grade guard).** `model_tiers.resolve_solver` mirrors `resolve_judge`: an explicit `solver_provider/model` override wins EXCEPT when it resolves to the same `(provider, model)` as the generator, which is hard-swapped to a **fixed generator-aware frontier peer** via the existing `_self_fallback` (`model_tiers.py:64` — the alternating `("claude","claude-opus-4-7")` / `("gemini","gemini-3.1-pro-preview")` pair, **not** tier-arithmetic). A flash solver grading flash output reproduces flash's own errors and catches nothing. In practice the launch-defaults seed (below) supplies a concrete non-null `solver_model`, so the null-override `_self_fallback` path is the rare fallback, not the norm.
- **Default solver = `gemini-3.1-pro-preview` over Vertex (cost + fleet-auth).** `launch_defaults.solver_* ` is **seeded** to `gemini` / `gemini-3.1-pro-preview` / `inherit` (Task 2 data-seed + Task 5 code default). Rationale: (a) an opus default costs ~$0.22/job (3 calls × ~10K in / 1K out at $5/$25 = ~+50% on a $0.45/hw basis) vs ~$0.10/job for 3.1-pro; (b) it stays Vertex-native so the all-Vertex fleet needs no `ANTHROPIC_API_KEY` on the common path; (c) the self-grade guard still swaps to the claude peer only when the generator itself is `gemini-3.1-pro-preview` (rare — content is 2.5-pro/flash). That residual claude path — plus any explicit `solver=claude/api` per-job override — is what the R1 capability/claim-gate (Task 8) covers. The smoke (Task 9) reports **actual** token counts → a verified $/job goes in the PR body.
- **Phase scope = the key-bearing computational phases.** `memory-check` (marks one A–D correct / states a fill-blank expected answer) and `practice-error-detection` (states "The correct version" + which block is broken) and `practice-rlc` (computable numeric answers) each embed a re-derivable key. **boss-arena is excluded**: it emits no written answer (open reasoning; only Feedback lines describe a good answer), so there is nothing to diff — its correctness is a judge/boundary concern, not a solver-diff. Scope is the single constant `_SOLVER_PHASES = ("memory-check", "practice-error-detection", "practice-rlc")`. *(If the gate re-adds boss-arena: it needs a different verdict — "does the Correct-feedback describe a genuinely correct, in-boundary answer" — a distinct prompt branch, not the diff path; flagged, not silently folded.)*
- **Division of labor vs CQ-B (verified, no overlap).** CQ-B's plan explicitly states the error-detection semantic defects (wrong `+1` sign) are "un-catchable without re-solving the algebra … CQ-C's job" and does deterministic format/count only, warn-only, into `validation_warnings`. CQ-C does semantic correctness via LLM solve + regen, into a **separate** `solver_status` column. Both touch the same `phase_outputs` row through different channels — no functional or column collision.
- **Verified load-bearing facts (against real code):** the judge is invoked from `pipeline._execute_phase`'s `if phase_name != "extract":` block, resolves its model via `model_tiers.resolve_judge(produced_by, …)` and transport via `resolve_role_transport(job.judge_transport, transport)`, calls `phase_judge.judge(..., schema=Verdict)`, and regenerates in a `for _ in range(settings.max_judge_regens):` loop feeding `base_phase_prompt + outcome.feedback` through `_run_with_failover(_make_run(regen_prompt))`; the final `set_status(..., "done", …, judge_status=…)` write persists the phase. `agent.run_phase(schema=…)` returns `PhaseResult.parsed` (validated Pydantic) and writes an `agent_usages` row on every attempt with `auth_mode=transport` and the given `operation`. Cost is computed on read by `pricing.cost_usd` keyed by `(provider, model)`; the solver's model must be in `PRICE_MAP` (all current claude/gemini priced models are). Per-role column shape (judge): `launch_defaults.judge_provider/model/transport` (all nullable) + `homework_jobs`/`batches` `judge_transport` (NOT NULL, server_default `'inherit'`, CHECK IN `('cli','api','inherit')`) + `judge_provider/model` (nullable). Migration head is `0042_books_toc_validation` (CQ-A and CQ-B add **no** migration). The worker capability layer (`worker._compute_capabilities`) + the claim-gate SQL predicate (`app/repositories/jobs.py:299-338`) compute per-role api-readiness at startup and AND each job's *resolved* per-role transport against them — the judge role has both; the solver role must too (Task 8 / R1).
- **Accepted risks (recorded, not defects):** (a) a solver-triggered regen output is **adopted without re-judging** it through `phase_judge` — deliberate, to avoid judge↔solver regen ping-pong; the regen still carries the original judge's approval of the phase's contract, and the solver only corrects the key. (b) The audit's boss-arena **wrong-feedback** class (`263d99c5` Q3 declaring one side of a two-sides-both-right debate the winner) stays **uncovered** by CQ-A + CQ-C combined — boss-arena is (correctly) excluded here because it emits no written key to diff; this residue gets a ROADMAP follow-up line at Finish. (c) The Task-9 smoke **reads** `edu_copy` (audited outputs) read-only — no writes to the production DB.

## Global Constraints

- **Serialize AFTER CQ-A, CQ-B, AND CQ-D (R3).** Cut `cq-c-key-solver` off `origin/Nggaev-v2` **only once `[CQ-A]` (0109), `[CQ-B]` (0110), AND `[CQ-D]` are all merged** — CQ-A/B edit the same `pipeline._execute_phase` region, and CQ-D also touches `pipeline.py` (+56) plus the append-only docs. Branch off the **post-CQ-D** base. Worktree `../HCGA-cqc`. **Commit prefix `cqc:`.** Worklog ID **0112** (re-verify next-free at finish — CQ-A=0109, CQ-B=0110, CQ-D likely 0111, so confirm). When resolving the `docs/memory/ROADMAP.md` R21 list, **hand-merge**: R21 item 2 is CQ-C's line (close it), item 6 is CQ-D's — do not duplicate or clobber CQ-D's close.
- **Commit the plan as the FIRST commit on `cq-c-key-solver` at cut (R6).** This file is untracked today; the moment the branch is cut, `git add` this plan and commit it (`cqc: plan — answer-key solver pass (R21.2)`) so the gate reads it at a committed location.
- **Line numbers in this plan are PLACEHOLDERS (pre-CQ-A/B/D).** After branching off the post-A+B+D base, re-anchor every edit on the named symbol (`_execute_phase`, the judge-regen block, the `resolve_role_transport(job.judge_transport, …)` line, the final `phase_repo.set_status(..., "done", …)` write, CQ-B's lint block, the claim-gate predicate `jobs.py:299-338`), NOT the line number.
- **Migration number:** `0043_solver_role_columns` off `0042_books_toc_validation` today — **re-derive at execution** (CQ-A/B/D add none per their plans, but another session might); chain onto the literal current-head revision string you find.
- **Transport for the acceptance smoke: `transport=api` (SDK) only** (CLAUDE.md standing decision 2026-07-01). No cli smoke.
- **Stage only the files each task lists** — never `git add -A` (other sessions commit to this branch, incl. `web/`).
- **The solver must NEVER fail a job.** Every solver/regen path is defensively wrapped; only an api-transport auth error re-raises (mirrors the judge). Disabled (`solver_enabled=False`) or non-target phase → `solver_status` stays NULL, zero solver calls.
- One commit per task; each task ends TDD-green with `uv run python -m pytest <its files> -q`.

## File Structure

- **Create** `app/schemas/solver.py` — `SolveVerdict` + `Discrepancy` (Task 1).
- **Create** `alembic/versions/0043_solver_role_columns.py` (Task 2).
- **Create** `app/services/solver.py` — `solve()`, `SolveOutcome`, prompt builder, instructions (Task 6).
- **Create** `scripts/cqc_solver_smoke.py` — real-model acceptance (Task 9).
- **Modify** `app/config.py` (Task 1), `app/models/{launch_defaults,homework_job,batch,phase_output}.py` (Task 3), `app/services/model_tiers.py` (Task 4), `app/services/agent_models.py` + the launch-stamping path (Task 5), `app/services/pipeline.py` + `app/repositories/phase_outputs.py` (Task 7), `app/services/worker.py` + `app/repositories/jobs.py` (Task 8 — solver capability/claim-gate, R1).
- **Modify (Finish)** `docs/memory/{MASTER_MEMORY,INDEX,ROADMAP,REMEDIATION_CLUSTERS}.md`, `docs/HOW_IT_WORKS.md`, `docs/CODE_MAP.md`.

---

### Task 1: `SolveVerdict` schema + config knobs

**Files:**
- Create: `app/schemas/solver.py`
- Modify: `app/config.py` (add `solver_enabled`, `max_solve_regens` near `max_judge_regens`, ~line 152)
- Modify: `app/schemas/__init__.py` (export `SolveVerdict`, `Discrepancy`) — only if the package re-exports schemas (verify).
- Test: `tests/schemas/test_solver_schema.py` (new)

**Interfaces:**
- Produces: `Discrepancy{item: str, generated_key: str, solver_answer: str, explanation: str, confidence: Literal["low","medium","high"]}` and `SolveVerdict{agrees: bool, discrepancies: list[Discrepancy]}`. Consumed by Task 6.
- Produces: `settings.solver_enabled: bool = True`, `settings.max_solve_regens: int = 1`. Consumed by Task 7.

- [ ] **Step 1: Write the failing test**

```python
# tests/schemas/test_solver_schema.py
import pytest
from pydantic import ValidationError
from app.schemas.solver import SolveVerdict, Discrepancy


def test_agrees_verdict_has_no_discrepancies():
    v = SolveVerdict(agrees=True, discrepancies=[])
    assert v.agrees is True and v.discrepancies == []


def test_discrepancy_roundtrip_and_confidence_literal():
    d = Discrepancy(item="card 9", generated_key="Oy option marked xato",
                    solver_answer="Oy symmetry is TRUE", explanation="both hold; origin composes",
                    confidence="high")
    v = SolveVerdict(agrees=False, discrepancies=[d])
    assert v.model_validate_json(v.model_dump_json()).discrepancies[0].confidence == "high"


def test_confidence_rejects_unknown_value():
    with pytest.raises(ValidationError):
        Discrepancy(item="x", generated_key="a", solver_answer="b",
                    explanation="c", confidence="certain")


def test_config_exposes_solver_knobs():
    from app.config import settings
    assert isinstance(settings.solver_enabled, bool)
    assert isinstance(settings.max_solve_regens, int) and settings.max_solve_regens >= 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/schemas/test_solver_schema.py -q`
Expected: FAIL — `ModuleNotFoundError: app.schemas.solver`.

- [ ] **Step 3: Write minimal implementation**

```python
# app/schemas/solver.py
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field


class Discrepancy(BaseModel):
    """One place the independently-solved answer disagrees with the generated key.
    confidence gates action: ONLY `high` triggers a regen (conservative — see the
    validate_toc false-positive lesson). low/medium are advisory."""
    item: str = Field(description="which item/question/block the disagreement is about")
    generated_key: str = Field(description="what the phase's key claims is correct")
    solver_answer: str = Field(description="what independent solving gives")
    explanation: str = Field(description="why the key is wrong, briefly")
    confidence: Literal["low", "medium", "high"]


class SolveVerdict(BaseModel):
    agrees: bool = Field(description="True iff every item's key is correct")
    discrepancies: list[Discrepancy] = Field(default_factory=list)
```

In `app/config.py`, beside `max_judge_regens` (~line 152):

```python
    solver_enabled: bool = True
    max_solve_regens: int = 1
```

If `app/schemas/__init__.py` re-exports schema classes, add `from .solver import SolveVerdict, Discrepancy` (verify the pattern; skip if schemas aren't re-exported).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/schemas/test_solver_schema.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add app/schemas/solver.py app/config.py tests/schemas/test_solver_schema.py
# + app/schemas/__init__.py only if you edited it
git commit -m "cqc: SolveVerdict/Discrepancy schema + solver_enabled/max_solve_regens config"
```

---

### Task 2: migration `0043_solver_role_columns`

**Files:**
- Create: `alembic/versions/0043_solver_role_columns.py` (re-derive number/down_revision at execution)
- Test: `tests/integration/test_migration_0043_solver.py` (new; DB-gated)

> **⚠️ CORRECTION (applied at execution):** the Step-1 test code below uses sync `create_engine`, which this **asyncpg-only** codebase has no driver for (`env.py` overrides `sqlalchemy.url` from `settings.database_url`; there is no psycopg2/psycopg dependency). The test as BUILT keeps `alembic command.upgrade/downgrade` (they run async via `env.py`) but does its schema introspection with **`asyncpg.connect()`** (mirroring `tests/migrations/test_0041_sa_keys.py`'s async convention), and additionally asserts the CHECK-constraint definitions via `pg_get_constraintdef` + the R2 seed value. Do NOT use sync `create_engine` here.

**Interfaces:** adds columns consumed by Task 3 (models) and Task 5 (stamping).

Columns (mirror the judge role exactly):
- `launch_defaults`: `solver_provider VARCHAR(32) NULL`, `solver_model VARCHAR(128) NULL`, `solver_transport VARCHAR(16) NULL`.
- `homework_jobs`: `solver_transport VARCHAR(16) NOT NULL DEFAULT 'inherit'` + CHECK `ck_homework_jobs_solver_transport` (`solver_transport IN ('cli','api','inherit')`); `solver_provider VARCHAR(32) NULL`; `solver_model VARCHAR(128) NULL`.
- `batches`: same three + CHECK `ck_batches_solver_transport`.
- `phase_outputs`: `solver_status VARCHAR(24) NULL` + CHECK `ck_phase_outputs_solver_status` (`solver_status IS NULL OR solver_status IN ('ok','mismatch_regen','mismatch_shipped','mismatch_regen_failed','unavailable','refused')`).

- [ ] **Step 1: Write the failing test** — a real upgrade→assert→downgrade→assert test on the scratch DB (pattern from the existing migration tests; restore-to-head in `finally` so it never strands the shared scratch DB — the 0039 lesson).

```python
# tests/integration/test_migration_0043_solver.py
import os
import pytest
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION") != "1",
    reason="requires RUN_DB_INTEGRATION=1 + DATABASE_URL",
)
REV = "0043_solver_role_columns"
PREV = "0042_books_toc_validation"  # re-verify current head at execution


def _cfg():
    from alembic.config import Config
    c = Config("alembic.ini")
    c.set_main_option("sqlalchemy.url",
                      os.environ["DATABASE_URL"].replace("+asyncpg", ""))
    return c


def _sync_url():
    return os.environ["DATABASE_URL"].replace("+asyncpg", "")


def test_0043_adds_and_drops_solver_columns():
    from alembic import command
    cfg = _cfg()
    eng = create_engine(_sync_url())
    try:
        command.upgrade(cfg, REV)
        with eng.connect() as c:
            cols = lambda t: {r[0] for r in c.execute(text(
                "select column_name from information_schema.columns where table_name=:t"), {"t": t})}
            assert {"solver_provider", "solver_model", "solver_transport"} <= cols("launch_defaults")
            assert {"solver_provider", "solver_model", "solver_transport"} <= cols("homework_jobs")
            assert {"solver_provider", "solver_model", "solver_transport"} <= cols("batches")
            assert "solver_status" in cols("phase_outputs")
            # NOT NULL + default on the job transport
            r = c.execute(text("select column_default, is_nullable from information_schema.columns "
                               "where table_name='homework_jobs' and column_name='solver_transport'")).one()
            assert "inherit" in (r[0] or "") and r[1] == "NO"
        command.downgrade(cfg, PREV)
        with eng.connect() as c:
            cols = {r[0] for r in c.execute(text(
                "select column_name from information_schema.columns where table_name='homework_jobs'"))}
            assert "solver_transport" not in cols
    finally:
        command.upgrade(cfg, "head")   # never strand the shared scratch DB
        eng.dispose()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `RUN_DB_INTEGRATION=1 DATABASE_URL=postgresql+asyncpg://macmini5@127.0.0.1:5432/edu_scratch_cqc uv run --extra dev python -m pytest tests/integration/test_migration_0043_solver.py -q`
Expected: FAIL — `Can't locate revision '0043_solver_role_columns'`. (Create the scratch DB if needed: `createdb -U macmini5 edu_scratch_cqc` then `DATABASE_URL=… RUN_DB_INTEGRATION=1 uv run --extra dev alembic upgrade head`. Pin `127.0.0.1`, not `localhost`.)

- [ ] **Step 3: Write the migration** — additive + nullable (online-safe), precedent `0027_per_role_provider_model.py` + the CHECK pattern from `0028_enum_check_constraints.py`:

```python
"""solver role columns + phase_outputs.solver_status (CQ-C / R21.2)"""
from alembic import op
import sqlalchemy as sa

revision = "0043_solver_role_columns"
down_revision = "0042_books_toc_validation"  # re-verify current head at execution
branch_labels = None
depends_on = None

_TXN = ("cli", "api", "inherit")
_STATUS = ("ok", "mismatch_regen", "mismatch_shipped", "mismatch_regen_failed",
           "unavailable", "refused")


def upgrade() -> None:
    op.add_column("launch_defaults", sa.Column("solver_provider", sa.String(32), nullable=True))
    op.add_column("launch_defaults", sa.Column("solver_model", sa.String(128), nullable=True))
    op.add_column("launch_defaults", sa.Column("solver_transport", sa.String(16), nullable=True))
    for tbl in ("homework_jobs", "batches"):
        op.add_column(tbl, sa.Column("solver_transport", sa.String(16),
                                     nullable=False, server_default="inherit"))
        op.add_column(tbl, sa.Column("solver_provider", sa.String(32), nullable=True))
        op.add_column(tbl, sa.Column("solver_model", sa.String(128), nullable=True))
        op.create_check_constraint(
            f"ck_{tbl}_solver_transport", tbl,
            "solver_transport IN " + str(_TXN))
    op.add_column("phase_outputs", sa.Column("solver_status", sa.String(24), nullable=True))
    op.create_check_constraint(
        "ck_phase_outputs_solver_status", "phase_outputs",
        "solver_status IS NULL OR solver_status IN " + str(_STATUS))
    # R2: seed the singleton launch_defaults row so the fleet default is the
    # cheap, Vertex-native frontier solver (no ANTHROPIC key on the common path).
    # No-op if the row doesn't exist yet — the app's ensure-defaults path (Task 5)
    # supplies the same values on first create.
    op.execute(
        "UPDATE launch_defaults SET solver_provider='gemini', "
        "solver_model='gemini-3.1-pro-preview', solver_transport='inherit' "
        "WHERE solver_provider IS NULL")


def downgrade() -> None:
    op.drop_constraint("ck_phase_outputs_solver_status", "phase_outputs")
    op.drop_column("phase_outputs", "solver_status")
    for tbl in ("homework_jobs", "batches"):
        op.drop_constraint(f"ck_{tbl}_solver_transport", tbl)
        op.drop_column(tbl, "solver_model")
        op.drop_column(tbl, "solver_provider")
        op.drop_column(tbl, "solver_transport")
    for col in ("solver_transport", "solver_model", "solver_provider"):
        op.drop_column("launch_defaults", col)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `RUN_DB_INTEGRATION=1 DATABASE_URL=postgresql+asyncpg://macmini5@127.0.0.1:5432/edu_scratch_cqc uv run --extra dev python -m pytest tests/integration/test_migration_0043_solver.py -q`
Expected: PASS (1 passed), scratch DB left at head.

- [ ] **Step 5: Commit**

```bash
git add alembic/versions/0043_solver_role_columns.py tests/integration/test_migration_0043_solver.py
git commit -m "cqc: migration 0043 — solver_* role columns + phase_outputs.solver_status"
```

---

### Task 3: SQLAlchemy model columns for the solver role

**Files:**
- Modify: `app/models/launch_defaults.py` (3 nullable columns beside `judge_*`)
- Modify: `app/models/homework_job.py` (`solver_transport` NOT NULL default `'inherit'` + `solver_provider/model` + CHECK in `__table_args__`)
- Modify: `app/models/batch.py` (same three + CHECK)
- Modify: `app/models/phase_output.py` (`solver_status` nullable)
- Test: `tests/models/test_solver_columns.py` (new — mirror any existing judge-column model test)

**Interfaces:** ORM attributes consumed by Tasks 5 & 7.

- [ ] **Step 1: Write the failing test**

```python
# tests/models/test_solver_columns.py
from app.models.homework_job import HomeworkJob
from app.models.batch import Batch
from app.models.launch_defaults import LaunchDefaults
from app.models.phase_output import PhaseOutput


def test_job_and_batch_have_solver_role_columns():
    for M in (HomeworkJob, Batch):
        cols = M.__table__.c
        assert "solver_transport" in cols and "solver_provider" in cols and "solver_model" in cols
        assert cols["solver_transport"].nullable is False
        assert cols["solver_transport"].server_default is not None


def test_launch_defaults_and_phase_output_columns():
    assert {"solver_provider", "solver_model", "solver_transport"} <= set(LaunchDefaults.__table__.c.keys())
    assert "solver_status" in PhaseOutput.__table__.c
    assert PhaseOutput.__table__.c["solver_status"].nullable is True
```

- [ ] **Step 2: Run** `uv run python -m pytest tests/models/test_solver_columns.py -q` → FAIL (KeyError on `solver_transport`).

- [ ] **Step 3: Add the columns**, copying the judge lines in each file verbatim and renaming `judge_`→`solver_` (transport CHECK included). E.g. `homework_job.py`:

```python
    solver_transport: Mapped[str] = mapped_column(String(16), nullable=False, server_default="inherit")
    solver_provider: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    solver_model: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
```

and in `__table_args__` add `CheckConstraint("solver_transport IN ('cli','api','inherit')", name="ck_homework_jobs_solver_transport")` (mirror `ck_homework_jobs_judge_transport`). `batch.py` the same with `ck_batches_solver_transport`. `phase_output.py`: `solver_status: Mapped[Optional[str]] = mapped_column(String(24), nullable=True)`. `launch_defaults.py`: three nullable columns beside `judge_provider/model/transport`.

- [ ] **Step 4: Run** `uv run python -m pytest tests/models/test_solver_columns.py -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add app/models/launch_defaults.py app/models/homework_job.py app/models/batch.py app/models/phase_output.py tests/models/test_solver_columns.py
git commit -m "cqc: ORM solver_* role columns + phase_outputs.solver_status"
```

---

### Task 4: `model_tiers.resolve_solver` (self-grade guard)

**Files:**
- Modify: `app/services/model_tiers.py` (add `resolve_solver` mirroring `resolve_judge`, ~line 90)
- Test: `tests/services/test_model_tiers_solver.py` (new — mirror `test_model_tiers` judge cases)

**Interfaces:**
- Produces: `def resolve_solver(gen_provider: str, gen_model: str|None, solver_provider_ov: str|None, solver_model_ov: str|None) -> tuple[str, str|None]` — explicit override wins EXCEPT a self-grade (resolves to the same `(provider, model)` as the generator after `default_model`), which is swapped to the **fixed generator-aware frontier peer** via `_self_fallback` (the alternating claude-opus / gemini-3.1-pro pair — NOT tier-arithmetic). A null override likewise routes through `_self_fallback` to a non-self frontier peer (in practice the launch-defaults seed makes null rare). Consumed by Task 7. **Note:** verify against `resolve_judge`'s actual null-path behavior at execution and keep this description matching the code.

- [ ] **Step 1: Write the failing test**

```python
# tests/services/test_model_tiers_solver.py
from app.services import model_tiers as mt


def test_explicit_override_is_honored_when_not_self():
    p, m = mt.resolve_solver("gemini", "gemini-2.5-flash", "claude", "claude-opus-4-7")
    assert (p, m) == ("claude", "claude-opus-4-7")


def test_self_grade_is_swapped_to_a_frontier_peer():
    # solver override == generator → must NOT be allowed to grade itself
    p, m = mt.resolve_solver("gemini", "gemini-2.5-flash", "gemini", "gemini-2.5-flash")
    assert (p, m) != ("gemini", "gemini-2.5-flash")


def test_null_override_resolves_a_non_self_frontier_peer():
    p, m = mt.resolve_solver("gemini", "gemini-2.5-flash", None, None)
    assert p and (p, m) != ("gemini", "gemini-2.5-flash")
```

- [ ] **Step 2: Run** `uv run python -m pytest tests/services/test_model_tiers_solver.py -q` → FAIL (no `resolve_solver`).

- [ ] **Step 3: Implement** — the cleanest form is to reuse the judge's resolution (a solver has the identical requirement: a non-self generator-aware frontier peer via `_self_fallback`). Extract the judge body into a shared helper if it isn't already, and expose `resolve_solver` as a thin alias with solver-named params:

```python
def resolve_solver(gen_provider, gen_model, solver_provider_ov, solver_model_ov):
    """Resolve the solver (provider, model): an explicit override wins unless it
    would let the generator's own model re-solve its own key (self-grade), which is
    swapped to a generator-aware frontier peer. Identical policy to resolve_judge —
    a solver, like a judge, must out-reason the producer. See _self_fallback."""
    return resolve_judge(gen_provider, gen_model, solver_provider_ov, solver_model_ov)
```

*(If `resolve_judge` carries judge-only semantics, copy its body verbatim under the new name instead of aliasing — the reviewer will confirm no judge-specific coupling leaks in.)*

- [ ] **Step 4: Run** `uv run python -m pytest tests/services/test_model_tiers_solver.py tests/services/test_model_tiers.py -q` → PASS (new + existing judge tests).

- [ ] **Step 5: Commit**

```bash
git add app/services/model_tiers.py tests/services/test_model_tiers_solver.py
git commit -m "cqc: model_tiers.resolve_solver with self-grade guard (never solves against generator)"
```

---

### Task 5: launch-time stamping of solver_* onto job/batch

**Files (the judge-role mirror — VERIFIED anchor map at execution tip):**
- Modify: `app/schemas/job.py` — add `solver_transport: str = "inherit"`, `solver_provider: Optional[str] = None`, `solver_model: Optional[str] = None` to BOTH request bodies (single-job ~L37-42 and batch ~L58-65), beside `judge_*`.
- Modify: `app/repositories/jobs.py` `create()` (L27) — add the three `solver_*` params (mirror judge L40/45/56/71) and stamp onto `HomeworkJob`.
- Modify: `app/repositories/batches.py` `get_or_create_for_book()` (L13) — add the three `solver_*` params (mirror judge L24/27-28/50/53-54) and stamp onto the batch row.
- Modify: `app/api/v1/jobs.py` — resolve `res_solver_provider,res_solver_model = resolve_role_selection(body.solver_provider, body.solver_model, ld.solver_provider, ld.solver_model)` + `res_solver_transport = resolve_role_transport_default(body.solver_transport, ld.solver_transport)` (mirror L253-256), pass all three into `jobs_repo.create(...)` (mirror L275-290), and add the transport/combo validation (mirror L148-149, L186-187).
- Modify: `app/api/v1/batch.py` — same resolve (mirror L217-223), pass into `batches_repo.get_or_create_for_book(...)` (L274-278) AND the per-section `jobs_repo.create(...)` (L333-343), add to the batch response dict (L100-105) + validation (L161-162, L202-203).
- Test: `tests/services/test_launch_stamps_solver.py` (new; DB-gated — mirror the existing judge-stamping launch test, find it via `grep -rln "judge_transport" tests`).
- **NOTE — no `agent_models.py` change and no code default needed:** `resolve_role_transport`/`resolve_role_selection`/`resolve_role_transport_default` are already role-generic (reused as-is). `launch_defaults` is **migration-seeded** (the repo raises if the row is missing — no app-create path), and migration `0043`'s `UPDATE … WHERE solver_provider IS NULL` runs after the row-creating `0039`, so fresh installs are covered by the migration alone. The R2 "code default" is therefore dropped.

**Interfaces:** ensures a launched job carries `solver_provider/model/transport` from `launch_defaults` (or `'inherit'`/NULL defaults), so Task 7's pipeline resolution reads real values.

- [ ] **Step 1: Write the failing test** — assert that when `launch_defaults` has `solver_provider='claude'`, `solver_transport='api'`, a job/batch created through the launch path carries those values (mirror the existing judge-stamping test — find it via `grep -rln "judge_transport" tests`).

- [ ] **Step 2: Run** the new test → FAIL (job's `solver_provider` is NULL / `solver_transport` defaulted, not copied from defaults).

- [ ] **Step 3: Implement** — at each anchor above, add the parallel `solver_*` handling by mirroring the judge line-for-line (same null/inherit handling, same helpers). Do NOT introduce a new copy mechanism. (No `agent_models.py` change, no code default — see the NOTE above.)

- [ ] **Step 4: Run** the new test + `uv run python -m pytest tests/api -q` (or the launch/batch test files touched) → PASS.

- [ ] **Step 5: Commit**

```bash
git add app/schemas/job.py app/repositories/jobs.py app/repositories/batches.py app/api/v1/jobs.py app/api/v1/batch.py tests/services/test_launch_stamps_solver.py
git commit -m "cqc: stamp solver_* from launch_defaults onto job/batch at launch (mirrors judge)"
```

---

### Task 6: `app/services/solver.py` — the solve() second opinion

**Files:**
- Create: `app/services/solver.py`
- Test: `tests/services/test_solver.py` (new; hermetic — stub `agent.run_phase`)

**Interfaces:**
- Produces: `SolveOutcome` dataclass `{available: bool, agrees: bool, warnings: list[str], feedback: str, has_mismatch: bool, refused: bool}` and
  `async def solve(*, subject, phase_name, phase_output_md, lesson_context, prior_outputs, output_language, solver_provider, solver_model, transport, homework_job_id, phase_output_id, contract_override=None) -> SolveOutcome`.
- `has_mismatch` (the regen trigger) = `available and not agrees and any(d.confidence == "high" for d in verdict.discrepancies)`. `feedback` = a "## Fix these answer-key errors" addendum listing each high-confidence discrepancy (`_serialize`), fed to regen by Task 7. Consumed by Task 7.

**Design (clone `phase_judge.py`):**
- `_INSTRUCTIONS`: "You are an expert who independently SOLVES each item, then checks the provided answer key. Solve using ONLY the current lesson's concepts (respect the CURRICULUM BOUNDARY note in the lesson context — if a key is 'correct' only by using next-lesson material, that is a discrepancy). Report a discrepancy ONLY when the key is demonstrably wrong: a wrong option marked correct, a numerically/logically wrong expected answer, a wrong 'correct version', a mis-identified broken block. Do NOT flag phrasing, ordering, accepted-alternative wording, formatting, or anything you are not certain is wrong — set confidence honestly; reserve `high` for unambiguous errors. If every key is correct, return `agrees=true` with an empty list."
- `_build_solver_prompt(subject, phase_name, contract, phase_output_md, ...)`: rebuild the generator's contract via `get_prompt(subject, phase_name, output_language=…)` (or `contract_override`) so the solver sees what was asked, then the produced markdown (the thing to check), then `_INSTRUCTIONS`, then the `OUTPUT FORMAT` (schema is injected by `run_phase(schema=SolveVerdict)`).
- `solve(...)`: `result = await agent.run_phase(provider=solver_provider, model=solver_model, phase_prompt=prompt, phase_name="__solver__", schema=SolveVerdict, lesson_context=lesson_context, prior_outputs=prior_outputs, difficulty=None, operation=f"solve:{phase_name}", homework_job_id=…, phase_output_id=…, transport=transport)`; `verdict = result.parsed`. Degrade contract identical to the judge: any exception → `SolveOutcome(available=False, agrees=True, has_mismatch=False, ...)` (never blocks) EXCEPT api-transport auth error (`agent.AuthEnvError` / `phase_judge._is_auth_error`) → **re-raise**; content-policy refusal → `refused=True`.

- [ ] **Step 1: Write the failing test** (hermetic — monkeypatch `agent.run_phase`):

```python
# tests/services/test_solver.py
import pytest
from types import SimpleNamespace
from app.services import solver
from app.schemas.solver import SolveVerdict, Discrepancy


def _run_stub(verdict):
    async def _r(**kw):
        return SimpleNamespace(text="", parsed=verdict, usage={}, raw_envelope={})
    return _r

COMMON = dict(subject="matematika", phase_name="memory-check", phase_output_md="...",
              lesson_context="ctx", prior_outputs={}, output_language="uz",
              solver_provider="claude", solver_model="claude-opus-4-7", transport="api",
              homework_job_id=None, phase_output_id=None)


@pytest.mark.asyncio
async def test_agree_yields_no_mismatch(monkeypatch):
    monkeypatch.setattr("app.services.agent.run_phase", _run_stub(SolveVerdict(agrees=True, discrepancies=[])))
    out = await solver.solve(**COMMON)
    assert out.available and out.agrees and not out.has_mismatch


@pytest.mark.asyncio
async def test_high_confidence_disagreement_triggers_mismatch_and_feedback(monkeypatch):
    v = SolveVerdict(agrees=False, discrepancies=[Discrepancy(
        item="card 9", generated_key="Oy=xato", solver_answer="Oy=to'g'ri",
        explanation="both symmetries hold", confidence="high")])
    monkeypatch.setattr("app.services.agent.run_phase", _run_stub(v))
    out = await solver.solve(**COMMON)
    assert out.has_mismatch and "card 9" in out.feedback


@pytest.mark.asyncio
async def test_low_confidence_disagreement_does_not_regen(monkeypatch):
    v = SolveVerdict(agrees=False, discrepancies=[Discrepancy(
        item="q2", generated_key="a", solver_answer="b", explanation="maybe", confidence="low")])
    monkeypatch.setattr("app.services.agent.run_phase", _run_stub(v))
    out = await solver.solve(**COMMON)
    assert not out.has_mismatch  # advisory only


@pytest.mark.asyncio
async def test_exception_degrades_never_blocks(monkeypatch):
    async def _boom(**kw):
        raise RuntimeError("model down")
    monkeypatch.setattr("app.services.agent.run_phase", _boom)
    out = await solver.solve(**COMMON)
    assert out.available is False and out.has_mismatch is False


@pytest.mark.asyncio
async def test_api_auth_error_reraises(monkeypatch):
    async def _auth(**kw):
        raise __import__("app.services.agent", fromlist=["AuthEnvError"]).AuthEnvError("no key")
    monkeypatch.setattr("app.services.agent.run_phase", _auth)
    with pytest.raises(Exception):
        await solver.solve(**{**COMMON, "transport": "api"})
```

- [ ] **Step 2: Run** `uv run python -m pytest tests/services/test_solver.py -q` → FAIL (no `app.services.solver`).

- [ ] **Step 3: Implement** `app/services/solver.py` cloning `phase_judge.py`'s structure (instructions + `_build_solver_prompt` + `solve` + `_serialize`/`_build_feedback` + the exact degrade/auth/refusal handling). Reuse `phase_judge._is_auth_error`/`_is_refusal` (import them) rather than re-deriving auth/refusal signals.

- [ ] **Step 4: Run** `uv run python -m pytest tests/services/test_solver.py -q` → PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add app/services/solver.py tests/services/test_solver.py
git commit -m "cqc: solver.solve — independent re-solve + high-confidence key-mismatch verdict"
```

---

### Task 7: pipeline wiring — solve-and-maybe-regen in `_execute_phase`

**Files:**
- Modify: `app/services/pipeline.py` — add `_SOLVER_PHASES` constant; resolve solver transport/model; a `_solve_and_maybe_regen(...)` block after the judge/regen block, gated on target phase + `settings.solver_enabled`; thread `solver_status` into the final `set_status(..., "done", …)` write.
- Modify: `app/repositories/phase_outputs.py` — add `solver_status` param to `set_status` (default `None`, persisted only when passed), mirroring `judge_status`.
- Test: `tests/services/test_pipeline_solver.py` (new; unit — monkeypatch `solver.solve` + the regen runner)

**Interfaces:** consumes `solver.solve` (Task 6), `model_tiers.resolve_solver` (Task 4), `resolve_role_transport` (existing), `settings.solver_enabled/max_solve_regens` (Task 1).

**Wiring (mirror the judge regen block):**
1. `_SOLVER_PHASES = ("memory-check", "practice-error-detection", "practice-rlc")` near the other phase-name sets.
2. Solver transport: `solver_transport = resolve_role_transport(getattr(job, "solver_transport", "inherit") or "inherit", transport)` (beside the judge-transport resolution).
3. After the judge/regen block finishes (so we solve the final `output_md`), inside `if phase_name != "extract":`, if `settings.solver_enabled and phase_name in _SOLVER_PHASES`:
   - `_sp, _sm = model_tiers.resolve_solver(produced_by, _gen_model_of(produced_by), getattr(job,"solver_provider",None), getattr(job,"solver_model",None))`
   - `outcome = await solver.solve(subject=…, phase_name=phase_name, phase_output_md=output_md, lesson_context=lesson_context, prior_outputs=prior_outputs, output_language=…, solver_provider=_sp, solver_model=_sm, transport=solver_transport, homework_job_id=job.id, phase_output_id=po_id, contract_override=custom_contract_or_None)`
   - if `outcome.has_mismatch`: `for _ in range(settings.max_solve_regens):` build `regen_prompt = base_phase_prompt + outcome.feedback`, re-run through `_run_with_failover(run_fn=_make_run(regen_prompt), …)`, re-`solve`; on agreement → `solver_status="mismatch_regen"`, adopt regenerated output; loop-exhausted still-mismatch → `solver_status="mismatch_shipped"`; regen exception (guarded, api-auth re-raises) → `solver_status="mismatch_regen_failed"`, keep original.
   - else → `solver_status = "ok"` (agree / low-med only) or `"unavailable"`/`"refused"` from the outcome.
4. Persist: pass `solver_status=solver_status` into the final `phase_repo.set_status(..., "done", …)`.

- [ ] **Step 1: Write the failing test** — monkeypatch `solver.solve` and the regen runner; assert the four behaviors. Make the assertions BITE (a vacuous test that passes with the gate deleted is rejected):

```python
# tests/services/test_pipeline_solver.py  (sketch — fill real fixtures/harness like test_pipeline_judge)
# - target phase + has_mismatch=True  -> regen runner CALLED, solver_status in {"mismatch_regen","mismatch_shipped"}
# - target phase + agrees            -> regen runner NOT called, solver_status == "ok"
# - non-target phase (e.g. flashcards)-> solver.solve NOT called, solver_status is None
# - settings.solver_enabled=False    -> solver.solve NOT called, solver_status is None
# - regen raises (non-auth)          -> job NOT failed, solver_status == "mismatch_regen_failed"
```

- [ ] **Step 2: Run** `uv run python -m pytest tests/services/test_pipeline_solver.py -q` → FAIL.

- [ ] **Step 3: Implement** the wiring above + `set_status(solver_status=…)` param. Keep every solver/regen path inside the existing defensive try structure so the job never fails (api-auth re-raise only).

- [ ] **Step 4: Run** `uv run python -m pytest tests/services/test_pipeline_solver.py tests/services/test_pipeline_judge.py -q` → PASS (solver + unchanged judge behavior).

- [ ] **Step 5: Commit**

```bash
git add app/services/pipeline.py app/repositories/phase_outputs.py tests/services/test_pipeline_solver.py
git commit -m "cqc: wire solver into _execute_phase — solve final output, regen once on high-conf key mismatch"
```

---

### Task 8: worker capability + claim-gate for the solver role (R1 — BLOCKING)

**Why:** the solver's api-auth error re-raises (job-level failure). On the all-Vertex fleet (no `ANTHROPIC_API_KEY`), any job whose *resolved* solver is claude/api — the self-grade swap of a `gemini-3.1-pro-preview` generator, or an explicit `solver=claude/api` override — would be **claimed** by a worker that cannot serve it, then die. The judge role already solves this with a startup capability flag + a claim-gate SQL predicate; the solver must mirror it, or the plan's "never fail a job" constraint is false in the deployment we actually run.

**Files:**
- **⚠️ CORRECTION (verified at execution): NO `worker.py` change.** `worker._compute_capabilities` returns only PROVIDER-level caps `{can_claude_api, can_gemini_api}` — there are no per-role flags (`judge_api_ok` etc. do not exist). The judge role is gated ENTIRELY in the claim-gate SQL by combining those provider caps with the job's stamped columns. So the solver mirrors that: **SQL only.**
- Modify: `app/repositories/jobs.py` `claim_next_job` — mirror the judge block (`jobs.py:330-371`) on the `solver_*` columns: add `solver_needs_api` (transport=='api' OR inherit-under-api), `job_is_self_solve` (`provider==solver_provider AND content_model_resolved==coalesce(solver_model,'')`), `self_solve_provider` (the `_PRIMARY_SELF_FALLBACK` CASE), and `solver_ok = or_(not_(solver_needs_api), and_(job_is_self_solve, _provider_api_ok(self_solve_provider)), and_(not_(job_is_self_solve), _provider_api_ok(HomeworkJob.solver_provider)))`. Add `.where(solver_ok)` to `pick_stmt` (beside `.where(judge_ok)`, ~L417), and add `solver_needs_api` to the `job_resolved_api` `or_(...)` (~L403-407) so a solver-api job is also fleet-pause-gated. Reuse the existing `_provider_api_ok`, `content_model_resolved`, `_PRIMARY_SELF_FALLBACK`.
- Test: extend/mirror `tests/integration/test_claim_gate_self_grade.py` (the judge claim-gate DB test) with solver cases.

**Interfaces:** consumes the `solver_*` job columns (Task 3, stamped by Task 5); reuses the existing judge claim-gate SQL helpers. `resolve_solver`==`resolve_judge`, so the SQL self-grade CASE is identical.

- [ ] **Step 1: Write the failing tests** — make them BITE (the vacuous-claim-gate trap: a predicate assertion that still passes when the guard is deleted is worthless — RED-prove it by asserting a no-`ANTHROPIC_API_KEY` worker does NOT claim a job whose solver resolves to claude-api, AND that it DOES claim one whose solver resolves to gemini-api):

```python
# capability: no ANTHROPIC key -> solver_api_ok False for a claude-resolving job; gemini path True
# claim-gate (DB): worker caps {gemini_api:True, claude_api:False}
#   - job A: content=gemini, solver resolves -> claude/api  => NOT claimed
#   - job B: content=gemini, solver resolves -> gemini/api  => claimed
#   - prove-it-bites: temporarily neutralize the new AND clause -> job A wrongly claimed (documents the guard is load-bearing)
```

- [ ] **Step 2: Run** the new tests → FAIL (claim predicate ignores solver → the claude-solver job IS wrongly claimed).

- [ ] **Step 3: Implement** — add the `solver_ok` clause + `.where(solver_ok)` + `solver_needs_api` in `job_resolved_api`, cloning the judge's handling. NO `worker.py` change. Do NOT invent a new gating mechanism.

- [ ] **Step 4: Run** the new tests + the existing judge claim-gate tests (`tests/integration/test_claim_gate_self_grade.py`, `tests/integration/test_claim_order.py`, `tests/integration/test_claim_contention.py`) → PASS (solver gated, judge behavior unchanged).

- [ ] **Step 5: Commit**

```bash
git add app/repositories/jobs.py tests/integration/test_claim_gate_self_grade.py
git commit -m "cqc: claim-gate solver role (don't claim api-solver jobs a worker can't serve)"
```

---

### Task 9: Full suite + real solver acceptance smoke (transport=api)

**Files:**
- Create: `scripts/cqc_solver_smoke.py`

**Interfaces:** none (acceptance artifact). This is the fact-over-theory gate: the solver must DISCRIMINATE — flag the real audited wrong keys, pass a real correct key — not just "look plausible".

> **⚠️ AMENDED AT THE GATE (2026-07-02) with characterization evidence — the original strict "flag all 3" bar was a FAIL; this is the honest, evidence-backed replacement, gemini-only policy.** The real smoke revealed gemini-3.1-pro-preview's actual behavior: of the 3 audited must-FLAG defects it reliably catches **1** (the objective sign error) and **misses 2** — the conceptual truth-value (symmetry, `263d99c5`) and expression-equivalence (`8f734563/rlc`) errors — with **zero false positives** throughout. `scripts/cqc_solver_characterize.py` (committed) is the evidence this is a **model-capability** miss, not a design/threshold artifact: EXP 1 → `agrees=True, 0 discrepancies` (a genuine miss, NOT a suppressed low/medium under the high-only gate); EXP 2 → a truth-value-directive prompt variant does NOT recover it (×3) while the must-PASS packet stays clean (×3). A claude-opus cross-model probe was **not** run — gemini-only is standing policy. **Do NOT round "1 of 3" up to "2 of 3".**

- [ ] **Step 1: Full suite green** — `uv run python -m pytest tests/ -q` (canonical bar is WITHOUT `RUN_DB_INTEGRATION`; the Task-2 DB test runs under the flag against `edu_scratch_cqc`).

- [ ] **Step 2: Write the smoke** — over `transport=api`, in-process, against the real audited phase outputs (read read-only from `edu_copy`; solver runs against `edu_scratch_cqc` so usage never touches prod):
  - **GATED (hard exit-0 bar):**
    - `8f734563` `practice-error-detection` — MUST flag (objective sign error — the class the solver reliably catches). ✅ verified flags with a precise correct explanation.
    - `1122356a` `practice-rlc` (clean key) — MUST pass, zero false positive (the load-bearing safety property). ✅ verified.
  - **INFORMATIONAL (reported, NOT gated — the recall boundary):**
    - `8f734563` `practice-rlc` (equivalence, x=5 → 21/100 vs 7/40) — MISSED.
    - `263d99c5` `memory-check` (card 9 marks the true Oy-symmetry option "xato") — MISSED.
  - **Exit 0 iff BOTH gated cases hold** (sign-error flags AND clean key passes). The informational cases print for the record but never gate.
  - **R2 cost report:** prints per-call `prompt_tokens`/`output_tokens` + `pricing.cost_usd` at `(gemini, gemini-3.1-pro-preview)` → measured **~$0.12/job** (confirms R2's estimate).

- [ ] **Step 3: Run the smoke (controller runs at the gate; user-authorized single-lesson calls, cheap):**

Run: `uv run python -m scripts.cqc_solver_smoke` (exit 0 = gated 2/2). Paste the verdicts (item + solver_answer + confidence), the **1-of-3 recall line**, and the measured $/job into the PR body — this is the acceptance evidence.

- [ ] **Step 4: Commit**

```bash
git add scripts/cqc_solver_smoke.py scripts/cqc_solver_characterize.py
git commit -m "cqc: acceptance smoke (honest gated bar) + characterization evidence (1-of-3 recall, api)"
```

---

## Finish (after all tasks green + final whole-branch review on the most capable model)

1. `git fetch origin` → `git log HEAD..origin/Nggaev-v2`; if the base moved (CQ-A/CQ-B/CQ-D or others), **rebase onto `origin/Nggaev-v2`**, resolve conflicts (expect append-only clashes in `MASTER_MEMORY.md`/`INDEX.md`, and the shared `pipeline._execute_phase` region — re-anchor the solver block relative to the now-merged CQ-A/CQ-B/CQ-D edits), re-run `uv run python -m pytest tests/ -q`.
2. Open PR titled **`[CQ-C] Answer-key solver pass (R21.2)`** → gatekeeper merges (no self-merge).
3. Worklog **0112** in `docs/memory/MASTER_MEMORY.md` + INDEX row (re-verify next-free at finish).
4. Close CQ-C in `docs/memory/REMEDIATION_CLUSTERS.md` (Cluster 10) + R21 deliverable #2 (R21.2) in `docs/memory/ROADMAP.md` — **hand-merge the R21 list** (item 2 = CQ-C's line to close; item 6 = CQ-D's — don't duplicate/clobber). **Add ROADMAP follow-ups:** (a) the deferred solver-config editing — its **backend half** (`launch_defaults_repo._MUTABLE` + `settings.py` `SettingsIn`/`SettingsOut` fields + the role validation loops at `settings.py:68,79` + `_to_out`) AND its **FE half** (a `/settings` editor for the solver role + a `solver_status` badge on Monitor/book views). Deliberately deferred as a unit: the launch path stamps solver config fine and the migration **seed** supplies the working fleet default (`gemini-3.1-pro-preview`), so an operator gets correct behavior without editing; making the default operator-*adjustable* via `/settings` is the follow-up. Kept out of this PR to hold the `web/` lane and keep scope tight (Task 5's launch-stamp test documents the `_MUTABLE` gap); (b) the **boss-arena wrong-feedback residue** (`263d99c5` Q3) uncovered by CQ-A+CQ-C — a future judge/boundary enhancement, since boss-arena emits no diffable key.
5. `git mv docs/superpowers/plans/2026-07-02-cq-c-answer-key-solver.md docs/superpowers/plans/shipped/`.
6. De-stale reference docs: `docs/HOW_IT_WORKS.md` + `docs/CODE_MAP.md` — document the new **solver role** (third LLM role beside generator + judge), the `_SOLVER_PHASES` scope, `solver_status` values, and the `solver_*` config columns. `docs/DATABASE.md` if it enumerates per-role columns.

## Self-Review (author)

- **Spec coverage:** R21.2 = independent re-solve + diff + regen-on-mismatch over the key-bearing phases → schema (T1) + migration+seed (T2) + models (T3) + non-self model resolution (T4) + launch stamping+code-default (T5) + solver.solve (T6) + pipeline wiring & regen (T7) + worker capability/claim-gate (T8, R1) + real discriminate + $/job smoke (T9). Every provisional decision is isolated (phase set = one constant; model = T4 alias; on-mismatch = T7 regen block + `solver_status`).
- **Type consistency:** `SolveVerdict/Discrepancy` (T1) → `solver.solve` returns `SolveOutcome{has_mismatch,feedback,…}` (T6) → consumed by T7's regen gate; `resolve_solver(gen_p,gen_m,ov_p,ov_m)->(str,str|None)` (T4) feeds T7; `set_status(..., solver_status=…)` (T7) persists the T2/T3 column.
- **Reused, not reinvented:** judge call shape, degrade/auth contract, regen loop, per-role column shape, `resolve_role_transport`, `_self_fallback`, `agent_usages` cost path, `_is_auth_error`/`_is_refusal` — all reused. Only genuinely new surface: `solver.py`, `SolveVerdict`, `solver_status`, `solver_*` columns.
- **Collision control:** serialized after CQ-A **and CQ-B and CQ-D** (R3); branch cut off the post-CQ-D base; separate `solver_status` column vs CQ-B's `validation_warnings`; line numbers marked placeholder → re-anchor at execution; plan committed as commit 1 on the branch (R6).
- **Cost (for the gate):** ~3 solver calls/job baseline + 1 regen per high-confidence mismatch, at the **seeded default `gemini-3.1-pro-preview`** over `transport=api` → ~$0.10/job (R2; the opus estimate ~$0.22/job is why the default is not opus). The smoke (T9) prints the **measured** $/job. `budget`/no-mass-gen rule honored — the smoke is single-lesson calls, never a homework ramp.
- **Gate R-conditions folded in:** R1 (worker capability/claim-gate) = Task 8; R2 (gemini default + real cost) = Approach + T2 seed + T5 default + T9 report; R3 (serialize after CQ-D) = Global Constraints; R4 (`_self_fallback` not tier-arithmetic) = Approach + T4; R6 (commit plan at cut) = Global Constraints. **R5 (CQ-B en-gate one-liner into `content_lint.py`) — NOT folded:** CQ-C never opens `content_lint.py`; hosting an orphaned CQ-B doc note there mixes lanes and breaks minimal-diff. Deferred to the gate to confirm CQ-B is frozen + supply the exact text, else it belongs to CQ-B or a standalone docs commit.
- **Open pre-flight conflict scan:** none internal. The one cross-plan decision — whether boss-arena is in scope (roadmap/brief name it; this plan drops it as un-diffable, gate endorses) — is surfaced as PROVISIONAL decision #1 for the human at the gate.
