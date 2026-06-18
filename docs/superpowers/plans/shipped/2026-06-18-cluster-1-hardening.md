# Cluster 1 — Quick-win hardening (worklog 0077)

## Approach & key decisions

Batch of independent hardening fixes; each its own task + commit + test. The only schema
change is the CHECK-constraint migration (online-safe; small tables, brief lock accepted).
`docs/DEPLOY.md` is de-staled with the knob fix. **No generation-path changes** → unit tests
are the proof; no CLI smoke needed.

Load-bearing facts verified against tip `e74274a`:
- **Migration head = `0027_per_role_provider_model`** (`uv run alembic heads`) → new migration is **`0028`**.
- **Knob inversion** is real: `agent.py:203` builds the semaphore from `settings.gemini_max_concurrency`; `config.py:62` documents `agent_max_concurrency` as the live knob but it has **zero readers**. Both default to 8 (behavior-identical today). DEPLOY.md (`:47-48`, `:257-258`) inherited the inversion.
- **Judge has no timeout**: generation is wrapped `asyncio.wait_for(..., timeout=settings.per_attempt_timeout_seconds)` (`pipeline.py:601-603`); the two `phase_judge.judge()` calls (`pipeline.py:861`, `:896`) are not. `judge()` already degrades to `JudgeOutcome(available=False, passed=True, warnings=["judge-unavailable: …"])` on any internal error (`phase_judge.py:174-178`) — a timeout must reach the same outcome, NOT kill the job.
- **Stale-pending gap**: `reclaim_stuck_jobs` only touches `status='running'` (`jobs.py:360-375`); `claim_next_job` WHERE skips `attempts >= max_attempts` (`jobs.py:247`); `mark_failed_with_retry` only runs for *claimed* jobs. So a `pending` row with `attempts >= max_attempts` is never claimed and never failed → stuck forever. Fix = a new sweep marking those `failed`, called from `main.lifespan` (startup) + `worker._sweep_stuck_jobs` (periodic).
- **`flow-1`**: `pipeline.py:508` embeds the dep dict as a literal `{{p: … for p in …}}` inside an f-string → renders as static text in the "scheduler stuck" RuntimeError.
- **`extract-2`**: `config.py:147` `extract_provider: str = "gemini"`; a blank `EXTRACT_PROVIDER=` in `.env` is read by pydantic as `""` (a value, not absence) → `get_provider("")` KeyError (`providers/__init__.py:36`). Fix = a `field_validator` mapping blank→default.
- **`test-hygiene-1`**: 4 modules set module-level `app.dependency_overrides[get_current_user]` at import and never clear it (`test_cancel_endpoint.py:8`, `test_retry_cancelled.py`, `test_notion_router.py`, `test_from_notion.py`) → global auth bypass leaks. `test_book_source_pdf.py:55` already carries a local `monkeypatch.delitem(...)` workaround that should become removable.
- **`dep-cve-1`**: installed `pypdf 6.10.2`, `starlette 1.0.0`, `python-multipart` (floor `>=0.0.20`). `fastapi 0.136.1` pins only `starlette>=0.46.0` (floor, no upper cap) → a starlette bump is permitted by the resolver but runtime compat is the risk → **full suite is the gate**.

CHECK-constraint value sets (verified from CLAUDE.md + grep of live `status == '…'` usages):
- `homework_jobs.status` ∈ {pending, running, done, failed, cancelling, cancelled}
- `homework_jobs.transport` / `batches.transport` ∈ {cli, api}
- `*.extract_transport` / `*.judge_transport` ∈ {cli, api, inherit}

Rejected alternative for the knob: deleting `gemini_max_concurrency` outright — rejected because existing `.env`s set `GEMINI_MAX_CONCURRENCY`; instead make it a deprecated fallback (read `agent_max_concurrency`, fall back to the old name only if the new one is unset relative to default) so no operator config silently breaks.

Task order is dependency-free; executed top-to-bottom, **commit per task**, prefix `c1:`.

---

## Task 1 — `dep-cve-1`: bump vulnerable deps

**Files:** `pyproject.toml`, `uv.lock` (regenerated), `web/package.json` + `web/package-lock.json` (npm audit fix).

No new unit test (dependency bump). Proof is `pip-audit` + the full suite + tsc/build at the acceptance gate.

1. In `pyproject.toml` bump floors: `pypdf>=6.13.3`, `python-multipart>=0.0.31`, and **add** a direct floor `starlette>=1.3.1` (currently transitive).
2. `uv lock` then `uv sync --extra dev`.
3. `cd web && npm audit fix` (react-router); if it cannot fix without `--force`, STOP and report rather than force a major bump.
4. Verify: `uv run pip-audit 2>&1 | grep -iE "pypdf|starlette|python-multipart"` shows no findings for the three; `uv run python -c "import pypdf,starlette,multipart; print(pypdf.__version__, starlette.__version__)"` shows the bumped versions.

**Commit:** `c1: bump pypdf/python-multipart/starlette past CVEs (dep-cve-1)`

> ⚠️ Run the WHOLE suite after this task locally before moving on — the starlette bump is the FastAPI-compat risk.

---

## Task 2 — `flow-1`: fix the dead f-string in the scheduler-stuck diagnostic

**File:** `app/services/pipeline.py` (~`:506-509`). **Test:** `tests/services/test_pipeline_flow1.py` (new, pure — no DB/IO).

TDD:
1. Write a test that builds the diagnostic message the same way the code does (extract the formatting into a tiny pure helper `_scheduler_stuck_message(pending, content_phases)` returning the string) and asserts the resolved-deps dict is **interpolated** (e.g. the message contains the actual dep list for a known phase, and does NOT contain the literal substring `"for p in"`). Red first (current literal renders `for p in`).
2. Implement: add `_scheduler_stuck_message(...)` that computes `{p: sorted(resolve_phase_deps(p, content_phases)) for p in sorted(pending)}` as a real value and formats it in; call it from the `raise RuntimeError(...)` site.
3. Green: `uv run python -m pytest tests/services/test_pipeline_flow1.py -q`.

**Commit:** `c1: interpolate resolved-deps in scheduler-stuck diagnostic (flow-1)`

---

## Task 3 — `concurrency-knob-1` (read-the-right-knob half)

**Files:** `app/services/agent.py` (`:200-204` + the module docstring `:12-14`, `:194-196`), `app/config.py` (`:59-63` comments), `docs/DEPLOY.md` (`:47-48`, `:257-258`). **Test:** `tests/services/test_agent_semaphore.py` (new).

TDD:
1. Test: monkeypatch `settings.agent_max_concurrency = 3`, reset `agent._agent_semaphore = None`, call `agent._semaphore()`, assert `._value == 3`. Second test: with `agent_max_concurrency` left at default and `gemini_max_concurrency` set, assert the alias path still honors a non-default deprecated value (back-compat). Red first (today reads `gemini_max_concurrency`).
2. Implement an `_effective_concurrency()` helper: return `agent_max_concurrency` when it differs from its default (8), else `gemini_max_concurrency` (so an operator who set only the old `GEMINI_MAX_CONCURRENCY` still works, and one who set the new one wins). Point `_semaphore()` at it. Update the docstrings to name `agent_max_concurrency` as live and `gemini_max_concurrency` as the deprecated fallback.
3. De-stale `docs/DEPLOY.md`: flip the two table rows (`AGENT_MAX_CONCURRENCY` = live, `GEMINI_MAX_CONCURRENCY` = deprecated fallback) and the checklist line `:257-258`. **Note in the doc that the cap is still per-process** (fleet-wide token-bucket is Cluster 5, out of scope here).
4. Green: `uv run python -m pytest tests/services/test_agent_semaphore.py -q`.

**Commit:** `c1: semaphore reads agent_max_concurrency, gemini_* deprecated-fallback + DEPLOY de-stale (concurrency-knob-1)`

---

## Task 4 — `extract-2`: blank `EXTRACT_PROVIDER` → default

**File:** `app/config.py` (add `field_validator`; import it from `pydantic`). **Test:** `tests/test_config_extract_provider.py` (new).

TDD:
1. Test: construct `Settings(extract_provider="")` (or set env `EXTRACT_PROVIDER=""` via monkeypatch + reload) → assert `.extract_provider == "gemini"`. A second test: a real value (`"claude"`) passes through unchanged. Red first.
2. Implement `@field_validator("extract_provider", mode="before")` that maps a blank/whitespace-only string to the field default `"gemini"`. (Scope strictly to `extract_provider` per the WISHLIST item.)
3. Green: `uv run python -m pytest tests/test_config_extract_provider.py -q`.

**Commit:** `c1: blank EXTRACT_PROVIDER falls back to default (extract-2)`

---

## Task 5 — `judge-timeout-1`: wrap the judge call in the per-attempt timeout

**File:** `app/services/pipeline.py` (the two `phase_judge.judge(...)` sites, `:861` and `:896`). **Test:** `tests/services/test_judge_timeout.py` (new — exercises a small real wrapper, mocks only the judge I/O).

Approach: extract a thin async helper `_judge_with_timeout(**kwargs) -> JudgeOutcome` that does
`asyncio.wait_for(phase_judge.judge(**kwargs), timeout=settings.per_attempt_timeout_seconds)` and on
`asyncio.TimeoutError` returns `JudgeOutcome(available=False, passed=True, warnings=["judge-unavailable: TimeoutError"], feedback="")` — the same shape `phase_judge.judge` itself produces on failure, so downstream (`outcome.available`/`has_major`) treats it as unavailable and the phase completes `done`. **Do not** let the auth-error re-raise path swallow this — a TimeoutError is not an auth error, so the existing `_is_auth_error` guard at `:905` is unaffected.

TDD:
1. Test runs the REAL `_judge_with_timeout` body with a stub `phase_judge.judge` that `await asyncio.sleep`s past a monkeypatched tiny `per_attempt_timeout_seconds` → assert the returned outcome has `available is False` and a `judge-unavailable: TimeoutError` warning and that **no exception propagates**. A second test: a fast stub returning a normal `JudgeOutcome` passes straight through. Red first (helper doesn't exist).
2. Implement the helper; replace both `await phase_judge.judge(...)` call sites with `await _judge_with_timeout(...)` (identical kwargs).
3. Green: `uv run python -m pytest tests/services/test_judge_timeout.py -q`.

**Commit:** `c1: per-attempt timeout on judge calls → judge-unavailable, not job kill (judge-timeout-1)`

---

## Task 6 — Stale-pending sweep

**Files:** `app/repositories/jobs.py` (new `fail_exhausted_pending_jobs`), `main.py` (lifespan), `app/services/worker.py` (`_sweep_stuck_jobs`). **Test:** `tests/repositories/test_fail_exhausted_pending.py` (new — real function body, mocks only the session.execute boundary OR uses the existing DB-integration harness if present; default to a unit test that asserts the built UPDATE statement targets `pending` + `attempts >= max_attempts` and sets `failed`).

TDD:
1. Test the REAL `fail_exhausted_pending_jobs` body: with a fake session capturing the executed statement, assert (a) it filters `status == 'pending'` AND `attempts >= max_attempts`, (b) sets `status='failed'` + `completed_at=func.now()` + an `error_message` like `"attempts exhausted"`, and (c) returns `rowcount`. (Mirror the construction style of `reclaim_stuck_jobs`.) Red first (function absent).
2. Implement `fail_exhausted_pending_jobs(session, *, max_attempts) -> int`.
3. Wire it: in `main.lifespan` after the `reclaim_stuck_jobs` call (`main.py:52`) add `await jobs_repo.fail_exhausted_pending_jobs(session, max_attempts=settings.queue_max_attempts)`; in `worker._sweep_stuck_jobs` add the same call inside the existing `session.begin()` block, logging a count when > 0.
4. Green: `uv run python -m pytest tests/repositories/test_fail_exhausted_pending.py -q`.

**Commit:** `c1: sweep attempts-exhausted pending jobs to failed (stale-pending)`

---

## Task 7 — `test-hygiene-1`: stop the auth-override leak

**Files:** `tests/api/test_cancel_endpoint.py`, `tests/api/test_retry_cancelled.py`, `tests/api/test_notion_router.py`, `tests/api/test_from_notion.py`; remove the now-unneeded workaround in `tests/api/test_book_source_pdf.py:55`.

Approach: in each of the 4 modules replace the module-level `app.dependency_overrides[get_current_user] = …` with an `@pytest.fixture(autouse=True)` that sets the override on setup and `app.dependency_overrides.pop(get_current_user, None)` on teardown — self-contained so each module still passes in isolation but nothing leaks across the suite.

Steps:
1. Convert all 4 modules.
2. Remove the `monkeypatch.delitem(app.dependency_overrides, get_current_user, raising=False)` workaround line in `test_book_source_pdf.py` (and drop `monkeypatch` from that test's signature if now unused).
3. Verify the leak is gone by running an order that previously bypassed auth, e.g. `uv run python -m pytest tests/api/test_cancel_endpoint.py tests/api/test_book_source_pdf.py -q` — `test_401_without_token` must pass, and the whole `tests/api/` dir green.

**Commit:** `c1: scope api-test auth overrides to fixtures w/ teardown (test-hygiene-1)`

---

## Task 8 — `db-check-constraints-1`: CHECK constraints migration

**Files:** new `alembic/versions/0028_enum_check_constraints.py`; `app/models/homework_job.py` + `app/models/batch.py` (add matching `CheckConstraint`s to `__table_args__` so the ORM/metadata agree with the DB). **Test:** `tests/repositories/test_check_constraints.py` (real-DB; gated behind `RUN_DB_INTEGRATION=1` like the other DB tests — assert a bad `status` insert raises `IntegrityError`).

TDD:
1. Test (DB-integration): insert a `homework_jobs` row with `status='bogus'` → expect `IntegrityError`; a valid `status='pending'` row succeeds. Mark/skip when `RUN_DB_INTEGRATION` unset, consistent with the existing real-DB tests.
2. Add `CheckConstraint`s to both models' `__table_args__`:
   - `homework_jobs`: `ck_homework_jobs_status` (6 values), `ck_homework_jobs_transport` (cli/api), `ck_homework_jobs_extract_transport` + `ck_homework_jobs_judge_transport` (cli/api/inherit).
   - `batches`: `ck_batches_transport`, `ck_batches_extract_transport`, `ck_batches_judge_transport`.
3. Hand-write migration `0028` (`down_revision = "0027_per_role_provider_model"`): `op.create_check_constraint(...)` for each (upgrade) + `op.drop_constraint(...)` (downgrade). Small tables → accept a brief lock (no NOT VALID needed). Verify `uv run alembic heads` shows a single head `0028…`.
4. Green: `uv run alembic upgrade head` against the local DB, then the DB-integration test.

**Commit:** `c1: CHECK constraints on status/transport enums + 0028 migration (db-check-constraints-1)`

---

## Acceptance gate (all must pass, in the worktree)

1. `uv run python -m pytest tests/ -q` — full suite green (the starlette-bump regression check).
2. `uv run pip-audit` — clean on pypdf / python-multipart / starlette.
3. `cd web && npx tsc -p tsconfig.app.json --noEmit && npm run build` — clean (npm audit fix touched lockfile only; verify FE still builds).
4. `uv run alembic upgrade head` + `alembic heads` shows single head `0028…`.

No CLI smoke — no generation path changed.

## Finish (after gate green)

1. `git fetch origin` → `git log HEAD..origin/Nggaev-v2`; if base moved, **rebase onto `origin/Nggaev-v2`** and re-run the suite.
2. Worklog **`[0077]`** block in `docs/memory/MASTER_MEMORY.md` + INDEX row in `docs/memory/INDEX.md` (append-only; expect a trivial conflict with sibling clusters — keep both blocks).
3. Close the WISHLIST items shipped: `dep-cve-1`, `judge-timeout-1`, `concurrency-knob-1` (read-the-right-knob half only — leave the fleet-wide token-bucket note for Cluster 5), `db-check-constraints-1`, `test-hygiene-1`, `extract-2`, `flow-1`, and the stale-pending sweep line.
4. `git mv docs/superpowers/plans/2026-06-18-cluster-1-hardening.md docs/superpowers/plans/shipped/`.
5. `docs/DEPLOY.md` de-stale already done in Task 3 — confirm it's staged.
6. **Stage only each task's listed files** (never `git add -A` — the shared backlog/worklog files are the trap).
7. Open PR titled `[cluster-1] Quick-win hardening (0077)`. **Do NOT self-merge** — the gatekeeper verifies + merges.
