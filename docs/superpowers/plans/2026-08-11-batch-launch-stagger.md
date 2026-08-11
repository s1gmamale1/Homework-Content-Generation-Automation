# Batch-Launch Wave Stagger Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop a batch launch from making every job claimable in the same instant, so a multi-lesson launch stops producing a synchronised burst of api calls that exhausts the fleet credential-slot wait.

**Architecture:** The launcher stamps `homework_jobs.scheduled_at` in waves (N jobs now, N more one interval later, …) instead of letting all of them default to `NOW()`. `scheduled_at` already exists, is already indexed, and is already honoured by the claim gate — no migration, no new machinery, no concurrency-config change.

**Tech Stack:** FastAPI, SQLAlchemy async, Postgres, pytest / pytest-asyncio.

---

## Approach & key decisions

**Chosen: Option 2 — stagger via `scheduled_at`.** Every job already carries a "don't start before" timestamp that the claim gate filters on (`app/repositories/jobs.py:551`, `HomeworkJob.scheduled_at <= func.now()`). Assigning a per-wave offset at launch is the entire feature.

**Rejected — Option 1 (copy `/generate`'s 503 guard).** It treats a timing problem as a volume problem. Measured: after the opening burst the *same* fleet sustained **81 calls in flight with zero exhaustions** — capacity was never the constraint. A 503 would also fire on the launch's own jobs, and would fail a 28-lesson launch because an unrelated queue is deep.

**Rejected — Option 3 (per-launch cap).** Pushes the problem onto the operator and gives up the "launch the whole book" UX for nothing the stagger doesn't already deliver.

**Rejected — Option 4 (hybrid with a depth check).** The measured incident had **no fleet contention at all** (830 calls in the window, all from this one batch), so the launcher alone is a complete control point. Adding a depth check buys nothing here and re-imports Option 1's failure mode.

**Rejected — raising `CREDENTIAL_MAX_CONCURRENT_GEMINI` instead (user-explored, then user-ruled-out).** The per-process semaphore is entered *before* the credential limiter (`agent.py:582` wraps `agent.py:613`), so 12 processes × `AGENT_MAX_CONCURRENCY=4` = **48 calls is the most the fleet can put in front of the limiter**. Any cap ≥48 — 48, 128, 1026 — is the identical setting, and all of them mean "limiter off". It is also a 14-host `.env` change during a deliberate freeze, it runs `gemini-3.1-pro-preview` (117 calls in the incident) above its measured-clean 32, and at cap 48 a 28-lesson launch drains in ~116s against a 120s budget — four seconds of margin, gone again at ~60 lessons. **User constraint, verbatim: "as long as it doesn't fail with current concurrency configuration, that's it."** The stagger is the only option that satisfies that with no worker touch.

**Load-bearing facts, each verified against code or production this session:**

1. `scheduled_at` exists (`app/models/homework_job.py:71`), is in the partial queue index (`:101-106`), and gates claiming (`jobs.py:551`). **No migration.**
2. Claim order is `priority DESC, toc_entries.order_index ASC, scheduled_at ASC` (`jobs.py:428-430`) — `scheduled_at` is only the final tiebreaker, so staggering changes *eligibility*, never lesson ordering inside an eligible set.
3. `queue_depth` also filters `scheduled_at <= func.now()` (`jobs.py:1321`), so **staggered jobs do not inflate the `/generate` backpressure count**. The two mechanisms compose instead of fighting.
4. `reset_for_retry` (`jobs.py:249-285`) never touches `scheduled_at`, so a **resumed** job keeps its original past timestamp and is instantly claimable — the resume paths reproduce the identical herd. Scope covers them (user-approved).
5. `requeue_slot_saturated` already pushes `scheduled_at` by `slot_saturation_requeue_seconds` (`jobs.py:1270`) — `scheduled_at`-as-backpressure is an **established pattern in this codebase**, applied reactively today and preventively here.
6. Slot saturation **refunds the attempt** (`attempts - 1`, `jobs.py:1264`), so this incident class wastes time and adds noise but does not terminally fail jobs. Severity is efficiency/predictability, not data loss. Sizing is deliberately conservative rather than heroic.

**Measured sizing** (production `edu_copy`, read-only, batch `d538c4ef-5347-400f-865a-40a21edbf627`, 2026-08-11):

| Quantity | Measured |
|---|---|
| Jobs / outcome | 28 · all `done`, 0 failed |
| Fleet contention in the window | **none** (830 calls, one batch) |
| `lesson.extract` duration | avg 12.6s · p50 13.1s · **max 16.1s** |
| Per-job **peak fan-out** | avg **5.54** · p50 5 · max 7 |
| Peak fleet demand | **81** calls in flight vs cap 32 |
| Slot exhaustions | **16** — 13 in minute 1, 2 in minute 2, 1 stray |
| Processes serving the batch | **12** distinct `host:pid` |

⇒ **wave_size 6** → opening burst ≈ 6 × 5.54 = **33 calls** against a cap of 32 (versus the ~155 that broke it).
⇒ **interval 60s** clears extract (max 16.1s) plus one content call (avg 35.9s) so consecutive waves cannot stack.

**Closed form worth recording:** during a burst that outlasts the 120s slot-wait budget, exhaustions ≈ `(processes × AGENT_MAX_CONCURRENCY) − credential_cap`. For the incident: `(12 × 4) − 32 = 16`, matching the 16 observed. This is why the stagger attacks burst *duration* — the only one of the three factors reachable without touching a frozen fleet.

**Deployment reality (state plainly in the PR):** the launcher runs on the **head**, pinned to the v968 worktree until the `AUTH_TOKEN` rotation. **This ships dark.** Interim mitigation is chunked launches of ~6 via `toc_entry_ids`.

---

## Global Constraints

Every task's requirements implicitly include this section.

- **Money rule (HARD):** never mass-generate homeworks. Only the bounded, cost-reported smoke in Task 8. Any api spend is reported to the cent.
- **Branch guard before EVERY commit:** `[ "$(git rev-parse --abbrev-ref HEAD)" = "feat/batch-launch-stagger" ] || exit 1`
- **Stage only the files the task lists.** Never `git add -A` — other sessions commit to this repo.
- **Worktree:** `/Users/macmini5/Documents/HCGA-launch-stagger`, branch `feat/batch-launch-stagger`, based on `origin/Nggaev-v2` (963aeed).
- **RED-proof every behavioural test.** Sabotage the implementation, re-run, confirm it fails *for the stated reason*, restore. A test that never failed proves nothing. Quote the actual failure text in the task report.
- **Both directions required.** Every wiring task pins that staggering happens when it should **and does not happen when it shouldn't** (launch ≤ wave_size, kill switch set).
- **DB clock only.** Offsets must be built as `func.now() + func.make_interval(0, 0, 0, 0, 0, 0, <secs>)`. Never `datetime.now()` — the claim gate compares against `func.now()` and worker host clocks drift (see the host-clock note at `jobs.py:1157`).
- **Do not change `/generate`'s behaviour** (`app/api/v1/jobs.py:252-265` stays byte-identical).
- **Do not touch the retired-model guard** on any resume/relaunch path (`job_reactivation.retired_models_in_job` call sites keep their exact semantics).
- **Do not self-merge.** Open the PR and stop.
- **Canonical suite bar:** `uv run python -m pytest tests/ -q` **without** `RUN_DB_INTEGRATION`. **Never point tests at `edu_copy` — that is production.**
- Repo has no `[build-system]`: run scripts as `uv run python -m scripts.<name>`.
- A git worktree loads the PARENT `/Users/macmini5/Documents/.env`, not the repo's. Anything reading config must assert the loaded module's `__file__`.

---

## File Structure

| File | Responsibility |
|---|---|
| `app/services/launch_stagger.py` *(new)* | Pure offset arithmetic. No DB, no settings, no imports from `app`. The only place the wave rule lives. |
| `tests/services/test_launch_stagger.py` *(new)* | The offset function, both directions + kill switch. |
| `app/config.py` *(modify)* | Two new knobs; `ge=1` hardening on the two concurrency knobs. |
| `tests/test_launch_stagger_settings.py` *(new)* | Defaults, env override, kill switch, zero-concurrency rejection. |
| `app/repositories/jobs.py` *(modify)* | `create` / `reset_for_retry` accept an offset; `resume_failed_in_batch` staggers its own loop. |
| `tests/repositories/test_launch_stagger_repo.py` *(new)* | Repo-level offset stamping, stub-session (no DB). |
| `app/api/v1/batch.py` *(modify)* | Wire the launch loop + `/resume`; report the stagger in both payloads. |
| `tests/api/test_batch_launch_stagger.py` *(new)* | Endpoint wiring, wave assignment, both directions. |
| `.env.example` *(modify)* | Document both knobs. |
| `docs/memory/MASTER_MEMORY.md`, `docs/memory/INDEX.md`, `docs/HOW_IT_WORKS.md`, `docs/CODE_MAP.md` *(modify)* | Worklog **0172** + row; de-stale live-system refs. |

---

### Task 1: The pure offset function

**Files:**
- Create: `app/services/launch_stagger.py`
- Test: `tests/services/test_launch_stagger.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `stagger_offset(index: int, *, wave_size: int, interval_seconds: int) -> int`. Every later task calls exactly this signature.

- [ ] **Step 1: Write the failing test**

Create `tests/services/test_launch_stagger.py`:

```python
"""Wave-based batch-launch stagger — the pure offset rule.

Sizing is measured, not chosen: see the plan's Approach section
(batch d538c4ef, 2026-08-11 — per-job peak fan-out 5.54 api calls,
CREDENTIAL_MAX_CONCURRENT_GEMINI=32).
"""
import pytest

from app.services.launch_stagger import stagger_offset

WAVE = dict(wave_size=6, interval_seconds=60)


def test_first_wave_starts_immediately():
    """Jobs 0..5 fill wave 0. A launch of <= wave_size lessons must be
    byte-identical to pre-stagger behaviour: nothing is delayed."""
    assert [stagger_offset(i, **WAVE) for i in range(6)] == [0] * 6


def test_second_wave_starts_one_interval_later():
    assert [stagger_offset(i, **WAVE) for i in range(6, 12)] == [60] * 6


def test_third_wave_starts_two_intervals_later():
    assert stagger_offset(12, **WAVE) == 120
    assert stagger_offset(17, **WAVE) == 120


def test_incident_shape_28_lessons_spans_five_waves():
    """The measured incident: 28 lessons -> 5 waves, last job at +4 min."""
    offsets = [stagger_offset(i, **WAVE) for i in range(28)]
    assert offsets[0] == 0
    assert offsets[-1] == 240
    assert sorted(set(offsets)) == [0, 60, 120, 180, 240]
    # No wave may hold more than wave_size jobs, or the burst arithmetic
    # (6 x 5.54 fan-out ~= the cap of 32) stops holding.
    for off in set(offsets):
        assert offsets.count(off) <= 6


def test_offsets_never_decrease():
    """Monotonic: a later job may never become claimable before an earlier one."""
    offsets = [stagger_offset(i, **WAVE) for i in range(50)]
    assert offsets == sorted(offsets)


def test_wave_size_zero_is_the_kill_switch():
    assert stagger_offset(500, wave_size=0, interval_seconds=60) == 0


def test_interval_zero_is_the_kill_switch():
    assert stagger_offset(500, wave_size=6, interval_seconds=0) == 0


@pytest.mark.parametrize("bad", [-1, -100])
def test_negative_index_never_delays(bad):
    assert stagger_offset(bad, **WAVE) == 0
```

- [ ] **Step 2: Run it and watch it fail**

```bash
cd /Users/macmini5/Documents/HCGA-launch-stagger
uv run python -m pytest tests/services/test_launch_stagger.py -q
```
Expected: collection error — `ModuleNotFoundError: No module named 'app.services.launch_stagger'`.

- [ ] **Step 3: Write the implementation**

Create `app/services/launch_stagger.py`:

```python
"""Wave-based batch-launch stagger.

Why this exists (MEASURED 2026-08-11 against production `edu_copy`, batch
`d538c4ef-5347-400f-865a-40a21edbf627` — geografiya g5 RU, 28 lessons,
transport=api):

  * The launcher creates every job in one loop and `scheduled_at` server-defaults
    to NOW(), so all 28 jobs became claimable in the same instant.
  * Every job's first phase (`extract`) is short and TIGHTLY distributed —
    avg 12.6s, p50 13.1s, max 16.1s — so all 28 crossed into their DAG tail
    within ~4s of each other and fanned out together.
  * Measured per-job peak fan-out: 5.54 concurrent api calls (p50 5, max 7).
    28 x 5.54 ~= 155 calls arriving at once against
    CREDENTIAL_MAX_CONCURRENT_GEMINI=32.
  * Result: 16 x "429 fleet credential slot wait exhausted", each burning the
    full 120s budget; 13 of the 16 landed in the first minute.

The decisive counter-evidence for treating this as a VOLUME problem: once the
jobs decorrelated, the same fleet sustained 81 calls in flight with ZERO
exhaustions. Capacity was never the constraint — synchronisation was.

During a burst that outlasts the slot-wait budget the exhaustion count has a
closed form: (model-calling processes x AGENT_MAX_CONCURRENCY) - credential cap.
For that incident (12 x 4) - 32 = 16, matching the 16 observed. Of those three
factors only the burst DURATION is reachable without reconfiguring a frozen
fleet, which is exactly what this module shortens.

This module answers one question and touches nothing else: given a job's 0-based
position in a launch, how many seconds after NOW() may it start?
"""


def stagger_offset(index: int, *, wave_size: int, interval_seconds: int) -> int:
    """Seconds after NOW() before the job at 0-based launch ``index`` may start.

    Job ``index`` lands in wave ``index // wave_size`` and starts that many
    intervals from now, so wave 0 is always offset 0. A launch of ``wave_size``
    jobs or fewer therefore behaves EXACTLY as it did before this feature: every
    offset is 0 and every job is claimable immediately.

    Either knob at <= 0 disables staggering (all offsets 0). That is the kill
    switch — `BATCH_LAUNCH_WAVE_SIZE=0` restores pre-plan behaviour with no code
    change and no deploy.

    ``index`` is the position among the jobs THIS launch actually makes
    claimable (created + resumed) — never the index in the target list. A
    relaunch that adopts or skips 20 of 28 sections adds only 8 jobs of load and
    must not be spread across 5 waves.
    """
    if index <= 0:
        return 0
    if wave_size <= 0 or interval_seconds <= 0:
        return 0
    return (index // wave_size) * interval_seconds
```

- [ ] **Step 4: Run the tests — all green**

```bash
uv run python -m pytest tests/services/test_launch_stagger.py -q
```
Expected: 11 passed.

- [ ] **Step 5: RED-proof (mandatory)**

Change `return (index // wave_size) * interval_seconds` to `return 0`. Re-run.
Expected: `test_second_wave_starts_one_interval_later`, `test_third_wave_starts_two_intervals_later`, and `test_incident_shape_28_lessons_spans_five_waves` FAIL with `assert [0, 0, ...] == [60, 60, ...]`. **Restore the line.** Quote the real failure text in your report.

- [ ] **Step 6: Commit**

```bash
[ "$(git rev-parse --abbrev-ref HEAD)" = "feat/batch-launch-stagger" ] || exit 1
git add app/services/launch_stagger.py tests/services/test_launch_stagger.py
git commit -m "feat(launch): wave-offset rule for batch launches"
```

---

### Task 2: Settings knobs + the zero-concurrency footgun

**Files:**
- Modify: `app/config.py` (insert after `queue_backpressure_limit`, currently `:74`; edit `agent_max_concurrency`/`gemini_max_concurrency`, currently `:81-82`)
- Modify: `.env.example`
- Test: `tests/test_launch_stagger_settings.py`

**Interfaces:**
- Produces: `settings.batch_launch_wave_size` (default 6), `settings.batch_launch_wave_interval_seconds` (default 60).

**Why the concurrency hardening rides here (scope note):** found while reading the semaphore for this feature. `_effective_concurrency()` (`agent.py:235-246`) returns whichever knob wins straight into `asyncio.Semaphore(n)` (`agent.py:249-253`). `asyncio.Semaphore(0)` has **zero permits** — every model call blocks forever, with no error and no log. A worker set to 0 would claim jobs, make no calls, look healthy, and lose each job to `job_timeout_seconds`. Neither field has a minimum today. One line each, and the user approved it explicitly.

- [ ] **Step 1: Write the failing test**

Create `tests/test_launch_stagger_settings.py`:

```python
"""Launch-stagger knobs + the Semaphore(0) silent-brick guard."""
import pytest
from pydantic import ValidationError

from app.config import Settings


def test_defaults_match_the_measured_incident():
    """6 x 5.54 measured fan-out ~= 33 calls vs a cap of 32; 60s clears the
    16.1s max extract plus one ~36s content call."""
    s = Settings(_env_file=None)
    assert s.batch_launch_wave_size == 6
    assert s.batch_launch_wave_interval_seconds == 60


def test_knobs_are_overridable():
    s = Settings(_env_file=None, batch_launch_wave_size=4,
                 batch_launch_wave_interval_seconds=90)
    assert s.batch_launch_wave_size == 4
    assert s.batch_launch_wave_interval_seconds == 90


@pytest.mark.parametrize("kwargs", [
    {"batch_launch_wave_size": 0},
    {"batch_launch_wave_interval_seconds": 0},
])
def test_zero_is_an_allowed_kill_switch(kwargs):
    """0 must be ACCEPTED here — it is the documented way to turn the stagger
    off without a deploy."""
    assert Settings(_env_file=None, **kwargs) is not None


@pytest.mark.parametrize("kwargs", [
    {"batch_launch_wave_size": -1},
    {"batch_launch_wave_interval_seconds": -1},
])
def test_negative_is_rejected(kwargs):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **kwargs)


@pytest.mark.parametrize("field", ["agent_max_concurrency", "gemini_max_concurrency"])
def test_zero_concurrency_is_rejected_not_silently_deadlocking(field):
    """asyncio.Semaphore(0) blocks FOREVER: a worker would claim jobs, make no
    model call, log nothing, and lose every job to the job timeout. Fail at
    startup instead of bricking silently."""
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: 0})
```

- [ ] **Step 2: Run it and watch it fail**

```bash
uv run python -m pytest tests/test_launch_stagger_settings.py -q
```
Expected: `test_defaults_match_the_measured_incident` fails with `AttributeError: 'Settings' object has no attribute 'batch_launch_wave_size'`, and both `test_zero_concurrency_...` cases fail with `DID NOT RAISE`.

- [ ] **Step 3: Write the implementation**

In `app/config.py`, insert immediately after the `queue_backpressure_limit: int = 50` line:

```python
    # ─── Batch-launch wave stagger (plan 2026-08-11) ──────────────────────
    # A batch launch stamps `scheduled_at` in waves instead of making every job
    # claimable at once. Sized against the MEASURED 2026-08-11 incident (batch
    # d538c4ef, 28 lessons, transport=api): per-job peak api-call fan-out is
    # 5.54 (p50 5, max 7), so 6 jobs per wave puts ~33 calls against
    # CREDENTIAL_MAX_CONCURRENT_GEMINI=32 instead of the ~155 that produced 16
    # slot-wait exhaustions. The interval clears the extract phase (p50 13.1s,
    # max 16.1s) plus one content call (avg 35.9s), so waves cannot stack.
    # Deliberately NOT sized against a raised credential cap: the point is that
    # this works at the fleet's CURRENT configuration with no worker touch.
    # Set either to 0 to disable — every job becomes claimable immediately,
    # exactly as before this feature.
    batch_launch_wave_size: int = Field(default=6, ge=0)
    batch_launch_wave_interval_seconds: int = Field(default=60, ge=0)
```

Replace the two concurrency lines with:

```python
    # ge=1 on BOTH: `_semaphore()` feeds whichever one wins into
    # `asyncio.Semaphore(n)` (agent.py:249-253), and `asyncio.Semaphore(0)` has
    # ZERO permits — every model call blocks forever with no error and no log,
    # so a host set to 0 claims jobs, makes no calls, looks healthy, and loses
    # each job to `job_timeout_seconds`. Fail at startup instead.
    agent_max_concurrency: int = Field(default=8, ge=1)  # LIVE knob — set AGENT_MAX_CONCURRENCY to tune
    gemini_max_concurrency: int = Field(default=8, ge=1)  # DEPRECATED fallback — honoured only when agent_max_concurrency==8
```

Keep the existing explanatory comment block above them unchanged. **Do not touch `_DEFAULT_CONCURRENCY` or the sentinel comparison in `agent.py`** — the `8`-means-fallback behaviour is unchanged by this task.

In `.env.example`, add after the `CREDENTIAL_SLOT_WAIT_SECONDS` block:

```bash
# How a batch launch spreads its jobs. Instead of making every lesson claimable
# at once (which made 28 lessons fire ~155 simultaneous api calls against a
# 32-slot credential cap on 2026-08-11), the launcher stamps `scheduled_at` in
# waves: BATCH_LAUNCH_WAVE_SIZE jobs now, that many again every
# BATCH_LAUNCH_WAVE_INTERVAL_SECONDS. A launch of <= wave-size lessons is not
# delayed at all. Set either to 0 to disable the stagger entirely.
# BATCH_LAUNCH_WAVE_SIZE=6
# BATCH_LAUNCH_WAVE_INTERVAL_SECONDS=60
```

- [ ] **Step 4: Run the tests — all green**

```bash
uv run python -m pytest tests/test_launch_stagger_settings.py -q
uv run python -m pytest tests/ -q -k "config or concurrency"
```

- [ ] **Step 5: RED-proof (mandatory)**

Change `agent_max_concurrency` back to `Field(default=8, ge=0)`. Re-run — `test_zero_concurrency_is_rejected_not_silently_deadlocking[agent_max_concurrency]` must fail with `DID NOT RAISE <class 'pydantic_core._pydantic_core.ValidationError'>`. **Restore.**

- [ ] **Step 6: Commit**

```bash
[ "$(git rev-parse --abbrev-ref HEAD)" = "feat/batch-launch-stagger" ] || exit 1
git add app/config.py .env.example tests/test_launch_stagger_settings.py
git commit -m "feat(config): launch-stagger knobs + reject zero agent concurrency"
```

---

### Task 3: `jobs_repo.create` accepts a start offset

**Files:**
- Modify: `app/repositories/jobs.py` (`create`, `:31-89`)
- Test: `tests/repositories/test_launch_stagger_repo.py`

**Interfaces:**
- Consumes: nothing from Task 1 (the repo takes a resolved integer, not the rule).
- Produces: `jobs_repo.create(..., start_offset_seconds: int = 0)`. Default 0 ⇒ column untouched ⇒ server default `NOW()` ⇒ **byte-identical to today** for every existing caller.

- [ ] **Step 1: Write the failing test**

Create `tests/repositories/test_launch_stagger_repo.py`:

```python
"""Repo-level launch-stagger stamping. No DB: `create` only calls
`session.add` + `session.flush`, so a stub session exercises it fully."""
import uuid

import pytest
from sqlalchemy.sql.elements import ClauseElement

from app.repositories import jobs as jobs_repo


class _StubSession:
    """Minimal AsyncSession stand-in for `create` (add + flush only)."""

    def __init__(self):
        self.added = []

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        return None


def _kwargs():
    return dict(book_id=uuid.uuid4(), toc_entry_id=uuid.uuid4(),
                subject="geografiya", output_language="ru")


def _sql(expr) -> str:
    return str(expr.compile(compile_kwargs={"literal_binds": True})).lower()


@pytest.mark.asyncio
async def test_no_offset_leaves_scheduled_at_to_the_server_default():
    """Existing callers must be unaffected: the column is never assigned, so
    Postgres applies its NOW() server default exactly as before."""
    job = await jobs_repo.create(_StubSession(), **_kwargs())
    assert job.scheduled_at is None


@pytest.mark.asyncio
async def test_offset_uses_the_db_clock_not_the_host_clock():
    """The claim gate compares against func.now(); worker host clocks drift."""
    job = await jobs_repo.create(_StubSession(), start_offset_seconds=120, **_kwargs())
    assert isinstance(job.scheduled_at, ClauseElement)
    sql = _sql(job.scheduled_at)
    assert "now()" in sql
    assert "make_interval" in sql
    assert "120" in sql


@pytest.mark.asyncio
async def test_zero_offset_is_indistinguishable_from_omitting_it():
    job = await jobs_repo.create(_StubSession(), start_offset_seconds=0, **_kwargs())
    assert job.scheduled_at is None
```

- [ ] **Step 2: Run it and watch it fail**

```bash
uv run python -m pytest tests/repositories/test_launch_stagger_repo.py -q
```
Expected: `test_offset_uses_the_db_clock_not_the_host_clock` fails with `TypeError: create() got an unexpected keyword argument 'start_offset_seconds'`.

- [ ] **Step 3: Write the implementation**

In `app/repositories/jobs.py::create`, add the parameter after `solver_model`:

```python
    solver_model: Optional[str] = None,
    start_offset_seconds: int = 0,
) -> HomeworkJob:
```

and immediately before `job = HomeworkJob(**kwargs)`:

```python
    # Launch stagger (plan 2026-08-11): a positive offset pushes scheduled_at
    # into the future so the claim gate (`scheduled_at <= func.now()`,
    # claim_next_job) holds this job back until its wave is due. DB clock, never
    # the host clock — the gate compares against func.now() and worker host
    # clocks drift (same reasoning as the host-clock note on
    # mark_failed_with_retry). Left unset at offset 0 so the column keeps its
    # NOW() server default and every pre-existing caller is untouched.
    if start_offset_seconds > 0:
        kwargs["scheduled_at"] = func.now() + func.make_interval(
            0, 0, 0, 0, 0, 0, start_offset_seconds)
```

- [ ] **Step 4: Run the tests — all green**

```bash
uv run python -m pytest tests/repositories/test_launch_stagger_repo.py -q
uv run python -m pytest tests/repositories tests/api -q
```

- [ ] **Step 5: RED-proof (mandatory)**

Replace the DB-clock expression with a host-clock one:
```python
        kwargs["scheduled_at"] = datetime.now(timezone.utc)
```
Re-run — `test_offset_uses_the_db_clock_not_the_host_clock` must fail on `isinstance(job.scheduled_at, ClauseElement)` (a `datetime` is not a `ClauseElement`). **Restore.** This is the test's whole point: it fails specifically on host-clock drift, not on "some value was set".

- [ ] **Step 6: Commit**

```bash
[ "$(git rev-parse --abbrev-ref HEAD)" = "feat/batch-launch-stagger" ] || exit 1
git add app/repositories/jobs.py tests/repositories/test_launch_stagger_repo.py
git commit -m "feat(jobs): create() accepts a DB-clock start offset"
```

---

### Task 4: `reset_for_retry` accepts a start offset

**Files:**
- Modify: `app/repositories/jobs.py` (`reset_for_retry`, `:249-285`)
- Test: `tests/repositories/test_launch_stagger_repo.py` (append)

**Interfaces:**
- Produces: `jobs_repo.reset_for_retry(session, job_id, batch_id=None, start_offset_seconds: int = 0)`. Default 0 ⇒ `scheduled_at` untouched ⇒ **exactly today's behaviour** (the resumed job keeps its original past timestamp and is instantly claimable).

- [ ] **Step 1: Write the failing test**

Append to `tests/repositories/test_launch_stagger_repo.py`:

```python
class _GetStubSession(_StubSession):
    """Adds `get`, which `reset_for_retry` uses to load the row."""

    def __init__(self, job):
        super().__init__()
        self._job = job

    async def get(self, model, pk):
        return self._job


class _FakeJob:
    """Only the attributes reset_for_retry writes."""

    def __init__(self):
        self.id = uuid.uuid4()
        self.status = "failed"
        self.error_message = "boom"
        self.current_phase = "flashcards"
        self.started_at = object()
        self.completed_at = object()
        self.attempts = 3
        self.claim_token = uuid.uuid4()
        self.claimed_at = object()
        self.claimed_by = "Host-02:1"
        self.batch_id = None
        self.scheduled_at = "ORIGINAL"


@pytest.mark.asyncio
async def test_resume_without_offset_leaves_scheduled_at_alone():
    """Pre-plan behaviour, preserved exactly: a resumed job keeps its original
    (past) timestamp and is claimable immediately."""
    job = _FakeJob()
    await jobs_repo.reset_for_retry(_GetStubSession(job), job.id)
    assert job.scheduled_at == "ORIGINAL"
    assert job.status == "pending"


@pytest.mark.asyncio
async def test_resume_with_offset_pushes_scheduled_at_on_the_db_clock():
    job = _FakeJob()
    await jobs_repo.reset_for_retry(_GetStubSession(job), job.id,
                                    start_offset_seconds=180)
    assert isinstance(job.scheduled_at, ClauseElement)
    sql = _sql(job.scheduled_at)
    assert "now()" in sql and "make_interval" in sql and "180" in sql


@pytest.mark.asyncio
async def test_resume_offset_does_not_disturb_the_lease_reset():
    """The stagger must not weaken the fenced-lease rotation (jobs.py:278-282)."""
    job = _FakeJob()
    await jobs_repo.reset_for_retry(_GetStubSession(job), job.id,
                                    start_offset_seconds=60)
    assert job.claim_token is None
    assert job.claimed_at is None
    assert job.claimed_by is None
    assert job.attempts == 0
```

- [ ] **Step 2: Run it and watch it fail**

Expected: `TypeError: reset_for_retry() got an unexpected keyword argument 'start_offset_seconds'`.

- [ ] **Step 3: Write the implementation**

Change the signature to:

```python
async def reset_for_retry(
    session: AsyncSession, job_id: UUID, batch_id: Optional[UUID] = None,
    *, start_offset_seconds: int = 0,
) -> Optional[HomeworkJob]:
```

Append to the docstring:

```
    ``start_offset_seconds``: launch stagger (plan 2026-08-11). This function
    deliberately did NOT touch ``scheduled_at`` before, so a resumed job kept its
    original (past) timestamp and became claimable the instant it flipped to
    pending — which reproduces the same synchronised burst a fresh batch launch
    does. A positive offset pushes it out on the DB clock. Default 0 keeps the
    historical behaviour byte-for-byte for every other caller.
```

and insert before `if batch_id is not None:`:

```python
    if start_offset_seconds > 0:
        job.scheduled_at = func.now() + func.make_interval(
            0, 0, 0, 0, 0, 0, start_offset_seconds)
```

- [ ] **Step 4: Run the tests — all green**

```bash
uv run python -m pytest tests/repositories/test_launch_stagger_repo.py -q
uv run python -m pytest tests/ -q -k "retry or resume or relaunch"
```

- [ ] **Step 5: RED-proof (mandatory)**

Change the guard to `if start_offset_seconds >= 0:`. Re-run — `test_resume_without_offset_leaves_scheduled_at_alone` must fail (`scheduled_at` became a `ClauseElement` instead of `"ORIGINAL"`), proving the no-offset path really is untouched. **Restore.**

- [ ] **Step 6: Commit**

```bash
[ "$(git rev-parse --abbrev-ref HEAD)" = "feat/batch-launch-stagger" ] || exit 1
git add app/repositories/jobs.py tests/repositories/test_launch_stagger_repo.py
git commit -m "feat(jobs): reset_for_retry() accepts a DB-clock start offset"
```

---

### Task 5: `resume_failed_in_batch` staggers its own loop

**Files:**
- Modify: `app/repositories/jobs.py` (`resume_failed_in_batch`, `:1423-1451`)
- Test: `tests/repositories/test_launch_stagger_repo.py` (append)

**Interfaces:**
- Produces: `resume_failed_in_batch(session, batch_id, *, wave_size: int = 0, interval_seconds: int = 0) -> dict` (unchanged return shape). Defaults 0/0 ⇒ no stagger ⇒ any other caller is unaffected.

**Two things this task must get right:**
1. **Wave position is `resumed`, not the loop index.** A retired-model job is skipped and adds no load, so it must not consume a wave slot.
2. **Add a deterministic `ORDER BY`.** The current `select` has none, so row order is whatever Postgres returns and wave assignment would vary run to run. Before writing, `grep -rn "resume_failed_in_batch" app/ tests/` and confirm every caller still compiles.

- [ ] **Step 1: Write the failing test**

Append to `tests/repositories/test_launch_stagger_repo.py`:

```python
class _ResumeStubSession:
    """Stands in for the `select(...)` in resume_failed_in_batch."""

    def __init__(self, jobs):
        self._jobs = jobs
        self.statements = []

    async def execute(self, stmt):
        self.statements.append(stmt)

        class _Result:
            def __init__(self, jobs):
                self._jobs = jobs

            def scalars(self):
                return self

            def all(self):
                return self._jobs

        return _Result(self._jobs)


@pytest.mark.asyncio
async def test_resume_assigns_waves_in_order(monkeypatch):
    jobs = [_FakeJob() for _ in range(8)]
    seen = []

    async def _fake_reset(session, job_id, batch_id=None, *, start_offset_seconds=0):
        seen.append(start_offset_seconds)

    monkeypatch.setattr(jobs_repo, "reset_for_retry", _fake_reset)
    monkeypatch.setattr(
        "app.services.job_reactivation.retired_models_in_job", lambda job: ())

    out = await jobs_repo.resume_failed_in_batch(
        _ResumeStubSession(jobs), uuid.uuid4(), wave_size=3, interval_seconds=60)

    assert out["resumed"] == 8
    assert seen == [0, 0, 0, 60, 60, 60, 120, 120]


@pytest.mark.asyncio
async def test_retired_jobs_do_not_consume_a_wave_slot(monkeypatch):
    """A skipped retired job adds no load, so the next real job must stay in
    the same wave it would have occupied anyway."""
    jobs = [_FakeJob() for _ in range(4)]
    retired_id = jobs[1].id
    seen = []

    async def _fake_reset(session, job_id, batch_id=None, *, start_offset_seconds=0):
        seen.append(start_offset_seconds)

    monkeypatch.setattr(jobs_repo, "reset_for_retry", _fake_reset)
    monkeypatch.setattr(
        "app.services.job_reactivation.retired_models_in_job",
        lambda job: (("content", "gemini", "gemini-2.5-flash"),) if job.id == retired_id else ())

    out = await jobs_repo.resume_failed_in_batch(
        _ResumeStubSession(jobs), uuid.uuid4(), wave_size=2, interval_seconds=60)

    assert out["resumed"] == 3
    assert len(out["skipped_retired"]) == 1
    # 3 resumable jobs at wave_size 2 -> waves 0, 0, 1 (NOT 0, 1, 1)
    assert seen == [0, 0, 60]


@pytest.mark.asyncio
async def test_resume_without_wave_args_does_not_stagger(monkeypatch):
    """Default call site behaviour is unchanged."""
    jobs = [_FakeJob() for _ in range(5)]
    seen = []

    async def _fake_reset(session, job_id, batch_id=None, *, start_offset_seconds=0):
        seen.append(start_offset_seconds)

    monkeypatch.setattr(jobs_repo, "reset_for_retry", _fake_reset)
    monkeypatch.setattr(
        "app.services.job_reactivation.retired_models_in_job", lambda job: ())

    await jobs_repo.resume_failed_in_batch(_ResumeStubSession(jobs), uuid.uuid4())

    assert seen == [0, 0, 0, 0, 0]


@pytest.mark.asyncio
async def test_resume_select_is_deterministically_ordered(monkeypatch):
    """Without an ORDER BY, which lesson lands in wave 0 varies per run."""
    session = _ResumeStubSession([])
    monkeypatch.setattr(
        "app.services.job_reactivation.retired_models_in_job", lambda job: ())

    await jobs_repo.resume_failed_in_batch(session, uuid.uuid4())

    assert "order by" in str(session.statements[0]).lower()
```

- [ ] **Step 2: Run it and watch it fail**

Expected: `TypeError: resume_failed_in_batch() got an unexpected keyword argument 'wave_size'`, and `test_resume_select_is_deterministically_ordered` fails on the missing `order by`.

- [ ] **Step 3: Write the implementation**

Replace the body of `resume_failed_in_batch` with:

```python
async def resume_failed_in_batch(
    session: AsyncSession, batch_id: UUID, *,
    wave_size: int = 0, interval_seconds: int = 0,
) -> dict:
    """Re-enqueue every failed/cancelled job in a batch via reset_for_retry
    (status->pending, attempts->0). reset_for_retry keeps phase rows, so the
    pipeline RESUMES — done phases are reused, only unfinished ones re-run.

    SKIPS any job pinned to a retired model (gemini-2.5, retired 2026-08-03,
    see job_reactivation.retired_models_in_job) instead of resuming it — resume
    reuses the job's pinned provider/model verbatim, so re-enqueuing a
    retired-stamped job would call a dead model.

    ``wave_size``/``interval_seconds``: launch stagger (plan 2026-08-11).
    Resuming N failed lessons makes them all claimable at once, which is the
    same synchronised burst a fresh batch launch produces — and resume is the
    LIKELIER re-trigger, since retrying is how operators react to the failure.
    Defaults of 0 mean "no stagger", so any other caller is unaffected.

    Returns ``{"resumed": <count re-enqueued>, "skipped_retired": [<job id
    str>, ...]}``.
    """
    from app.services import job_reactivation
    from app.services.launch_stagger import stagger_offset

    rows = await session.execute(
        select(HomeworkJob)
        .where(
            HomeworkJob.batch_id == batch_id,
            HomeworkJob.status.in_(["failed", "cancelled"]))
        # Deterministic wave assignment: with no ORDER BY the DB may return rows
        # in any order, so which lesson lands in wave 0 would vary run to run.
        .order_by(HomeworkJob.created_at, HomeworkJob.id))
    jobs = list(rows.scalars().all())
    resumed = 0
    skipped_retired: list[str] = []
    for job in jobs:
        if job_reactivation.retired_models_in_job(job):
            skipped_retired.append(str(job.id))
            continue
        # Wave position is `resumed`, NOT the loop index: a skipped retired job
        # adds no load and must not consume a wave slot.
        await reset_for_retry(
            session, job.id,
            start_offset_seconds=stagger_offset(
                resumed, wave_size=wave_size, interval_seconds=interval_seconds))
        resumed += 1
    return {"resumed": resumed, "skipped_retired": skipped_retired}
```

- [ ] **Step 4: Run the tests — all green**

```bash
uv run python -m pytest tests/repositories/test_launch_stagger_repo.py -q
uv run python -m pytest tests/ -q -k "resume"
```

- [ ] **Step 5: RED-proof (mandatory)**

Change `stagger_offset(resumed, ...)` to `stagger_offset(len(skipped_retired) + resumed, ...)` — the plausible wrong version. Re-run: `test_retired_jobs_do_not_consume_a_wave_slot` must fail with `assert [0, 0, 60] == [0, 60, 60]`. **Restore.**

- [ ] **Step 6: Commit**

```bash
[ "$(git rev-parse --abbrev-ref HEAD)" = "feat/batch-launch-stagger" ] || exit 1
git add app/repositories/jobs.py tests/repositories/test_launch_stagger_repo.py
git commit -m "feat(jobs): stagger batch resume across waves, deterministically ordered"
```

---

### Task 6: Wire the batch launcher

**Files:**
- Modify: `app/api/v1/batch.py` (imports; `_stagger_summary` helper; `launch_batch` loop `:332-435`)
- Test: `tests/api/test_batch_launch_stagger.py`

**Interfaces:**
- Consumes: `stagger_offset` (Task 1), `settings.batch_launch_wave_*` (Task 2), `jobs_repo.create(start_offset_seconds=)` (Task 3), `jobs_repo.reset_for_retry(start_offset_seconds=)` (Task 4).
- Produces: `stagger` key in the launch payload: `{wave_size, interval_seconds, jobs_launched, waves, last_start_offset_seconds}`.

**The load-bearing rule:** the wave counter increments **only** for jobs this launch actually makes claimable — `created` and `resumed`. `adopted` and `skipped` sections are already running or already done; they add no load and must not consume wave slots. Using the target index instead would spread a mostly-adopted relaunch of 8 new jobs across 5 waves for no reason.

**Do not touch:** the preview branch (`:285-314`, returns before any write — it must stay zero-write), and the retired-model 409 (`:382-400`).

- [ ] **Step 1: Write the failing test**

Create `tests/api/test_batch_launch_stagger.py`. Reuse the fixture shape from `tests/api/test_batch_class_filter.py` (`SimpleNamespace` rows, `_apply_common_monkeypatches`), extended to capture `jobs_repo.create` kwargs:

```python
"""Batch-launch wave stagger — endpoint wiring, both directions."""
import uuid
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

BOOK_ID = uuid.uuid4()
_ROWS = [
    SimpleNamespace(id=uuid.uuid4(), section_number=f"1.{i}",
                    section_title=f"Dars {i}", page_start=i, page_end=i + 1,
                    order_index=i)
    for i in range(1, 15)          # 14 plain lesson rows
]


def _fake_book():
    return SimpleNamespace(id=BOOK_ID, status="toc_ready", subject="geografiya",
                           grade="5", source_language="ru",
                           original_filename="g.pdf")


def _fake_launch_defaults():
    return SimpleNamespace(
        judge_provider="claude", judge_model="claude-sonnet-4-6", judge_transport="inherit",
        extract_provider="gemini", extract_model="gemini-3.5-flash-lite", extract_transport="api",
        solver_provider="claude", solver_model="claude-haiku-4-5-20251001", solver_transport="inherit",
        output_language="ru",
    )


def _wire(monkeypatch, batch_mod, *, offsets_sink, latest=None):
    async def _get_book(session, book_id):
        return _fake_book()

    async def _list_for_book(session, book_id):
        return list(_ROWS)

    async def _get_ld(session):
        return _fake_launch_defaults()

    async def _find_active(session, book_id, toc_entry_id, *, transport=None,
                           output_language):
        return None

    async def _latest(session, book_id, toc_entry_id, *, transport=None,
                      output_language):
        return latest

    async def _lock(session, book_id, toc_entry_id=None):
        return None

    async def _create(session, **kwargs):
        offsets_sink.append(kwargs.get("start_offset_seconds"))
        return SimpleNamespace(id=uuid.uuid4())

    async def _get_or_create_batch(session, **kwargs):
        return SimpleNamespace(id=uuid.uuid4(), book_id=BOOK_ID, paused_at=None,
                               transport=kwargs.get("transport"))

    async def _rollup(session, batch_id):
        return {}

    async def _archive_rollup(session, batch_id):
        return {"archived": 0, "unarchived": 0, "stale": 0}

    async def _toc_total(session, batch_id):
        return len(_ROWS)

    monkeypatch.setattr(batch_mod.books_repo, "get", _get_book)
    monkeypatch.setattr(batch_mod.books_repo, "lock_book_shared",
                        lambda session, book_id: _lock(session, book_id))
    monkeypatch.setattr(batch_mod.toc_repo, "list_for_book", _list_for_book)
    monkeypatch.setattr(batch_mod.launch_defaults_repo, "get", _get_ld)
    monkeypatch.setattr(batch_mod.jobs_repo, "find_active_for_section", _find_active)
    monkeypatch.setattr(batch_mod.jobs_repo, "latest_for_section", _latest)
    monkeypatch.setattr(batch_mod.jobs_repo, "lock_section_for_generate", _lock)
    monkeypatch.setattr(batch_mod.jobs_repo, "create", _create)
    monkeypatch.setattr(batch_mod.batches_repo, "get_or_create_for_book",
                        _get_or_create_batch)
    monkeypatch.setattr(batch_mod.batches_repo, "rollup_for_batch", _rollup)
    monkeypatch.setattr(batch_mod.batches_repo, "archive_rollup_for_batch",
                        _archive_rollup)
    monkeypatch.setattr(batch_mod.batches_repo, "toc_total_for_batch", _toc_total)


async def _launch(payload):
    from main import app
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://t") as c:
        return await c.post("/api/v1/jobs/batch",
                            headers={"Authorization": "Bearer 123"},
                            json=payload)


@pytest.mark.asyncio
async def test_large_launch_is_spread_across_waves(monkeypatch):
    from app.api.v1 import batch as batch_mod
    offsets = []
    _wire(monkeypatch, batch_mod, offsets_sink=offsets)
    monkeypatch.setattr(batch_mod.settings, "batch_launch_wave_size", 6)
    monkeypatch.setattr(batch_mod.settings, "batch_launch_wave_interval_seconds", 60)

    resp = await _launch({"book_id": str(BOOK_ID)})

    assert resp.status_code == 201
    # 14 lessons at wave 6 -> 6 x 0, 6 x 60, 2 x 120
    assert offsets == [0] * 6 + [60] * 6 + [120] * 2
    assert resp.json()["stagger"] == {
        "wave_size": 6, "interval_seconds": 60, "jobs_launched": 14,
        "waves": 3, "last_start_offset_seconds": 120}


@pytest.mark.asyncio
async def test_small_launch_is_not_staggered_at_all(monkeypatch):
    """The other direction: a launch that fits in one wave is untouched."""
    from app.api.v1 import batch as batch_mod
    offsets = []
    _wire(monkeypatch, batch_mod, offsets_sink=offsets)
    monkeypatch.setattr(batch_mod.settings, "batch_launch_wave_size", 6)
    monkeypatch.setattr(batch_mod.settings, "batch_launch_wave_interval_seconds", 60)

    resp = await _launch({"book_id": str(BOOK_ID),
                          "toc_entry_ids": [str(r.id) for r in _ROWS[:5]]})

    assert resp.status_code == 201
    assert offsets == [0, 0, 0, 0, 0]
    assert resp.json()["stagger"]["waves"] == 1
    assert resp.json()["stagger"]["last_start_offset_seconds"] == 0


@pytest.mark.asyncio
async def test_kill_switch_disables_the_stagger(monkeypatch):
    from app.api.v1 import batch as batch_mod
    offsets = []
    _wire(monkeypatch, batch_mod, offsets_sink=offsets)
    monkeypatch.setattr(batch_mod.settings, "batch_launch_wave_size", 0)
    monkeypatch.setattr(batch_mod.settings, "batch_launch_wave_interval_seconds", 60)

    resp = await _launch({"book_id": str(BOOK_ID)})

    assert resp.status_code == 201
    assert offsets == [0] * 14


@pytest.mark.asyncio
async def test_resumed_sections_share_the_same_wave_counter(monkeypatch):
    """A resumed job is as claimable as a created one, so both must advance the
    counter — otherwise a resume-heavy relaunch rebuilds the herd."""
    from app.api.v1 import batch as batch_mod
    offsets = []
    resume_offsets = []
    saved = SimpleNamespace(id=uuid.uuid4(), status="failed", provider="gemini",
                            model="gemini-3.6-flash", extract_provider=None,
                            extract_model=None, judge_provider=None,
                            judge_model=None, solver_provider=None,
                            solver_model=None)
    _wire(monkeypatch, batch_mod, offsets_sink=offsets, latest=saved)

    async def _reset(session, job_id, batch_id=None, *, start_offset_seconds=0):
        resume_offsets.append(start_offset_seconds)

    monkeypatch.setattr(batch_mod.jobs_repo, "reset_for_retry", _reset)
    monkeypatch.setattr(batch_mod.settings, "batch_launch_wave_size", 3)
    monkeypatch.setattr(batch_mod.settings, "batch_launch_wave_interval_seconds", 60)

    resp = await _launch({"book_id": str(BOOK_ID)})

    assert resp.status_code == 201
    assert offsets == []                      # everything resumed, nothing created
    assert resume_offsets[:7] == [0, 0, 0, 60, 60, 60, 120]
```

> **Implementer note:** the `_wire` helper is a starting point, not gospel — run it, and if `launch_batch` reaches a repo call this stub doesn't cover, add the stub. Do **not** loosen an assertion to make it pass. If `saved` needs more attributes for `retired_models_in_job` to return empty, add them; that guard must keep working unchanged.

- [ ] **Step 2: Run it and watch it fail**

```bash
uv run python -m pytest tests/api/test_batch_launch_stagger.py -q
```
Expected: `AttributeError: module 'app.api.v1.batch' has no attribute 'settings'`, and once that is stubbed, `offsets == [None, None, ...]` because `create` receives no `start_offset_seconds`.

- [ ] **Step 3: Write the implementation**

Add to `app/api/v1/batch.py` imports:

```python
from app.config import settings
from app.services.launch_stagger import stagger_offset
```

Add a module-level helper next to `_rollup_payload`:

```python
def _stagger_summary(launched: int, wave_size: int, interval_seconds: int) -> dict:
    """What the launcher did to `scheduled_at`, so an operator can tell a
    deliberately staggered launch apart from a stuck queue. `waves` is 1 and the
    offset 0 when the stagger is off or the launch fits inside one wave."""
    last = stagger_offset(max(launched - 1, 0), wave_size=wave_size,
                          interval_seconds=interval_seconds)
    waves = (last // interval_seconds) + 1 if interval_seconds > 0 and last > 0 else 1
    return {"wave_size": wave_size, "interval_seconds": interval_seconds,
            "jobs_launched": launched, "waves": waves,
            "last_start_offset_seconds": last}
```

In `launch_batch`, replace `created = adopted = skipped = resumed = 0` with:

```python
    created = adopted = skipped = resumed = 0
    # Launch stagger (plan 2026-08-11). `launched` counts only the jobs THIS
    # call actually makes claimable — created + resumed. Adopted/skipped
    # sections are already running or already done: they add no load and must
    # not consume a wave slot, or a mostly-adopted relaunch of 8 new jobs would
    # be spread over 5 waves for nothing.
    _wave_size = settings.batch_launch_wave_size
    _wave_interval = settings.batch_launch_wave_interval_seconds
    launched = 0
```

In the resume branch, replace the `reset_for_retry` call with:

```python
            await jobs_repo.reset_for_retry(
                session, latest.id, batch_id=batch.id,
                start_offset_seconds=stagger_offset(
                    launched, wave_size=_wave_size,
                    interval_seconds=_wave_interval))   # reuses done phases + adopts batch
            launched += 1
            resumed += 1
            continue
```

In the create branch, add the final kwarg and bump the counter:

```python
                               solver_model=res_solver_model,
                               start_offset_seconds=stagger_offset(
                                   launched, wave_size=_wave_size,
                                   interval_seconds=_wave_interval))
        launched += 1
        created += 1
```

Extend the payload update:

```python
    payload.update(jobs_created=created, jobs_adopted=adopted,
                   jobs_skipped=skipped, jobs_resumed=resumed,
                   rebill_warnings=rebill_warnings,
                   stagger=_stagger_summary(launched, _wave_size, _wave_interval))
```

- [ ] **Step 4: Run the tests — all green**

```bash
uv run python -m pytest tests/api/test_batch_launch_stagger.py -q
uv run python -m pytest tests/api tests/integration -q
```

- [ ] **Step 5: RED-proof (mandatory)**

Two sabotages, both required:
1. Change the create-branch offset argument to `stagger_offset(created, ...)` — `test_resumed_sections_share_the_same_wave_counter` must fail, proving the shared counter is really tested.
2. Change `launched += 1` in the resume branch to a no-op — the same test must fail with all-zero resume offsets.

**Restore after each.** Quote both failures.

- [ ] **Step 6: Commit**

```bash
[ "$(git rev-parse --abbrev-ref HEAD)" = "feat/batch-launch-stagger" ] || exit 1
git add app/api/v1/batch.py tests/api/test_batch_launch_stagger.py
git commit -m "feat(batch): stagger launched jobs across waves"
```

---

### Task 7: Wire the batch `/resume` endpoint

**Files:**
- Modify: `app/api/v1/batch.py` (`resume_batch`, `:511-537`)
- Test: `tests/api/test_batch_launch_stagger.py` (append)

**Interfaces:**
- Consumes: Task 5's `resume_failed_in_batch(wave_size=, interval_seconds=)`, Task 6's `_stagger_summary`.
- Produces: `stagger` key in the `/resume` payload.

**Do not touch** the advisory-lock / `session.expire` / re-fetch sequence (`:518-533`) — it is the BE-02 book-delete race guard and is load-bearing.

- [ ] **Step 1: Write the failing test**

Append to `tests/api/test_batch_launch_stagger.py`:

```python
@pytest.mark.asyncio
async def test_resume_endpoint_passes_the_wave_settings(monkeypatch):
    from app.api.v1 import batch as batch_mod
    from main import app

    batch_id = uuid.uuid4()
    seen = {}

    async def _resume(session, bid, *, wave_size=0, interval_seconds=0):
        seen["wave_size"] = wave_size
        seen["interval_seconds"] = interval_seconds
        return {"resumed": 7, "skipped_retired": []}

    async def _lock(session, book_id):
        return None

    monkeypatch.setattr(batch_mod.jobs_repo, "resume_failed_in_batch", _resume)
    monkeypatch.setattr(batch_mod.books_repo, "lock_book_shared", _lock)
    monkeypatch.setattr(batch_mod.settings, "batch_launch_wave_size", 6)
    monkeypatch.setattr(batch_mod.settings, "batch_launch_wave_interval_seconds", 60)

    async def _get(model, pk):
        return SimpleNamespace(id=batch_id, book_id=BOOK_ID)

    class _FakeSession:
        async def get(self, model, pk):
            return await _get(model, pk)

        def expire(self, obj):
            return None

        async def commit(self):
            return None

    app.dependency_overrides[batch_mod.get_session] = lambda: _FakeSession()
    try:
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://t") as c:
            resp = await c.post(f"/api/v1/jobs/batch/{batch_id}/resume",
                                headers={"Authorization": "Bearer 123"})
    finally:
        app.dependency_overrides.pop(batch_mod.get_session, None)

    assert resp.status_code == 200
    assert seen == {"wave_size": 6, "interval_seconds": 60}
    body = resp.json()
    assert body["jobs_resumed"] == 7
    # 7 jobs at wave 6 -> last one is in wave 1
    assert body["stagger"]["waves"] == 2
    assert body["stagger"]["last_start_offset_seconds"] == 60
```

- [ ] **Step 2: Run it and watch it fail**

Expected: `KeyError: 'stagger'` (and `seen == {}` if the endpoint passes no wave args).

- [ ] **Step 3: Write the implementation**

In `resume_batch`, replace the `resume_failed_in_batch` call and return with:

```python
    result = await jobs_repo.resume_failed_in_batch(
        session, batch_id,
        wave_size=settings.batch_launch_wave_size,
        interval_seconds=settings.batch_launch_wave_interval_seconds)
    await session.commit()
    return {"batch_id": str(batch_id), "jobs_resumed": result["resumed"],
            "jobs_skipped_retired": result["skipped_retired"],
            "stagger": _stagger_summary(
                result["resumed"], settings.batch_launch_wave_size,
                settings.batch_launch_wave_interval_seconds)}
```

- [ ] **Step 4: Run the tests — all green**

```bash
uv run python -m pytest tests/api/test_batch_launch_stagger.py -q
uv run python -m pytest tests/ -q
```
Expected: full suite green (baseline ≈2411 passed / 426 skipped from worklog 0170 — record the real numbers).

- [ ] **Step 5: RED-proof (mandatory)**

Drop the two wave kwargs from the `resume_failed_in_batch` call. Re-run — the test must fail with `assert {'wave_size': 0, 'interval_seconds': 0} == {'wave_size': 6, 'interval_seconds': 60}`. **Restore.**

- [ ] **Step 6: Commit**

```bash
[ "$(git rev-parse --abbrev-ref HEAD)" = "feat/batch-launch-stagger" ] || exit 1
git add app/api/v1/batch.py tests/api/test_batch_launch_stagger.py
git commit -m "feat(batch): stagger the /resume path too"
```

---

### Task 8: Acceptance — real generation smoke over `transport=api`

**Files:**
- Create: `scripts/smoke_launch_stagger.py`
- Create: `docs/research/2026-08-11-launch-stagger-smoke.md`

**This task is the controller's, not a subagent's.** It spends real money and touches a real credential.

**Why the scope is what it is.** The feature's entire runtime effect is the `scheduled_at` stamps the launcher writes; that a later `scheduled_at` delays a claim is pre-existing, already-tested behaviour (`jobs.py:551`, `tests/integration/test_clock_skew.py`). So the gate proves two things, and the expensive one stays at one lesson:

- **(a) Schedule proof — $0, zero model calls.** Run the real `launch_batch` in-process against a **scratch** DB (`127.0.0.1`, never `edu_copy`) with `BATCH_LAUNCH_WAVE_SIZE=2`, `BATCH_LAUNCH_WAVE_INTERVAL_SECONDS=60`, launching 6 lessons. Read `scheduled_at` straight back out of the DB and assert three distinct values ~60s apart, 2 jobs each, wave 0 at ~`now()`. Then assert `jobs_repo.queue_depth(session) == 2` — only wave 0 counts toward backpressure, which is the composition claim in fact 3 of the Approach section.
- **(b) Generation still works — ONE lesson, `transport=api`, in-process.** Prove the offset column doesn't break a real run end to end. **Estimated ≈$1.15** (measured: the 28-lesson batch billed ≈$1.15/lesson across 830 calls, 7.48M prompt / 2.58M output tokens).

**Pre-flight (all must hold before spending anything):**
```bash
# generation must be unpaused and the queue quiet
psql "$DSN" -c "select api_paused_at from budget_state;"
psql "$DSN" -c "select status, count(*) from homework_jobs where status in ('pending','running') group by 1;"
```
Confirmed clean at plan time: `api_paused_at` NULL, zero pending/running.

**Deliberately NOT run:** a 6-lesson production launch to observe a live opening burst. The head is pinned to v968, so production would run the *old* launcher and prove nothing about this code; and a real 6-lesson launch is ~$7 for an observation the schedule proof already establishes. **Flagged for the user as an optional operator-gated step at head-unfreeze** — it needs an explicit go-ahead, not this plan's.

- [ ] **Step 1: Write `scripts/smoke_launch_stagger.py`**

Requirements: `DATABASE_URL` **must** be passed explicitly and asserted to contain `127.0.0.1` and **not** `edu_copy`; assert `app.config.__file__` resolves inside this worktree (the parent-`.env` trap); run as `uv run python -m scripts.smoke_launch_stagger`.

- [ ] **Step 2: Run part (a) — schedule proof, $0**
- [ ] **Step 3: Run part (b) — one-lesson api generation; capture the real cost from `agent_usages`**
- [ ] **Step 4: Cancel any smoke jobs left behind; confirm no stray `running` rows**
- [ ] **Step 5: Write `docs/research/2026-08-11-launch-stagger-smoke.md`** — the observed `scheduled_at` values, the `queue_depth` reading, the one-lesson result, and the **exact** spend.
- [ ] **Step 6: Commit**

```bash
[ "$(git rev-parse --abbrev-ref HEAD)" = "feat/batch-launch-stagger" ] || exit 1
git add scripts/smoke_launch_stagger.py docs/research/2026-08-11-launch-stagger-smoke.md
git commit -m "test(launch): acceptance smoke for the wave stagger"
```

---

### Task 9: Finish — docs, rebase check, PR

**Files:**
- Modify: `docs/memory/MASTER_MEMORY.md` (worklog `## 0172`), `docs/memory/INDEX.md` (row 0172)
- Modify: `docs/HOW_IT_WORKS.md`, `docs/CODE_MAP.md`
- Rename: `docs/superpowers/plans/2026-08-11-batch-launch-stagger.md` → `docs/superpowers/plans/shipped/`

**Worklog number:** **0172**. 0171 is claimed by unmerged PR #131. The INDEX counter is unreserved and collisions are normal — **re-check the INDEX tail immediately before writing**, and if 0172 is taken, renumber **both** the INDEX row and the `## NNNN` heading.

- [ ] **Step 1: De-stale the reference docs**

`docs/HOW_IT_WORKS.md` — in the batch-launch/queue section, state that a launch stamps `scheduled_at` in waves, that ≤ wave-size launches are unaffected, and that staggered jobs are excluded from `queue_depth` so they never trip `/generate`'s 503.
`docs/CODE_MAP.md` — add `app/services/launch_stagger.py` with a one-line responsibility.

- [ ] **Step 2: Write worklog 0172 + the INDEX row**

Must record: the measured evidence (fan-out 5.54, extract p50 13.1s, 16 exhaustions, 81-in-flight-with-zero-failures counter-evidence); the closed form `(processes × AMC) − cap = 16`; **why raising `CREDENTIAL_MAX_CONCURRENT_GEMINI` was explored and rejected** (48-call fleet ceiling makes ≥48 all identical, Pro solver untested above 32, frozen fleet); the `ge=1` concurrency hardening as an in-scope-by-approval addition; **the ships-dark caveat**; and the smoke's exact spend.

- [ ] **Step 3: `git mv` the plan into `shipped/`**

- [ ] **Step 4: Rebase check (mandatory, immediately before pushing)**

```bash
git fetch origin
git log HEAD..origin/Nggaev-v2 --oneline
# if non-empty: git rebase origin/Nggaev-v2, resolve, then RE-RUN the full suite
uv run python -m pytest tests/ -q
```

- [ ] **Step 5: Commit + push + open the PR**

```bash
[ "$(git rev-parse --abbrev-ref HEAD)" = "feat/batch-launch-stagger" ] || exit 1
git add docs/memory/MASTER_MEMORY.md docs/memory/INDEX.md docs/HOW_IT_WORKS.md docs/CODE_MAP.md
git add docs/superpowers/plans/shipped/2026-08-11-batch-launch-stagger.md
git commit -m "docs: worklog 0172 — batch-launch wave stagger"
git log origin/Nggaev-v2..HEAD --oneline    # verify base before pushing
git push -u origin feat/batch-launch-stagger
```

**Open the PR and STOP. Do not merge** — GK2 gates and merges.

The PR body must lead with: this is sized for the **current** concurrency configuration and changes none of it; it **ships dark** until the head leaves v968; and the interim mitigation is chunked launches of ~6 via `toc_entry_ids`.

---

## Self-review

**Spec coverage.** Filed defect (unguarded `create` loop at `batch.py:406`) → Tasks 3 + 6. Configurable + documented → Task 2. Justified default from the measured numbers → Approach + Task 2. Both test directions → Tasks 1, 6 (`test_small_launch_is_not_staggered_at_all`, `test_kill_switch_disables_the_stagger`). `/generate` untouched → Global Constraints, no task edits `jobs.py:252-265`. Retired-model guard untouched → Tasks 5 + 6 both say so. RED-proof → a mandatory Step 5 on every code task. Real api smoke, bounded and costed → Task 8. Worklog + INDEX + plan `git mv` + de-staled refs + rebase check + PR-not-merged → Task 9.

**Placeholder scan.** No TBDs; every code and test block is complete and runnable. The one judgement call left to the implementer is explicit and bounded: extend the `_wire` stub if `launch_batch` reaches an uncovered repo call — with an explicit instruction never to loosen an assertion instead.

**Type consistency.** `stagger_offset(index, *, wave_size, interval_seconds) -> int` is called identically in Tasks 5, 6, 7. `start_offset_seconds: int = 0` is the parameter name on both `create` and `reset_for_retry`. `resume_failed_in_batch` keeps its `{"resumed", "skipped_retired"}` return shape. `_stagger_summary` returns the same five keys in both payloads, and the Task 6/7 assertions match it exactly.

**Known gap, deliberate.** No FE change: the `stagger` payload key is additive and unread by the SPA, so a staggered launch still shows jobs as plain "queued". Acceptable — the operator-facing signal exists in the API response, and touching the FE would widen the diff past the filed defect. Filed as a WISHLIST follow-up in Task 9.
