# PR #37 "Habibullo" Integration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to
> implement Tasks 2–7 task-by-task (Task 1 is a controller-executed rebase — see its note).
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebase PR #37 (`origin/Habibullo`, commit `4c337a9`) onto current `origin/Nggaev-v2`,
adopting its three new features (custom-prompt upload, per-job phase-picker, monitor redesign)
while preserving every C1–C4 behavior — grafting C4's budget-pause UI/payload back into the
files PR #37 rewrote, re-chaining its migrations linearly, and fixing two latent bugs it ships.

**Architecture:** Single-commit PR, merge-base `94847bb` (after C3, before C4). Rebase replays
its diff onto `e9979af`; conflicts land in `batch.py`, `batch-funnel.tsx`, `jobs.py`, `types.ts`.
Resolution rule (user-set): *adopt their additions, preserve every C1–C4 behavior, take "their"
version only where clearly better — which is nowhere a C1–C4 deliverable would be replaced.*

**Tech Stack:** FastAPI + Alembic + SQLAlchemy (async) / React + TS (Vite) / pytest + pytest-asyncio.

**Delivery (user decision 2026-06-19):** force-push the rebased result to `origin/Habibullo`
(updates PR #37 in place). **No self-merge — route the PR to the gatekeeper; no merge without the user's GO.**

---

## Approach & key decisions

- **Adopt / preserve split (user principle).** Custom-prompt upload + phase-picker + monitor
  redesign + subject drilldown = **new value, adopt**. C3 judge fidelity, C4 cost-safety, the
  claim gate, migrations 0029–0032 = **load-bearing, preserve**. Verified: there is no file where
  "their" version is strictly better than a C1–C4 deliverable it would replace — overlap is purely
  additive or accidental (rewritten files that *happen* to drop C4 UI).
- **Migrations: re-chain linearly, delete the merge migs.** PR forked custom-prompts off `0026`
  (rev `a8c7e6d5f4b3`); the only two *real* schema migs are `c1d2e3f4a5b6` (add 4 cols) and
  `d2e3f4a5b6c7` (widen `prompt_hash` 64→128). The two merge migs (`43cde4a391e0`, `daa93bd3ce94`)
  only reconciled the pre-rebase fork → **delete**. Renumber the two real ones to `0033`/`0034`
  chained onto `0032_budget_state` → single head. **Verified: no hidden 3rd schema migration.**
- **Graft C4 into theirs (two precise spots).** (a) `batch.py::_rollup_payload` — theirs kept the
  `not_started`-aware `lessons_covered`/`complete` but dropped `paused_at`/`paused_reason`; re-add
  those two lines, keep their math. (b) `batch-funnel.tsx::TransportRow` — theirs dropped the
  `PauseCircle` import + the paused badge; re-add both (badge goes right after `<RollupBar>`).
- **Claim gate survives untouched.** Verified the PR commit does **not** edit `claim_next_job`
  (only `jobs.create()` + new columns) and it is byte-identical at the merge-base → C3+C4 gate
  rebases cleanly. `_fidelity_flags` is byte-identical too → C3 fidelity intact.
- **Fix two latent bugs the PR ships** (required by the acceptance gate): the ON-CONFLICT
  NULL-out in `batches.get_or_create_for_book` (COALESCE), and the vacuous `inspect`-grep
  judge-override test (rewrite behavioral).
- **Rejected:** "keep the fork + add a 3rd merge migration" — works but leaves a 3-merge graph;
  we're rebasing anyway, so linear is free and cleaner.

---

## Pre-flight (controller, before Task 1)

- [ ] Confirm rebase target: `git fetch origin && git rev-parse origin/Nggaev-v2` = `e9979af` (or newer — rebase onto whatever is current).
- [ ] Create an isolated worktree (superpowers:using-git-worktrees) tracking `origin/Habibullo`:
  ```bash
  git worktree add -b pr37-integration ../hcg-pr37 origin/Habibullo
  ```
  All tasks run inside `../hcg-pr37`. (The branch is named `pr37-integration` locally; it force-pushes to `Habibullo` at finish.)

---

### Task 1: Rebase onto `origin/Nggaev-v2` and resolve conflicts (controller-executed)

> **Why controller, not a fresh subagent:** mid-rebase conflict resolution depends on live
> `<<<<<<<` markers that a context-free subagent can't see reliably. The controller does the
> rebase, applying the exact graft rules below, then stress-tests the result.

**Files (expected conflicts):** `app/api/v1/batch.py`, `web/src/components/fleet/batch-funnel.tsx`,
`app/api/v1/jobs.py`, `web/src/lib/types.ts` (likely auto), `.gitignore` (likely auto).

- [ ] **Step 1: Start the rebase**
  ```bash
  cd ../hcg-pr37 && git rebase origin/Nggaev-v2
  ```
  Expect conflicts in the files above.

- [ ] **Step 2: Resolve `app/api/v1/batch.py::_rollup_payload`** — keep **theirs** `not_started`
  math AND **re-add C4's** pause fields. Final function body must contain BOTH:
  ```python
        "rollup": tally,
        "lessons_covered": sum(v for k, v in tally.items() if k != "not_started"),
        "complete": (
            sum(tally.values()) > 0
            and tally.get("not_started", 0) == 0
            and (tally.get("pending", 0) + tally.get("running", 0)
                 + tally.get("cancelling", 0)) == 0
        ),
        "created_at": batch.created_at.isoformat(),
        # Cost-safety fields (C4): None when the batch is not paused.
        "paused_at": batch.paused_at.isoformat() if batch.paused_at else None,
        "paused_reason": batch.paused_reason,
    }
  ```
  Also confirm the **fleet-rollup payload** further down the file still emits
  `paused_at` / `paused_reason` / `fleet_api_paused_at` / `fleet_api_paused_reason` (C4 added that
  block in a region the PR doesn't touch — it should survive; verify, don't assume).

- [ ] **Step 3: Resolve `web/src/components/fleet/batch-funnel.tsx`** — keep **theirs**
  `TransportRow` rewrite AND re-add C4's paused badge. Restore the import:
  ```tsx
  import { ChevronDown, ChevronRight, PauseCircle } from "lucide-react";
  ```
  Insert the badge inside `TransportRow`, immediately after `<RollupBar … />`:
  ```tsx
      <RollupBar rollup={batch.rollup} covered={batch.lessons_covered} />

      {/* Paused badge — shown when the budget monitor (C4) has gated this batch. */}
      {batch.paused_at && (
        <div className="flex items-center gap-1.5 rounded-lg border border-amber-500/30 bg-amber-500/[0.08] px-2.5 py-1.5 text-xs text-amber-300">
          <PauseCircle className="size-3.5 shrink-0" />
          <span>
            Paused — budget cap reached
            {batch.paused_reason ? ` (${batch.paused_reason})` : ""}
          </span>
        </div>
      )}
  ```

- [ ] **Step 4: Resolve `app/api/v1/jobs.py`** — keep **both** C4's `generate()`/`_job_out` edits
  AND the PR's custom-prompt/phase-subset validation + `planned_phases`/`added_phases`. Neither
  side touches `claim_next_job`. After resolving, eyeball that no C4 line was dropped.

- [ ] **Step 5: Resolve `web/src/lib/types.ts`** — union of both: C4's `paused_at`/`paused_reason`
  on `BatchSummary` AND the PR's new fields (`not_started`, custom-prompt/phase types). Likely auto-merges.

- [ ] **Step 6: Finish the rebase**
  ```bash
  git add -A && git rebase --continue
  ```
  (Migrations still two-headed at this point — fixed in Task 2.)

- [ ] **Step 7: Stress-test the resolution**
  ```bash
  grep -rn '<<<<<<<\|>>>>>>>\|=======' app/ web/src/ ; echo "exit=$?"   # expect no markers
  grep -n "PauseCircle\|paused_at" web/src/components/fleet/batch-funnel.tsx   # badge present
  grep -n "paused_at\|paused_reason" app/api/v1/batch.py   # both rollup + fleet blocks
  cd web && npx tsc -p tsconfig.app.json --noEmit ; cd ..   # FE typechecks
  uv run python -c "import app.api.v1.batch, app.api.v1.jobs"   # backend imports
  ```
  Expected: no markers, badge + pause fields present, tsc exit 0, import OK.

- [ ] **Step 8: No commit** (rebase already rewrote `4c337a9`). Proceed to Task 2.

---

### Task 2: Re-chain migrations linearly onto `0032`

**Files:**
- Rename: `alembic/versions/0027_add_custom_prompts_selected_phases.py` → `0033_custom_prompts_selected_phases.py`
- Rename: `alembic/versions/0028_widen_prompt_hash.py` → `0034_widen_prompt_hash.py`
- Delete: `alembic/versions/43cde4a391e0_merge_per_role_provider_model_and_.py`, `alembic/versions/daa93bd3ce94_merge_custom_prompts_monitor_line_with_.py`

- [ ] **Step 1: Delete the two merge migrations**
  ```bash
  git rm alembic/versions/43cde4a391e0_merge_per_role_provider_model_and_.py \
         alembic/versions/daa93bd3ce94_merge_custom_prompts_monitor_line_with_.py
  ```

- [ ] **Step 2: Rename + re-chain the custom-prompts migration**
  ```bash
  git mv alembic/versions/0027_add_custom_prompts_selected_phases.py \
         alembic/versions/0033_custom_prompts_selected_phases.py
  ```
  Edit its header to:
  ```python
  revision: str = "0033_custom_prompts_selected_phases"
  down_revision: Union[str, Sequence[str], None] = "0032_budget_state"
  ```

- [ ] **Step 3: Rename + re-chain the widen migration**
  ```bash
  git mv alembic/versions/0028_widen_prompt_hash.py \
         alembic/versions/0034_widen_prompt_hash.py
  ```
  Edit its header to:
  ```python
  revision: str = "0034_widen_prompt_hash"
  down_revision: Union[str, Sequence[str], None] = "0033_custom_prompts_selected_phases"
  ```

- [ ] **Step 4: Verify exactly one head**
  ```bash
  uv run alembic heads        # expect: 0034_widen_prompt_hash (head)
  uv run alembic history | head -6
  ```
  Expected: single head `0034_widen_prompt_hash`; history shows `…0032_budget_state → 0033 → 0034`.

- [ ] **Step 5: Scratch-DB acceptance (real upgrade, not import-only)**
  ```bash
  docker exec edu-postgres psql -U edu -d edu_homework -c "DROP DATABASE IF EXISTS edu_pr37_scratch;"
  docker exec edu-postgres psql -U edu -d edu_homework -c "CREATE DATABASE edu_pr37_scratch;"
  DATABASE_URL="postgresql+asyncpg://edu:edu@localhost:5433/edu_pr37_scratch" uv run alembic upgrade head
  docker exec edu-postgres psql -U edu -d edu_pr37_scratch -c "\d homework_jobs" | grep -E "custom_prompts|selected_phases"
  docker exec edu-postgres psql -U edu -d edu_pr37_scratch -c "\d batches"       | grep -E "custom_prompts|selected_phases"
  docker exec edu-postgres psql -U edu -d edu_pr37_scratch -c "\d phase_outputs" | grep prompt_hash   # expect varchar(128)
  docker exec edu-postgres psql -U edu -d edu_homework -c "DROP DATABASE edu_pr37_scratch;"
  ```
  Expected: upgrade runs clean through `0034`; both tables have `custom_prompts`+`selected_phases` (jsonb); `prompt_hash` is `varchar(128)`.

- [ ] **Step 6: Commit**
  ```bash
  git add alembic/versions/
  git commit -m "fix(db): re-chain PR37 migrations linearly onto 0032 (0033 cols, 0034 widen); drop merge migs"
  ```

---

### Task 3: Lock the `_rollup_payload` graft with a test (both pause fields + not_started)

**Files:**
- Test: `tests/api/test_rollup_pause_and_not_started.py` (create)

- [ ] **Step 1: Write the failing test** — assert the rebased `_rollup_payload` emits C4 pause
  fields AND the `not_started`-aware math, in one place:
  ```python
  import types
  from datetime import datetime, timezone

  from app.api.v1 import batch as batch_api


  def _fake_batch(paused=False):
      return types.SimpleNamespace(
          id="b1", book_id="bk1", subject="math", grade=9,
          provider="claude", model="claude-sonnet-4-6", transport="api",
          extract_transport="inherit", judge_transport="inherit",
          extract_provider=None, extract_model=None, judge_provider=None, judge_model=None,
          created_at=datetime(2026, 6, 19, tzinfo=timezone.utc),
          paused_at=datetime(2026, 6, 19, tzinfo=timezone.utc) if paused else None,
          paused_reason="batch cap reached" if paused else None,
      )

  def test_rollup_keeps_c4_pause_fields_and_not_started_math():
      tally = {"done": 3, "running": 1, "not_started": 2}
      out = batch_api._rollup_payload(_fake_batch(paused=True), tally, "math_g9.pdf")
      # C4 pause fields preserved through the PR rewrite
      assert out["paused_reason"] == "batch cap reached"
      assert out["paused_at"] is not None
      # PR not_started math: covered excludes not_started; complete is False while not_started>0
      assert out["lessons_covered"] == 4          # 3 done + 1 running, NOT the 2 not_started
      assert out["complete"] is False

  def test_rollup_complete_true_only_when_no_not_started_and_no_inflight():
      out = batch_api._rollup_payload(_fake_batch(), {"done": 5}, "math_g9.pdf")
      assert out["complete"] is True
      assert out["paused_at"] is None
  ```

- [ ] **Step 2: Run it** — `uv run python -m pytest tests/api/test_rollup_pause_and_not_started.py -v`
  Expected: PASS (the graft from Task 1 makes it green; if it fails, the graft was wrong — fix Task 1).

- [ ] **Step 3: Commit**
  ```bash
  git add tests/api/test_rollup_pause_and_not_started.py
  git commit -m "test(api): lock _rollup_payload graft — C4 pause fields + PR not_started math"
  ```

---

### Task 4: Fix the ON-CONFLICT NULL-out in `batches.get_or_create_for_book`

**Files:**
- Modify: `app/repositories/batches.py` (the `set_=` clause in `get_or_create_for_book`)
- Test: `tests/integration/test_batches_repo.py` (extend — real DB, `RUN_DB_INTEGRATION=1`)

- [ ] **Step 1: Write the failing test** — a same-transport re-launch WITHOUT custom prompts must
  not NULL out a previously-stored `custom_prompts`/`selected_phases`:
  ```python
  @pytest.mark.integration
  @pytest.mark.asyncio
  async def test_relaunch_without_prompts_preserves_stored(db_session):
      book_id = await _seed_book(db_session)
      # first launch carries provenance
      b1 = await batches_repo.get_or_create_for_book(
          db_session, book_id=book_id, subject="math", grade=9,
          provider="claude", model="claude-sonnet-4-6", transport="api",
          custom_prompts={"reading": "x"}, selected_phases=["reading"],
      )
      await db_session.commit()
      # plain re-launch (top-up): no custom prompts passed
      b2 = await batches_repo.get_or_create_for_book(
          db_session, book_id=book_id, subject="math", grade=9,
          provider="claude", model="claude-sonnet-4-6", transport="api",
      )
      await db_session.commit()
      assert b2.id == b1.id
      assert b2.custom_prompts == {"reading": "x"}      # NOT nulled
      assert b2.selected_phases == ["reading"]
      await _cleanup(db_session, book_id)
  ```

- [ ] **Step 2: Run it (red)** —
  `RUN_DB_INTEGRATION=1 DATABASE_URL=… uv run python -m pytest tests/integration/test_batches_repo.py::test_relaunch_without_prompts_preserves_stored -v`
  Expected: FAIL (returns `None` — the unconditional `set_=` overwrote them).

- [ ] **Step 3: Fix the `set_=` clause** — COALESCE to existing on a NULL incoming value:
  ```python
      .on_conflict_do_update(
          index_elements=["book_id", "transport"],
          set_={
              "updated_at": func.now(),
              "custom_prompts": func.coalesce(stmt.excluded.custom_prompts, Batch.custom_prompts),
              "selected_phases": func.coalesce(stmt.excluded.selected_phases, Batch.selected_phases),
          },
      )
  ```
  (Match the real symbol names in the file — `stmt.excluded` vs an aliased insert; verify the existing import of `func`.)

- [ ] **Step 4: Run it (green)** — same command. Expected: PASS.

- [ ] **Step 5: Commit**
  ```bash
  git add app/repositories/batches.py tests/integration/test_batches_repo.py
  git commit -m "fix(batches): COALESCE custom_prompts/selected_phases on re-launch (no NULL-out on top-up)"
  ```

---

### Task 5: Harden the vacuous judge-override test (behavioral, not grep)

**Files:**
- Rewrite: `tests/services/test_judge_contract_override.py`

- [ ] **Step 1: Replace the `inspect`-grep test with a behavioral one** that lands on the REAL LLM
  boundary. Verified against `phase_judge.judge`: the boundary is `agent.run_phase(..., phase_prompt=
  judge_prompt, schema=Verdict)`; the result's `.parsed` must be a `Verdict` instance (a tuple crashes
  the `isinstance(verdict, Verdict)` guard). The fake captures `phase_prompt` and returns a real `Verdict`.
  ```python
  import types
  import pytest
  from app.services import phase_judge
  from app.services.phase_judge import Verdict

  _JUDGE_KW = dict(
      subject="math", phase_name="reading", output_md="OUT",
      lesson_context="SRC", prior_outputs={},
      gen_provider="claude", gen_model="claude-sonnet-4-6",
      judge_provider="claude", judge_model="claude-sonnet-4-6",
  )

  @pytest.mark.asyncio
  async def test_override_is_the_contract_used(monkeypatch):
      captured = {}
      async def fake_run_phase(**kw):
          captured["phase_prompt"] = kw["phase_prompt"]
          return types.SimpleNamespace(parsed=Verdict(passed=True))
      monkeypatch.setattr(phase_judge.agent, "run_phase", fake_run_phase)
      await phase_judge.judge(contract_override="CUSTOM-CONTRACT-SENTINEL", **_JUDGE_KW)
      assert "CUSTOM-CONTRACT-SENTINEL" in captured["phase_prompt"]

  @pytest.mark.asyncio
  async def test_no_override_falls_back_to_get_prompt(monkeypatch):
      captured = {}
      async def fake_run_phase(**kw):
          captured["phase_prompt"] = kw["phase_prompt"]
          return types.SimpleNamespace(parsed=Verdict(passed=True))
      monkeypatch.setattr(phase_judge.agent, "run_phase", fake_run_phase)
      monkeypatch.setattr(phase_judge, "get_prompt", lambda s, p: "BUILTIN-CONTRACT-SENTINEL")
      await phase_judge.judge(contract_override=None, **_JUDGE_KW)
      assert "BUILTIN-CONTRACT-SENTINEL" in captured["phase_prompt"]
  ```
  > **Executor note (gatekeeper-required):** the boundary is `agent.run_phase` with the `phase_prompt`
  > kwarg — NOT a `_judge_call(prompt=…)` returning a tuple (that symbol doesn't exist). The fake MUST
  > return an object whose `.parsed` is a real `Verdict(...)`. Re-read `phase_judge.judge` and the
  > `agent` import before finalizing; if `agent` is imported as `from app.services import agent`, patch
  > `phase_judge.agent.run_phase` (as above). Match the full keyword-only `judge(...)` signature.

- [ ] **Step 2: Run it** — `uv run python -m pytest tests/services/test_judge_contract_override.py -v` → PASS.

- [ ] **Step 3: Sanity — prove it can fail** — temporarily flip `contract = contract_override or …`
  to `contract = get_prompt(...)` in a scratch copy and confirm the override test goes red, then revert. (Don't commit the flip.)

- [ ] **Step 4: Commit**
  ```bash
  git add tests/services/test_judge_contract_override.py
  git commit -m "test(judge): behavioral contract_override test (was inspect-grep, passed even if miswired)"
  ```

---

### Task 6: Drop the stale "stub" comment in `phase_judge.py` (cosmetic)

**Files:** Modify: `app/services/phase_judge.py` (the line-~200 comment)

- [ ] **Step 1:** Remove the misleading `# Task A2; stub returns [] until A2 lands` comment on the
  `_fidelity_flags(...)` call — the function is the real C3 implementation (verified byte-identical
  to `Nggaev-v2`), not a stub. Leave the code untouched.
- [ ] **Step 2:** `uv run python -m pytest tests/services/test_phase_judge.py -q` → green.
- [ ] **Step 3: Commit** — `git commit -am "docs(judge): drop stale 'stub' comment on _fidelity_flags (C3 impl is real)"`

---

### Task 7: Finish — full suite, docs de-stale, force-push, route to gatekeeper

- [ ] **Step 1: Full offline suite** — `uv run python -m pytest tests/ -q`. Expected: green
  (only the known notion-network skips). The PR's own feature tests + the new tests above all pass.

- [ ] **Step 2: DB-integration suite** (where PR meets C4 — rollup + claim gate):
  ```bash
  RUN_DB_INTEGRATION=1 DATABASE_URL="postgresql+asyncpg://edu:edu@localhost:5433/edu_homework" \
    uv run python -m pytest tests/integration/ -q
  ```
  Expected: green (custom-prompt persistence, batches re-launch, rollup, claim gate).

- [ ] **Step 3: FE build** — `cd web && npx tsc -p tsconfig.app.json --noEmit && npm run build && cd ..`. Expected: clean.

- [ ] **Step 4: Rebase-currency check** (CLAUDE.md finish rule) — `git fetch origin && git log HEAD..origin/Nggaev-v2`.
  If the base moved ahead since Task 1, rebase onto it again and re-run Steps 1–3.

- [ ] **Step 5: De-stale `docs/DATABASE.md`** — bump head stamp `0032_budget_state` → `0034_widen_prompt_hash`
  on **line 5** and the **"Current head"** paragraph (~line 31); add the chain note
  (`0033 = custom_prompts/selected_phases on homework_jobs+batches`, `0034 = widen prompt_hash 64→128`)
  and the 4 new column rows + the `prompt_hash` widen + the `custom:sha256:<hex>` provenance note.
  Also de-stale `docs/HOW_IT_WORKS.md` / `docs/CODE_MAP.md` for custom-prompt + phase-picker + the monitor redesign.

- [ ] **Step 6: Worklog + INDEX** — add a worklog entry to `docs/memory/MASTER_MEMORY.md` and an
  **INDEX row numbered `0083`** (0080 last used; 0081/0082 reserved for C5/C6 — **verify no collision
  in INDEX.md at commit time**, the known worklog-collision gotcha). Note: features adopted,
  C1–C4 preserved, migrations re-chained, 2 bugs fixed.

- [ ] **Step 7: Ship the plan** — `git mv docs/superpowers/plans/2026-06-19-pr37-integration.md docs/superpowers/plans/shipped/`.
  Also move the PR's own design/plan docs it brought under `docs/superpowers/plans/` into `shipped/` if they belong there (don't delete the contributor's docs).

- [ ] **Step 8: Commit docs**
  ```bash
  git add docs/
  git commit -m "docs(pr37): worklog 0083 + DATABASE/HOW_IT_WORKS/CODE_MAP de-stale + ship plan"
  ```

- [ ] **Step 9: Force-push to PR #37 (user-approved delivery)**
  ```bash
  git push --force-with-lease origin pr37-integration:Habibullo
  ```
  Use `--force-with-lease` (not bare `--force`) so a concurrent push by the contributor aborts the push instead of clobbering it.

- [ ] **Step 10: Route to gatekeeper** — post the PR back to the gatekeeper for the merge gate.
  **Do NOT merge.** Hand over the §7 acceptance checklist below as the re-verify list. Then remove the worktree: `git worktree remove ../hcg-pr37`.

---

## Acceptance gate (gatekeeper re-verifies on the rebased PR)

- `uv run alembic heads` = exactly one (`0034_widen_prompt_hash`); scratch-DB `upgrade head` clean through `0034`.
- `_rollup_payload` (both the batch fn AND the fleet endpoint) emits `paused_at`/`paused_reason`/`fleet_api_paused_at`.
- `batch-funnel.tsx` renders the paused badge in `TransportRow`; `tsc` + `npm run build` clean.
- `claim_next_job` byte-unchanged vs `Nggaev-v2` (C3+C4 gate preserved).
- `phase_judge._fidelity_flags` path intact (byte-identical to `Nggaev-v2`).
- Full offline suite + DB-integration green; custom-prompt + phase-picker + judge-override tests are behavioral (not grep-only); re-launch-without-prompts test green.
