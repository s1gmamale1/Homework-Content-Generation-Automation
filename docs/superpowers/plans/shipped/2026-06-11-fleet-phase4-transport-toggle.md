# Fleet Phase 4 — CLI | API transport toggle (implementation plan)

> **For agentic workers:** REQUIRED SUB-SKILL: use superpowers:subagent-driven-development (one fresh subagent per task, TDD → commit) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) tracking. The controller stress-tests every commit (read the diff **and** re-run the gate) before moving on.

**Goal:** A per-job (and per-batch) `transport` toggle — `cli` (today's subscription auth, $0 marginal, default) or `api` (pay-per-token, claude + gemini only) — so the operator can empirically benchmark CLI-vs-API for mass generation. Ships the real-time toggle only; batch-discount transport stays deferred (`fleet-api-1`).

**Spec:** `docs/superpowers/specs/2026-06-10-fleet-phase4-transport-toggle-design.md` (locked with user 2026-06-10; claim-gate mechanism pinned in `3df7529`).

**Architecture (verified against source 2026-06-11):**
- `transport` enum threads **job → pipeline → `_spawn`**. The auth transform lands in one pure helper `_auth_env(provider, transport, base_env)` consumed by `agent._spawn` (`agent.py:224`, builds `child_env` at `:257`). `transport` defaults to `"cli"` at every layer, so **every spawn — including book-upload TOC extraction, which has no job — gets the cli baseline** (spec §3 invariant).
- Validation at the two creation entry points: `POST /generate` (`jobs.py:121`) and `POST /jobs/batch` (`batch.py:75`).
- Worker fail-fast: `claim_next_job` (`jobs.py:207`) gates `api` jobs on a startup-computed `has_api_keys`.
- Attribution: `agent_usages.auth_mode` recorded on every row; static price map drives a per-transport `$` rollup.

**Tech stack:** Backend — FastAPI, SQLAlchemy async, Postgres, Alembic. Frontend — React 19, react-router, @tanstack/react-query, Tailwind, `lib/ui.ts`.

**Environment-agnostic test invocation** (runs on this Mac now; Windows worker PCs later — **do not hardcode paths/ports**). Set once per shell before the DB tasks:
```bash
# repo root — the dir containing pyproject.toml
export REPO=$(git rev-parse --show-toplevel)
# a throwaway/migrated Postgres reachable from this box. Mac dev default is 5433
# (CLAUDE.md); a Windows worker box may use its own. Override as needed.
export DB_URL=${DATABASE_URL:-postgresql+asyncpg://edu:edu@localhost:5433/edu_homework}
PYBIN="uv run python"   # or the venv python on a worker box
```
- **Backend real-DB tests:** `cd "$REPO" && RUN_DB_INTEGRATION=1 DATABASE_URL="$DB_URL" $PYBIN -m pytest <path> -q`
- **DB-free unit tests:** `cd "$REPO" && $PYBIN -m pytest <path> -q`
- **Full-suite baseline (record before Task 1):** `cd "$REPO" && $PYBIN -m pytest tests/ -q` → note the exact `N failed / M passed / K skipped` line; every task must hold it (the pre-existing Notion failures are the only allowed reds).
- **Frontend gate** (no FE unit harness): `cd "$REPO/web" && npx tsc -p tsconfig.app.json --noEmit` clean **and** `npm run build` succeeds.

**Pre-flight (before Task 1):**
1. Confirm the alembic head: `cd "$REPO" && $PYBIN -m alembic heads` → expect the `0023_batches` revision. The new migration's `down_revision` is **that printed revision id** (read it from `alembic/versions/0023_batches.py`, do not assume the filename == revision id).
2. Confirm a migrated Postgres is reachable at `$DB_URL` (`alembic upgrade head` against it).
3. `cd "$REPO/web" && npm install` current before the FE task.

**Standing rules:** stage ONLY the files each task lists (other sessions touch `web/`); commit per task; **the live billed API smokes (spec §7.1/§7.2/§7.5) are operator-run** — this plan implements + unit/mode-isolation/claim-gate tests in-band and the free invalid-key 401 proof, and documents the billed gates as a runbook (Task 8).

---

## File Structure

**Backend:**
- `app/models/homework_job.py`, `app/models/batch.py` — add `transport` column.
- `app/models/agent_usage.py` — add `auth_mode` column.
- `alembic/versions/0024_transport_auth_mode.py` — new migration (3 columns, server_default `'cli'`).
- `app/services/agent.py` — `_auth_env` helper; thread `transport` through `_spawn` (+ its 4 call sites), `run_phase`, `run_phase_prompt`; thread `auth_mode` through `_record_usage`.
- `app/services/agent_models.py` — `API_PROVIDERS` + `api_supported()`; manifest endpoint gains `api_supported`.
- `app/api/v1/jobs.py` — `transport` on the generate request + validation + persist; manifest endpoint; claim-gate wiring.
- `app/api/v1/batch.py` — `transport` on `BatchLaunchRequest` + validation + persist on batch & jobs.
- `app/repositories/jobs.py` — `create(...transport=)`; `claim_next_job(..., has_api_keys)`.
- `app/repositories/batches.py` — `get_or_create_for_book(...transport=)` persists `transport` on the batch row (see Task 3 Step 6 for the re-launch-staleness decision).
- `app/services/worker.py` — compute `has_api_keys` at startup; startup gemini `selectedType` warning; pass to claim.
- `app/services/pipeline.py` — read `job.transport`, thread into `_execute_phase` → run fns + extract.
- `app/services/phase_judge.py` — thread `transport`; loud auth-failure on api jobs.
- `app/services/pricing.py` — **new** static price map + `cost_usd(provider, model, usage)`.
- `app/repositories/agent_usage.py` — aggregation grouped by `auth_mode`.
- Tests under `tests/` per task.

**Frontend:**
- `web/src/lib/types.ts` — `Transport`, `transport` on Job/Batch, `api_supported` on manifest.
- `web/src/lib/api.ts` — `transport` in generate + launchBatch bodies.
- Generate form + `web/src/components/fleet/launcher.tsx` — segmented `CLI | API` toggle.
- Job/batch views — `api` badge; `web/src/routes/usage.tsx` — per-transport `$` rollup.

---

### Task 1: Schema — `transport` (jobs, batches) + `auth_mode` (agent_usages)

**Files:** Modify `app/models/homework_job.py`, `app/models/batch.py`, `app/models/agent_usage.py`; Create `alembic/versions/0024_transport_auth_mode.py`; Test `tests/integration/test_transport_schema.py` (new).

- [ ] **Step 1: Failing real-DB test** — `tests/integration/test_transport_schema.py`: after `alembic upgrade head`, a freshly inserted `HomeworkJob` / `Batch` defaults `transport == "cli"` and a freshly inserted `AgentUsage` defaults `auth_mode == "cli"`; an explicit `transport="api"` round-trips. (Seed a minimal book+toc like `tests/integration/test_batches.py` `_seed_book`.)
- [ ] **Step 2: Run it — expect failure** (column missing).
- [ ] **Step 3: Add columns to the models** (mirror existing `mapped_column` style):
  - `homework_job.py` (after `model`, ~`:26`) and `batch.py` (after `model`, ~`:23`):
    ```python
    transport: Mapped[str] = mapped_column(String(16), nullable=False, server_default="cli")
    ```
  - `agent_usage.py` (after `model_name`, ~`:42`):
    ```python
    auth_mode: Mapped[str] = mapped_column(String(8), nullable=False, server_default="cli")
    ```
- [ ] **Step 3b: Batch key → `(book_id, transport)`** (spec §9 amendment) — in `batch.py`: replace `__table_args__ = (UniqueConstraint("book_id", name="uq_batches_book_id"),)` with `UniqueConstraint("book_id", "transport", name="uq_batches_book_id_transport")`, and **fix the now-false docstring** (`:11` — "UNIQUE(book_id) -> at most one logical batch per book" → "UNIQUE(book_id, transport) -> one batch per (book, transport); a different-transport re-launch forks a new batch, same-transport reuses it"). The `transport` column from Step 3 must be `NOT NULL` (it is) so it can sit in the unique key.
- [ ] **Step 4: Migration** `alembic/versions/0024_transport_auth_mode.py` — `down_revision = "a1b2c3d4e5f6"` (the `0023_batches` revision id; confirm in pre-flight). `upgrade()`: (1) `op.add_column` the 3 columns with `server_default="cli"`, `nullable=False` (covers existing rows — no separate backfill); (2) **add `transport` to the batch unique key** — `op.drop_constraint("uq_batches_book_id", "batches", type_="unique")` then `op.create_unique_constraint("uq_batches_book_id_transport", "batches", ["book_id", "transport"])` (the add-column must run before this so the column exists). `downgrade()` reverses both in inverse order. **Order matters:** existing batch rows all get `transport='cli'` via the server_default, so no two collide on the new key. **Downgrade caveat (one comment line in the migration):** recreating `UNIQUE(book_id)` in `downgrade()` will *fail* if by then any book has both a cli and an api batch (the §9 fan-out) — acceptable for a dev migration, but note it so the implementer isn't surprised.
- [ ] **Step 5: Apply + run the test green** — `RUN_DB_INTEGRATION=1 DATABASE_URL="$DB_URL" $PYBIN -m alembic upgrade head` then the Step-1 test.
- [ ] **Step 6: Full-suite baseline holds** (DB-free) — no new failures.
- [ ] **Step 7: Commit** — `git add app/models/homework_job.py app/models/batch.py app/models/agent_usage.py alembic/versions/0024_transport_auth_mode.py tests/integration/test_transport_schema.py` → `feat(fleet): transport + auth_mode columns (Phase 4)`.

---

### Task 2: Auth-env adapter + `transport` threading through `_spawn`

The core. Pure, DB-free, CLI-free — fully unit-testable (satisfies spec §7.3 mode-isolation).

**Files:** Modify `app/services/agent.py`; Test `tests/services/test_auth_env.py` (new).

- [ ] **Step 1: Failing unit test** `tests/services/test_auth_env.py` — call `agent._auth_env(provider, transport, base_env)` directly with a `base_env` that contains **both** keys + `PYTHONIOENCODING`:
  - `("gemini", "cli", env)` → `GOOGLE_GENAI_USE_GCA == "true"`, `"GEMINI_API_KEY" not in result`, `"ANTHROPIC_API_KEY" not in result`.
  - `("claude", "cli", env)` → both keys absent, no `GOOGLE_GENAI_USE_GCA`.
  - `("gemini", "api", env)` → `GEMINI_API_KEY` present, `ANTHROPIC_API_KEY` absent, `GOOGLE_GENAI_USE_GCA` absent.
  - `("claude", "api", env)` → `ANTHROPIC_API_KEY` present, `GEMINI_API_KEY` absent.
  - `("kimi", "cli", env)` → both keys scrubbed (hygiene), unchanged otherwise; `PYTHONIOENCODING` preserved in all cases.
  - **`("claude", "api", env_without_ANTHROPIC_API_KEY)` → raises `RuntimeError`** (loud missing-key, not silent `""` injection); same for `("gemini", "api", env_without_GEMINI_API_KEY)`.
- [ ] **Step 2: Run it — expect failure** (no `_auth_env`).
- [ ] **Step 3: Implement `_auth_env`** in `agent.py` (near `_spawn`):
  ```python
  def _auth_env(provider_name: str, transport: str, base_env: dict[str, str]) -> dict[str, str]:
      """Per-call auth shaping (spec §4). cli is the unconditional baseline for
      EVERY spawn; api is the only deviation. Scrub both provider keys first, then
      grant exactly what the (provider, transport) needs — so an api gemini spawn
      never carries the Anthropic key, and a cli spawn never accidentally bills."""
      env = dict(base_env)
      env.pop("GEMINI_API_KEY", None)
      env.pop("ANTHROPIC_API_KEY", None)
      env.pop("GOOGLE_GENAI_USE_GCA", None)
      if transport == "api":
          # Missing key in api mode must be LOUD: an empty env var is falsy to
          # both CLIs → claude would silently fall back to OAuth (billing the
          # subscription while the row says auth_mode=api). The claim gate makes
          # this near-unreachable, but defense-in-depth for this phase's exact
          # failure class. Raise rather than inject "".
          key_var = {"gemini": "GEMINI_API_KEY", "claude": "ANTHROPIC_API_KEY"}.get(provider_name)
          key = base_env.get(key_var) if key_var else None
          if not key:
              raise RuntimeError(f"transport=api for {provider_name} but {key_var} is unset/empty")
          env[key_var] = key
          # kimi/codex/opencode never reach api (blocked at validation)
      else:  # cli baseline
          if provider_name == "gemini":
              env["GOOGLE_GENAI_USE_GCA"] = "true"  # GCA OAuth, wins over any key
          # claude/others: scrubbed keys above IS the whole cli adapter
      return env
  ```
- [ ] **Step 4: Thread `transport` into `_spawn`** — add `transport: str = "cli"` to the `_spawn` signature (`agent.py:224`) and apply the adapter at the `child_env` build (`:257`):
  ```python
  child_env = _auth_env(provider.name, transport, {**os.environ, "PYTHONIOENCODING": "utf-8"})
  ```
- [ ] **Step 5: Add `transport` to `run_phase` + `run_phase_prompt`** (`:498`, `:1508`), default `"cli"`, and pass it into every `_spawn(...)` call. There are **four** `_spawn` call sites — `:566` (`run_phase`), `:1097` (`extract_toc`), `:1338` (`extract_lesson_context`), `:1480` (`summarize_lesson`). The latter three are reached via their own wrapper functions: add a `transport` param (default `"cli"`) to each and pass it through, so no caller silently loses the toggle. (Read each wrapper's signature; thread, don't guess.) **Note:** `extract_lesson_context` (`:1268`) is **dead code** — unused by the pipeline since worklog 0035 (no caller in `app/` or `tests/`; only its `__all__` export references it). Thread it for consistency/no-broken-signature, but don't go hunting for a live caller — there isn't one. The live extract path is `summarize_lesson` (`:1442`/`:1480`).
- [ ] **Step 6: Run the unit test green** + a quick threading assertion: monkeypatch `_auth_env` to capture args, call `run_phase(..., transport="api", provider="claude")` with a stubbed subprocess (or assert via the existing `_spawn` seam) → `_auth_env` saw `("claude", "api", ...)`. Keep it DB-free (stub `_record_usage`).
- [ ] **Step 7: Full-suite baseline holds.**
- [ ] **Step 8: Commit** — `git add app/services/agent.py tests/services/test_auth_env.py` → `feat(fleet): per-call auth-env adapter + transport threading through _spawn (Phase 4)`.

---

### Task 3: Validation + persistence at creation (generate + batch) + manifest flag

**Files:** Modify `app/services/agent_models.py`, `app/api/v1/jobs.py`, `app/api/v1/batch.py`, `app/repositories/jobs.py`, `app/repositories/batches.py`; Tests `tests/api/test_transport_validation.py` (new) + extend `tests/integration/test_batches.py` + update `tests/integration/test_batches_repo.py` (per-`(book, transport)` idempotency).

- [ ] **Step 1: Failing tests** — (a) API-level (`httpx` against the app, no live CLI): `POST /generate` with `transport="api", provider="kimi"` → 400; `transport="api", provider="gemini", model=None` → 400; `transport="api", provider="claude", model="claude-opus-4-8"` → 201/200 and the created job has `transport="api"`; default (no `transport`) → job `transport="cli"`. (b) Same matrix for `POST /jobs/batch`, asserting the **batch row** and **every created job** carry the transport. (c) `GET /agent/models` payload exposes `api_supported` per provider (`true` for claude/gemini, `false` otherwise).
- [ ] **Step 2: Run — expect failure.**
- [ ] **Step 3: `agent_models.py`** — add:
  ```python
  API_PROVIDERS: frozenset[str] = frozenset({"claude", "gemini"})  # spec §1; codex deferred (fleet-api-5)

  def api_supported(provider: str) -> bool:
      return provider in API_PROVIDERS
  ```
  Add a shared validator both endpoints call:
  ```python
  def validate_transport(provider: str, model: str | None, transport: str) -> str | None:
      """Return an error string if (provider, model, transport) is invalid, else None."""
      if transport not in ("cli", "api"):
          return f"unknown transport {transport!r} (expected 'cli' | 'api')"
      if transport == "api":
          if not api_supported(provider):
              return f"transport=api unsupported for provider {provider!r} (only {sorted(API_PROVIDERS)})"
          if model is None:
              return "transport=api requires an explicit model (no provider-default) — it would diverge between OAuth and API-key auth"
      return None
  ```
- [ ] **Step 4: `jobs.py`** — add `transport: str = "cli"` to the generate request model (the `body` validated at `:121`); after the `is_valid` check, call `validate_transport(...)` → `HTTPException(400, err)`; pass `transport=body.transport` into `jobs_repo.create` (`:159`). Update the `/agent/models` handler (`:376`) to return `{"providers": MODEL_MANIFEST, "api_supported": {p: api_supported(p) for p in MODEL_MANIFEST}}`.
- [ ] **Step 5: `batch.py`** — add `transport: str = "cli"` to `BatchLaunchRequest` (`:22`); after `is_valid` (`:75`) call `validate_transport(provider, body.model, body.transport)`; pass `transport` into `batches_repo.get_or_create_for_book` (`:78`) and every `jobs_repo.create` (`:94`).
- [ ] **Step 6: Repos** —
  - `jobs_repo.create` accepts `transport: str = "cli"` and writes it. (Default keeps existing callers/tests valid.)
  - `batches_repo.get_or_create_for_book` (the fn is **`get_or_create_for_book`**, `batches.py:12` — there is NO `batches_repo.create`) gains a **required** `transport` param: add it to the `pg_insert(...).values(...)` and change the conflict target from `index_elements=["book_id"]` to **`index_elements=["book_id", "transport"]`** (matches the new `uq_batches_book_id_transport`). Pass `transport=body.transport` from its one caller (`batch.py:78`). Per spec §9 this means a same-book/different-transport re-launch forks a **new** batch row (clean per-transport rollup), while same-book/same-transport still reuses the row (retry/top-up unchanged).
  - **Update `tests/integration/test_batches_repo.py`**: its idempotency assertion flips from per-**book** to per-**`(book, transport)`** — `get_or_create_for_book(book, transport="cli")` twice → same id; `(book, "cli")` then `(book, "api")` → **two distinct ids**. Update the three call sites (`:47, :53, :79`) to pass `transport=`.
- [ ] **Step 6b: Transport-scope the per-section job dedup (BLOCKER — spec §9a).** Without this the flagship flow no-ops: `find_active_for_section` (`jobs.py:54`) matches `(book_id, toc_entry_id, status IN pending/running/done)` **transport-blind**, so launching an api batch over a book already generated on cli skips every lesson → the api batch gets **zero jobs**.
  - Add an optional param to `find_active_for_section(session, book_id, toc_entry_id, *, transport: str | None = None)`; when `transport is not None`, add `HomeworkJob.transport == transport` to the `.where(...)`. Default `None` = current behavior (no caller breaks).
  - **Batch path** (`batch.py:85`): pass `transport=body.transport` to the lookup. Because the lookup is now transport-scoped, a non-matching-transport job (e.g. a cli orphan when launching api) simply isn't returned → control falls through to `jobs_repo.create`, so the cli orphan is left alone and a fresh api job is made. (The existing `existing.batch_id is None` adoption branch is now reached only for *same-transport* orphans — exactly right. Keep an explicit `existing.transport == body.transport` assert there as belt-and-suspenders.) This kills both the empty-batch skip AND the cli-orphan-into-api-batch blend.
  - **`/generate` path** (`jobs.py:136`): pass `transport=body.transport` too (locked §9a — section idempotency becomes per-`(section, transport)`, so a single-lesson cli-then-api comparison runs both).
  - **Test** (in `tests/api/test_transport_validation.py` or the batch integration file): seed a book with N `done` **cli** jobs → `POST /jobs/batch` with `transport="api"` → the api batch has **N created, 0 adopted, 0 skipped**, and each new job has `transport="api"`. Plus: an orphan cli job is NOT adopted into an api batch.
- [ ] **Step 7: Run tests green** + full-suite baseline holds.
- [ ] **Step 8: Commit** — staged files above → `feat(fleet): transport validation + persistence + api_supported manifest (Phase 4)`.

---

### Task 4: Pipeline + judge threading; api failover restriction; loud judge auth-failure

**Files:** Modify `app/services/pipeline.py`, `app/services/phase_judge.py`; Tests `tests/services/test_judge_auth.py` (new) + `tests/services/test_failover_api.py` (new, the requested-provider-only assertion).

- [ ] **Step 1: Failing tests** — (a) `phase_judge.judge(...)` (the fn is `judge`, `:99` — NOT `judge_phase`) with `transport="api"` where the judge `run_phase` raises an auth/401 error → it **re-raises** (job-level failure), it does **not** return `JudgeOutcome(available=False, passed=True)`. (b) The same auth error under `transport="cli"` still degrades to `judge-unavailable, passed=True` (existing behavior preserved). Stub `agent.run_phase` to raise a sentinel auth error; classify on the message/`type`.
- [ ] **Step 2: Run — expect failure.**
- [ ] **Step 3: Pipeline threading** — `pipeline.py` reads `job.transport` next to `provider = job.provider` (`:75-76`); thread it through `_execute_phase` (add `transport` param) into:
  - the content run fn (`agent.run_phase_prompt(provider=prov, ..., transport=transport)`, the `_make_run` closure, `:659`),
  - the extract run fn (**`agent.summarize_lesson(..., transport=transport)`**, the `_extract_run` closure, `:637` — NOT `run_phase_prompt`; extract uses `summarize_lesson`. The extract provider/model pin stays; only auth follows the job, spec §3),
  - the regen run fn (`_make_run(regen_prompt)`, `:726`),
  - the judge call (`:701`/`:730`, see below).
  `_run_with_failover`'s `run_fn` closures already capture `transport` (it's in scope), so no signature change there — just include `transport=transport` inside each closure.
- [ ] **Step 3b: Restrict failover to the requested provider for `transport=api` (BLOCKER — verified gap).** `_failover_chain` (`pipeline.py:475`) = `[requested] + settings.failover_provider_order` (`["codex","gemini","kimi","opencode"]`, `config.py:75`). For an api job this is broken two ways: (1) an api **gemini** job falling over hits codex/kimi/opencode legs, each calling `_auth_env(<provider>, "api", …)` → `key_var is None` → the loud `RuntimeError` from Task 2 → caught by the leg loop, classified, backed-off — **wasted legs + noise rows**; (2) an api **claude** job can fall back to api **gemini** (gemini *is* in the order and is `api_supported`) running `model=None` (the failover invariant) — which **violates the explicit-model rule** *and* makes `cost_usd("gemini", None)` resolve to an unknown pair → **silent `$0` under-reporting on exactly the failed-over jobs**. (claude itself is absent from the failover order — reserved for Claude Max — so the failure surfaces via gemini, not a claude fallback leg.)
  **Fix (decided — requested-provider-only for api; chosen over the reviewer's `api_supported` filter because that still permits the claude→gemini `model=None` path):** `_run_with_failover` gains a `transport: str = "cli"` param; pass `transport` from each of its three call sites (`pipeline.py:648` extract, `:676` content, `:724` regen). Inside, build the chain as `chain = _failover_chain(requested_provider)` then **`if transport == "api": chain = [requested_provider]`** — an api job does NOT cross-provider failover. Rationale: (a) no `model=None` fallback leg ever exists → explicit-model rule preserved; (b) each api job's cost stays attributable to exactly one provider → the benchmark stays clean; (c) the `$0`-pricing path is eliminated at the source; (d) same-provider retry budgets (`_SAME_RETRY_BUDGET`) still give intra-provider resilience. **Test:** an api job whose only provider's `run_fn` always raises → `_run_with_failover` retries *same-provider* per budget then raises, and **never** invokes a second provider's `run_fn` (assert via a spy that only the requested provider was attempted). Defense-in-depth lives in Task 6 (`cost_usd(provider, None)` → that provider's default-model price, never silent `$0`).
- [ ] **Step 4: Judge** — `phase_judge.judge` gains `transport: str = "cli"`, passes it into its `agent.run_phase` (`:118`). Wrap that call so an **auth error on an api job** escapes the broad `except` (`:134`): detect auth/401 (reuse `failure_classifier` if it has an auth class, else match `401`/`invalid api key`/`unauthorized` case-insensitively in `str(exc)`); when `transport == "api"` and the error is auth, re-raise instead of returning the unavailable outcome. Pipeline passes `transport=transport` at the judge call sites.
- [ ] **Step 5: Run tests green** + full-suite baseline holds.
- [ ] **Step 6: Commit** — `git add app/services/pipeline.py app/services/phase_judge.py tests/services/test_judge_auth.py tests/services/test_failover_api.py` → `feat(fleet): thread transport through pipeline+judge; api=requested-provider-only failover; loud judge auth-fail (Phase 4)`.

---

### Task 5: Worker fail-fast — `has_api_keys` claim gate

**Files:** Modify `app/repositories/jobs.py`, `app/services/worker.py`; Test extend `tests/integration/test_claim_contention.py` (the file holding the `claim_next_job` tests — verified; there is no `test_queue.py`).

- [ ] **Step 1: Failing real-DB test** — seed two pending jobs, one `transport="cli"`, one `transport="api"`. `claim_next_job(..., has_api_keys=False)` claims **only** the cli job (the api job is skipped even when it's higher priority/older); with `has_api_keys=True` both are claimable. (Order assertion: with `has_api_keys=False`, repeatedly claiming drains cli jobs and never returns the api one.)
- [ ] **Step 2: Run — expect failure** (param missing).
- [ ] **Step 3: `claim_next_job`** — add `has_api_keys: bool` param; add a predicate to `pick_stmt` (`jobs.py:225`):
  ```python
  .where(or_(HomeworkJob.transport == "cli", literal(has_api_keys)))
  ```
  (import `or_`, `literal` from sqlalchemy if not already.) This covers the extract-failover path too — the gate is at claim time, before any provider switch.
- [ ] **Step 4: `worker.py`** — compute once at startup:
  ```python
  has_api_keys = bool(os.environ.get("ANTHROPIC_API_KEY") and os.environ.get("GEMINI_API_KEY"))
  ```
  pass it into every `claim_next_job(...)` call. Add a startup warning **unless both keys are set** (i.e. warn whenever `has_api_keys` is False — including the dangerous *half-configured* case of exactly one key present: "worker cannot claim transport=api jobs — both ANTHROPIC_API_KEY and GEMINI_API_KEY required"). **Also** the gemini `selectedType` guard (spec §4): if `~/.gemini/settings.json` has `security.auth.selectedType`, log a loud warning at startup (an interactive `gemini` re-persists it and silently breaks the toggle). Best-effort file read, swallow errors.
- [ ] **Step 5: Run tests green** + full-suite baseline holds.
- [ ] **Step 6: Commit** — `git add app/repositories/jobs.py app/services/worker.py tests/integration/test_claim_contention.py` → `feat(fleet): worker fail-fast — api jobs gated on has_api_keys at claim (Phase 4)`.

---

### Task 6: `auth_mode` attribution + price map + per-transport `$` readout

**Files:** Modify `app/services/agent.py` (record `auth_mode`), `app/repositories/agent_usage.py`; Create `app/services/pricing.py`; Modify `app/api/v1/jobs.py` (stats endpoint); Tests `tests/services/test_pricing.py` (new) + extend the usage/stats test.

- [ ] **Step 1: Failing tests** — (a) `pricing.cost_usd("claude", "claude-opus-4-8", {"prompt_tokens": 1_000_000, "output_tokens": 1_000_000, "cached_tokens": 0})` returns the expected `$` from the dated map (assert the Opus rate is the **current** $5 in / $25 out, NOT the deprecated $15/$75 the project mis-priced once). (b) After a recorded usage row with `auth_mode="api"`, the stats aggregation returns a per-transport breakdown with a nonzero `$` for api and `$0` for cli rows.
- [ ] **Step 2: Run — expect failure.**
- [ ] **Step 3: `pricing.py`** — a static dict keyed `(provider, model)` → `{input, output, cache_read}` $/Mtok, each entry with a **`# as of 2026-06-11, source: <url>`** comment (spec §5; stale prices silently corrupt the readout this feature rides on). Cover the claude + gemini manifest models at minimum; `cost_usd(provider, model, usage)` returns `0.0` for unknown/cli-free pairs (and log-once on a missing price so gaps surface). **Defense-in-depth for `model=None`:** `cost_usd(provider, None, …)` must **not** silently return `$0` — resolve `None` to that provider's default-model price (`agent_models.default_model(provider)`) before lookup, with a comment saying so. (Task 4 Step 3b removes the api `model=None` failover path at the source; this is the belt-and-suspenders so any *other* `model=None` row still bills correctly rather than reading $0.)
- [ ] **Step 4: Record `auth_mode`** — thread `transport` into `_record_usage` (`agent.py:358`, add `auth_mode: str = "cli"`) → `usage_repo.create(..., auth_mode=auth_mode)`. `usage_repo.create` accepts/writes `auth_mode`. **Real call surface (15 calls across 5 fns — don't undercount):** `run_phase` has **6** (`:581, :598, :622, :653, :687, :722`), `extract_toc` 4, `extract_lesson_context` 3 (dead code — see Task 2), `summarize_lesson` 1, `record_cached_lesson_extract` 1. Each call inside a `transport`-aware fn passes `auth_mode=transport` (the param is already threaded in Task 2). **`record_cached_lesson_extract` (`:1551`)** writes a free `$0` cross-job extract-reuse row with **no `transport` in scope** — leave its `auth_mode` at the default `"cli"`. That's *correct on purpose* (a reuse marker bills nothing), not an oversight — call it out in the commit so it's a conscious choice.
- [ ] **Step 5: Aggregation** — extend `agent_usage_repo.stats_by_provider` (or add `stats_by_provider_transport`) to group by `auth_mode`; the `/agent/stats` handler (`jobs.py:401`) joins token sums × `pricing.cost_usd` to emit `$ per provider per transport`.
- [ ] **Step 6: Run tests green** + full-suite baseline holds.
- [ ] **Step 7: Commit** — staged files → `feat(fleet): auth_mode attribution + static price map + per-transport $ stats (Phase 4)`.

---

### Task 7: Frontend — toggle, badge, `$` rollup

**Files:** Modify `web/src/lib/types.ts`, `web/src/lib/api.ts`, the generate form, `web/src/components/fleet/launcher.tsx`, job/batch views, `web/src/routes/usage.tsx`.

- [ ] **Step 1: Types** — `export type Transport = "cli" | "api";` add `transport: Transport` to Job + Batch types; add `api_supported: Record<string, boolean>` to the manifest type.
- [ ] **Step 2: api.ts** — add `transport?: Transport` to the generate body and `launchBatch` body; consume `api_supported` from `getAgentModels`.
- [ ] **Step 3: Generate form + `launcher.tsx`** — a `CLI | API` segmented toggle, **rendered only when the picked provider's `api_supported` is true** (claude/gemini); selecting `API` forces a concrete model selection (disable the provider-default option / require an explicit pick) and defaults back to `cli` when the provider changes to an unsupported one. Default `cli`.
- [ ] **Step 3b: Fix the launcher "already-batched" gate (BLOCKER — spec §9).** `launcher.tsx:57,64` currently drops a book from the "ready" tray once *any* batch references it: `batchedBookIds = new Set(batches.map(b => b.book_id))` → `ready = ...!batchedBookIds.has(b.id)`. With the `(book_id, transport)` key a book can now have a cli batch AND still be launchable on api — but this filter would hide it, making the api benchmark run **unlaunchable from the UI**. Fix: make the gate transport-aware — a ready book should still offer launch for any transport it doesn't yet have a batch for. Simplest: key the set on `${b.book_id}:${b.transport}` and, in the ready-row, show the launch control per *not-yet-used* transport (or surface which transports are already launched). Verify by hand: a book with a cli batch still shows an API-launch affordance.
- [ ] **Step 4: Badges + `$`** — a small `api` chip on job rows / batch funnel cards where `transport === "api"` (visually distinct billed runs); `usage.tsx` gains the per-provider-per-transport `$` rollup from the extended stats payload.
- [ ] **Step 5: FE gate** — `tsc --noEmit` clean + `npm run build` succeeds. Manually: toggle hidden for kimi, shown for claude, forces a model on API.
- [ ] **Step 6: Commit** — stage ONLY the `web/` files touched → `feat(fleet): CLI|API transport toggle + api badge + per-transport $ (Phase 4)`.

---

### Task 8: Acceptance, operator runbook, worklog 0053

**Files:** Create `docs/runbooks/phase4-transport-operator-acceptance.md`; Modify `docs/memory/MASTER_MEMORY.md`, `docs/memory/INDEX.md`, `docs/memory/ROADMAP.md`, `docs/memory/WISHLIST.md`.

- [ ] **Step 1: In-band acceptance** (all free):
  - Full suite green at baseline (no new reds).
  - Mode-isolation (Task 2) + claim-gate (Task 5) + judge-auth (Task 4) + validation (Task 3) tests all pass.
  - **Free invalid-key 401 proof** (spec §7.1 partial, zero cost): with a deliberately invalid `ANTHROPIC_API_KEY`, one in-process `agent.run_phase(provider="claude", transport="api", ...)` returns an immediate auth error (no subscription fallback, no spend). Record the observed error. (Skip if no network; note it.)
- [ ] **Step 2: Operator runbook** `docs/runbooks/phase4-transport-operator-acceptance.md` — document the **billed, operator-run** gates the plan does NOT execute:
  - §7.1 live valid-key smoke (claude + gemini): one headless call each bills the right account (AI Studio / Anthropic console), envelope still carries token stats in api mode, gemini api auth actually selected at runtime.
  - **Deployment ordering (spec §3):** ship this GCA-injecting code to a worker **before** removing `security.auth.selectedType` on that PC — removal-first instantly breaks all gemini calls there.
  - §7.2 post-removal upload smoke: after `selectedType` removed (code live), a plain book upload → TOC extraction still succeeds (proves the unconditional cli baseline).
  - §7.5 end-to-end (spec §9b wording): one real lesson `transport=api` → every **non-`<cache>`** `agent_usages` row (extract + content + judge) has `auth_mode=api` and the `$` readout is nonzero. **Cross-job extract-reuse `<cache>` rows correctly stay `cli`** (free $0 markers) — don't assert `api` on them. For a clean cost benchmark use a **fresh-extract book** (or `force`), else the api run's extract cost reads `$0`/unmeasured because it reused a prior cli extract.
  - Worker env setup: `ANTHROPIC_API_KEY` + `GEMINI_API_KEY` via the worker compose/env file (never committed); both required for any api job.
- [ ] **Step 3: Backlog hygiene** — confirm `fleet-api-1..5` are in `WISHLIST.md` (add `fleet-api-5` codex API mode if missing, per spec §8); close Phase 4 in `ROADMAP.md`.
- [ ] **Step 4: Worklog 0053** in `MASTER_MEMORY.md` + an `INDEX.md` row.
- [ ] **Step 5: Commit** — `git add docs/runbooks/phase4-transport-operator-acceptance.md docs/memory/MASTER_MEMORY.md docs/memory/INDEX.md docs/memory/ROADMAP.md docs/memory/WISHLIST.md` → `docs(memory): worklog 0053 — fleet Phase 4 (CLI|API transport toggle)`.

---

## Self-Review

**Spec coverage:** §2 toggle/enum/default + validation → Tasks 1, 3. **§9 amendment (batch key → `(book_id, transport)`)** → Task 1 (constraint swap + migration + docstring), Task 3 Step 6 (`get_or_create_for_book` transport + conflict target + repo-test flip), Task 7 Step 3b (FE launcher gate). The original "what does the badge show on re-launch" question is **moot** — each batch row now holds exactly one transport. **§9a (job dedup transport-blind → silent empty-batch no-op)** → Task 3 Step 6b (`find_active_for_section(transport=)` + transport-matched adoption, both batch and `/generate`, with the N-cli→N-api test). **§9b (cache rows exempt from §7.5)** → Task 8 wording + Task 2 loud missing-key raise. **Failover × api gap** (verified: `_failover_chain` is transport-blind, so an api job wastes non-api fallback legs and an api-claude→api-gemini `model=None` leg under-reports `$0`) → Task 4 Step 3b (api = requested-provider-only failover) + Task 6 (`cost_usd(provider, None)`→default-model price). §3 transport-covers-every-spawn + cli-baseline-for-non-job spawns (TOC at upload) → Task 2 (default `"cli"` at every layer) + Task 4 (extract/content/judge threading). §3 required-keys fail-fast at claim + loud judge auth → Tasks 5, 4. §4 per-provider adapters (gemini GCA/key, claude key) → Task 2 `_auth_env`; `selectedType` startup guard → Task 5. §5 `auth_mode` + static price map + per-transport `$` → Task 6. §6 frontend toggle/badge/`$` → Task 7. §7 acceptance → Task 8 (in-band free + operator runbook for billed). §8 deferred items → Task 8 backlog hygiene.

**Why `_auth_env` lives in `_spawn` (not per-caller):** there are four `_spawn` call sites and several wrappers; centralizing the transform at the single `child_env` build means no spawn path can bypass the cli baseline — which is exactly the §3 invariant (book-upload TOC extract, with no job, must still get GCA). The `transport` param threads down; the *logic* lives in one tested pure function.

**Type/threading consistency:** `transport` default `"cli"` at every new param keeps all existing callers and ~330 existing tests valid without edits. `auth_mode` mirrors `transport` 1:1 and is recorded where provider/model already are. Validation lives in one `validate_transport` shared by both creation endpoints, so `/generate` and `/jobs/batch` can't diverge. The claim gate is a single `WHERE` predicate on the existing `pick_stmt` — no new query path.

**Placeholder scan:** backend logic (migration, `_auth_env`, `validate_transport`, claim predicate, `cost_usd` shape) is given as real code; the FE task is structural (no unit harness — gate is `tsc` + build + manual). The only deliberately deferred-to-runtime values: the migration `down_revision` (read from the 0023 file in pre-flight) and the exact `$/Mtok` numbers (operator/reviewer fills from the dated source URLs — flagged, not hidden).

**Pre-flight for the implementer:** alembic head == 0023 and `$DB_URL` migrated before Task 1; `npm install` current before Task 7; remember the billed gates (Task 8) are operator-run — do not attempt live valid-key calls in-band.
