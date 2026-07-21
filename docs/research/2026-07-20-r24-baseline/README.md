# R24 T2 — tiered-contract baseline ("before")

The frozen before-measurement for the core-first change. Every specimen is pinned by **job UUID**
in `specimens.json`; T6 regenerates and T7 re-audits **those exact jobs**.

Instrument: `app/services/teaching_audit.py` at commit `daaab6b` (T1 — objectives tiered
`core`/`supporting` by the examiner, from the textbook window only; verdict keys to the core subset).
Run with `scripts/teaching_audit.py`, `transport=api`, examiner `gemini-2.5-pro`,
student `gemini-2.5-flash`. **Total spend $0.8163.**

## Result

| specimen | span | core learned | full learned | core teaching-equiv | core learnable | $ |
|---|---|---|---|---|---|---|
| L1 g8 Xorazmshoh | 15pp | **1/4 (25%)** | 1/5 | YES | **NO** | 0.2189 |
| L2 g8 Amir Temur | 11pp | **2/4 (50%)** | 3/5 | YES | **NO** | 0.2221 |
| L3 g10 Buxoro/Qo'qon | 12pp | **1/4 (25%)** | 1/6 | YES | **NO** | 0.2042 |
| **long subtotal** | — | **4/12 (33%)** | 5/16 | — | 0/3 pass | 0.6452 |
| S1 g8 XIII asr (control) | 3pp | **3/3 (100%)** | 4/4 | YES | **YES** | 0.1711 |

L1, L2 and S1 are from the **same book, same grade, same authors** (`6129e3df`, 8-sinf
O'zbekiston tarixi 2023) — span is the only variable. L3 is a different book and grade, so the
result is not an artifact of one book's style.

## Instrument sensitivity — validated on the load-bearing specimen

S1 ran paired (`--sensitivity`). The short control is exactly where a *good* score must be proven
not to be latent-knowledge leakage, since the whole long-vs-short comparison rests on it:

- real packet → 4/4 objectives learned
- **empty control packet → 0/4 learned, all four `coverage=absent`**
- `sensitivity_pass=True`, zero failures

So the 100% on S1 is the packet teaching, not the simulated student already knowing.

## The finding this baseline sharpens

**The long packets do not fail on coverage. They fail on depth.**

Every core objective on all three long specimens carries `coverage=taught` — the material is
present in the packet. Yet `core_learnable=False` on all three: 8 of 12 core objectives sit at
`not_learnable` (present, drilled, still not absorbed). Only one objective anywhere scored
`not_taught` (L3 O4, a `supporting` one).

This **refines the fix, and rules out the naive version of it**: an allocation rule that merely
guarantees "at least one card per core objective" changes nothing, because core objectives already
get cards. What the data demands is that prioritisation **buy depth** — peripheral breadth must be
spent down so the fixed ~12-card / 8–12-item / 4–6-question budget lands repeatedly on the core.
T4/T5 must be written as a *trade*, not as an *addition*.

The tiered contract also did not simply lower the bar into a pass: **all three long specimens fail
it too.** A bar the broken cases still fail is measuring something real.

## Limits

- **n=1 short control.** The 100% is one specimen; the earlier untiered sweep had 4 short vs 6
  long (78% vs 32%), consistent in direction. Do not quote 100% as the short-lesson rate.
- Objective sets are re-derived per run, so before/after compares **rates**, not objective ids.
  L1's historical "0/17 across 3 runs" and today's 1/5 are the same picture at different n.
- Borderline objectives (post 1.0–1.4/2) flip between runs; the signal is the hard-fails and the
  long-vs-short gap, not any single verdict.

## Files

- `specimens.json` — the frozen set (job UUIDs, book ids, page spans, selection rationale)
- `L1-*.json`, `L2-*.json`, `L3-*.json` — single-leg reports (5 calls each)
- `S1-*.json` — paired report (7 calls): `normal` + `control` + sensitivity verdict

Each report embeds the exact textbook excerpt and the exact study document with sha256s, plus the
full evidence chain (exam, keys, both sittings' answers, grades, coverage) — so a human can audit
the audit without the source PDF or the packet outputs being unchanged.
