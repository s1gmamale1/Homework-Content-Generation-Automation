# Extract-completeness check — calibration against the labeled coverage-audit dataset

**Date:** 2026-08-07 · **Lane:** extract-completeness check (warn-only) · Task 5 of
`docs/superpowers/plans/shipped/2026-08-07-extract-coverage-check.md`
**Verdict: NEGATIVE — ships `extract_coverage_check_enabled=False` (default off).**

## Method

The check (`agent.check_extract_coverage`) was run against the 9 lessons of the
2026-07-06 coverage audit, whose extracts are still stored verbatim in `edu_copy`
(`builtin:extract:v2`, char counts matching the audit's table byte-for-byte). For each
lesson the harness (`scripts/extract_coverage_calibrate.py`) fed the check the stored
extract plus the lesson's own printed page window (±1, via the production
`agent.read_page_range_text`) and compared its reported omissions against the audit's
per-item `in_extract` labels (`docs/research/2026-07-06-coverage-audit-data.json`):
**8 labeled extract-omissions across 5 lessons, 4 lessons clean.**

Both candidate models were run over `transport=api` on the plain Gemini key. All 18
calls succeeded (`agent_usages` success-count guard passed: 9 of 9 per model, no
fail-open masking).

**The labels are `gemini-3.1-pro`'s judgement plus one hand-verified case — this
measures agreement with a strong model, not with truth.**

## Result

| Model | Recall over 8 labeled misses | Hard bar A (kimyo §13) | Hard bar B (compact math §5/§2) | Items on the 4 clean lessons |
|---|---|---|---|---|
| `gemini-3.5-flash-lite` | 5/8 | **FAIL** | PASS | 18 |
| `gemini-3.5-flash` | **8/8** | PASS (hand-confirmed) | **FAIL** | 18 |

### Hard bar A — the hand-verified case

kimyo §13's extract dropped both numerical worked-example types. This is the audit's
strongest, hand-verified finding and the dominant loss class the whole feature targets.

- **flash-lite: FAIL.** It reported two items, but *different* ones — a description of
  arsenic's properties and Mendeleev's predicted elements. Neither dropped worked-example
  type was named. Matcher scored 0/2, and hand inspection agrees.
- **flash: PASS.** Reported `masala_izotop_tarkibi` (isotope-composition problem) and
  `masala_vodorodli_birikma_va_oksid` (hydrogen-compound/oxide problem → identify the
  element by valence). Hand-confirmed as genuinely the two labeled types, not a
  single-token matcher artefact — the plan required this confirmation precisely because
  `_matches` can bridge unrelated items on one generic token.

### Hard bar B — the false-positive guard

The bar: the check must **not** fire on math-algebra §5 (440 chars, 100% coverage — the
case Gate B once false-failed) or §2.

- **flash-lite: PASS** (0 items on both).
- **flash: FAIL** — 5 items on §2. Hand-checked against the stored extract, at least two
  are clear false positives:
  - *"Kasrning asosiy xossasi formulasi"* — the extract literally states
    `"Kasrning asosiy xossasiga ko'ra, surat va maxrajini bir xil algebraik ifodaga
    ko'paytirish yoki bo'lish natijasida unga teng kasr hosil bo'ladi."` The property is
    present in prose; only its symbolic form is absent. The check's own prompt says a
    more general statement that still covers the item is NOT a miss.
  - *"Ishoralarni o'zgartirish formulalari"* — likewise present in prose
    (`"Kasrning surat yoki maxrajidagi ishorani qarama-qarshisiga o'zgartirish…"`).

  The remaining three (permissible-value worked examples, the `(y−x)/(x−y)` reduction
  type, building a fraction from a word description) are plausibly **real** omissions the
  audit's judge did not enumerate as core — but the bar is absolute and it fired.

*(Caveat on completeness of this record: math §5's per-lesson line from the `flash` run
was not retained in the captured output. It does not change the verdict — bar B already
failed on §2 — but it is a gap in the raw record, noted rather than papered over.)*

### Clean-lesson noise

Both models reported 18 items across the 4 clean lessons. flash-lite's were concentrated
in biology (13); flash's spread more evenly. Many are finer-grained than the audit's core
enumeration and some are genuinely absent from the extract — but at ~4–5 advisory items
per clean lesson, the warning would read as noise rather than signal.

## Money-rule log

| Model | Calls | Input tokens | Output tokens | Cost |
|---|---|---|---|---|
| `gemini-3.5-flash-lite` | 9 | 59,215 | 1,911 | $0.0225 |
| `gemini-3.5-flash` | 9 | 59,215 | 25,371 | $0.3172 |
| **Total** | **18** | | | **$0.3397** |

**Per-lesson cost correction — the plan's estimate was wrong by more than an order of
magnitude.** The plan assumed ~$0.001/lesson. Measured: **$0.0025/lesson on flash-lite and
$0.035/lesson on flash**, because the check's output is verbose (flash averaged ~2.8K
output tokens per lesson). Over the ~3,200-lesson corpus that is ~$8 (lite) or **~$113
(flash)**, not the ~$3 the plan projected. Any future decision to enable this must budget
from these numbers, not the plan's.

## Decision

The plan's pre-registered, mechanical rule: flash-lite must pass both bars, else flash
must pass both, else ship default-off. **flash-lite fails bar A; flash fails bar B.
Neither passes → `extract_coverage_check_enabled` ships `False`.**

The bars were fixed before the run and were not moved afterwards. The code ships complete,
tested and wired; it simply does not run until an operator enables it.

## Is this verdict itself trustworthy? (added at the merge gate)

**The verdict is conservative but NOT proven.** Two challenges were raised at the gate; one is
now ruled out by measurement, the other stands.

**Ruled out — page-offset did not confound this run.** `_lesson_source_or_none` reads
`toc_entries.page_start/end` with `margin=1` while `teaching_audit.py:127-144` documents a
measured **−3..+2** printed-vs-physical spread on other books, which would hand the check a
neighbouring lesson and produce confident *false* omissions. Probed deterministically (no
model calls) across all 9 calibration lessons: **9/9 windows contain their own lesson's
title** (title-token match ≥ half, most 100%). So on these four books printed and physical
pages align within the ±1 margin, and the precision numbers above are not an offset artefact.
**This does not clear the hazard generally** — it clears it for this dataset only, which is
exactly why the fix stays a precondition on enabling.

**Stands — bar B's ground truth is not exhaustive.** Spot-probed the two most suspicious
clean-lesson reports: on history §10 the window genuinely contains `Kayxusrav` (a Rum Seljuk
sultan — legitimately *this* lesson's content, absent from the extract), and biology's lichen
items sit inside the Fungi lesson's own page range, not the next lesson's. So a material
share of what bar B counted as over-firing looks like **real, finer-grained omissions the
audit's judge never enumerated as core** — not noise.

**Consequence for the reader:** treat "flash fails precision" as **unproven**, not
established. What is established is that flash's recall is 8/8 and that the pre-registered
bar fired. The honest state is *"we do not yet know this check's precision"*, and the
default-off decision is the conservative response to that uncertainty rather than a measured
indictment. Re-deriving clean-lesson ground truth (`extract-coverage-precision-1`) is what
would settle it.

## What this measured, honestly

The negative result is narrower than "the idea doesn't work":

- **The detector is real.** flash caught **8 of 8** labeled omissions including the
  hand-verified worked-example case that motivated the whole lane. Recall is not the
  problem.
- **Precision at the item level is the problem.** It cannot reliably tell "absent" from
  "stated more generally", which is exactly the distinction its prompt asks for and the
  distinction Gate B's false-positive history says matters most here.
- **Bar B may itself be mis-specified.** It assumed the audit's four clean lessons are
  *complete*, but the audit only labelled the core items it enumerated. Some of what flash
  reported on them looks genuinely missing. A future attempt should re-derive the
  clean-lesson ground truth before reusing this bar rather than treating this run as
  proof the check over-fires.
- **Cost is 35× the plan's estimate on the model that works.** That alone would have
  forced a re-think of "enable it fleet-wide by default".

Follow-ups worth their cost, in order: tighten the prompt against the
prose-vs-formula false-positive class and re-run these same 9 lessons (cheap, $0.34/run);
re-derive clean-lesson ground truth so bar B measures what it claims; only then revisit
the default. `extract-coverage-regen-1` (acting on findings) stays parked behind all of
this — precision must come first.
