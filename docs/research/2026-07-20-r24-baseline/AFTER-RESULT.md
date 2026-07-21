# R24 T7 — after-result: the core-first depth trade did NOT work (negative result)

**Bottom line: the intervention failed its own success criterion.** Long-specimen core-learned
**fell 33% → 18%**, and packet coverage **regressed 94% → 67% taught**. The change made packets
*less comprehensive* without making them *more learnable*. **Do not ship the T3–T5 prompt changes.**

Measured on the same T1 instrument, the 4 pinned specimens regenerated on the new prompts
(scratch DB `edu_scratch_r24t6`, fresh process — prompt-freshness asserted at load AND confirmed in
output: every regenerated extract emits `## Core objectives`; the judge enforced the new depth rule
and regenerated thin first attempts). After-jobs pinned in `after-jobs.json`. T6 regen $2.73, T7
audit $0.79.

## The numbers (before → after)

| specimen | span | core-learned before → after | full-learned | core-learnable |
|---|---|---|---|---|
| L1 g8 Xorazmshoh | 15pp | 1/4 → **0/4** | 1/5 → 0/5 | NO → NO |
| L2 g8 Amir Temur | 11pp | 2/4 → **1/3** | 3/5 → 2/5 | NO → NO |
| L3 g10 Buxoro | 12pp | 1/4 → **1/4** | 1/6 → 1/5 | NO → NO |
| **long total** | — | **4/12 (33%) → 2/11 (18%)** | 5/16 → 3/13 | 0/3 → 0/3 |
| S1 g8 control | 3pp | 3/3 → **2/2** | 4/4 → 3/4 | YES → YES |

Sensitivity still valid (S1 paired): `sensitivity_pass=True` before and after; the empty control
learned 0 after — so the drop is real packet behaviour, not instrument drift.

## Why it failed — the mechanism, and it's structural (not noise)

**Coverage regressed hard on exactly the specimens we tried to fix.** Long-specimen objective
coverage:

| | taught | mentioned | absent | taught% |
|---|---|---|---|---|
| BEFORE | 15 | 0 | 1 | **94%** |
| AFTER | 10 | 3 | 2 | **67%** |

The before-packets taught nearly everything the examiner tested (comprehensive but shallow — the
original R24 finding). The after-packets **stopped covering a quarter of the tested objectives**,
producing new `not_taught` failures — while the surviving core objectives stayed `not_learnable`
just as before. So the trade spent coverage and bought no learning.

**Root cause — the anti-circularity that makes the audit valid is the same property that makes the
fix unsafe.** The audit examiner derives its objectives from the **textbook, independently**, and
never sees the extract (this is load-bearing — it's what makes the measurement non-circular). The
generator selects **its own** `## Core objectives` from the same lesson, also independently. These
two core-lists **do not align**. So instructing the generator to "concentrate on *your* core and
cut the periphery" systematically drops material that the examiner — standing in for a real exam —
happens to test. The generator cannot see what the real exam will consider core, so cutting on its
own judgement of "core" is cutting blind.

The depth half of the hypothesis also did not hold: the judge **enforced** multiple-angle depth per
core objective (it regenerated L3's flashcards and S1's tictactoe for failing the new rule), so the
after-packets genuinely carry more angles per core objective — and the simulated student **still**
did not learn them (`not_learnable` unchanged). More cards on the same objective did not convert
coverage into learning at this level.

## Confidence and caveats

- **n=4, single audit each, and before/after use different independently-derived exams** (examiner
  non-determinism). Individual borderline verdicts flip between runs — established property.
- **But the load-bearing signal is structural, not a borderline flip:** the 94%→67% coverage drop
  is a change in what the packets *contain*, consistent across all three long specimens and
  mechanistically explained. That is not plausibly noise. The core-learned point estimate (33%→18%)
  is noisier on its own; it is believed because it moves *with* the coverage regression, not against
  it.
- The deterministic wide-check (`2026-07-20-teaching-audit-drill-density.py`, R24 gate condition 3)
  **cannot** validate or refute this without a production rollout of the new prompts — it reads
  `status='done'` production jobs, which are all still old-prompt. Re-running it now reproduces the
  baseline. A rollout to make it measurable is exactly what this negative result says NOT to do.

## Disposition

- **KEEP (independent of the failed fix):** T1 (audit objective-tiering — a genuine instrument
  improvement) and T2 (the baseline). These make the *measurement* better and stand on their own.
- **DO NOT SHIP:** T3 (`## Core objectives` in the extract), T4 (flashcards depth trade), T5
  (memory-check/boss-arena core anchor). They did not improve learnability and they regressed
  coverage. Shipping them would trade the one thing the packets currently do well (broad coverage)
  for nothing.
- **R24 stays OPEN.** The plan anticipated this exact branch: "if the tier doesn't survive contact
  with the model, T7's data points cleanly at shape (3) — chunk long lessons." The data now says:
  concentrating a fixed budget is not the fix; and cutting periphery is actively harmful given the
  generator/examiner independence. The live candidates are (3) **chunk long lessons** so each piece
  gets a full budget without cutting anything, or a re-examination of the contract (homework as a
  supplement rather than sole teacher). **This is a product decision, not an engineering one — it
  belongs at a plan gate.**

## Files
- `after/L*.json`, `after/S1-*.json` — the 4 after-audit reports (full evidence chain, sha256s).
- `after-jobs.json` — specimen → regenerated job_id map (scratch DB).
- Baseline ("before"): `README.md` + `L*.json` / `S1-*.json` in the parent dir.
