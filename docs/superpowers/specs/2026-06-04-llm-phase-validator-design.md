# LLM Phase Validator — Prompt-Derived, Self-Verifying — Design Spec

**Date:** 2026-06-04
**Branch:** Nggaev-v2
**Status:** Draft for user review

## Goal

Give every generated phase a real quality gate. After a phase produces its markdown, an
**LLM judge** verifies the output actually satisfies the phase's own prompt contract, and
on a substantiated failure the phase **regenerates once** with the cited problems fed back.
The judge **self-verifies its findings in the same call** so a hallucinated "failure" can
never trigger a needless regeneration or a false warning.

This replaces today's `phase_validator.py` (3 universal markdown rules, warn-only) with a
single semantic validator. There is **no deterministic rule layer** — the prompts are the
rubric, and the judge reads them.

## Why

- The current `phase_validator` checks only non-empty / has-heading / image-targets. It
  cannot catch the contract violations that actually matter: a CBP with 4 checkpoints, a
  boss-arena question missing its *What*, an error-detection task with two broken blocks, a
  reflection that drops a section.
- The prompts already **assume** a validator. `_general/practice-error-detection.md` literally
  says *"Exactly one error. Any other count is rejected by the validator."* We are building the
  thing the rewritten prompts were authored against.
- A hand-authored rule table (`RULES = {}`, left empty in Effort A) is a second source of truth
  that drifts from the prompts. Proven concretely during design: the dead `prompts/biology/`
  files encode a **4-section, score-branched** reflection, while the live `_general/reflection.md`
  is **5-section, unconditional** — hand-authoring rules from the wrong file would have shipped a
  wrong contract. The only safe rubric is the prompt the generator actually received.

## Grounding facts (verified against the runtime, not assumed)

- **Served prompts live in `prompts/_general/`, not the subject dirs.** `prompts.py` sets
  `USE_SUBJECT_PROMPTS = False`, so `_resolve_dir` always returns `_general`. The 7 subject
  directories are inert today.
- **The effective contract is composed, not a raw file.** `get_prompt(subject, phase_name)`
  loads `_general/<phase>.md` and substitutes `{{SUBJECT}}`, `{{LANGUAGE_RULES}}`, and
  `{{FAMILY_RULES}}` (family = sciences / math / languages / humanities / `_default`; only CBP
  and flashcards vary by family). The judge MUST read this resolved text — never a raw file.
- **All live phases are markdown-native.** Every `_general/*.md` ends with *"Respond in Markdown
  only (no JSON…)"*. There is no JSON-schema phase in the live flow.
- **Runtime flow (9 phases/job):** `extract` → `case-based-preview` → `flashcards` →
  `memory-check` → `practice-rlc` → `practice-error-detection` → **one** subject game →
  `boss-arena` → `reflection`. The game is chosen per subject in `flows.SUBJECT_GAME`
  (memory-match | tictactoe | jigsaw | sentence), so the four game prompts are mutually
  exclusive within a job.
- **Several contracts are cross-phase.** memory-check requires the flashcards output and each
  item must name a studied card; every game + boss-arena must align terminology with the
  flashcards; reflection is tied to the CBP + boss-arena outputs. Faithful judging therefore
  needs the generator's prior-phase inputs, not just the prompt + output.

## Scope

**IN:**
1. A single LLM judge per content phase, reading the resolved prompt contract.
2. Self-verifying (single-call, same-session refutation) to suppress hallucinated failures.
3. Retry-then-warn enforcement (one regeneration with cited failures fed back; non-blocking).
4. Tier-up judge selection (the judge model is a tier above the generator).
5. Retiring the deterministic `phase_validator` rule engine; reusing its `validation_warnings`
   storage + console surface for the verdict.

**OUT:**
- Any hand-authored per-phase rule code / `## Validation` blocks in prompts (explicitly rejected
  — the prompt *is* the rubric).
- A separate deterministic structural layer.
- Operator review gate / `needs-review` job state.
- Coupling to the job-resilience effort (failover/resume). The judge retry is its own
  phase-scoped loop and does not touch the failover driver or job-level `attempts`.
- `extract` validation (pinned builtin summary, no prompt contract — exempt, as today).

## Design

### 1. Two new units + one retirement

- **`app/services/model_tiers.py` (new, pure)** — `judge_model_for(gen_provider, gen_model)
  -> (judge_provider, judge_model)`. A capability ranking + per-tier designate. No I/O,
  fully unit-testable.
- **`app/services/phase_judge.py` (new)** — `async judge(*, subject, phase_name, output_md,
  lesson_context, prior_outputs, gen_provider, gen_model) -> Verdict`. Resolves the judge
  model, builds the meta-prompt, calls the CLI through the existing `agent` plumbing, parses a
  structured `Verdict` (`model_validate_json` + one reparse retry — `run_phase` already
  implements exactly this schema-mode reparse at `agent.py:742-779`, so the judge can reuse
  `run_phase(schema=Verdict)` rather than re-implement it; see §3 for the prompt-shape caveats
  that come with that reuse).
- **`app/services/phase_validator.py` (retire the rule engine)** — delete `_non_empty` /
  `_has_top_heading` / `_visuals_resolve` / `RULES` / `validate`, and replace the
  `phase_validator.validate(...)` call at `pipeline.py:570`. Keep nothing but what the judge
  path needs; the `phase_outputs.validation_warnings` column and the `preview.tsx` console
  strip stay as the verdict's storage + display. **Retire the orphaned rule tests**
  `tests/services/test_phase_validator.py` (they assert on `pv.validate(...)`); the
  column/plumbing test `tests/repositories/test_phase_validation_warnings.py` stays — the
  column is unchanged.

### 2. Judge selection — tier-up map (coarse tiers, jump one)

A single cross-provider capability ranking of `MODEL_MANIFEST`, grouped into coarse tiers
(1 = strongest). **Proposed (config-tunable, not load-bearing on exact placement):**

| Tier | Models |
|---|---|
| 1 Frontier | `claude-opus-4-7` · `gpt-5.5` · `gemini-3.1-pro-preview` |
| 2 Strong | `claude-sonnet-4-6` · `gpt-5.2` · `gpt-5` · `gemini-3-flash-preview` · `gemini-2.5-pro` |
| 3 Mid | `claude-haiku-4-5-20251001` · `gpt-5-mini` · `gemini-3.1-flash-lite-preview` · `gemini-2.5-flash` · `kimi-code/kimi-for-coding` |
| 4 Light | `gpt-5-nano` · `gemini-2.5-flash-lite` · `opencode/*` |

**Rule:** judge = the **designated model of the tier above** the generator's tier. The
designates are **non-claude**, so validation never draws down the scarce claude Max pool
(consistent with the resilience spec's provider isolation):

| Generator tier | Judge designate (default) | Alternate |
|---|---|---|
| 4 Light | `gemini-2.5-flash` (T3) | `gpt-5-mini` |
| 3 Mid | `gemini-2.5-pro` (T2) | `gpt-5` |
| 2 Strong | `gemini-3.1-pro-preview` (T1) | `gpt-5.5` |
| 1 Frontier | `gemini-3.1-pro-preview` (T1 peer) | `gpt-5.5` |

**Clamp + no-self rule:** a Tier-1 generator is judged by a Tier-1 **peer** (nothing is
higher). If the computed judge equals the generating model (e.g. generator is the T1 designate
`gemini-3.1-pro-preview`), fall back to the tier's **alternate** so judge ≠ generator. A claude
generator (any tier) is thus judged by a non-claude model a tier up (or a frontier peer),
and claude is never itself a judge.

All of this is config: `JUDGE_TIERS` (model → tier), `JUDGE_DESIGNATES` (tier → primary/alt),
and a `judge_enabled` master switch.

### 3. The judge call — single pass, self-verifying

**Inputs mirror the generator's exact inputs**, so cross-phase checks are verifiable by
citation rather than guessed:
- the **resolved contract** = `get_prompt(subject, phase_name)` (family/language/subject
  substituted — what the generator was given),
- `lesson_context` (the extract — for "traces to this lesson" grounding),
- the **declared** prior outputs = `filter_prior_outputs(phase_name, prior_outputs)` (e.g. the
  flashcards markdown for memory-check / games / boss-arena), keeping tokens bounded to deps,
- the phase `output_md` under review.

**Prompt-shape caveat (if reusing `run_phase`).** `run_phase` builds its prompt via
`_build_master_prompt`, which appends phase-shaped extras keyed off `phase_name`: the
universal `_SVG_RULES` when `phase_name in _SVG_PHASES` (`agent.py:525`), the provider
visual-policy suffix, and the `--- LESSON CONTEXT ---` framing. Those are noise for a judge.
The judge must therefore either (a) pass a **neutral `phase_name`** (not in `_SVG_PHASES`) and
suppress the provider suffix, assembling the contract/context itself, or (b) use a **dedicated
judge spawn** (`_spawn` + `parse_envelope`) that builds only the meta-prompt. The plan picks
one; the requirement is that the judge sees the contract + inputs and nothing phase-decorative.

**Meta-prompt instructs a single call to do both passes:**
1. List every requirement stated in the contract that the output **violates** — each candidate
   failure MUST cite the **exact offending text** from the output (or the exact missing element,
   quoted from where the contract demands it).
2. Then **challenge your own list**: for each candidate, confirm it is genuinely violated by the
   quoted evidence; **drop any item you cannot substantiate with a direct citation** from the
   output or the provided prior outputs. Treat anything you cannot quote as your own
   hallucination and discard it.
3. Emit only the survivors.

**Visual-placeholder rule (do NOT over-flag).** The contract instructs the generator to emit
`![placeholder: <desc> — image gen required](placeholder)` for any raster/photo instead of
fabricating one. A correctly-emitted placeholder is **compliant** — the prescribed terminal
state, not a defect — so the judge must never raise a "missing image / incomplete visual"
failure over it (a regen cannot produce the raster anyway; image-gen happens downstream).
Conversely, a **fabricated image or an invented `http(s)` image URL IS a violation** the judge
should catch — the contract explicitly forbids it. The meta-prompt states both halves so the
judge neither loops on a legitimate placeholder nor lets an invented URL pass.

**Structured `Verdict`** (pydantic, parsed via `model_validate_json` + one reparse retry):
```
Verdict:
  passed: bool
  failures: list[Failure]   # only citation-backed survivors
Failure:
  requirement: str          # the contract rule violated
  evidence: str             # the exact quote (or quoted absence) proving it
```

### 4. Enforcement — retry-then-warn (inside `_execute_phase`, before the `done` write)

```
generate output_md  (existing agent.run_phase_prompt path)
  → judge() → verdict
  → verdict.passed            → set_status done
  → verdict.failures present  → regenerate ONCE:
        phase_prompt augmented with a "## Fix these (prior attempt failed validation)"
        block listing each failure.requirement + failure.evidence
     → judge() again
     → still failing → set_status done, failures recorded in validation_warnings (ACCEPT)
```

- **Non-blocking.** Worst case the phase ships with its surviving failures recorded as
  warnings (richer than today). Validation never *fails* a job.
- **Bounded cost.** Success path: +1 judge call per phase. Failure path: +1 regen +1 re-judge,
  exactly once (`judge_max_retries = 1`).
- **Storage shape — `list[str]`, not dicts.** The surviving `failures` are flattened to
  strings (`f"{requirement} — {evidence}"`) before being written to `validation_warnings`. The
  column feeds the frontend, which types it `validation_warnings: string[] | null`
  (`web/src/lib/types.ts:62`) and renders each entry directly (`preview.tsx:108` `.map`);
  writing `Failure` dicts would break that strip ("Objects are not valid as a React child").
  The structured `Verdict` lives only in-memory for the retry decision.
- **`extract` exempt** (same carve-out as today's validator: `phase_name != "extract"`).
- **Graceful degradation.** If the judge CLI call itself errors (provider down / quota /
  unparseable after reparse), keep the generated output, mark the phase `done`, and record a
  single `"judge-unavailable: <reason>"` warning. The validator must never make generation less
  reliable than it is today.
- **Self-contained.** The judge retry is scoped to one `_execute_phase` call; it does not
  consume job-level `attempts` and does not interact with the resilience failover/resume work.

### 5. Attribution & usage

- Each judge call writes one `agent_usages` row labelled `operation="judge:<phase_name>"`,
  with the judge's own provider/model — so the dashboards stay honest and the cost of
  validation is visible. **This is NOT free as written:** `run_phase` hardcodes
  `operation="phase.run"` at all six `_record_usage` call sites (`agent.py:642/659/683/714/748/783`)
  and has no `operation` parameter. The plan must add an `operation` arg to `run_phase` and
  thread it to those sites — small, because `_record_usage` already accepts `operation=`
  (`agent.py:421`). (A dedicated judge spawn would set the label directly and avoid touching
  `run_phase`; either path is acceptable, but one of them is required.)
- **Token accounting.** Judge tokens are recorded only on their own `agent_usages` row — never
  folded into the phase's returned `tin/tout`. On a regeneration, the phase's returned counts
  reflect the **regenerated** output (the last generation that was stored).
- The producing phase row keeps recording its **generator** model/provider. The judge model is
  not the phase's provider.

### 6. Integration with the job-resilience effort (the seam that must be explicit)

This effort and the **active** job-resilience effort both edit the **same** non-extract branch
of `_execute_phase` (`pipeline.py:542-558`). Resilience Task 6 wraps the generation call in
`_run_with_failover(...)` and returns a `produced_by` (the provider that actually produced the
phase after any failover); this effort inserts judge → regen → re-judge around that same call,
and the regen re-invokes generation. "No coupling" in Scope-OUT is true of the failover
*driver* and job `attempts`, but the two are operationally adjacent and must compose.

**Decisions (proposed — to ratify with the resilience effort):**
- **Key the judge off the *actual producer*, not the requested provider.** `gen_provider /
  gen_model` passed to `phase_judge.judge` and `model_tiers.judge_model_for` = `produced_by`
  (the model that generated the output under review) when a failover occurred, else the
  job's requested provider/model. Rationale: "judge ≥ generator" only means something relative
  to the model that *actually* wrote the output.
- **Regen on the producer, with failover still available.** The validator's single regen runs
  through the same generation path as the original — i.e. if resilience is present, the regen
  calls `_run_with_failover` again (it may itself fail over), and its `produced_by` becomes the
  new attribution. Re-pinning the regen to the *requested* provider risks re-hitting the
  provider that already failed.
- **Order of landing.** Whichever effort lands second integrates at this branch. If resilience
  lands first: generation already returns `(output_md, tin, tout, produced_by)`; the validator
  consumes `produced_by`. If the validator lands first: generation is a plain
  `run_phase_prompt` on the job provider/model and `produced_by == requested`; resilience later
  swaps the inner call for `_run_with_failover` and feeds its `produced_by` into the judge/regen.
- **Attempt budgets stay separate.** The judge's one regen does not consume job-level `attempts`
  (whole-job retry guard) and the failover same-provider retries do not consume the judge's
  regen budget. They are independent loops at different scopes.

## Data flow

1. `_execute_phase` generates `output_md` on the job's provider/model (unchanged; under
   resilience this is `_run_with_failover`, yielding `produced_by`).
2. `phase_judge.judge(...)` resolves the judge model via `model_tiers` (keyed off the
   **actual producer** — `produced_by` under resilience, else the requested provider/model),
   assembles `get_prompt + lesson_context + filtered prior_outputs + output_md`, runs the
   single-call self-verifying judge, returns a `Verdict`.
3. `passed` → write `done`. Failures → augment the prompt with the cited failures, regenerate
   once, re-judge.
4. Surviving failures (if any) are stored in `validation_warnings`; the phase is marked `done`
   regardless.

## Testing strategy

- **DB-free pure unit tests:**
  - `model_tiers.judge_model_for` — every manifest generator maps to the correct tier-up
    designate; claude is never a judge; a top-tier generator gets a peer; judge ≠ generator
    (alternate kicks in when they'd collide).
  - meta-prompt builder — includes the resolved contract, lesson_context, and the declared
    prior outputs; instructs the cite-then-refute protocol; and is free of phase-decorative
    noise (no `_SVG_RULES`, no provider visual-policy suffix).
  - `Verdict` parsing — valid JSON parses; malformed triggers exactly one reparse; still-bad
    degrades to "judge-unavailable".
  - failures serialize to `list[str]` (`"requirement — evidence"`) before storage.
  - degradation branch — a raising judge yields a `done` phase + one warning, never raises.
- **Retire** the orphaned rule tests in `tests/services/test_phase_validator.py` (they assert
  `pv.validate(...)`, which no longer exists). The repository column test
  `tests/repositories/test_phase_validation_warnings.py` is unaffected and stays.
- **Real CLI smoke (acceptance gate, per CLAUDE.md — generation-affecting behaviour proven by a
  real run):**
  - (a) a deliberately broken output (e.g. a CBP with 4 checkpoints) → judge returns a
    citation-backed failure and the regen fixes it;
  - (b) a correct output → `passed`, no regen;
  - (c) a plausible-but-fine output → judge does **not** invent a failure (the refutation pass
    suppresses it). This is the anti-hallucination proof and is mandatory.

## Risks / open items

- **Cost.** +1 judge call per content phase (8/job), tier-up routes judging to frontier
  gemini/codex (never claude). `judge_enabled` is the off switch; `judge_max_retries` bounds the
  failure path. Acceptable given the explicit "judge must be ≥ generator" requirement.
- **Tier ranking is a judgment call** over partly-future models — kept entirely in config so it
  re-tunes without code.
- **Cross-phase token cost** — bounded by only injecting the phase's *declared* deps
  (`filter_prior_outputs`), with SVGs already stripped by that helper.
- **`USE_SUBJECT_PROMPTS=False` today** — the judge reads `get_prompt`, so it automatically
  tracks `_general` now and any subject-specific prompt later if the flag flips. No extra
  coupling.
- **Judge provider availability** — if a designated judge provider's CLI is absent on the host,
  the call errors and degrades to "judge-unavailable" (never blocks). A future refinement could
  fall back to the alternate designate before degrading.
