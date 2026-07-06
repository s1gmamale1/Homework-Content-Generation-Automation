# Coverage audit — is *lesson-core ⊆ packet*?

**Date:** 2026-07-06 · **Lane:** extract COVERAGE-CONTRACT (round-2 headline) · Phase 0 (audit-before-contract)
**Author:** implementer · **Status:** evidence for `docs/superpowers/plans/2026-07-06-extract-coverage-contract.md`

## Why this audit

Nothing in the shipped quality stack (LLM judge, boundary note, content-lint, answer-key
solver, golden harness) verifies that everything the *lesson teaches* made it into the
packet. Every check grades what is **in** the packet; none asks whether the packet is
**complete** w.r.t. the source lesson. Worse: the judge grades the packet against the
**extract**, not the source — so if the extract under-summarizes the lesson, the gap is
invisible to every downstream check. A packet can pass 10/10 audit dimensions while
silently skipping the lesson's third theorem or its whole class of worked problems.

This audit measures how big that gap actually is, and — decisively for the design —
**where** the loss happens: at the **extract** (under-summarized → invisible downstream)
or at the **phases** (extract had it, generators dropped it).

## Method

- **Sample:** 9 real `done` packets from `edu_copy` (read-only), spanning 5 subject
  families, 2 languages (Uzbek + Russian), and lesson sizes from 3 to 12 printed pages —
  including the WISHLIST false-positive case (Algebra §5, the compact lesson Gate B
  false-failed twice on 2026-07-03).
- **Per lesson:** `pdftotext` the printed page window from `var/books/<book_id>/source.pdf`
  → enumerate the lesson's **core teachable items** (concepts/terms, rules/theorems,
  formulas, worked-example types, key facts) from the **source alone** → for each item
  judge `in_extract` (did the extract capture it?) and `in_packet` (did any phase teach /
  test / use it?).
- **Judge:** one bounded `gemini-3.1-pro-preview` call per lesson over `transport=api`.
  **Cost (money-rule log): 226,251 input + 44,363 output tokens over 9 lessons ≈ $0.72.**
  No generation; read-only DB. Harness + raw verdicts:
  `docs/research/2026-07-06-coverage-audit-data.json` (`scratchpad/coverage_audit.py`).
- **Hand-verification:** the strongest claim (chem §13 extract dropping both worked-example
  types) and the FP case were confirmed by direct inspection of the source PDF and the
  stored extract — not taken on the judge's word.
- **Limitations:** single LLM judge (presence calls carry error); 9 lessons; math/Uzbek-
  heavy corpus; the English irregular-verb reference page had no text layer and was
  dropped. Treat the *rates* as indicative and the *loss-class pattern* + the two
  hand-verified findings as solid.

## Headline result

| Lesson | pages | items | central | packet cov | central cov | extract-loss | phase-loss |
|---|---|---|---|---|---|---|---|
| math-algebra §5 *(FP case)* | 3 | 5 | 4 | **100%** | 100% | 0 | 0 |
| math-algebra §2 | 6 | 7 | 7 | 100% | 100% | 0 | 0 |
| geometriya §1 | 3 | 12 | 9 | 92% | 100% | 1 | 0 |
| geometriya §5–6 | 3 | 7 | 6 | 100% | 100% | 0 | 0 |
| kimyo §13 | 12 | 7 | 5 | **71%** | 100% | **2** | 0 |
| kimyo §16 | 3 | 8 | 6 | 100% | 100% | 0 | 0 |
| history §18 (Crusades) | 10 | 15 | 11 | 80% | 91% | 2 | 1 |
| history §10 (Saljuqiylar) | 4 | 11 | 10 | 100% | 100% | 0 | 0 |
| biology §6 (Fungi) | 6 | 17 | 13 | 88% | 92% | 0 | 2 |
| **TOTAL** | | **89** | **71** | **91%** | **~97%** | **5** | **3** |

**Overall packet coverage is 91% (81/89 core items); central-item coverage is ~97%.**
This is a real, measurable leak — not a catastrophe. It concentrates in three ways:

### Finding 1 — the extract systematically drops the lesson's *worked-example / problem types* (dominant, invisible)

The single clearest pattern. **kimyo §13 lost BOTH its numerical worked-example types**
(isotope mass-fraction → average atomic mass; composition+valence → unknown element),
dropping coverage to 71% while every *central concept* stayed at 100%. Hand-verified: the
source (p51–62) contains explicit `3-misol`/`4-misol` worked examples (chlorine isotope
mixture → Ar=35.5; silver Ag-107/109), and the stored extract is *entirely* the
periodic-trends narrative — **none** of those problem types survive. The packet then can't
build practice problems it never saw, and the judge (reading the extract) can't see the
gap. Geometry §5–6 shows the mirror image: a worked-example type absent from the extract
that the phases happened to reconstruct from domain knowledge — luck, not a guarantee.

→ **The extract must explicitly inventory the lesson's worked-example/problem types**, not
just its concepts. This is the core of the contract.

### Finding 2 — extract *length* is orthogonal to coverage (closes `extract-gateb-short-lesson-fp-1`)

- Algebra §5 (the FP case): a **440-char** extract achieved **100%** coverage.
- kimyo §13: a **1452-char** extract *lost* two worked-example types (71%).

A short extract can be complete; a long one can be lossy. Gate B's `extract_min_summary_chars=400`
floor is a **proven-bad validity proxy** — it nearly rejected a perfectly complete extract
(§5 measured 378–650 chars across attempts) while waving through a lossy longer one.
**Validity is structural, not length-based:** a compact lesson with N enumerated core items
present is valid; a refusal/garble with no enumerated items is not. Reworking Gate B to a
structural check closes the WISHLIST false-positive.

### Finding 3 — phase-side loss is real but smaller (3/8 misses)

Three items the extract **captured** were dropped by the generators: biology's
fungi-vs-bacteria structural contrast and the ecto-/endotrophic mycorrhiza distinction;
the Crusades' class-differentiated motives. These are secondary/comparative facts. A
contract-at-extract does nothing for them — they need a phase-side signal.

## What the split decides (design implications)

**5 of 8 misses are EXTRACT-loss, 3 are PHASE-loss** — and the extract losses are the
insidious ones (invisible to the judge and every downstream check). So:

1. **Primary fix — enumerated contract at the extract.** Rework the free-form prose
   summary into a structured, parseable inventory whose required sections include
   **worked-example/problem types** alongside concepts, rules/theorems, and formulas.
   This directly attacks the 5 extract-losses and the dominant worked-example gap, and it
   makes coverage *checkable* (a set comparison) for the first time.
2. **Gate B becomes structural.** Validity = required contract sections present with ≥1
   enumerated item, not char-count. Compact §5 passes; refusal/garble still fails. Closes
   `extract-gateb-short-lesson-fp-1`.
3. **Phase-side coverage — a smaller, separable need (3/8).** Options: a cheap prompt-only
   "cover the enumerated contract items" instruction to phases, and/or a deterministic
   post-job coverage check into `validation_warnings` (warn-only, the CQ-B channel). The
   audit supports doing the extract contract now and treating phase-side as
   warn-only-first (never gate on a new check's first version — the validate_toc/solver
   lesson), with phase-prompt nudges optionally in-lane or as a fast follow-up.

The bigger the lesson, the worse the leak (the 3 lowest-coverage lessons are the 3 largest
by page count; every ≤4-page lesson scored 100%). An item-level enumerated contract
resists the lossy compression that free-form prose suffers as lesson size grows.

## Composition constraints carried into the plan

- The contract lives on the same extract path as CQ-D's fidelity guard
  (`extract_fidelity_candidates` → `verify_extract_fidelity` → regen-once) — the rework
  must compose with it (its tests stay green unmodified).
- Changing the built-in extract prompt requires bumping `prompt_hash="builtin:extract:v2"`
  → `:v3` (`pipeline.py:924`), which invalidates the cross-job extract cache: every book's
  **next** job re-extracts once. Extract is pinned to cheap flash and bounded per-job, so
  the cost is one re-extract per active book, incurred organically per-job (no bulk
  backfill).
- The extract feeds every content phase, the judge, and the solver as `lesson_context`; a
  shape change touches all of them → regression evidence required at acceptance.
