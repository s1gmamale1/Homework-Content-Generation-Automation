# PR #37 "Habibullo" — Integration Brief (for the implementer)

> **Purpose.** PR #37 (`origin/Habibullo`, author VenEl101) ships three genuinely new
> features but **cannot be merged as-is** — it was branched at `94847bb` (after cluster-3,
> **before cluster-4**), so it (a) breaks Alembic with two heads, (b) conflicts textually with
> C4, and (c) if those conflicts are resolved naively, **silently deletes C4's budget-pause UI
> and payload**. This brief is the verified map for a clean rebase + reconciliation.
>
> **Guiding principle (from the user):** *preserve our shipped work (C1–C4), adopt their new
> features, and pick their version of a thing ONLY where it is clearly better.* Concretely:
> their custom-prompt + phase-picker + monitor redesign are **new value → adopt**; C3 judge,
> C4 cost-safety, the claim gate, migrations 0029–0032 are **load-bearing → preserve**.
>
> **Workflow:** this is a pre-plan brief. Turn it into a gated plan
> (`docs/superpowers/plans/YYYY-MM-DD-pr37-integration.md`) with the
> `## Approach & key decisions` header + TDD-per-task list, **route the plan back to the
> gatekeeper for the single approval gate**, then execute via subagent-driven-development.
> Do NOT write code before the plan is approved.

---

## 0. State of the world (verified 2026-06-19)

- Target branch `Nggaev-v2` tip = **`f683153`**; live DB `edu_copy` migrated to head **`0032_budget_state`** (C1–C4 all merged + applied).
- PR #37 = **one commit** (`4c337a9`), no description, +3545/−114 over 42 files, merge-base `94847bb`, **mergeable=CONFLICTING**.
- The PR's own tests pass in isolation and its FE typechecks clean (`tsc --noEmit` exits 0 on `4c337a9`).

## 1. What PR #37 actually ships (4 things bundled in 1 commit)

1. **Custom-prompt upload** — per-job / per-batch user-supplied prompt that overrides the built-in phase prompt. New columns `homework_jobs.custom_prompts`, `batches.custom_prompts`; widens `phase_outputs.prompt_hash` 64→128 to fit a `custom:sha256:<hex>` provenance hash. New `/books`-area upload endpoint + `app/api/v1/batch.py`/`jobs.py` wiring; judge gets a `contract_override`.
2. **Per-job phase-picker** — run a chosen subset of phases. New columns `homework_jobs.selected_phases`, `batches.selected_phases`; `flows.order_phase_selection` helper; `pipeline.run` filters the flow to the chosen subset.
3. **Monitor redesign** — rewrites `web/src/components/fleet/batch-funnel.tsx` (book-grouping `TransportRow`), new `monitor-stats.tsx`, `batch-lesson-list.tsx` changes, a `not_started` rollup status (whole-book lesson list incl. unlaunched lessons), `rollup-bar`/`status.ts` tweaks.
4. **Subject-category drilldown** — new `web/src/lib/subjects.ts`, `layout.tsx`/`monitor.tsx` tweaks, `section.tsx` (+379) phase-picker + custom-prompt UI, `job.tsx` (+152).

## 2. Functional verdict — the feature is SOUND and does NOT undo C1–C4 (all verified against real code)

- **C3 judge fidelity is intact.** The override is `contract = contract_override or get_prompt(...)`; the `_fidelity_flags(output_md, lesson_context)` call + the LESSON-CONTEXT source-fidelity instruction are **untouched** (`phase_judge.py`). A custom-prompted phase is still fact-checked against the source. ✅
- **Claim gate is intact.** The PR touches only `jobs.create()`, **not `claim_next_job`** — C3's per-job-provider COALESCE gate and C4's batch-pause + `fleet_api_paused` gates fully survive. ✅
- **Phase-picker is DAG-safe.** `resolve_phase_deps` only waits on deps present in the live subset, so deselecting a dependency degrades quality (a phase may run on the lesson summary alone) but **does not deadlock** the wave scheduler. `pipeline.run` re-filters the stored subset against the live flow defensively. ✅
- **Model columns coexist.** custom_prompts/selected_phases (PR) and paused_at/paused_reason + cache_creation_tokens (C4) are in different file regions → auto-merge, no revert. ✅

## 3. BLOCKERS — must be handled during the rebase (each verified)

### 3.1 🔴 Alembic multiple-heads (hard break — `alembic upgrade head` fails)
Their branch reconciled the custom-prompts chain to our `0029` via two merge migrations; head = `daa93bd3ce94`. **C4 added `0030→0031→0032` off `0029`.** After merge, two heads exist (`daa93bd3ce94` and `0032_budget_state`).

**PREFERRED FIX (clean): re-chain linearly, delete the merge migrations.**
Because we're rebasing anyway, drop the fork entirely:
- **Delete** `43cde4a391e0_merge_*.py` and `daa93bd3ce94_merge_*.py` (they only existed to reconcile the pre-rebase fork — moot after rebase).
- **Renumber + re-chain** the two REAL schema migrations linearly onto our tip:
  - `0033_custom_prompts_selected_phases` — `down_revision="0032_budget_state"` (adds the 4 columns: custom_prompts/selected_phases on homework_jobs + batches).
  - `0034_widen_prompt_hash` — `down_revision="0033_custom_prompts_selected_phases"` (widen `phase_outputs.prompt_hash` 64→128).
- Result: single linear chain `…0032→0033→0034`, one head. Re-stamp `docs/DATABASE.md` head to `0034` (lines 5 + 31 — the per-cluster head-stamp bump that every cluster forgets) and add the 3 new column rows + the prompt_hash widen note.
- *(Avoid the alternative "keep the fork + add a 3rd merge migration" — it works but leaves a messy 3-merge graph.)*

### 3.2 🔴 `app/api/v1/batch.py` `_rollup_payload` — C4 pause fields lost if mis-resolved
C4's `_rollup_payload` emits `paused_at`/`paused_reason` (lines ~70-71) **and** the fleet payload emits `paused_at`/`paused_reason`/`fleet_api_paused_at` (~275-278). The PR rewrote the SAME function for `not_started`-aware `lessons_covered`/`complete` and has **no pause fields**. **Resolve to keep BOTH:** C4's pause fields *and* the PR's `not_started` completeness math. Verify the fleet-rollup endpoint still emits the pause fields too.

### 3.3 🔴 `web/src/components/fleet/batch-funnel.tsx` — C4 budget-pause badge silently deleted
Looks like a one-line import conflict (`useMemo` vs `PauseCircle`), but the PR **rewrote the component into `TransportRow` and dropped C4's paused-badge block entirely** (`PauseCircle`/`paused_at`/`paused_reason` appear ZERO times in the PR's whole fleet FE). **Resolve:** keep `PauseCircle` in the import AND **re-add C4's paused-badge block into the new `TransportRow`** (right after its `RollupBar`, ~line 52). The C4 block to graft back:
```tsx
{batch.paused_at && (
  <div className="...amber...">
    <PauseCircle className="size-3.5 shrink-0" />
    <span>Paused — budget cap reached{batch.paused_reason ? ` (${batch.paused_reason})` : ""}</span>
  </div>
)}
```
(`web/src/lib/types.ts` auto-merges and keeps C4's `paused_at`/`paused_reason` on `BatchSummary`, so the data is available — only the render was dropped.)

### 3.4 ⚠️ `app/api/v1/jobs.py` — content conflict, verify C4 edits survive
Both branches edited the jobs schema/endpoint. Auto-merge is likely but **confirm C4's edits aren't lost**; resolve to keep both C4's and the PR's `generate()`/`_job_out` changes.

## 4. NON-blocking issues to fix while in there (recommended, not strictly required)

- 🟡 **ON-CONFLICT NULL-out bug** (`batches.get_or_create_for_book`): the `set_=` clause unconditionally writes `custom_prompts`/`selected_phases` to the passed value, so a plain same-transport re-launch/top-up (no custom prompts) **NULLs out** the batch's stored provenance. Self-contained (job rows carry the authoritative copy, so it's label corruption not data loss). Fix: `COALESCE(excluded.custom_prompts, batches.custom_prompts)` (same for selected_phases). **Add a test** for the re-launch-without-prompts path.
- 🟡 **Vacuous test** — `tests/services/test_judge_contract_override.py` is pure `inspect.getsource`/string-grep; it passes even if the override is wired wrong. Rewrite to call `judge(..., contract_override=...)` and assert the contract actually used. (Parts of `test_pipeline_custom_prompt.py` are the same grep style.)
- 🟡 **No test** covers the deselected-dependency runtime path end-to-end (only the helper's "no deps added" contract) — add one if cheap.

## 5. "Is theirs better?" — adopt/preserve decisions

| Area | Decision | Why |
|---|---|---|
| Custom-prompt upload + phase-picker | **ADOPT (new value)** | We have nothing equivalent; logically compatible with C1–C4. |
| Monitor redesign + subject drilldown + `not_started` rollup | **ADOPT the redesign, GRAFT C4 into it** | It's the intended new UI; just blind to C4's pause state. Re-add the pause badge (3.3) + keep pause payload (3.2). |
| C3 judge / C4 cost-safety / claim gate / migrations 0029–0032 | **PRESERVE ours** | Load-bearing; PR's changes are compatible/additive — never take "their" version of these. |

There is **no place where their version is clearly better than a C1–C4 deliverable it would replace** — the overlap is purely additive (new features) or accidental (the rewritten files that happen to drop C4's UI). So the rule is simple: **adopt their additions, preserve every C1–C4 behavior, graft C4's pause UI/payload into their rewritten files.**

## 6. Suggested task skeleton for the plan (TDD-per-task, commit per task)

1. **Rebase** `Habibullo` onto `origin/Nggaev-v2` (`f683153`) in a worktree.
2. **Migrations:** delete the 2 merge migrations; renumber custom-prompts→`0033`, widen→`0034`, re-chain onto `0032`; verify `alembic heads` shows exactly one; migrate a **scratch DB** to head and confirm.
3. **Resolve `batch.py` `_rollup_payload`** (keep C4 pause fields + PR `not_started`); test both.
4. **Resolve `batch-funnel.tsx`** (keep `PauseCircle` + re-add the paused badge in `TransportRow`); `tsc --noEmit` clean.
5. **Verify `jobs.py` merge** keeps C4 edits.
6. **Fix ON-CONFLICT NULL-out** with COALESCE + test (§4).
7. **Harden the vacuous judge-override test** (§4).
8. **Finish:** full suite green incl. `RUN_DB_INTEGRATION=1` (rollup + claim-gate are exactly where PR meets C4); de-stale `docs/DATABASE.md` head + new columns; worklog + INDEX + plan→shipped; **route the plan to the gatekeeper before execution, and the PR back to the gatekeeper for merge** (no self-merge).

## 7. Acceptance gate (what the gatekeeper will re-verify)
- `alembic heads` = 1; scratch-DB `upgrade head` clean through `0034`.
- `_rollup_payload` (both batch + fleet) still emits `paused_at`/`paused_reason`/`fleet_api_paused_at`.
- `batch-funnel.tsx` renders the paused badge; `tsc` clean.
- `claim_next_job` byte-unchanged from `Nggaev-v2` (C3+C4 gate preserved).
- `phase_judge` `_fidelity_flags` path intact.
- Full suite + DB-integration green; custom-prompt + phase-picker tests are real (not grep-only).
