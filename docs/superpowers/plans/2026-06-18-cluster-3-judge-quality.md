# Cluster 3 — Judge Quality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the LLM phase-judge verify source-fidelity (not just contract-format), make grading observable + non-silent-fail, and stop per-job judge/extract picks from stranding jobs at the claim gate.

**Architecture:** Three independent workstreams on one branch (`cluster-3-judge-quality`, worklog **0079**): (A) judge-fidelity — reframe the judge prompt + a conservative warning-only deterministic key-fact check; (B) judge-softfail — a queryable `phase_outputs.judge_status`, judge retry-once, configurable regen cap, post-regen re-check; (C) judge-claimgate — thread the job's `judge_provider`/`extract_provider` into the claim gate. R20 (golden-eval) is a deliberate follow-on, NOT in this plan.

**Tech Stack:** FastAPI, async SQLAlchemy, Alembic, Postgres, pytest/pytest-asyncio, the CLI/SDK agent router.

---

## Approach & key decisions

- **Fidelity (corrected from the brief):** the lesson source ALREADY reaches the judge model — `phase_judge.judge()` passes `lesson_context` to `agent.run_phase`, and `_build_master_prompt` (agent.py:550-552) appends a `--- LESSON CONTEXT ---` block. Verified. So the fix is **not** "thread the source in"; it is: (1) frame the source as ground-truth INSIDE `_build_judge_prompt` (bounded excerpt, to control judge token cost) and instruct the judge to fact-check against it, and (2) feed a **conservative, warning-only** deterministic key-fact/number signal into the judge for adjudication.
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

### Task A1: Reframe the judge prompt to verify source-fidelity

**Files:**
- Modify: `app/services/phase_judge.py` (`_INSTRUCTIONS`, `_build_judge_prompt` ~:75, `judge()` ~:148-164)
- Test: `tests/services/test_phase_judge.py`

- [ ] **Step 1: Update the failing test** — replace `test_build_judge_prompt_contains_contract_output_and_protocol` (test_phase_judge.py:24) and add a source-fidelity assertion:

```python
def test_build_judge_prompt_contains_contract_output_and_source(monkeypatch):
    p = pj._build_judge_prompt(
        contract="CONTRACT-TEXT", output_md="OUTPUT-TEXT",
        source_excerpt="SOURCE-FACTS", fidelity_flags=["claimed year 1991 absent from source"],
    )
    assert "CONTRACT-TEXT" in p and "OUTPUT-TEXT" in p
    assert "SOURCE-FACTS" in p                      # source is in the prompt
    assert "ground truth" in p.lower() or "faithful" in p.lower()
    assert "1991" in p                              # deterministic signal surfaced

def test_build_judge_prompt_omits_source_section_when_absent():
    p = pj._build_judge_prompt(contract="C", output_md="O", source_excerpt=None, fidelity_flags=[])
    assert "SOURCE OF TRUTH" not in p
```

- [ ] **Step 2: Run → FAIL** (`uv run python -m pytest tests/services/test_phase_judge.py::test_build_judge_prompt_contains_contract_output_and_source -v`). Expected: TypeError (new kwargs) / AssertionError.

- [ ] **Step 3: Implement.** Add a fidelity clause to `_INSTRUCTIONS` and rebuild `_build_judge_prompt`:

```python
_FIDELITY_RULE = (
    "\n\nSource-fidelity (CRITICAL): a SOURCE OF TRUTH section may be provided — the lesson "
    "the output must stay faithful to. Treat it as ground truth. Raise a `major` failure for any "
    "factual claim ABOUT THE WORLD in the OUTPUT that is contradicted by, or absent from, the "
    "SOURCE OF TRUTH (e.g. an invented date, statistic, name, or definition). DO NOT flag numbers "
    "the OUTPUT generates for teaching — practice-problem values, worked-example arithmetic, "
    "invented student names, hypothetical scenarios — these are expected and are NOT fidelity "
    "violations. A 'POSSIBLE SOURCE ISSUES' list may be provided as a hint; verify each against the "
    "SOURCE before trusting it, and drop any you cannot substantiate."
)

def _build_judge_prompt(
    *, contract: str, output_md: str,
    source_excerpt: Optional[str] = None, fidelity_flags: Optional[list[str]] = None,
) -> str:
    parts = [
        _INSTRUCTIONS + _FIDELITY_RULE,
        "\n\n## CONTRACT (the authoring instructions the output must satisfy)",
        contract.strip(),
    ]
    if source_excerpt:
        parts += ["\n## SOURCE OF TRUTH (the lesson the output must stay faithful to)", source_excerpt.strip()]
    if fidelity_flags:
        parts += ["\n## POSSIBLE SOURCE ISSUES (hints — verify before trusting)",
                  "\n".join(f"- {f}" for f in fidelity_flags)]
    parts += ["\n## OUTPUT UNDER REVIEW", output_md.strip(), ""]
    return "\n".join(parts)
```

Add a bounded-excerpt helper (control judge token cost — `lesson_context` can be large):

```python
_SOURCE_EXCERPT_CHARS = 6000  # head+tail budget; the full extract can be 20k+

def _source_excerpt(lesson_context: Optional[str]) -> Optional[str]:
    s = (lesson_context or "").strip()
    if not s:
        return None
    if len(s) <= _SOURCE_EXCERPT_CHARS:
        return s
    half = _SOURCE_EXCERPT_CHARS // 2
    return f"{s[:half]}\n…[source trimmed]…\n{s[-half:]}"
```

- [ ] **Step 4: Wire `judge()`** — build the excerpt + flags, embed them in the judge prompt, and STOP passing the full `lesson_context` to `run_phase` (avoid an unbounded duplicate LESSON CONTEXT block). In `judge()`:

```python
    excerpt = _source_excerpt(lesson_context)
    flags = _fidelity_flags(output_md, lesson_context)   # Task A2; returns [] for now if A2 not yet landed
    judge_prompt = _build_judge_prompt(
        contract=contract, output_md=output_md, source_excerpt=excerpt, fidelity_flags=flags,
    )
    result = await agent.run_phase(
        provider=judge_provider, model=judge_model, phase_prompt=judge_prompt,
        phase_name="__judge__", schema=Verdict,
        lesson_context=None,          # source is now embedded in judge_prompt (bounded); no dup block
        prior_outputs=prior_outputs, difficulty=None,
        operation=f"judge:{phase_name}", homework_job_id=homework_job_id,
        phase_output_id=phase_output_id, transport=transport,
    )
```

(For this task land `_fidelity_flags` as a stub `def _fidelity_flags(output_md, lesson_context): return []` so A1 is self-contained; A2 replaces the body.)

- [ ] **Step 5: Run → PASS** (`uv run python -m pytest tests/services/test_phase_judge.py -v`). Fix the other existing prompt-shape tests if they assert old structure.

- [ ] **Step 6: Commit** — `git add app/services/phase_judge.py tests/services/test_phase_judge.py && git commit -m "c3: judge prompt verifies source-fidelity (bounded excerpt + ground-truth framing)"`

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

### Task B1: Migration 0028 — `phase_outputs.judge_status`

**Files:** Create `alembic/versions/0028_phase_output_judge_status.py`

- [ ] **Step 1:** confirm head — `uv run alembic heads` → expect `0027_per_role_provider_model`.
- [ ] **Step 2:** write the migration (nullable text; no backfill — historical rows stay NULL = "pre-feature"):

```python
"""add phase_outputs.judge_status

Revision ID: 0028_phase_output_judge_status
Revises: 0027_per_role_provider_model
"""
from alembic import op
import sqlalchemy as sa

revision = "0028_phase_output_judge_status"
down_revision = "0027_per_role_provider_model"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.add_column("phase_outputs", sa.Column("judge_status", sa.String(length=24), nullable=True))

def downgrade() -> None:
    op.drop_column("phase_outputs", "judge_status")
```

- [ ] **Step 3:** `uv run alembic upgrade head` against the dev DB → succeeds; `uv run alembic downgrade -1` then `upgrade head` round-trips clean.
- [ ] **Step 4: Commit** — `git add alembic/versions/0028_phase_output_judge_status.py && git commit -m "c3: migration 0028 — phase_outputs.judge_status column"`

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

**Files:** Modify `app/services/pipeline.py` (the judge block ~:846-934); Test `tests/services/test_pipeline_judge_status.py`

> Keep edits surgical — cluster 1 (judge-timeout) and clusters 2/4 also touch this file. Do NOT add a timeout here.

- [ ] **Step 1: Write failing tests** (stub `phase_judge.judge` + `_run_with_failover`; assert the recorded `judge_status` per path). Cover: (i) clean pass → `ok`; (ii) MAJOR → regen → clean → `ok`; (iii) MAJOR → regen → still MAJOR → `major_shipped` (NOT silently accepted); (iv) judge `unavailable` (transient, cli) retried once then still unavailable → `unavailable`; (v) regen disabled (`max_judge_regens=0`) → MAJOR recorded as `major_shipped` with no regen call.

- [ ] **Step 2: Implement.** In the judge block:
  - After the initial `judge()`: if `not outcome.available` and the error was transient (cli path; the api-auth case already re-raises in `phase_judge`), retry the judge ONE more time before accepting `unavailable`.
  - Replace the single `if outcome.available and outcome.has_major:` regen with a loop bounded by `settings.max_judge_regens` (default 1 → identical to today). After each regen's `judge()`, if still `has_major` and budget remains, loop; else stop.
  - Compute `judge_status`:
    ```python
    if not outcome.available:        judge_status = "unavailable"
    elif outcome.passed:             judge_status = "ok"
    elif outcome.has_major:          judge_status = "major_shipped"      # regen budget spent, still MAJOR
    else:                            judge_status = "ok"                 # minor-only warnings
    ```
    On the regen-failed `except` branch (pipeline.py:917) set `judge_status = "major_regen_failed"`.
  - Pass `judge_status=judge_status` into the final `phase_repo.set_status(...)` (pipeline.py:927).

- [ ] **Step 3: Run → PASS** (all five paths).
- [ ] **Step 4: Commit** — `git add app/services/pipeline.py tests/services/test_pipeline_judge_status.py && git commit -m "c3: judge retry-once + capped regen loop + post-regen re-check + judge_status"`

---

## Workstream C — judge-claimgate-1 (per-job judge/extract honored at the claim gate)

### Task C1: Evaluate worker capability against the JOB's resolved judge/extract provider

**Files:** Modify `app/services/worker.py` (`_compute_capabilities` ~:56, `CAPABILITIES` ~:83, the claim call), `app/repositories/jobs.py` (`claim_next_job` ~:268-301); Tests `tests/services/test_auth_env.py`, `tests/integration/test_claim_contention.py`

- [ ] **Step 1: Write the failing test** in `tests/services/test_auth_env.py` (these tests build the SQL/caps without a live DB where possible; add a claim test in `test_claim_contention.py` if a DB is required):

```python
def test_claim_gate_uses_job_judge_provider_not_settings(monkeypatch):
    # Worker: Vertex-only (gemini api yes, claude no). settings.judge_provider = claude.
    # A job with judge_provider='gemini', judge_transport='api' MUST be claimable.
    caps = {"can_claude_api": False, "can_gemini_api": True,
            "judge_pair": ("claude", "claude-opus-4-7"),
            "settings_judge_provider": "claude", "settings_extract_provider": "gemini"}
    # build the claim WHERE and assert the gemini-judge api job is NOT excluded.
    # (mirror the existing not-pair-branch test's construction.)
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

### Task C2: Record the settings-drift audit

- [ ] In the worklog (finish step), record the audit result: only `worker._compute_capabilities`→claim gate drifted; `model_tiers.resolve_judge` (pipeline.py:858) and `pipeline._resolve_extract` (pipeline.py:49-50) already prefer the per-job override; `worker.py:197` is advisory-startup-only (acceptable); `toc_extractor` is job-less (settings correct). No code change beyond C1.

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
- [ ] **Backlog closes WITH the mechanism correction:** in `WISHLIST.md`/`ROADMAP.md`, close `judge-fidelity-1`, `judge-softfail-1`, `judge-claimgate-1`; **fix the wrong text** in `judge-fidelity-1` and `R20` ("judge never sees the source"/"not fed into the prompt" → the source DOES reach the judge; the gap was instructions + no deterministic check). Leave R20 OPEN (follow-on) with corrected framing.
- [ ] **Plan `git mv`** this file → `docs/superpowers/plans/shipped/`.
- [ ] **De-stale reference docs:** `docs/HOW_IT_WORKS.md` (judge now checks source-fidelity + `judge_status`), `docs/CODE_MAP.md` (phase_judge fidelity signal; claim-gate per-job providers), `docs/DATABASE.md` (`phase_outputs.judge_status`).
- [ ] **PR:** `[cluster-3] judge quality — source-fidelity, judge_status, per-job claim gate`. Do NOT self-merge.

---

## Self-review

- **Spec coverage:** judge-fidelity-1 → A1+A2+D; judge-softfail-1 → B1-B4; judge-claimgate-1 → C1+C2; R20 deliberately excluded (follow-on, recorded in E). ✓
- **Type consistency:** `_build_judge_prompt(*, contract, output_md, source_excerpt=None, fidelity_flags=None)` used consistently in A1/A2; `judge_status` string set in B4 and persisted in B2; `judge_status` values (`ok`/`major_shipped`/`major_regen_failed`/`unavailable`) are the same set in B4's compute + tests. ✓
- **Guardrail encoded:** A2's math test is the gate; the deterministic signal is warning-only (never sets `has_major`); D is two-sided. ✓
- **No placeholders:** every code step has real code; every test step has real asserts; commands are exact. ✓
- **Lane discipline:** only this cluster's files staged per task; rebase-conflict expectation called out in E. ✓
