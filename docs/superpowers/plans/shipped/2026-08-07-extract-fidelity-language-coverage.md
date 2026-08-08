# Extract-Fidelity Guard: Language/Humanities Coverage — Measure, Then Fix the Noise

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish, by measurement, what extract-fidelity drift actually occurs in language/humanities lessons — where the deterministic CQ-D guard is blind — and separately fix a *measured* false-positive defect in that guard's pre-filter. **Do not build a language-side fidelity checker in this lane.**

---

## Approach & key decisions

- **Chosen approach: measure first, fix only what is already proven.** The lane delivers (a) a reusable extract-fidelity audit instrument + one real run over ~40 non-math lessons, and (b) a narrow precision fix to the existing pre-filter. It deliberately stops before designing a language-side guard, because the base rate of language drift is unknown and a guard built blind would re-introduce the R14 regen tax.
- **The premise was verified against code AND partially corrected by free measurement.** `agent.extract_math_expressions` (`agent.py`, `_FIDELITY_EXPR_RE`) requires `/` or `=` AND (a digit OR a paren); `extract_fidelity_candidates` draws only from it; `pipeline._verify_and_maybe_regen_extract` early-returns `out, 0, 0` on an empty candidate list (**locate by symbol — the writeup's `pipeline.py:1207` was stale; today it is ~`1316-1317`**). The only other deterministic signal, `phase_judge._fidelity_flags`, is years-only and advisory (never gates a regen).
- **Load-bearing measured fact — measured with PRODUCTION semantics (3,427 real done extracts in `edu_copy`, $0, no model calls).** "Production semantics" means `agent.extract_fidelity_candidates(extract_md, agent.read_whole_book_text(pdf))` — the grounded candidate list that actually feeds the paid verify call — **not** raw `extract_math_expressions` yield. The distinction matters: raw yield is only an upper bound and overstates humanities guard activity by 2–5×. An earlier draft of this plan quoted raw yield while labelling it production behavior; those numbers are corrected below.

  | subject | family | lessons | **candidates today** | after fix | expressions |
  |---|---|---|---|---|---|
  | `english` | languages | 22 | **8 (36.4%)** | **0** | 26 → 0 |
  | `adabiyot` | languages | 2 | 0 | 0 | 0 → 0 |
  | `tarbiya` | humanities | 18 | 0 | 0 | 0 → 0 |
  | `history` | humanities | 811 | 19 (2.3%) | 6 | 34 → 7 |
  | `geografiya` | humanities | 306 | 9 (2.9%) | 2 | 10 → 2 |
  | `biology` | sciences | 683 | 18 | **18** | 23 → 23 |
  | `physics` | sciences | 224 | 66 | **66** | 127 → 127 |
  | `math-algebra` | math | 444 | 210 | **210** | 715 → 715 |

  So: the guard is inert on `tarbiya`/`adabiyot`, **more** inert on `history`/`geografiya` than first reported (2.3% / 2.9%), and **actively mis-fires on `english` — 8/22 lessons produce 26 candidates of which ZERO are real**, all prose glosses and grammar alternations (`(likes/dislikes)`, `(*was/were*)`, `(my/mine`). The paren arm, added for digitless algebra like `(a−b)/(a+b)`, collides with how language extracts write gloss pairs. English is the one methodology-invariant column — invented glosses are absent from the source either way — which is why it alone reproduces identically under both measurements.
- **That noise has never billed.** `agent_usages` has **zero** `lesson.extract.verify` rows for `english` — those 22 jobs ran 2026-06-24 (`created_at::date` is uniformly 2026-06-24; an earlier draft said "06-23/24"), before the CQ-D guard shipped 2026-07-02 (#77 / worklog 0111). The 36.4% is a **prediction for the next English launch**, not past spend. Total historical verify calls: **297** (296 join to a book; one row has no joinable job/book), of which **266/296 = 89.9%** are math/science subjects. That concentration is why the fix must leave the math/science path provably untouched.
- **Rejected — subject-agnostic regex tightening.** Two variants were tested against the real corpus: (i) max-alpha-run ≤ 2 unless a digit is present, (ii) same plus "contains a math operator". Both killed real physics/algebra catches (`(mrt)/(mp)`, `j/(mol*k)`, `y=arcsin`, `y=sqrt(x`). Neither is acceptable given the guard's proven value is math/science.
- **Chosen fix — subject-FAMILY gate.** For `subjects.REGISTRY[code].family in {"languages", "humanities"}`, keep only expressions containing a digit or an `=`. Measured effect is the table above. **Math and sciences are unchanged BY CONSTRUCTION** — the strict post-filter is never applied to them, so no measurement is needed to establish it; the empirical confirmation (biology 18/18, physics 66/66, math-algebra 210/210) is a check on the *implementation*, not on the *claim*. Every real fraction and unit that matters survives in history/geografiya (`1/3`, `(2/3`, `(2h/g)`, `750/760-taxminan`, `kishi/km²)`). **Not quite "only glosses are dropped"** — two corpus-verified exceptions: geografiya drops `kishi/yil)` (a real digitless unit) and history drops `h/g)` (a formula fragment whose sibling `(2h/g)` survives, so that lesson keeps a drift signal). Impact is nil, but record them in the report rather than claiming purity.
- **Transferability caveat.** These extracts were produced by the *then-current* extract prompt and model config — the english set is all 2026-06-23/24, predating the v3 coverage contract and the 3.x-flash migration (worklog 0161). Both the 36.4% "next English launch" prediction and the Task 4 base rate assume extract style is stable across those changes. It probably is (the pre-filter is style-agnostic), but it is an assumption, not a measurement, and the report must say so.
- **`kishi/km²)` survives only because `'²'.isdigit()` is `True`.** This is real corpus data, not a hypothetical — an implementer reaching for `isdecimal()` (`False` for `'²'`) would silently drop real geografiya units. Pinned by test in Task 5.
- **`subject` needs no new plumbing.** `_verify_and_maybe_regen_extract` is called from the `_extract_run` closure nested inside `_execute_phase`, which already takes `subject` as a parameter — it is in lexical scope at the call site.
- **The instrument must be calibrated or its number is worthless.** The audit ships with mutation injection (plant a known date/name/definition drift, confirm detection) — same discipline as `teaching_audit.py --sensitivity`. If planted drift is not detected, the report states the base rate is uninterpretable rather than printing a number.
- **Money rule.** Two bounded, cost-reported spends, **~$0.87 total**: the Task 4 audit (48 gemini-3.5-flash calls ≈ **$0.85**, hard-capped by `--limit`) and the Task 5 acceptance smoke (**~$0.02**, one billed verify call to prove the pre-fix noise was reachable). No content is generated — the audit reads *already-done* extracts. Task 6 is `$0`. `gemini-3.5-flash-lite` is 5× cheaper but was measured at 33% structured-output flake in the TOC-validator work (worklog 0161) — not used here.
- **Review history.** Reviewed adversarially 2026-08-07 by a fresh agent that independently re-derived the corpus measurements. It found that the first draft quoted raw `extract_math_expressions` yield while labelling it production candidate behavior — corrected throughout, and the corpus harness is now a committed deliverable (Task 6) rather than an uncommitted scratch script. Calibration was also strengthened from sensitivity-only to paired sensitivity + specificity.

---

## Global Constraints

- **Branch:** `feat/extract-fidelity-language`, cut from `origin/Nggaev-v2` at `2ebab53`, in worktree `/Users/macmini5/Documents/HCGA-extract-fidelity-lang`.
- **Collision gate (recorded, per global CLAUDE.md):** `git fetch --all --prune` run 2026-08-07. Open PRs #108 (`fix/dashboard-mobile-wrap`), #117 (DRAFT review, do-not-merge), #118 (`fix/content-json-gate-corrections`). Branches touching `app/services/pipeline.py`: `feat/fenced-job-leases`, `fix/content-json-gate-corrections`, `feat/structured-output-gate`, `review/content-json-retro`, `feat/content-json-completion`. For each, `git diff origin/Nggaev-v2...<branch> -- app/services/pipeline.py | grep -E 'fidelity|candidates|extract_math'` returns **empty** — no branch touches `_verify_and_maybe_regen_extract`, `extract_math_expressions`, `extract_fidelity_candidates`, or `_fidelity_flags`. `app/services/agent.py` is untouched by every open branch. **Conclusion: no overlap; no serialization needed.** #118 will conflict textually in `pipeline.py` only if it rewrites the extract block — re-check at finish time.
- **PR ownership gate:** no PR authored by `s1gmamale1` is touched by this lane.
- **No new migration.** No schema change in this lane.
- **Transport:** `transport=api`, provider `gemini` (cli is retired operationally). The audit script defaults to `api` and must not offer a cli default.
- **Read-only against production data.** The audit reads `edu_copy` and `var/books/<id>/source.pdf`. It **must never write** to `phase_outputs`, `homework_jobs`, or `agent_usages` beyond the usage rows `agent.run_phase` records for its own calls. No regeneration of any packet.
- **Staging discipline:** stage only the files each task lists — never `git add -A`. The untracked root `Wishlist.md` and `scripts/export_homeworks.py` belong to another session and are never staged.
- **Locate by symbol, not line number.** Line numbers in this plan drift; every anchor is given as `file:function`.
- **Suite bar:** `uv run python -m pytest tests/ -q` green (without `RUN_DB_INTEGRATION`) at every commit.

---

## File Structure

**New files**
- `app/services/extract_fidelity_audit.py` — loader, Pydantic result models, prompt builders, mutation injection, orchestrator.
- `scripts/extract_fidelity_audit.py` — CLI: sample selection, `--limit` cap, cost print, JSON report.
- `scripts/extract_candidate_corpus_diff.py` — Task 6's committed, reproducible corpus stop-gate harness ($0, no model calls).
- `tests/services/test_extract_fidelity_audit.py` — pure-unit tests for models/prompts/mutation/aggregation (no model calls).
- `tests/services/test_extract_fidelity_family_gate.py` — Task 5's pipeline-level family-gate tests.
- `docs/memory/reports/2026-08-07-extract-fidelity-language-baseline.md` — the measured base rate + the decision it supports.

**Modified files**
- `app/services/agent.py` — `extract_fidelity_candidates` gains `strict: bool = False`.
- `app/services/pipeline.py` — `_verify_and_maybe_regen_extract` gains `subject`, derives `strict` from `subjects.REGISTRY`; `_extract_run` passes `subject=subject`.
- `tests/services/test_extract_fidelity.py` — new cases for the family gate.
- `docs/HOW_IT_WORKS.md`, `docs/CODE_MAP.md` — de-stale the fidelity-guard description.
- `docs/memory/MASTER_MEMORY.md`, `docs/memory/INDEX.md` (worklog **0164**), `docs/memory/ROADMAP.md`.

---

## Task 1 — Audit inputs + result models (pure, no model calls)

**Scene:** We need to load one already-done extract and the textbook pages it was written from, exactly as the live guard's verify call sees them, and define the structured verdict shape. Mirror `app/services/teaching_audit.py`'s `AuditInputs` / `load_audit_inputs` pattern — including its **fail-loud** posture (missing job/book/TOC row, NULL page range, missing PDF, empty page text all raise, never silently degrade).

- [ ] **RED:** Write `tests/services/test_extract_fidelity_audit.py` with tests for: `ClaimVerdict` accepts only `contradicts` / `unsupported` / `ok` for `status` and only `name` / `date` / `number` / `definition` / `quote` / `term` / `other` for `claim_type`; `ExtractFidelityReport` aggregates a claim list into per-status counts; an empty claim list aggregates to zero drift, not an error. Run: `uv run python -m pytest tests/services/test_extract_fidelity_audit.py -q` — MUST fail on import.
- [ ] **GREEN:** Create `app/services/extract_fidelity_audit.py` with `ExtractFidelityAuditError(RuntimeError)`, Pydantic `ClaimVerdict` / `ExtractFidelityReport`, a frozen dataclass `ExtractAuditInputs` (`job_id`, `book_id`, `subject`, `family`, `grade`, `source_language`, `output_language`, `lesson_title`, `page_start`, `page_end`, `extract_md`, `source_text`, `whole_book_text`), and `async load_extract_audit_inputs(job_id)`.
  - **`ClaimVerdict` MUST carry a verbatim `claim_span: str`** — the exact substring of the extract the verdict is about — alongside `status` and `claim_type`. Two later steps depend on it and would otherwise each invent their own: Task 2's `reground_unsupported` substring match, and Task 3's paired planted-span detection. **Define the match semantics once, here, in a local `_normalize_span`:** casefold, collapse each whitespace run to a single space, strip — and nothing else. Exact normalized substring only, **no fuzzy matching** (a fuzzy match would silently downgrade real drift). **Do NOT reuse `agent._normalize_expr`**: it is math-specific — it removes *all* spaces and folds `·*×`→`*`, `−–—`→`-`, `÷`→`/`. Removing all spaces would make coincidental cross-word substring matches *easier*, compounding the short-span hazard that Task 2's length floor exists to prevent.
  - `source_text` = `agent.read_page_range_text(storage.book_pdf_path(book_id), page_start, page_end, margin=4)` — **margin=4, matching `teaching_audit._PAGE_WINDOW_MARGIN`**, whose comment records a *measured* `-3..+2` TOC page-offset spread. An earlier draft of this plan used `margin=1` to mirror `pipeline._verify_source_for_section`; that is the right window for measuring *guard blindness* but the WRONG one for measuring a *true drift base rate* — a window that misses the lesson body makes every claim read `unsupported`, including a planted mutation, so the calibration gate would pass on a broken instrument.
  - `whole_book_text` = `agent.read_whole_book_text(pdf)`, kept alongside. **The extract was generated from the whole book** (the 0035 local-text path), so a claim can be legitimately grounded outside any page window. This field backs the free re-grounding pass in Task 2 — it is NOT sent to the model.
  - `extract_md` comes from the `extract` phase row (`phase_name='extract'`, `status='done'`, non-NULL `output_md`); raise if absent.
  - `family` from `subjects.REGISTRY[subject].family`, defaulting to `"default"` for an unknown code.
- [ ] **VERIFY:** `uv run python -m pytest tests/services/test_extract_fidelity_audit.py -q` green; full suite green.
- [ ] **COMMIT:** stage exactly `app/services/extract_fidelity_audit.py tests/services/test_extract_fidelity_audit.py`.

## Task 2 — Adjudicator prompt + mutation injection (pure)

**Scene:** The adjudicator decides which claims in an extract are unfaithful to its source pages. Two hazards dominate. **(1) Translation.** These extracts routinely render an English or Russian source into Uzbek — a naive checker calls every translation a drift. **(2) Legitimate compression.** An extract is a summary; absence of detail is not drift. The prompt must separate `contradicts` (the extract asserts something the source denies) from `unsupported` (asserted but not locatable) from `ok`, and must be explicit that paraphrase, translation, transliteration, rounding, and omission are all `ok`.

- [ ] **RED:** Add tests: `build_adjudicator_prompt(inputs)` contains the extract, the source text, the lesson title, an explicit translation-tolerance clause, and an explicit "omission is not drift" clause; `inject_mutation(md, kind, seed)` for `kind in {"date","name","definition"}` returns text that **differs** from the input and returns a `Mutation` record naming the original span, the replacement span, and its character offset; injecting into text with no mutable target returns `None` (caller skips that lesson rather than fabricating). Run the file — MUST fail.
  - **The planted value MUST be absent from the source.** `inject_mutation` takes a required `forbidden_text: str` (the whole book text); a candidate replacement is usable only if its normalized form appears nowhere in it, and if every candidate collides the function returns `None`. Without this, a planted value that happens to occur elsewhere in the book gets downgraded by `reground_unsupported`, the adjudicator appears to have missed it, and **the ≥6/8 sensitivity gate fails on an instrument artifact — killing a real $0.85 run for no reason.** Tests: a colliding replacement is rejected; all-colliding returns `None`.
- [ ] **GREEN:** Implement `build_adjudicator_prompt` and `inject_mutation` in `app/services/extract_fidelity_audit.py`. `inject_mutation` is **deterministic given `seed`** (no `random` without a seeded `Random` instance) so a run is reproducible.
- [ ] **RED → GREEN:** Implement `reground_unsupported(claims, whole_book_text)` — a **free, deterministic** pass that re-checks every claim the model marked `unsupported` against the WHOLE book text (normalized substring match on `claim_span`) and downgrades a hit to `ok`. Rationale: the extract was generated from the whole book, so a claim grounded on a page outside the window is not drift. `contradicts` claims are never downgraded — only `unsupported` is a window artifact. Tests: a claim present in `whole_book_text` but absent from `source_text` downgrades; a `contradicts` claim never downgrades; an unmatched claim stays `unsupported`. **Zero added model calls, so no cost change.**
  - **A short span MUST NOT be able to trigger a downgrade.** A normalized substring match on a short span is not grounding: if the adjudicator emits `claim_span="1917"` or a bare surname, that token matches *somewhere* in a 200 KB textbook almost by chance, and the pass would downgrade a genuinely invented claim to `ok` — silently hiding real drift, the exact opposite of this tool's purpose. Require, in a named module constant with the reasoning in a comment: the normalized span must have **≥2 whitespace-separated tokens AND ≥12 characters** before a downgrade may fire. Below that, the claim keeps `unsupported` regardless of whether it matches. Tests: a short span that DOES appear in the book is NOT downgraded; a long multi-word span that appears IS downgraded; `contradicts` never downgrades.
  - **`reground_unsupported` returns the downgrade count** alongside the claims, and Task 4's report records it per lesson — otherwise the pass's aggressiveness is invisible and un-auditable.
  - **Known limitation — this pass is language-blind.** Where the extract's output language differs from the source language (a uz extract of an en/ru textbook — the English corpus is exactly this case), a normalized substring match will almost never hit, so `unsupported` inflates for precisely those lessons. That *particular* failure is conservative — it over-reports rather than under-reports — but note this is a statement about the cross-language case only, **not** a general "can never hide drift" guarantee: the short-span hazard above is precisely a hide-drift path, which is why the length floor is mandatory. `contradicts` is never downgraded either way. **Task 4's report MUST split `unsupported` by same-language vs cross-language**, or the base rate averages two different measurements.
- [ ] **VERIFY:** file tests green; full suite green.
- [ ] **COMMIT:** stage exactly `app/services/extract_fidelity_audit.py tests/services/test_extract_fidelity_audit.py`.

## Task 3 — Orchestrator + CLI

**Scene:** Wire the pieces into one auditable run. Mirror `teaching_audit._call` — fail loud, record `{step, provider, model, usage}` per call, and carry its documented caveat that a structured-output retry logs an extra `agent_usages` row the printed total does not include.

- [ ] **RED:** Add tests with `agent.run_phase` patched (`AsyncMock`): `audit_one(...)` returns an `ExtractFidelityReport` and appends exactly one entry to `calls`; a `run_phase` exception raises `ExtractFidelityAuditError` (never silently returns a clean report — a dead adjudicator must not read as "no drift"); `audit_with_control(...)` is **paired** — it reports `detected_planted` only when the mutated arm flags the planted span AND the **pristine arm of the same lesson does not flag that same span**. A run that flags the span in both arms counts as a FALSE POSITIVE, not a detection. Run — MUST fail.
- [ ] **GREEN:** Implement `audit_one`, `audit_with_control`, and `summarize_runs(reports)` (per-subject and overall counts of `contradicts` / `unsupported` per lesson, split by `claim_type`). Every `run_phase` call passes `operation=f"xfid:{step}"` and `homework_job_id=None` / `phase_output_id=None` — the audit's own usage rows must be **distinguishable from, and unattributed to, the production job being audited**.
- [ ] **GREEN:** Create `scripts/extract_fidelity_audit.py`:
  - `--subject` (repeatable), `--limit N` (**required, hard cap on billed calls**), `--sample-seed`, `--provider gemini`, `--model gemini-3.5-flash`, `--transport api`, `--mutations N`, `--out`, `--dry-run`.
  - `--dry-run` selects the sample and prints the plan + estimated $ **without a single model call** — run this first in Task 4.
  - Selection is deterministic given `--sample-seed`; stratify `history` across grade and `source_language`.
  - Prints per-lesson verdict counts, the instrument-calibration result, and total `$` via `pricing.cost_usd`, and writes JSON to `var/extract_fidelity_audit/<stamp>.json`.
- [ ] **VERIFY:** file tests green; full suite green; `uv run python scripts/extract_fidelity_audit.py --subject english --limit 2 --dry-run` prints a plan and makes **no** billed call (confirm: `SELECT count(*) FROM agent_usages WHERE operation LIKE 'xfid:%'` unchanged).
- [ ] **COMMIT:** stage exactly `app/services/extract_fidelity_audit.py scripts/extract_fidelity_audit.py tests/services/test_extract_fidelity_audit.py`.

## Task 4 — ACCEPTANCE GATE: run the audit for real (~$0.85, approved)

**Scene:** This is the lane's actual deliverable. Real `transport=api` gemini calls against already-generated extracts.

- [ ] Run `--dry-run` first and confirm the sample is: `english` 22 (all that exist), `history` 10 (**best-effort** stratification across grades 5–11 × uz/ru — that is 14 cells for 10 samples, so full coverage is impossible; spread across as many grade/language cells as possible and record which were sampled), `geografiya` 4, `tarbiya` 2, `adabiyot` 2 = **40 lessons**. `geografiya` is in-sample deliberately — it is the humanities subject with real formulas and units (`kishi/km²`, `(2h/g)`), and Task 4's cross-check names it. Confirm estimated cost ≤ $1.20 before proceeding.
- [ ] **`--limit` counts BILLED MODEL CALLS, not lessons.** The budget is 40 lesson audits + 8 mutated arms = **48**. The 8 pristine arms of the mutation pairs are *the same calls* as 8 of the 40 lesson audits and are reused, not re-run. Structured-output validation retries log extra `agent_usages` rows that the printed `$` does not include (the caveat carried from `teaching_audit._call`) — the cap counts *attempted logical calls*, so a retry does not silently consume budget, but real spend may exceed the printed figure slightly. Run for real with `--limit 48` and record actual `$`.
- [ ] **Calibration first, base rate second — sensitivity AND specificity.** Gate: (a) **sensitivity** — the planted span is flagged in the mutated arm and NOT in the pristine arm for ≥6 of 8 pairs; (b) **specificity** — across the 40 pristine audits, the `contradicts` rate is not so high that the instrument is flagging everything (if >50% of lessons show a `contradicts` claim, treat the instrument as un-calibrated, not the corpus as broken). Failing either gate, the report **states the base rate is uninterpretable** and the lane stops here for a user decision. Do not report a drift number from an uncalibrated instrument.
- [ ] **Record the known confound.** The adjudicator is a gemini model and most audited extracts were produced by a gemini model. Shared-family blind spots cannot be excluded by this design; the report must say so rather than imply an independent check.
- [ ] Write `docs/memory/reports/2026-08-07-extract-fidelity-language-baseline.md` (**the `docs/memory/reports/` directory does not exist yet — create it**): per-subject `contradicts` / `unsupported` rate per lesson, breakdown by `claim_type`, calibration result (both gates), actual $, and an explicit **cross-check**: *of the drift the audit found in `history`/`geografiya`, would any of it have been caught by the gloss tokens Task 5 drops?* (Expected: no — the dropped tokens are all glosses — but this must be checked, not assumed.)
- [ ] **The report MUST state its own precision limits, not just a point estimate:**
  - Per-family 95% confidence bounds. At n=40 with zero observed drift the upper bound is roughly 7–9% per lesson — enough to rule out rampant drift, **not** enough to rule out meaningful drift. Say that in those words.
  - **Scope the `languages` conclusion to English-G8 explicitly.** All 22 english extracts come from a single grade-8 book (verified: one `book_id`), and `ona-tili`, `russian`, `alifbe`, `oqish-savodxonligi` — all family `languages` — have **zero** extracts in the corpus. This measurement says nothing about them.
  - `tarbiya` (n=2 sampled of 18) and `adabiyot` (n=2, the entire corpus) are anecdote, not rate. Report counts, never percentages, for these.
- [ ] State plainly whether the measurement supports building a language-side guard, and file the follow-up in `docs/memory/ROADMAP.md` (worked-up) or `docs/memory/WISHLIST.md` (raw) accordingly. **Do not build it here.**
- [ ] **COMMIT:** stage exactly the report + the backlog file touched.

## Task 5 — Fix the measured false positive (family gate)

**Scene:** `english` extracts feed 26 pure-noise suspects across **8 separate paid verify calls** (one per affected lesson, each capped at `_FIDELITY_MAX_CANDIDATES = 12`), risking a spurious confirmed "mismatch" → a regen of a good extract. Fix it without touching math/science behavior.

- [ ] **RED:** Add to `tests/services/test_extract_fidelity.py`:
  - `extract_fidelity_candidates(summary, book, strict=True)` drops real gloss tokens taken from the corpus — English: `(likes/dislikes)`, `(cycling/bikes)`, `(*was/were*)`, `shall/should)`; history: `(tale/narration)`, `(kompyuter/hisoblagich)`. (Both history tokens are labelled as such — an earlier draft mislabelled `(kompyuter/hisoblagich)` as English.)
  - `strict=True` **keeps** digit-bearing real hits: `1/3`, `9/10`, `3/4`, `(2h/g)`.
  - `strict=True` **keeps** a digitless `=` formula, e.g. `(yaim=c+i+g)` — see the GREEN predicate below.
  - **Pin the superscript behavior:** `kishi/km²` survives `strict=True` because `'²'.isdigit()` is `True`. This is deliberate and matches `extract_math_expressions`'s own use of `isdigit`. Add an explicit test — an implementer who reaches for `isdecimal()` (which is `False` for `'²'`) would silently change behavior.
  - `strict=False` (the default) is **unchanged** — the existing digitless-algebra test `(a−b)/(a+b)` and the `−3/(2a)` drift test must still pass untouched.
  - Run `uv run python -m pytest tests/services/test_extract_fidelity.py -q` — MUST fail.
- [ ] **GREEN:** Add `strict: bool = False` to `agent.extract_fidelity_candidates`. Strict predicate = **keep expressions containing a digit OR an `=`**, not digit-only. Measured justification: across the corpus every dropped gloss (26 english + 27 history + 8 geografiya) contains `/` and **none contains `=`**, so `=` costs nothing on measured data — but it preserves digitless `=`-formulas for the humanities subjects with **zero corpus data today**: `iqtisodiyot`, `huquq`, `chqbt` are all family `humanities` (verified against `subjects.REGISTRY`), and the claim "digitless algebra does not occur in humanities prose" is unmeasured for economics. Cheap insurance, identical measured outcome. Do **not** modify `extract_math_expressions` or `_FIDELITY_EXPR_RE` — the change is a post-filter on the candidate list, which is why the non-strict (math/science) path is provably untouched.
  - **Apply strict BEFORE the `[:_FIDELITY_MAX_CANDIDATES]` slice**, not after. The function currently ends `cands = sorted(...); return cands[:_FIDELITY_MAX_CANDIDATES]` — filtering after the slice would let 12 glosses crowd real hits out of the cap entirely. No strict-family lesson currently exceeds the cap, so today's measured numbers are order-invariant and this cannot be caught by the corpus diff — **pin it with a unit test** (>12 glosses plus one real `1/3`, assert the real hit survives).
- [ ] **RED:** New file `tests/services/test_extract_fidelity_family_gate.py`: with `agent.extract_fidelity_candidates` patched, `pipeline._verify_and_maybe_regen_extract(subject="english", ...)` calls it with `strict=True`; `subject="math-algebra"` and `subject="physics"` call it with `strict=False`; a family-`default` code (`informatika`) calls it with `strict=False`; an unknown/absent subject code calls it with `strict=False` (**fail toward current behavior**). MUST fail.
- [ ] **GREEN:** Add `subject: str` to `_verify_and_maybe_regen_extract`; compute `strict` from the family, defaulting an unknown code to `"default"` (i.e. non-strict); pass `subject=subject` from `_extract_run` (already in lexical scope — no signature change to `_execute_phase`). Define `_STRICT_FIDELITY_FAMILIES = frozenset({"languages", "humanities"})` in `pipeline.py` with a comment citing the measured numbers.
  - **`subjects` is not currently imported by `pipeline.py`** — add it to the existing `from app.services import agent, book_fetch, ...` line (alphabetical position: after `storage`), do not add a second import statement.
  - **Say out loud what stays non-strict.** Family `default` — `musiqa`, `tasviriy-sanat`, `texnologiya`, `informatika` (verified against `subjects.REGISTRY`) — keeps today's noisy behavior. `informatika` is legitimately non-strict (code tokens are genuine `/`+paren content), but music/fine-arts/technology are as gloss-prone as humanities and simply have no corpus data yet. This is a deliberate fail-toward-current-behavior choice, not an oversight; record it in the worklog so the next such launch does not rediscover it.
- [ ] **VERIFY:** both files green; full suite green.
- [ ] **ACCEPTANCE SMOKE (~$0.02, real api) — the changed pipeline path, not just the audit module.** `CLAUDE.md` requires a real generation smoke for anything affecting generation, and Task 4's paid run exercises the *new audit module* while Task 6 is `$0` — neither touches `_verify_and_maybe_regen_extract`. Close that gap with a bounded 2-leg in-process smoke over a real english lesson whose extract is known to yield gloss-only candidates:
  - Leg A (`strict=False`, pre-fix behavior): exactly **one** `lesson.extract.verify` call fires and bills (~$0.02). This proves the noise was real and reachable, not theoretical. **Mechanism:** with the fix shipped an english lesson resolves `strict=True`, so force pre-fix behavior explicitly — monkeypatch `_STRICT_FIDELITY_FAMILIES` to an empty frozenset for Leg A only. State which mechanism was used in the report.
  - **Stub `agent.summarize_lesson` in Leg A.** If the verify model *confirms* a gloss as a mismatch — entirely plausible, since glosses genuinely are absent from the source, which is the whole point of this fix — `_verify_and_maybe_regen_extract` proceeds to a regen with whole-book input, an extra **~$0.05–0.15** call the ~$0.02 budget does not cover. The verify call stays real; only the regen is stubbed. Assert the stub was reached-or-not and report it either way — *whether a gloss gets confirmed as drift is itself a finding worth recording.*
  - Leg B (`strict=True`, shipped behavior): **zero** calls fire, `$0`, and the extract is returned unchanged.
  - Assert by counting `agent_usages` rows with `operation='lesson.extract.verify'` before/after each leg. Report actual `$`.
  - **Use a scratch DB with a seeded job row, or pass `homework_job_id=None`.** Do **not** fabricate a job UUID: `AgentUsage.homework_job_id` is `ForeignKey("homework_jobs.id", ondelete="SET NULL")` (verified in `app/models/agent_usage.py`), so a made-up id fails the insert *after* the call has already billed. Must not write to any production job's phase rows.
- [ ] **COMMIT:** stage exactly `app/services/agent.py app/services/pipeline.py tests/services/test_extract_fidelity.py tests/services/test_extract_fidelity_family_gate.py`.

## Task 6 — Corpus regression proof (committed harness, grounded numbers)

**Scene:** Unit tests prove the intent; only the real corpus proves the implementation matches it. The harness that produced this plan's numbers was a scratch script that exists nowhere in the repo — **commit it**, so the stop-gate below is reproducible by whoever runs it and by anyone revisiting this later.

- [ ] **Commit the harness** as `scripts/extract_candidate_corpus_diff.py`: for each subject, read every `done` extract + its book PDF, compute `agent.extract_fidelity_candidates(md, agent.read_whole_book_text(pdf))` (grounded — **this is the production call, not raw `extract_math_expressions`**) and the same list under `strict`, and print a before/after table. Read-only, `$0`, no model calls. Cache each book's whole text (one read per `book_id`, not per lesson) — a naive per-lesson read is ~30× slower.
- [ ] Run it over all 3,427 done extracts against the **shipped** implementation.
- [ ] **THE STOP-GATE IS A WITHIN-RUN INVARIANT, NOT A FROZEN CONSTANT.** The corpus is **live** — verified 2026-08-07: 301 jobs created in the preceding 7 days, newest done extract 2026-08-05. A new physics lesson landing between now and execution legitimately moves a count by one with a perfectly correct gate. So:
  - **Hard stop (cannot fail for a correct implementation):** for every `math` and `sciences` subject, the grounded candidate list computed **before** the strict filter must equal the list computed **under** strict, *within the same run*. `strict` is never `True` for those families, so the post-filter is never reached; any inequality means the family gate leaked. This invariant is immune to corpus growth.
  - **Informational snapshot (2026-08-07, NOT a gate):** english 8 → 0 (26 → 0 exprs); history 19 → 6 (34 → 7); geografiya 9 → 2 (10 → 2); tarbiya 0; adabiyot 0; biology 18 (23); physics 66 (127); kimyo-g7-11 25 (56); math-algebra 210 (715); matematika 21 (44); geometriya-g7-11 69 (153); 3,427 extracts total. A difference here is expected drift, **not** a failure — record the new number and move on.
  - The harness prints **both** the within-run comparison and the delta vs this snapshot, clearly labelled, so nobody mistakes one for the other.
- [ ] If the within-run math/science invariant fails by even one expression, **stop** — the post-filter has leaked into the math path.
- [ ] For any subject, confirm qualitatively that the dropped tokens are still glosses and the kept ones still digit/`=`-bearing. That check does not depend on the snapshot.
- [ ] **COMMIT:** stage exactly `scripts/extract_candidate_corpus_diff.py`; recorded numbers go in the worklog (Task 7).

## Task 7 — Finish

- [ ] `git fetch origin` then `git log HEAD..origin/Nggaev-v2` — if the base moved (likely: #118 is open and touches `pipeline.py`), **rebase onto `origin/Nggaev-v2`**, resolve, and **re-run the full suite** before continuing.
- [ ] De-stale `docs/HOW_IT_WORKS.md` and `docs/CODE_MAP.md` where they describe the extract-fidelity guard — both currently imply uniform coverage. No `docs/DATABASE.md` / `DEPLOY.md` change (no schema, no deploy).
- [ ] Worklog **0164** in `docs/memory/MASTER_MEMORY.md` + row in `docs/memory/INDEX.md`. **Re-check the INDEX tail at rebase time — worklog numbers go stale mid-lane** (this bit #92 and #93).
- [ ] Close/file items in `docs/memory/ROADMAP.md`.
- [ ] `git mv docs/superpowers/plans/2026-08-07-extract-fidelity-language-coverage.md docs/superpowers/plans/shipped/`.
- [ ] `superpowers:finishing-a-development-branch` — user decides push/PR.

---

## Out of scope (deliberate)

- Any language-side fidelity guard (proper-noun / date / definition grounding). Gated on Task 4's measurement.
- Any change to `phase_judge._fidelity_flags` (years-only, advisory). If Task 4 shows date drift dominates, that is the natural next lane — file it, don't build it.
- Any change to `_FIDELITY_EXPR_RE` or the verify prompt.
- Regenerating any packet.
