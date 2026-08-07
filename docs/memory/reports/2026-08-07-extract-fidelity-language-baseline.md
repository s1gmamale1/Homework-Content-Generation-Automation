# Extract-fidelity language/humanities baseline — 2026-08-07

**Verdict: the base rate is UNINTERPRETABLE. The calibration gate failed.** Per the plan's
own rule, no drift number is quoted from an uncalibrated instrument.

## Calibration (the gate) — FAILED, 5/8

Two passes were run. The first attempted only 3 of 8 pairs (targets were chosen before it was
known which lessons load or can carry a plant — a selection-order defect, since fixed by
round-robin selection over *feasible* candidates). The second pass re-ran calibration alone,
reusing the already-paid-for pristine arms, with 8 targets spanning all 5 subjects.

| gate | required | measured | result |
|---|---|---|---|
| sensitivity | ≥6 of 8 planted detected | **5 of 8** | **FAIL** |
| specificity | `contradicts` in <50% of lessons | 3 of 31 (9.7%) | PASS |

**The failure is structured, not noise — this is the substantive finding:**

| plant kind | detected | lessons |
|---|---|---|
| `name` (swapped proper noun) | **4 / 4** | english, history, geografiya, tarbiya |
| `definition` (swapped definitional predicate) | **1 / 4** | adabiyot ×2, english, geografiya |

The adjudicator reliably catches a swapped entity and reliably misses a swapped definition.

**Likely mechanism (hypothesis, not measured):** `inject_mutation` swaps predicate A onto term
B *within the same document*, leaving both predicates present in the extract. Every phrase the
adjudicator sees is therefore genuinely grounded in the source — only the *attachment* is
wrong. Nothing in the prompt directs attention to mis-attachment, so a definitional swap reads
as faithful. If so, this is a limitation of the mutation design as much as of the adjudicator,
and it plausibly extends to the production `phase_judge`, which grades fidelity the same way.
**Worth its own investigation — see WISHLIST.**

**Consequence:** no general drift base rate is quotable. The instrument is demonstrably
sensitive to entity/name contradictions and demonstrably weak on definitional drift, so a
"zero drift found" reading over 31 lessons cannot be trusted for the definition class — which
is exactly the class a language/humanities guard would most need to catch.

## Content finding (independent of the audit, and more important than it)

**The English grade-8 textbook PDF is TRUNCATED.** The file has **104 pages**; its TOC runs to page **157**. Verified: pages 110-126, 127-157, 158+ all yield **0 chars**;
pages 100-109 yield only 2,387.

Consequence: **10 english lessons hold `status='done'` extracts whose source pages are absent
from the file** (an earlier draft said 9 — it counted only ranges yielding zero text and
missed `Magazines and Books` 106-109, which also starts past page 104) — `British TV Around the World` (110-111), `School Can Be Fun!` (112-115),
`Families` (116-119), `Emotional Skills` (120-121), `Review 5` (122-123), `Extra Activities`
(124-126), `Vocabulary List` (127-136), `Grammar Reference` (137-157), `List of Irregular
Verbs` (158-NULL). The extract path reads `read_whole_book_text`, so these were generated
from a book that does not contain their lessons. Same class as the known G10-physics
truncation. **This warrants its own re-ingest ticket regardless of the fidelity question.**

`List of Irregular Verbs` also has `page_end = NULL`, a second defect on the same book.

## What the data shows (NOT certifiable — instrument uncalibrated)

31 lessons audited, 599 `ok` / 3 `contradicts` / 225 `unsupported`, 44 regrounding downgrades.

The two populations behave completely differently:
- **history / geografiya / tarbiya / adabiyot (18 lessons):** `ok` 16-56 per lesson,
  `contradicts` 0-1, `unsupported` ≈ 0. Suggestive of low drift.
- **english (13 lessons):** `unsupported` 7-30 with low `ok`. Consistent with the truncation
  above and with page-range drift near the end of the book — an instrument artifact, not
  evidence of drift.

All 3 `contradicts` are single instances (`cc5005bb` english, `1eab534b` history,
`07bf6161` geografiya) and were not individually adjudicated.

## Limits that stand regardless

- **The cross-language split never fired.** `books.source_language = 'uz'` for an
  English-language textbook, so every lesson was classed same-language and the split
  mandated to separate the two populations was inert. Book metadata defeats it.
- **`languages` = English-G8 only**, from a single truncated book. `ona-tili`, `russian`,
  `alifbe`, `oqish-savodxonligi` have zero extracts in the corpus.
- `tarbiya` (n=2) and `adabiyot` (n=2) are anecdote, not rate.
- The adjudicator is gemini; most audited extracts were produced by gemini. Shared-family
  blind spots are not excluded by this design.
- Audited extracts predate the v3 coverage contract and the 3.x model config.

## Cost

**$3.2483 total** across 44 `agent_usages` rows (459,433 in / 284,348 out, gemini-3.5-flash
@ $1.50/$9.00 per M):
- $0.0422 — single-call auth probe
- ~$2.55 — the 40-lesson base-rate pass (31 audited, 9 skipped)
- $0.6582 — the 8-pair calibration re-run

Approved was ~$2.20 with a stated worst case of ~$2.40 for the main pass, then ~$0.75 for the
calibration pass. The main pass **overran** at ~$2.55: measured $0.0749/call vs the $0.0422
single-call probe, because history lessons carry more source pages than the english lesson
probed. Extrapolating a per-call rate from one lesson in one subject was the error. An earlier
$0.018/call figure was stale — computed before the source window widened from margin=1 to
margin=4.

A prior aborted attempt cost **$0.00** (auth failed on the first call; crash-safe persistence
preserved state and lost nothing).
