# Extract-Fidelity Guard: Language/Humanities Coverage — Measure, Then Fix the Noise

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish, by measurement, what extract-fidelity drift actually occurs in language/humanities lessons — where the deterministic CQ-D guard is blind — and separately fix a *measured* false-positive defect in that guard's pre-filter. **Do not build a language-side fidelity checker in this lane.**

---

## Approach & key decisions

- **Chosen approach: measure first, fix only what is already proven.** The lane delivers (a) a reusable extract-fidelity audit instrument + one real run over ~40 non-math lessons, and (b) a narrow precision fix to the existing pre-filter. It deliberately stops before designing a language-side guard, because the base rate of language drift is unknown and a guard built blind would re-introduce the R14 regen tax.
- **The premise was verified against code AND partially corrected by free measurement.** `agent.extract_math_expressions` (`agent.py`, `_FIDELITY_EXPR_RE`) requires `/` or `=` AND (a digit OR a paren); `extract_fidelity_candidates` draws only from it; `pipeline._verify_and_maybe_regen_extract` early-returns `out, 0, 0` on an empty candidate list (**locate by symbol — the writeup's `pipeline.py:1207` was stale; today it is ~`1316-1317`**). The only other deterministic signal, `phase_judge._fidelity_flags`, is years-only and advisory (never gates a regen).
- **Load-bearing measured fact (3,427 real done extracts in `edu_copy`, $0, no model calls):** the guard is **not** uniformly inert. It is inert on `tarbiya` (0/18) and `adabiyot` (0/2), near-inert on `history` (5.9%), and **actively mis-fires on `english`: 8/22 lessons (36.4%) produce 26 candidates, of which ZERO are real** — all are prose glosses and grammar alternations (`(likes/dislikes)`, `(*was/were*)`, `(cycling/bikes)`). The paren arm, added for digitless algebra like `(a−b)/(a+b)`, collides with how language extracts write gloss pairs.
- **That noise has never billed.** `agent_usages` has **zero** `lesson.extract.verify` rows for `english` — those 22 jobs ran 2026-06-23/24, before the CQ-D guard shipped 2026-07-02 (#77 / worklog 0111). The 36.4% is a **prediction for the next English launch**, not past spend. Total historical verify calls: 297, ~85% math/science.
- **Rejected — subject-agnostic regex tightening.** Two variants were tested against the real corpus: (i) max-alpha-run ≤ 2 unless a digit is present, (ii) same plus "contains a math operator". Both killed real physics/algebra catches (`(mrt)/(mp)`, `j/(mol*k)`, `y=arcsin`, `y=sqrt(x`). Neither is acceptable given the guard's proven value is math/science.
- **Chosen fix — subject-FAMILY gate.** For `subjects.REGISTRY[code].family in {"languages", "humanities"}`, require a digit (drop the paren arm). Measured effect: `english` 8 rows → **0**, `history` 48 → 35, `geografiya` 50 → 44, and **math + sciences byte-identical** (biology 27/27, kimyo 68/68, physics 70/70, matematika 27/27, math-algebra 218/218, geometriya 79/79). Every real fraction (`1/3`, `9/10`, `3/4`, `(2h/g)`) survives in history/geografiya; only glosses are dropped. Digitless algebra does not occur in language/humanities prose, so nothing real is lost.
- **`subject` needs no new plumbing.** `_verify_and_maybe_regen_extract` is called from the `_extract_run` closure nested inside `_execute_phase`, which already takes `subject` as a parameter — it is in lexical scope at the call site.
- **The instrument must be calibrated or its number is worthless.** The audit ships with mutation injection (plant a known date/name/definition drift, confirm detection) — same discipline as `teaching_audit.py --sensitivity`. If planted drift is not detected, the report states the base rate is uninterpretable rather than printing a number.
- **Money rule.** One bounded, cost-reported run: ~48 gemini-3.5-flash calls ≈ **$0.85**, hard-capped by `--limit`. No content is generated; the audit reads *already-done* extracts. `gemini-3.5-flash-lite` is 5× cheaper but was measured at 33% structured-output flake in the TOC-validator work (worklog 0161) — not used here.

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
- `tests/services/test_extract_fidelity_audit.py` — pure-unit tests for models/prompts/mutation/aggregation (no model calls).
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
- [ ] **GREEN:** Create `app/services/extract_fidelity_audit.py` with `ExtractFidelityAuditError(RuntimeError)`, Pydantic `ClaimVerdict` / `ExtractFidelityReport`, a frozen dataclass `ExtractAuditInputs` (`job_id`, `book_id`, `subject`, `family`, `grade`, `source_language`, `output_language`, `lesson_title`, `page_start`, `page_end`, `extract_md`, `source_text`), and `async load_extract_audit_inputs(job_id)`.
  - `source_text` = `agent.read_page_range_text(storage.book_pdf_path(book_id), page_start, page_end, margin=1)` — **margin=1 deliberately matches `pipeline._verify_source_for_section`**, so the audit judges the extract against the same source window the live guard would use. Do **not** copy `teaching_audit`'s `margin=4`.
  - `extract_md` comes from the `extract` phase row (`phase_name='extract'`, `status='done'`, non-NULL `output_md`); raise if absent.
  - `family` from `subjects.REGISTRY[subject].family`, defaulting to `"default"` for an unknown code.
- [ ] **VERIFY:** `uv run python -m pytest tests/services/test_extract_fidelity_audit.py -q` green; full suite green.
- [ ] **COMMIT:** stage exactly `app/services/extract_fidelity_audit.py tests/services/test_extract_fidelity_audit.py`.

## Task 2 — Adjudicator prompt + mutation injection (pure)

**Scene:** The adjudicator decides which claims in an extract are unfaithful to its source pages. Two hazards dominate. **(1) Translation.** These extracts routinely render an English or Russian source into Uzbek — a naive checker calls every translation a drift. **(2) Legitimate compression.** An extract is a summary; absence of detail is not drift. The prompt must separate `contradicts` (the extract asserts something the source denies) from `unsupported` (asserted but not locatable) from `ok`, and must be explicit that paraphrase, translation, transliteration, rounding, and omission are all `ok`.

- [ ] **RED:** Add tests: `build_adjudicator_prompt(inputs)` contains the extract, the source text, the lesson title, an explicit translation-tolerance clause, and an explicit "omission is not drift" clause; `inject_mutation(md, kind, seed)` for `kind in {"date","name","definition"}` returns text that **differs** from the input and returns a `Mutation` record naming the original and replacement span; injecting into text with no mutable target returns `None` (caller skips that lesson rather than fabricating). Run the file — MUST fail.
- [ ] **GREEN:** Implement `build_adjudicator_prompt` and `inject_mutation` in `app/services/extract_fidelity_audit.py`. `inject_mutation` is **deterministic given `seed`** (no `random` without a seeded `Random` instance) so a run is reproducible.
- [ ] **VERIFY:** file tests green; full suite green.
- [ ] **COMMIT:** stage exactly `app/services/extract_fidelity_audit.py tests/services/test_extract_fidelity_audit.py`.

## Task 3 — Orchestrator + CLI

**Scene:** Wire the pieces into one auditable run. Mirror `teaching_audit._call` — fail loud, record `{step, provider, model, usage}` per call, and carry its documented caveat that a structured-output retry logs an extra `agent_usages` row the printed total does not include.

- [ ] **RED:** Add tests with `agent.run_phase` patched (`AsyncMock`): `audit_one(...)` returns an `ExtractFidelityReport` and appends exactly one entry to `calls`; a `run_phase` exception raises `ExtractFidelityAuditError` (never silently returns a clean report — a dead adjudicator must not read as "no drift"); `audit_with_control(...)` runs the pristine arm and the mutated arm and reports `detected_planted` per mutation kind. Run — MUST fail.
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

- [ ] Run `--dry-run` first and confirm the sample is: `english` 22 (all), `history` 12 (stratified across grades 5–11 and uz/ru), `tarbiya` 4, `adabiyot` 2 = 40 lessons, plus 8 mutation-control pairs. Confirm estimated cost ≤ $1.20 before proceeding.
- [ ] Run for real with `--limit 48`. Record actual `$` from the script's own output.
- [ ] **Calibration first, base rate second.** If planted drift is detected in fewer than 6 of 8 mutation controls, the report **states the base rate is uninterpretable** and the lane stops here for a user decision. Do not report a drift number from an uncalibrated instrument.
- [ ] Write `docs/memory/reports/2026-08-07-extract-fidelity-language-baseline.md`: per-subject `contradicts` / `unsupported` rate per lesson, breakdown by `claim_type`, calibration result, actual $, and an explicit **cross-check**: *of the drift the audit found in `history`/`geografiya`, would any of it have been caught by the gloss tokens Task 5 drops?* (Expected: no — the dropped tokens are all glosses — but this must be checked, not assumed.)
- [ ] State plainly whether the measurement supports building a language-side guard, and file the follow-up in `docs/memory/ROADMAP.md` (worked-up) or `docs/memory/WISHLIST.md` (raw) accordingly. **Do not build it here.**
- [ ] **COMMIT:** stage exactly the report + the backlog file touched.

## Task 5 — Fix the measured false positive (family gate)

**Scene:** `english` extracts feed 26 pure-noise suspects into a paid verify call, risking a spurious confirmed "mismatch" → a regen of a good extract. Fix it without touching math/science behavior.

- [ ] **RED:** Add to `tests/services/test_extract_fidelity.py`:
  - `extract_fidelity_candidates(summary, book, strict=True)` drops real English gloss tokens: `(likes/dislikes)`, `(cycling/bikes)`, `(*was/were*)`, `shall/should)`, `(tale/narration)`, `(kompyuter/hisoblagich)`.
  - `strict=True` **keeps** digit-bearing real hits: `1/3`, `9/10`, `3/4`, `(2h/g)`.
  - `strict=False` (the default) is **unchanged** — the existing digitless-algebra test `(a−b)/(a+b)` and the `−3/(2a)` drift test must still pass untouched.
  - Run `uv run python -m pytest tests/services/test_extract_fidelity.py -q` — MUST fail.
- [ ] **GREEN:** Add `strict: bool = False` to `agent.extract_fidelity_candidates`; when strict, keep only expressions containing a digit. Do **not** modify `extract_math_expressions` or `_FIDELITY_EXPR_RE` — the change is a post-filter, so the math path is provably untouched.
- [ ] **RED:** Add to `tests/services/test_pipeline_*` (new file `tests/services/test_extract_fidelity_family_gate.py`): with `agent.extract_fidelity_candidates` patched, `pipeline._verify_and_maybe_regen_extract(subject="english", ...)` calls it with `strict=True`; `subject="math-algebra"` and `subject="physics"` call it with `strict=False`; an unknown subject code calls it with `strict=False` (**fail toward current behavior**). MUST fail.
- [ ] **GREEN:** Add `subject: str` to `_verify_and_maybe_regen_extract`; compute `strict` from the family, defaulting an unknown code to `"default"` (i.e. non-strict); pass `subject=subject` from `_extract_run` (already in lexical scope — no signature change to `_execute_phase`). Define `_STRICT_FIDELITY_FAMILIES = frozenset({"languages", "humanities"})` in `pipeline.py` with a comment citing the measured numbers.
  - **`subjects` is not currently imported by `pipeline.py`** — add it to the existing `from app.services import agent, book_fetch, ...` line (alphabetical position), do not add a second import statement.
- [ ] **VERIFY:** both files green; full suite green.
- [ ] **COMMIT:** stage exactly `app/services/agent.py app/services/pipeline.py tests/services/test_extract_fidelity.py tests/services/test_extract_fidelity_family_gate.py`.

## Task 6 — Corpus regression proof

**Scene:** Unit tests prove the intent; only the real corpus proves no math regression.

- [ ] Re-run the corpus comparison over all 3,427 done extracts (deterministic, `$0`, no model calls) with the **shipped** `strict` implementation rather than the scratch reimplementation.
- [ ] **Assert and record in the commit message:** `math` + `sciences` candidate counts are *identical* before and after (biology 27, kimyo 68, physics 70, matematika 27, math-algebra 218, geometriya 79 rows); `english` 8 → 0; `history` 48 → 35; `geografiya` 50 → 44.
- [ ] If any math/science count moves by even one, **stop** — the post-filter has leaked into the math path.
- [ ] **COMMIT:** the recorded numbers go in the worklog (Task 7); no source change expected here.

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
