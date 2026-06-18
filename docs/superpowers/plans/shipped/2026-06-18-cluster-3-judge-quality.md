# Cluster 3 — Judge Quality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the LLM phase-judge verify source-fidelity (not just contract-format), make grading observable + non-silent-fail, and stop per-job judge/extract picks from stranding jobs at the claim gate.

**Architecture:** Three independent workstreams on one branch (`cluster-3-judge-quality`, worklog **0079**): (A) judge-fidelity — reframe the judge prompt + a conservative warning-only deterministic key-fact check; (B) judge-softfail — a queryable `phase_outputs.judge_status`, judge retry-once, configurable regen cap, post-regen re-check; (C) judge-claimgate — thread the job's `judge_provider`/`extract_provider` into the claim gate. R20 (golden-eval) is a deliberate follow-on, NOT in this plan.

**Tech Stack:** FastAPI, async SQLAlchemy, Alembic, Postgres, pytest/pytest-asyncio, the CLI/SDK agent router.

---

## Approach & key decisions

- **Fidelity (corrected from the brief):** the lesson source ALREADY reaches the judge model — `phase_judge.judge()` passes `lesson_context` to `agent.run_phase`, and `_build_master_prompt` (agent.py:550-552) appends a `--- LESSON CONTEXT ---` block. Verified. So the fix is **not** "thread the source in"; it is: (1) **keep the existing full-coverage injection** and reframe `_INSTRUCTIONS` to point the judge at that LESSON CONTEXT block as ground-truth and fact-check against it — **do NOT trim** (gate ruling: trimming adds a mid-lesson fidelity blind spot and saves no new cost since the full source already flows today; only bound if a real judge context-window limit is hit on the longest lessons, and if so bound generously + `log()` the trim), and (2) feed a **conservative, warning-only** deterministic key-fact/number signal into the judge for adjudication.
- **Deterministic check guardrail (non-negotiable, from the gate):** flag ONLY world-claim facts (4-digit years / dates, named quantities-with-units stated as declarative claims) that are absent-or-contradicted in source. NEVER flag generated exercise / worked-example numbers — a blind "number not in source" check false-positives on every algebra/geometry lesson → the R14 regen-tax. The signal is **advisory input to the LLM judge, never an independent regex regen-gate**. Acceptance is two-sided (flags an invented date; does NOT flag a math worked-example number). If (b) can't be clean, it stays warning-only (it already is) and the gating version defers to R20.
- **Softfail:** add `phase_outputs.judge_status` (`ok`/`major_shipped`/`unavailable`/`major_regen_failed`) so degraded grades are queryable; retry the judge once on a transient (non-auth) error; make the regen cap a setting (`max_judge_regens`, default 1 = today's behavior); re-check the post-regen verdict and record `major_shipped` when a regen still fails MAJOR (today it's silently accepted). Judge-timeout (cluster 1) is OUT of this lane.
- **Claimgate:** the ONLY genuine per-job-override drift is `worker._compute_capabilities`→`jobs.claim_next_job` (audit confirmed `model_tiers.resolve_judge` and `pipeline._resolve_extract` already honor per-job picks; `toc_extractor` is job-less; `worker.py:197` is advisory-only). Fix: evaluate the worker's per-provider api capability against the JOB's resolved judge/extract provider (`COALESCE(job.col, settings.default)`), not against the settings provider.
- **Rejected:** bundling R20 (balloons scope + blast radius; it consumes this fidelity check) — follow-on. Splitting claimgate to its own branch (no second worklog ID reserved; it's small and same-cluster) — keep it here as workstream C.
- **Parallel-run caution:** clusters 1/2/4/5 also touch `pipeline.py`/`phase_judge.py`/`jobs.py`/`worker.py`. Stage ONLY this cluster's files; expect a `MASTER_MEMORY.md`/`INDEX.md`/`WISHLIST.md`/`ROADMAP.md` rebase conflict (append-only, keep both) and rebase on `origin/Nggaev-v2` before PR.

---

## File structure

- `app/services/phase_judge.py` — fidelity prompt reframe (A1) + deterministic signal (A2).
- `app/services/pipeline.py` — judge retry-once, regen cap, post-regen re-check, judge_status threading (B4).
- `app/config.py` — `max_judge_regens` setting (B3).
- `app/models/phase_output.py` — `judge_status` column (B2).
- `app/repositories/phase_outputs.py` — `set_status(..., judge_status=...)` (B2/B4).
- `alembic/versions/0028_phase_output_judge_status.py` — new migration (B1).
- `app/services/worker.py` — capabilities carry settings defaults (C1).
- `app/repositories/jobs.py` — per-row judge/extract provider capability in claim SQL (C1).
- `scripts/smoke_judge_fidelity.py` — two-sided real-CLI acceptance (D).
- Tests: `tests/services/test_phase_judge.py`, `tests/services/test_pipeline_judge_status.py` (new), `tests/services/test_auth_env.py`, `tests/integration/test_claim_contention.py`.

---

## Workstream A — judge-fidelity-1 (generation-affecting → CLI smoke at the gate)

### Task A1: Reframe the judge prompt to verify source-fidelity (FULL-coverage; no trim)

> **Gate ruling (change #3):** keep the FULL source the judge already receives — do NOT embed a copy in `_build_judge_prompt` and do NOT trim to a 6k excerpt (that adds a mid-lesson blind spot and saves no new cost). Keep `judge()` passing the full `lesson_context` to `run_phase` (which injects the `--- LESSON CONTEXT ---` block via `_build_master_prompt`). Just reframe the INSTRUCTIONS to point the judge at that block as ground-truth. `_build_judge_prompt` gains only the A2 `fidelity_flags` hints — never the source itself.

**Files:**
- Modify: `app/services/phase_judge.py` (`_INSTRUCTIONS`, `_build_judge_prompt` ~:75, `judge()` ~:148-164)
- Test: `tests/services/test_phase_judge.py`

- [ ] **Step 1: Update the failing test** — keep `test_build_judge_prompt_contains_contract_output_and_protocol` (test_phase_judge.py:24) passing and ADD:

```python
def test_build_judge_prompt_has_fidelity_rule_and_flags():
    p = pj._build_judge_prompt(
        contract="CONTRACT-TEXT", output_md="OUTPUT-TEXT",
        fidelity_flags=["output states year 1991 as fact; not found in source"],
    )
    assert "CONTRACT-TEXT" in p and "OUTPUT-TEXT" in p
    # instruction points the judge at the (separately-injected) LESSON CONTEXT block as truth
    assert "lesson context" in p.lower() and ("ground truth" in p.lower() or "faithful" in p.lower())
    assert "1991" in p                      # deterministic hint surfaced
    assert "POSSIBLE SOURCE ISSUES" in p

def test_build_judge_prompt_omits_flags_section_when_empty():
    p = pj._build_judge_prompt(contract="C", output_md="O", fidelity_flags=[])
    assert "POSSIBLE SOURCE ISSUES" not in p
```

- [ ] **Step 2: Run → FAIL** (`uv run python -m pytest tests/services/test_phase_judge.py::test_build_judge_prompt_has_fidelity_rule_and_flags -v`). Expected: TypeError (new kwarg) / AssertionError.

- [ ] **Step 3: Implement.** Add a fidelity clause to `_INSTRUCTIONS` (pointing at the injected block — NOT an embedded copy) and add `fidelity_flags` to `_build_judge_prompt`:

```python
_FIDELITY_RULE = (
    "\n\nSource-fidelity (CRITICAL): a LESSON CONTEXT section is provided below — the lesson the "
    "output was authored from. Treat it as ground truth. Raise a `major` failure for any factual "
    "claim ABOUT THE WORLD in the OUTPUT that is contradicted by, or absent from, the LESSON "
    "CONTEXT (e.g. an invented date, statistic, name, or definition). DO NOT flag numbers the "
    "OUTPUT generates for teaching — practice-problem values, worked-example arithmetic, invented "
    "student names, hypothetical scenarios — these are expected and are NOT fidelity violations. A "
    "'POSSIBLE SOURCE ISSUES' list may be provided as a hint; verify each against the LESSON "
    "CONTEXT before trusting it, and drop any you cannot substantiate."
)

def _build_judge_prompt(
    *, contract: str, output_md: str, fidelity_flags: Optional[list[str]] = None,
) -> str:
    parts = [
        _INSTRUCTIONS + _FIDELITY_RULE,
        "\n\n## CONTRACT (the authoring instructions the output must satisfy)",
        contract.strip(),
    ]
    if fidelity_flags:
        parts += ["\n## POSSIBLE SOURCE ISSUES (hints — verify against LESSON CONTEXT before trusting)",
                  "\n".join(f"- {f}" for f in fidelity_flags)]
    parts += ["\n## OUTPUT UNDER REVIEW", output_md.strip(), ""]
    return "\n".join(parts)
```

(No `_source_excerpt` helper — the full source stays in the run_phase-injected block.)

- [ ] **Step 4: Wire `judge()`** — compute flags, pass them to `_build_judge_prompt`, and KEEP passing the full `lesson_context` to `run_phase` (it injects the LESSON CONTEXT block the instruction now references). In `judge()`:

```python
    flags = _fidelity_flags(output_md, lesson_context)   # Task A2; stub returns [] until A2 lands
    judge_prompt = _build_judge_prompt(
        contract=contract, output_md=output_md, fidelity_flags=flags,
    )
    result = await agent.run_phase(
        provider=judge_provider, model=judge_model, phase_prompt=judge_prompt,
        phase_name="__judge__", schema=Verdict,
        lesson_context=lesson_context,    # UNCHANGED — full source still injected by _build_master_prompt
        prior_outputs=prior_outputs, difficulty=None,
        operation=f"judge:{phase_name}", homework_job_id=homework_job_id,
        phase_output_id=phase_output_id, transport=transport,
    )
```

(For this task land `_fidelity_flags` as a stub `def _fidelity_flags(output_md, lesson_context): return []` so A1 is self-contained; A2 replaces the body.)

- [ ] **Step 5: Run → PASS** (`uv run python -m pytest tests/services/test_phase_judge.py -v`). Fix any existing prompt-shape test that asserts old structure.

- [ ] **Step 6: Commit** — `git add app/services/phase_judge.py tests/services/test_phase_judge.py && git commit -m "c3: judge verifies source-fidelity against the full injected LESSON CONTEXT (reframe, no trim)"`

### Task A2: Conservative warning-only deterministic key-fact/number signal

**Files:** Modify `app/services/phase_judge.py`; Test `tests/services/test_phase_judge.py`

- [ ] **Step 1: Write failing tests** — encode the guardrail (the math case is the load-bearing one):

```python
def test_fidelity_flags_catches_world_claim_year_absent_from_source():
    out = "The treaty was signed in 1991, ending the union."
    src = "The republic became independent. (no dates given)"
    flags = pj._fidelity_flags(out, src)
    assert any("1991" in f for f in flags)

def test_fidelity_flags_ignores_math_worked_example_numbers():
    out = "Solve 3x + 7 = 22. Subtract 7: 3x = 15. Divide by 3: x = 5."
    src = "Linear equations: isolate the variable using inverse operations."
    assert pj._fidelity_flags(out, src) == []          # MUST be empty — no regen-tax on math

def test_fidelity_flags_passes_year_present_in_source():
    out = "Independence was declared in 1991."
    src = "In 1991 the republic declared independence."
    assert pj._fidelity_flags(out, src) == []
```

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement — conservative + scoped.** Only 4-digit calendar years (1000–2099) stated as declarative claims, absent from source, and NOT adjacent to math/exercise cues. Returns advisory strings (never raises, never gates):

```python
import re
_YEAR_RE = re.compile(r"\b(1[0-9]{3}|20[0-9]{2})\b")
# math/exercise cues near a number => generated, never a world-claim
_MATH_CUES = ("=", "x ", " x", "solve", "equation", "calculate", "simplify",
              "÷", "×", "·", "√", "step", "answer:", "problem")

def _fidelity_flags(output_md: str, lesson_context: Optional[str]) -> list[str]:
    """ADVISORY ONLY (never gates a regen): surface declarative world-claim YEARS in the
    output that are absent from the source. Deliberately narrow — years only, and only when
    no math/exercise cue sits on the same line — so generated exercise numbers never flag
    (the R14 regen-tax guard). The LLM judge adjudicates these hints."""
    src = (lesson_context or "")
    if not src.strip():
        return []
    src_years = set(_YEAR_RE.findall(src))
    flags: list[str] = []
    for line in output_md.splitlines():
        low = line.lower()
        if any(cue in low for cue in _MATH_CUES):
            continue                                   # generated/teaching numbers — skip
        for y in _YEAR_RE.findall(line):
            if y not in src_years and not any(y in f for f in flags):
                flags.append(f"output states year {y} as fact; not found in source")
    return flags[:8]                                   # cap the hint list
```

- [ ] **Step 4: Run → PASS** (all three; the math test is the gate).

- [ ] **Step 5: Commit** — `git add app/services/phase_judge.py tests/services/test_phase_judge.py && git commit -m "c3: conservative warning-only fidelity signal (years only; skips math worked-examples)"`

---

## Workstream B — judge-softfail-1 (schema + observable grading)

### Task B1: Migration 0029 — `phase_outputs.judge_status`

> **Gate change #1 (collision):** C1 shipped `0028_enum_check_constraints` (now head). This migration is **`0029_judge_status`**, `down_revision="0028_enum_check_constraints"`. Gate verifies single alembic heads.
> **Gate change #6 (populated-table safety):** `phase_outputs` is populated. The column is **nullable, NO CHECK constraint** — values are app-enforced (`ok`/`major_shipped`/`major_regen_failed`/`unavailable`), historical rows stay NULL = "pre-feature". If a future CHECK/enum is wanted it must allow NULL (or backfill first) — out of scope here.

**Files:** Create `alembic/versions/0029_judge_status.py`

- [ ] **Step 1:** confirm head — `uv run alembic heads` → expect exactly `0028_enum_check_constraints (head)`.
- [ ] **Step 2:** write the migration (plain nullable text, no CHECK — safe on the populated table):

```python
"""add phase_outputs.judge_status

Revision ID: 0029_judge_status
Revises: 0028_enum_check_constraints
"""
from alembic import op
import sqlalchemy as sa

revision = "0029_judge_status"
down_revision = "0028_enum_check_constraints"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.add_column("phase_outputs", sa.Column("judge_status", sa.String(length=24), nullable=True))

def downgrade() -> None:
    op.drop_column("phase_outputs", "judge_status")
```

- [ ] **Step 3:** `uv run alembic upgrade head` against the dev DB (which HAS rows) → succeeds with no constraint violation; `uv run alembic downgrade -1` then `upgrade head` round-trips clean; `uv run alembic heads` shows a single head.
- [ ] **Step 4: Commit** — `git add alembic/versions/0029_judge_status.py && git commit -m "c3: migration 0029 — phase_outputs.judge_status (nullable, app-enforced)"`

### Task B2: Model field + repo write-through

**Files:** Modify `app/models/phase_output.py`, `app/repositories/phase_outputs.py`; Test `tests/services/test_pipeline_judge_status.py` (new)

- [ ] **Step 1:** add to `PhaseOutput` (after `validation_warnings`, phase_output.py:32):

```python
    judge_status: Mapped[Optional[str]] = mapped_column(String(24), nullable=True)
```

- [ ] **Step 2:** extend `phase_repo.set_status` to accept and persist `judge_status: Optional[str] = None` (mirror how `validation_warnings` is set; only overwrite when the arg is not None so other callers don't clobber it). Write a unit test that sets `judge_status="ok"` and reads it back (use the existing repo test harness / in-memory-or-RUN_DB_INTEGRATION pattern already used for phase_outputs).
- [ ] **Step 3:** Run → PASS.
- [ ] **Step 4: Commit** — `git add app/models/phase_output.py app/repositories/phase_outputs.py tests/services/test_pipeline_judge_status.py && git commit -m "c3: PhaseOutput.judge_status field + set_status write-through"`

### Task B3: Configurable regen cap

**Files:** Modify `app/config.py` (~:108 area); Test `tests/test_config.py` (or the existing settings test)

- [ ] **Step 1:** failing test — `settings` exposes `max_judge_regens` defaulting to `1` (= today's single-regen behavior).
- [ ] **Step 2:** add `max_judge_regens: int = 1` with a comment near `judge_provider`.
- [ ] **Step 3:** Run → PASS. **Step 4: Commit** — `c3: max_judge_regens setting (default 1 = current behavior)`.

### Task B4: Pipeline — judge retry-once, regen-cap loop, post-regen re-check, judge_status

**Files:** Modify `app/services/pipeline.py` (the judge block, current lines **884-963**; final `set_status` ~:990); Test `tests/services/test_pipeline_judge_status.py`

> **Gate change #2 (build on C1, don't clobber):** C1 already wrapped BOTH judge call sites in `_judge_with_timeout` (pipeline.py:639, called at :899 and :934). My changes wrap AROUND `_judge_with_timeout` — call it, not raw `phase_judge.judge`. A timeout already degrades to `available=False` (judge-unavailable: TimeoutError), so the `not outcome.available → judge_status="unavailable"` mapping covers the timeout path automatically. Keep the existing api-auth re-raise in the `except` (pipeline.py:942-954). Do NOT add another timeout.

- [ ] **Step 1: Write failing tests** (`tests/services/test_pipeline_judge_status.py` — stub `_judge_with_timeout` + `_run_with_failover`; assert the recorded `judge_status` per path). Cover: (i) clean pass → `ok`; (ii) MAJOR → regen → clean → `ok`; (iii) MAJOR → regen → still MAJOR → `major_shipped` (NOT silently accepted — today's gap); (iv) initial judge `unavailable` (e.g. the C1 timeout-degrade shape) retried once, still unavailable → `unavailable`; (v) `max_judge_regens=0` → MAJOR recorded `major_shipped`, zero regen calls; (vi) regen raises (non-auth) → existing soft-degrade keeps original output, `judge_status="major_regen_failed"`.

- [ ] **Step 2: Implement** in the judge block (884-963):
  - **Retry-once:** after the initial `_judge_with_timeout` call (line 899), if `not outcome.available`, call `_judge_with_timeout` once more before accepting unavailable. (An `unavailable` outcome is by definition non-auth — api-auth errors re-raise inside `phase_judge.judge`, never degrade — so retrying is safe.)
  - **Capped regen loop:** replace the single `if outcome.available and outcome.has_major:` (line 909) with `for _ in range(settings.max_judge_regens):` guarded by `if not (outcome.available and outcome.has_major): break`. Each iteration runs the existing regen-generation + post-regen `_judge_with_timeout` (lines 921-941), then loops only if still MAJOR and budget remains. Default `max_judge_regens=1` ⇒ byte-identical behavior to today.
  - **judge_status** (compute after the loop):
    ```python
    if not outcome.available:   judge_status = "unavailable"
    elif outcome.passed:        judge_status = "ok"
    elif outcome.has_major:     judge_status = "major_shipped"   # budget spent, still MAJOR (was silently accepted)
    else:                       judge_status = "ok"              # minor-only warnings
    ```
    In the regen-failed `except` soft-degrade branch (line 955) set `judge_status = "major_regen_failed"`.
  - Pass `judge_status=judge_status` into the final `phase_repo.set_status(...)` (~:990). For phases that aren't judged (extract / the `phase_name == "extract"` skip) leave `judge_status=None`.

- [ ] **Step 3: Run → PASS** (all six paths).
- [ ] **Step 4: Commit** — `git add app/services/pipeline.py tests/services/test_pipeline_judge_status.py && git commit -m "c3: judge retry-once + capped regen loop (wraps C1 timeout) + post-regen re-check + judge_status"`

---

## Workstream C — judge-claimgate-1 (per-job judge/extract honored at the claim gate)

### Task C1: Evaluate worker capability against the JOB's resolved judge/extract provider

**Files:** Modify `app/services/worker.py` (`_compute_capabilities` ~:56, `CAPABILITIES` ~:83, the claim call), `app/repositories/jobs.py` (`claim_next_job` ~:268-301); Tests `tests/services/test_auth_env.py`, `tests/integration/test_claim_contention.py`

> **Gate change #5 (composable):** C4 (cost kill-switch) also edits `claim_next_job`. Keep `judge_ok`/`extract_ok` as **separate additive `.where(...)` predicates** (as today) so C4 can `AND` its own gate alongside — do NOT fold everything into one monolithic predicate. Whoever merges second composes; flag the gate to sequence C3↔C4.
> **Gate note 7 (literal stranding test is mandatory):** the claim-gate test MUST include the exact live bug — an `api` + `gemini`-judge job is claimable by a Vertex-only worker with NO `ANTHROPIC_API_KEY`. That scenario is the whole reason this fix exists.

- [ ] **Step 1: Write the failing test(s)** in `tests/services/test_auth_env.py` (build the SQL/caps without a live DB, mirroring the existing not-pair-branch test; add a DB-backed claim in `test_claim_contention.py` under `RUN_DB_INTEGRATION`). The literal stranding scenario:

```python
def test_api_gemini_judge_job_claimable_by_vertex_only_worker():
    # THE live bug: Vertex-only worker (gemini api yes, NO anthropic key);
    # settings.judge_provider='claude'. A job transport=api, judge_provider='gemini',
    # judge_transport='inherit' MUST NOT be excluded by the claim gate.
    caps = {"can_claude_api": False, "can_gemini_api": True,
            "judge_pair": ("claude", "claude-opus-4-7"), "judge_fallback_api_ok": False,
            "extract_api_ok": True,
            "settings_judge_provider": "claude", "settings_extract_provider": "gemini"}
    # Build the claim WHERE with these caps; assert the gemini-judge api job row is INCLUDED.
    # Pre-fix it is excluded (judge_api_ok=cap['claude']=False) — that is the strand.

def test_null_model_and_settings_default_jobs_still_claim():
    # regression: a job with judge_provider=NULL falls back to settings.judge_provider;
    # the existing not-pair-branch / null-model tests stay green.
```

- [ ] **Step 2: Implement.** `_compute_capabilities` already has `can_claude_api`/`can_gemini_api`. Add the settings defaults to its return so the SQL can `COALESCE`:

```python
        "settings_judge_provider": judge_provider,
        "settings_extract_provider": extract_provider,
```

In `claim_next_job`, resolve the per-row judge provider and gate on the matching per-provider cap:

```python
    s_judge = caps.get("settings_judge_provider") or ""
    s_extract = caps.get("settings_extract_provider") or ""
    resolved_judge_provider = func.coalesce(HomeworkJob.judge_provider, s_judge)
    resolved_extract_provider = func.coalesce(HomeworkJob.extract_provider, s_extract)

    def _provider_api_ok(resolved):
        # worker can serve api for this provider?
        return or_(
            and_(resolved == "claude", literal(bool(caps.get("can_claude_api")))),
            and_(resolved == "gemini", literal(bool(caps.get("can_gemini_api")))),
        )

    judge_ok = or_(
        not_(judge_needs_api),
        and_(job_is_judge_pair, literal(bool(caps.get("judge_fallback_api_ok")))),
        and_(not_(job_is_judge_pair), _provider_api_ok(resolved_judge_provider)),
    )
    extract_ok = or_(not_(extract_needs_api), _provider_api_ok(resolved_extract_provider))
```

Keep the existing `job_is_judge_pair` self-fallback branch (jobs.py:288-296) — it still gates on the configured pair. Preserve the NULL-model `coalesce` note.

- [ ] **Step 3:** ensure the live claim call passes the enriched caps (it already passes `CAPABILITIES` / `capabilities=`); no signature change needed beyond the dict keys.
- [ ] **Step 4: Run → PASS**, and re-run the existing claim tests (`test_null_model_job_claims_via_not_pair_branch`, the api-readiness tests) to prove no regression: `uv run python -m pytest tests/services/test_auth_env.py tests/integration/test_claim_contention.py -v` (the integration ones need `RUN_DB_INTEGRATION=1` + `DATABASE_URL`).
- [ ] **Step 5: Commit** — `git add app/services/worker.py app/repositories/jobs.py tests/services/test_auth_env.py tests/integration/test_claim_contention.py && git commit -m "c3: claim gate honors per-job judge/extract provider (COALESCE job over settings)"`

### Task C2: Record the settings-drift audit (gate re-verifies — note 7)

- [ ] In the worklog (finish step), record the audit result (the standing per-job-selection-overrides-env principle: a per-job pick must override `.env` on EVERY path). Verified this branch: only `worker._compute_capabilities`→claim gate drifted; `model_tiers.resolve_judge` (pipeline.py:896) and `pipeline._resolve_extract` (pipeline.py:49-50) already prefer the per-job override; `worker.py:197` is advisory-startup-only (acceptable); `toc_extractor` is job-less (settings correct). No code change beyond C1. **The gate will independently `grep` every `settings.judge_*`/`settings.extract_*` read at the PR to confirm — so the audit note must be exhaustive and current, not a summary.**

---

## Task D — Acceptance gate: two-sided real-CLI fidelity smoke (REQUIRED — generation-affecting)

**Files:** Create `scripts/smoke_judge_fidelity.py`

- [ ] **Step 1:** in-process script (no server) that calls the REAL `phase_judge.judge()` with a real `claude`/`gemini` judge (cli transport; pick whichever CLI authenticates headless — claude has worked in this env per session notes). Two cases:
  - **(a) catches an invented fact:** a history-phase `output_md` asserting a date NOT in a small hand-written `lesson_context` → assert `outcome.has_major is True` and a failure cites the date.
  - **(b) does NOT regen-tax math:** a math-phase `output_md` full of worked-example numbers, with a `lesson_context` that states only the method → assert `outcome.passed is True` (or at least `has_major is False`).
- [ ] **Step 2:** run it for real; capture output in the worklog. **If (b) is not clean, do NOT ship the gating behavior** — the deterministic signal is already warning-only, so confirm the LLM judge isn't over-flagging math; if it is, tighten `_FIDELITY_RULE` wording and re-run. Fact over theory: paste the actual judge verdicts into the worklog.
- [ ] **Step 3: Commit** — `git add scripts/smoke_judge_fidelity.py && git commit -m "c3: two-sided real-CLI fidelity smoke (flags invented date; spares math worked-examples)"`

---

## Task E — Finish (do NOT defer any sub-item)

- [ ] **Rebase:** `git fetch origin` → `git log HEAD..origin/Nggaev-v2`; if base moved, `git rebase origin/Nggaev-v2`, resolve the expected append-only conflicts in `MASTER_MEMORY.md`/`INDEX.md`/`WISHLIST.md`/`ROADMAP.md` (keep both blocks), re-run `uv run python -m pytest tests/ -q`.
- [ ] **Full suite green:** `uv run python -m pytest tests/ -q` + `cd web && npx tsc -p tsconfig.app.json --noEmit` (FE untouched, but confirm).
- [ ] **Worklog 0079:** append `## [0079]` to `docs/memory/MASTER_MEMORY.md` (fidelity correction + the three fixes + the smoke results) + an `INDEX.md` row.
- [ ] **Backlog closes WITH the mechanism correction:** in `WISHLIST.md`/`ROADMAP.md`, close `judge-fidelity-1`, `judge-softfail-1`, `judge-claimgate-1`; **fix the wrong text** in `judge-fidelity-1` and `R20` ("judge never sees the source"/"not fed into the prompt" → the source DOES reach the judge; the gap was instructions + no deterministic check). Leave **R20 OPEN** (follow-on) with a note that it now **builds on the shipped fidelity check** (reframed judge + warning-only year signal) — R20 is the tuned/baselined gating home for the deterministic check.
- [ ] **Plan `git mv`** this file → `docs/superpowers/plans/shipped/`.
- [ ] **De-stale reference docs:** `docs/HOW_IT_WORKS.md` (judge now checks source-fidelity + `judge_status`), `docs/CODE_MAP.md` (phase_judge fidelity signal; claim-gate per-job providers), `docs/DATABASE.md` (`phase_outputs.judge_status`).
- [ ] **PR:** `[cluster-3] judge quality — source-fidelity, judge_status, per-job claim gate`. Do NOT self-merge.

---

## Self-review

- **Spec coverage:** judge-fidelity-1 → A1+A2+D; judge-softfail-1 → B1-B4; judge-claimgate-1 → C1+C2; R20 deliberately excluded (follow-on, recorded in E). ✓
- **Type consistency:** `_build_judge_prompt(*, contract, output_md, fidelity_flags=None)` (no source_excerpt — full source stays in the run_phase-injected block) used consistently in A1/A2; `judge()` keeps `lesson_context=lesson_context` to `run_phase`; `judge_status` string set in B4, persisted via B2's `set_status(..., judge_status=...)`; `judge_status` values (`ok`/`major_shipped`/`major_regen_failed`/`unavailable`) are the same set in B4's compute + tests. ✓
- **Gate changes folded:** #1 migration **0029** (down_rev `0028_enum_check_constraints`); #2 wraps `_judge_with_timeout` (timeout→`unavailable`); #3 full source coverage, no trim; #4 A2 years-only as-is; #5 additive claim predicate; #6 nullable no-CHECK on populated table; #7 literal Vertex-only stranding test + exhaustive audit note. ✓
- **Guardrail encoded:** A2's math test is the gate; the deterministic signal is warning-only (never sets `has_major`); D is two-sided. ✓
- **No placeholders:** every code step has real code; every test step has real asserts; commands are exact. ✓
- **Lane discipline:** only this cluster's files staged per task; rebase-conflict expectation + C3↔C4 claim-gate composition called out. ✓
