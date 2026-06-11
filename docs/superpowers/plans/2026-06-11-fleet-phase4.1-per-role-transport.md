# Fleet Phase 4.1 — per-role transport (implementation plan)

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development (one fresh subagent per task, TDD → commit; controller stress-tests every commit: read the diff AND re-run the gate).

**Goal:** Per-job, per-role billing choice — `extract_transport` and `judge_transport` (`cli|api|inherit`, default `inherit`) on jobs + batch launches, so e.g. gemini-API content (Vertex credit) pairs with subscription claude extract+judge. Capability-routed claiming; typed credential errors so every misprediction is loud.

**Spec:** `docs/superpowers/specs/2026-06-11-fleet-phase4.1-per-role-transport-design.md` (user-locked per-job decision; both reviewers' findings folded as §4a/§5a/§9.4).

**Environment / invocation** (set once per shell):
```bash
export REPO=$(git rev-parse --show-toplevel)
export DB_URL="postgresql+asyncpg://edu:edu@192.168.1.69:5436/edu_copy"   # shared live-clone, migrated head f7e6d5c4b3a2 (0024)
```
- Real-DB: `RUN_DB_INTEGRATION=1 DATABASE_URL="$DB_URL" uv run python -m pytest <path> -q`
- DB-free baseline gate (record before Task 1; currently **378 passed, 0 failed, 35 skipped**): `uv run python -m pytest tests/ -q`
- FE gate: `cd "$REPO/web" && npx tsc -p tsconfig.app.json --noEmit` clean AND `npm run build` succeeds.

**Pre-flight:**
1. `uv run python -m alembic heads` → expect `f7e6d5c4b3a2` (0024). New migration `0025`, `down_revision="f7e6d5c4b3a2"`.
2. ⚠ The shared DB serves a LIVE Windows server running Phase-4 code. Migration 0025 is **additive** (`server_default='inherit'`) — safe under the running process (old code ignores the columns). Do NOT touch the `(book_id, transport)` key.
3. Standing rules: stage ONLY each task's listed files; never `git add -A`; rebase before push (shared branch).

---

### Task 1: Schema — `extract_transport` / `judge_transport` columns (jobs + batches)

**Files:** Modify `app/models/homework_job.py`, `app/models/batch.py`; Create `alembic/versions/0025_role_transports.py`; Test extend `tests/integration/test_transport_schema.py`.

- [ ] **Step 1 (red):** extend `tests/integration/test_transport_schema.py`: fresh `HomeworkJob`/`Batch` default `extract_transport == "inherit"` and `judge_transport == "inherit"`; explicit `judge_transport="cli"` round-trips.
- [ ] **Step 2:** models — after `transport` in both files:
  ```python
  extract_transport: Mapped[str] = mapped_column(String(16), nullable=False, server_default="inherit")
  judge_transport: Mapped[str] = mapped_column(String(16), nullable=False, server_default="inherit")
  ```
- [ ] **Step 3:** migration `0025_role_transports.py` — `down_revision = "f7e6d5c4b3a2"`; `upgrade()` = 4× `op.add_column(..., sa.String(16), nullable=False, server_default="inherit")` (homework_jobs ×2, batches ×2); `downgrade()` drops them. No constraint changes.
- [ ] **Step 4:** apply (`alembic upgrade head` against `$DB_URL`), test green, DB-free baseline holds.
- [ ] **Step 5: Commit** — `feat(fleet): extract_transport + judge_transport columns (Phase 4.1)`.

---

### Task 2: Typed `AuthEnvError` + loud-by-construction judge (§5a)

**Files:** Modify `app/services/agent.py`, `app/services/phase_judge.py`; Tests extend `tests/services/test_auth_env.py`, `tests/services/test_judge_auth.py`.

- [ ] **Step 1 (red):**
  - `test_auth_env.py`: all `_auth_env` credential raises are `isinstance(exc, agent.AuthEnvError)` (missing claude key, missing gemini key+SA, unsupported provider api).
  - `test_judge_auth.py`: judge with `transport="api"` where `run_phase` raises `AuthEnvError("transport=api for gemini but ...")` (message matches NO signal substring) → **re-raises**; same error under cli → degrades. Proves isinstance matching, not substring luck.
- [ ] **Step 2:** `agent.py` — `class AuthEnvError(RuntimeError)` (module-level, exported); the three `_auth_env` raises become `AuthEnvError`. Existing `RuntimeError` expectations keep passing (subclass).
- [ ] **Step 3:** `phase_judge.py` — `_is_auth_error`: `isinstance(exc, AuthEnvError) or any(s in msg for s in _AUTH_SIGNALS)`. The pipeline regen-guard uses `_is_auth_error` already → inherits the fix.
- [ ] **Step 4:** suites green + baseline holds. **Commit** — `feat(fleet): typed AuthEnvError — credential mispredictions loud by construction (Phase 4.1 §5a)`.

---

### Task 3: Validation + persistence + resolution helper

**Files:** Modify `app/services/agent_models.py`, `app/schemas/job.py`, `app/api/v1/jobs.py`, `app/api/v1/batch.py`, `app/repositories/jobs.py`, `app/repositories/batches.py`; Test extend `tests/api/test_transport_validation.py`.

- [ ] **Step 1 (red):** POST tests — `judge_transport="bogus"` → 400; `extract_transport="api"` + valid body → persists on the job; batch launch stamps both fields onto batch row + every created job; defaults `inherit` when omitted.
- [ ] **Step 2:** `agent_models.py` —
  ```python
  ROLE_TRANSPORTS = ("cli", "api", "inherit")

  def validate_role_transport(field: str, value: str) -> str | None:
      if value not in ROLE_TRANSPORTS:
          return f"unknown {field} {value!r} (expected 'cli' | 'api' | 'inherit')"
      return None

  def resolve_role_transport(role_value: str, job_transport: str) -> str:
      """'inherit' follows the job's transport; explicit value wins."""
      return job_transport if role_value == "inherit" else role_value
  ```
- [ ] **Step 3:** schemas — `GenerateRequest` + `JobOut` + `BatchLaunchRequest` gain `extract_transport: str = "inherit"`, `judge_transport: str = "inherit"`. Endpoints call `validate_role_transport` for both fields after `validate_transport`; pass through to `jobs_repo.create` and `get_or_create_for_book` (batch row fields are launch-default labels; jobs carry the truth — spec §8).
- [ ] **Step 4:** repos accept + write both fields (default `"inherit"`).
- [ ] **Step 5:** green + baseline. **Commit** — `feat(fleet): per-role transport validation + persistence + resolution (Phase 4.1)`.

---

### Task 4: Claim gate v2 — capability routing + self-fallback (§4 + §4a)

**Files:** Modify `app/services/worker.py`, `app/repositories/jobs.py`; Test extend `tests/integration/test_claim_contention.py` + `tests/services/test_auth_env.py` (capability unit tests).

- [ ] **Step 1 (red):** real-DB matrix —
  - api-content(gemini)+`judge_transport=cli`+`extract_transport=cli` job **claimable** by a worker with ONLY the gemini capability (no Anthropic key) — the user's exact case.
  - cli-content job with `judge_transport=api` **not claimable** without judge capability.
  - **§4a:** content `claude/claude-opus-4-7` (== worker judge pair) + `judge_transport=api` → **NOT claimable** by a claude-only-keyed worker (judge self-falls-back to gemini); claimable when gemini capability present.
  - plain cli jobs claimable by anyone (unchanged).
- [ ] **Step 2:** `worker.py` — replace `_compute_has_api_keys` with a capability set computed once at startup:
  ```python
  def _compute_capabilities(env, judge_provider, judge_model, extract_provider) -> dict:
      can_claude = bool(env.get("ANTHROPIC_API_KEY"))
      can_gemini = bool(env.get("GEMINI_API_KEY") or
                        (env.get("GOOGLE_APPLICATION_CREDENTIALS") and env.get("GOOGLE_CLOUD_PROJECT")))
      cap = {"claude": can_claude, "gemini": can_gemini}
      fb_provider, _ = model_tiers._SELF_FALLBACK
      return {
          "can_claude_api": can_claude,
          "can_gemini_api": can_gemini,
          "judge_api_ok": cap.get(judge_provider, False),
          "judge_fallback_api_ok": cap.get(fb_provider, False),  # §4a
          "extract_api_ok": cap.get(extract_provider, False),
          "judge_pair": (judge_provider, judge_model),
      }
  ```
  Startup warning enumerates missing sides (replaces the both-keys text).
- [ ] **Step 3:** `claim_next_job` — replace the single `or_` predicate with three AND-ed role conditions (bindparams from the capability dict). Judge condition (§4a — the explicit-model rule guarantees `model` non-null where it matters):
  ```python
  job_is_judge_pair = and_(HomeworkJob.provider == judge_pair[0], HomeworkJob.model == judge_pair[1])
  judge_needs_api = or_(HomeworkJob.judge_transport == "api",
                        and_(HomeworkJob.judge_transport == "inherit", HomeworkJob.transport == "api"))
  judge_ok = or_(not_(judge_needs_api),
                 and_(job_is_judge_pair, literal(caps["judge_fallback_api_ok"])),
                 and_(not_(job_is_judge_pair), literal(caps["judge_api_ok"])))
  ```
  content: `transport=='cli' OR (provider=='claude' AND :can_claude_api) OR (provider=='gemini' AND :can_gemini_api)`; extract analogous to judge minus the pair-branch.
- [ ] **Step 4:** green (matrix + existing claim tests) + baseline. **Commit** — `feat(fleet): claim gate v2 — per-role capability routing incl. judge self-fallback (Phase 4.1)`.

---

### Task 5: Pipeline threading — resolved transports per spawn (§5)

**Files:** Modify `app/services/pipeline.py`; Test extend `tests/services/test_execute_phase_api_auth.py` (the real-`_execute_phase` harness).

- [ ] **Step 1 (red):** harness drives `_execute_phase` with job `transport="api"`, `extract_transport="cli"`, `judge_transport="cli"`: assert the extract spawn received `transport="cli"`, content `"api"`, judge `"cli"` (spy on `agent.summarize_lesson` / `run_phase_prompt` / `phase_judge.judge` kwargs); loud-judge does NOT fire for a cli-resolved judge auth error; DOES fire when `judge_transport="api"` on a cli job.
- [ ] **Step 2:** `pipeline.run` — resolve next to the existing read:
  ```python
  transport = getattr(job, "transport", "cli") or "cli"
  extract_transport = resolve_role_transport(getattr(job, "extract_transport", "inherit"), transport)
  judge_transport = resolve_role_transport(getattr(job, "judge_transport", "inherit"), transport)
  ```
  Thread both through `_run_content_phases_parallel` → `_execute_one_phase` → `_execute_phase`: `_extract_run` gets `transport=extract_transport` (and its `_run_with_failover` call passes the same — the api restriction follows the SPAWN's transport); both `phase_judge.judge(...)` calls + the regen guard's `transport == "api"` check use `judge_transport`; content paths keep the job transport.
- [ ] **Step 3:** green + baseline. **Commit** — `feat(fleet): thread per-role resolved transports through pipeline (Phase 4.1)`.

---

### Task 6: Frontend — Extract/Judge billing selects

**Files:** Modify `web/src/lib/types.ts`, `web/src/lib/api.ts`, `web/src/routes/section.tsx` (generate form / `AgentPicker`), `web/src/components/fleet/launcher.tsx`.

- [ ] **Step 1:** types — `export type RoleTransport = "cli" | "api" | "inherit"`; `extract_transport`/`judge_transport` on Job/BatchSummary + request bodies (default `"inherit"`).
- [ ] **Step 2:** generate form + launcher — two compact selects labeled **Extract** / **Judge**: `Auto (follow job)` / `CLI (subscription)` / `API (billed)`, default Auto, always visible. Wire into the generate/launch bodies. Reuse the existing select styling.
- [ ] **Step 3:** FE gate (`tsc` + `build`). Manual reasoning: defaults send `inherit`; user's case = job API + both selects CLI. **Commit** — stage only touched `web/` files → `feat(fleet): per-role Extract/Judge billing selects (Phase 4.1)`.

---

### Task 7: Acceptance + worklog 0054

**Files:** Modify `docs/runbooks/phase4-transport-operator-acceptance.md`, `docs/memory/MASTER_MEMORY.md`, `docs/memory/INDEX.md`, `docs/memory/WISHLIST.md` (close `fleet-api-7`), `docs/memory/ROADMAP.md` if touched.

- [ ] **Step 1: In-band acceptance:** full suite at/above baseline; the spec-§9 unit/matrix tests pass; **live smoke of the user's exact case** (free-ish, Vertex-billed content only): in-process job-shaped run with `transport=api` + both roles `cli` on this Mac (gemini via SA; claude via subscription) → content rows `auth_mode=api`, extract+judge rows `auth_mode=cli`. **§9.4 self-fallback legs**: gate-refusal covered in Task 4 (real-DB); loud-`AuthEnvError` covered in Tasks 2/5; the live fallback-judge leg runs only if cheap — else document as operator-run.
- [ ] **Step 2:** runbook — add the per-role section (what `inherit/cli/api` mean per role; capability routing — a worker only claims what it can serve; §9.4 operator legs if deferred).
- [ ] **Step 3:** WISHLIST: close `fleet-api-7` as SHIPPED; worklog **0054** + INDEX row. **Commit** — `docs(memory): worklog 0054 — Phase 4.1 per-role transport`.

---

## Self-Review

**Spec coverage:** §2 fields+resolution → T1/T3/T5. §3 validation → T3. §4 gate v2 → T4. **§4a self-fallback** → T4 (predicate + matrix test). §5 threading + loud-judge-by-resolved → T5. **§5a AuthEnvError** → T2 (before the gate relaxation lands in T4 — ordering deliberate: the loud backstop exists before the gate gets liberal). §6 attribution → no work (verified shipped). §7 FE → T6. §8 schema → T1 (no key changes). §9 acceptance → tasks' reds + T7.

**Ordering rationale:** T2 (typed errors) lands before T4 (gate relaxation) so at no commit does a liberal gate exist without the loud backstop.

**Back-compat:** every new field/param defaults to `inherit`/existing behavior; the live Windows server (Phase 4 code) keeps running against the migrated DB throughout (0025 is additive).

**Placeholder scan:** migration/validator/capability/gate-predicate/resolution given as real code; FE structural (no harness; gate = tsc+build). `down_revision` confirmed in pre-flight.
