# R24 — Core-first teaching: make long fact-dense packets actually teach

Closes ROADMAP **R24**. Backing evidence: `docs/research/2026-07-20-teaching-audit-drill-density.{md,py,json}`
(deterministic, 1,362 packets, $0) + the teaching audit (`app/services/teaching_audit.py`, worklog 0148/0152).

---

## Approach & key decisions

**The learnability contract — decided (was the open gate question).** The bar is **tiered**:
a packet must **teach the lesson's core** (the 3–6 things an exam would test) to a student with
only prior-grade knowledge and nothing but the packet, and must **represent** the periphery
(coverage, as today) without being required to teach it from scratch.

- *Rejected — "packet alone teaches everything."* This is the audit's current contract and it is
  **unachievable by construction**, not merely ambitious: see the budget finding below. Holding it
  would force fix shape (1), which the phase formats block.
- *Rejected — "reinforces the core the teacher covered."* Unmeasurable: we have no model of what
  any teacher covered, so in practice this degrades into no bar at all and R24 could never be
  proven closed.
- The tiered bar is the only one that is simultaneously **achievable** and **measurable with the
  instrument already built**.

**Root cause, refined this session — sharper than R24 records, and it eliminates a fix shape.**
R24 says drill items are flat at ~210. Reading the prompts shows *why*, and that 210 is the wrong
unit: the **teachable-atom budget is fixed by format, with no lesson-size term anywhere**.

| Where | Budget | Scales with lesson size? |
|---|---|---|
| `prompts/_general/flashcards.md:15-17` | G5-6 → 6-8 · G7-8 → 8-10 · G9-11 → 10-12 cards | no — grade only |
| `prompts/_general/memory-check.md:16` | 8–12 items, each keyed to a card | no |
| `prompts/_general/boss-arena.md:20` | 4–6 questions | no |
| the four `_GAMES` | structurally capped (a 3×3 tic-tac-toe board is 9 cells) | **cannot** |

So an 82-fact history lesson and a 25-fact one both get ~12 cards. The ~210 items my regex counted
are mostly *sub-fields* (`explanation`/`example`/`misconception` lines) of those same ~30 atoms —
which is why item count looked flat while facts doubled. **Fix shape (1) "scale drill volume" is
blocked, not just impractical** — you cannot lengthen a tic-tac-toe board. This independently
confirms shape (2), *prioritise within the budget*.

**There is also an R14-class prompt self-conflict.** `flashcards.md:3` orders the model to "extract
**every** key term, name, structure, process, rule, formula, and classification term ... that
matters", while `:15-17` caps it at 6–12 cards. On a 25-fact lesson both can be satisfied; on an
82-fact lesson the model must silently drop ~85% of the material **with nothing telling it what to
keep** — so it spreads thin. That is the observed failure, and it is a missing *signal*, not a
missing *budget*.

**Where the signal comes from.** The extract is currently a **flat** enumerated contract
(`_CONTRACT_INSTRUCTIONS`, `app/services/agent.py:2379`) — every bullet equal weight. Adding a
`## Core objectives` tier there is a **single change point that reaches all 11 phases**, because
every phase receives the extract as `lesson_context`. No per-phase re-plumbing.

**Anti-circularity is load-bearing and preserved.** The audit examiner (`build_exam_prompt`,
`teaching_audit.py:387`) derives objectives from **textbook pages only** and never sees the
extract. The generator's core list and the examiner's core list are therefore derived
*independently*; their agreement is the **result being measured**, not an assumption. If the
extract ever fed the exam, the entire measurement becomes circular and worthless.

**Ordering is methodologically load-bearing.** The instrument changes **first** (T1), the frozen
specimens are re-baselined with the tiered examiner (T2, the "before"), and only then do the
generation prompts change (T3–T5). Changing the exam and the packets in the same step would make
before/after uninterpretable.

**Costs, stated up front (money rule).** T2 re-baseline ~$0.80 (4 packets × ~$0.20).
T6 regeneration ~$3.50 (6 packets × ~$0.58). T7 re-audit ~$1.20. **Total ~$5.50**, bounded,
no mass generation. The `prompt_hash` bump `builtin:extract:v3` → `v4` (`pipeline.py:976`)
deliberately invalidates cross-job extract reuse — every lesson re-extracts once, at
gemini-2.5-flash rates. Accepted and cheap; called out so it is not discovered as a surprise.

**Cross-plan collision check.** The only in-flight lane is the worktree `../HCGA-gemini-global`
(`feat/gemini-global-default`, `f82bdc2`) which touches `api_transport._location_for` — no overlap
with prompts, extract, or the audit. No serialisation needed.

**CQ-E obligation.** `prompts/_general/*` changes invalidate the frozen golden-set baselines
(worklog 0113/0120). T7 re-runs CQ-E and re-freezes, or records why a shift is expected.

---

## Tasks

Each task: TDD (test first, RED-proved), one commit, controller stress-tests the diff **and**
re-runs the tests before the next task starts.

### T1 — Audit examiner tiers objectives (instrument only, no generation change)

- `app/services/teaching_audit.py`: `ExamSpec` objectives gain `tier: Literal["core","supporting"]`.
  `build_exam_prompt` step 2 additionally asks the examiner to mark the **3–6 objectives the lesson
  is centrally teaching** as `core`, the rest `supporting` — **still derived from the textbook
  window only**. `aggregate` reports `core_learned` / `core_total` alongside today's totals; the
  verdict keys to **core**, with the full-set number retained as a reported figure.
- Tests (`tests/services/test_teaching_audit.py`): tier defaults and validation; an exam with zero
  core objectives fails loud (`TeachingAuditError`) rather than passing vacuously — **RED-prove it
  by mutating the guard**; `aggregate` core arithmetic; the existing zero-objective offset guard
  still fires.
- **No prompt under `prompts/` is touched in this task.**

### T2 — Re-baseline the frozen specimens with the tiered examiner ("before")

- Specimen set, frozen here so before/after compares identical lessons: G8 *Muhammad
  Xorazmshohning…* (the reproducible 0/17), two further long-history packets, and one **short**-history
  packet from the same books as the control.
- **Gate condition 1 — pin the job UUIDs.** The selection of "two further long-history packets" is
  resolved *once*, at T2, and the chosen **job IDs are written into the committed baseline dir**
  (`specimens.json`: job_id, book_id, toc_entry_id, lesson title, page span). T6 regenerates and T7
  re-audits **those exact job IDs**. Before/after integrity depends on identical jobs, not on
  re-resolving a name.
- Run `scripts/teaching_audit.py --job <id>` per specimen; persist raw JSON under
  `docs/research/2026-07-20-r24-baseline/` — **committed, not `/tmp`** (the $0.70 lesson).
- Deliverable: a before-table of `core_learned/core_total` per specimen. ~$0.80, reported.

### T3 — Extract emits a `## Core objectives` tier

- `app/services/agent.py` `_CONTRACT_INSTRUCTIONS`: new **first** section `## Core objectives` —
  3–6 bullets naming what the lesson centrally teaches (what an exam would test), *before* the
  existing enumerated headings, which stay exactly as they are (coverage must not regress).
- `app/services/pipeline.py:976`: `builtin:extract:v3` → `v4`.
- `app/services/content_lint.py`: add `("core_objectives", ("core objective",))` to
  `_CONTRACT_SECTION_NEEDLES`, plus a **warn-only** `core_uncovered` finding when a core item is
  absent from the packet (warn-only per the standing quality bar — hard gates only for wrongness).
- Tests: `parse_extract_contract` picks up the new section; `core_uncovered` **bites** (RED-proved,
  not vacuous); **Gate B (`agent.py:1425` `contract_has_items`) still passes** when `## Core
  objectives` is the first heading — verify, don't assume.

### T4 — Flashcards: resolve the self-conflict, allocate core-first as a DEPTH TRADE

**Reframed after T2 (approved within shape (2), 2026-07-20).** The baseline proved a floor-rule
("≥1 card per core objective") is a **no-op**: every core objective already scored
`coverage=taught`, and 8 of 12 were still `not_learnable`. The long packets fail on **depth, not
coverage**. So T4 is a **trade**, not an addition: peripheral breadth is spent *down* so the fixed
~12-card budget lands on the **same** core objectives **repeatedly, from multiple angles**
(definition → application → misconception → contrast), which is what moves an objective from
"present" to "learned".

- `prompts/_general/flashcards.md`:
  - Replace the "extract **every** key term…" instruction (`:3`) with an explicitly *selective*
    one keyed to the extract's `## Core objectives`.
  - Add the **depth-trade allocation rule**: the card budget is spent to give each core objective
    **multiple cards attacking it from different angles**, and peripheral detail is **cut** to buy
    that depth — not "one card each then move on". When the lesson's material exceeds the band, drop
    periphery, never core depth. Grade bands at `:15-17` unchanged (the budget is fixed; only its
    *allocation* changes).
- **Reconcile the anti-concentration conflict (mandatory — same R14 class the plan diagnoses).**
  The `:123` self-check carries "no micro-skill exceeds ~1/3 of cards" and "the deck covers ≥3
  sub-skills". Deliberately concentrating ~12 cards on 3–4 core objectives is exactly what those
  rules forbid, so the model must not be left to arbitrate two contradictory instructions.
  **Resolution:** those anti-concentration caps are **scoped to the periphery** — they govern
  *supporting* material (their original purpose: stop one incidental micro-skill crowding out the
  lesson) and **do not apply to core objectives, which are intended to be drilled repeatedly**.
  Rewrite the `:123` self-check and the sub-skill lines (`:59-66`) to say this explicitly (e.g.
  "no *supporting* micro-skill exceeds ~1/3 of the *peripheral* cards; core objectives are exempt —
  depth on core is the goal"). Do NOT simply delete the caps — periphery still needs them.
- Test: prompt-text assertions are weak by nature — assert (a) the "extract every key term" phrasing
  is gone, (b) the depth-trade/multiple-angles clause is present, (c) the anti-concentration cap is
  re-scoped to periphery and no longer reads as an unconditional deck-wide cap. The **real** proof
  is T7.

### T5 — Memory-check and boss-arena anchor to core (depth, not breadth)

- `prompts/_general/memory-check.md:16`: items must trace to cards carrying **core** objectives, and
  the 8–12 items concentrate on the core rather than sampling every card once.
- `prompts/_general/boss-arena.md:20`: the 4–6 questions must target core objectives (it already
  targets "skills flagged as weak"; make core the primary axis) — repeated core exposure from a
  reasoning angle, complementing flashcards' recall angle.
- Tests as T4.

### T6 — Regenerate the frozen specimens on the new prompts

- Re-run generation for the T2 specimen jobs (**the pinned UUIDs**), `transport=api`. ~$3.50,
  reported. Bounded to the frozen set — no sweep.
- **Gate condition 2 — prompt-cache freshness.** `prompts/_general/` is cached in-process (standing
  repo lesson: restart workers after prompt edits). The regeneration must **demonstrably** load the
  T4/T5 text: either restart the worker or run in a fresh process, and **state in the worklog which
  was done**. A regeneration served from a stale cache would silently measure the OLD prompts and
  produce a false negative for the whole plan.

### T7 — Acceptance: re-audit, compare, close

- Re-audit the regenerated specimens with the **same T1 examiner**; produce the after-table.
- **Success criterion:** long-history core-learned reaches parity with the short-history control
  (the control is the load-bearing comparison — same books, same authors, span the only variable),
  and the G8 0/17 specimen moves off zero. A rise in *total* learned that leaves core flat is
  **not** success.
- **Gate condition 3 — re-run the deterministic script** (`docs/research/2026-07-20-teaching-audit-drill-density.py`,
  $0) over the post-change corpus as a scheduled acceptance step, not merely a mentioned risk. It is
  the instrument that found R24 and it is the only wide check against the 4-specimen sample; report
  the items/fact figures before and after.
- Re-run CQ-E golden-set; re-freeze baselines or record the expected shift.
- Full suite green; write `docs/research/2026-07-20-r24-after.md`, worklog entry (**re-check the
  INDEX tail at finish — 0153 is the current tail, but numbers go stale between write and merge**),
  close R24 in ROADMAP, `git mv` this plan to `shipped/`, de-stale `docs/HOW_IT_WORKS.md` +
  `docs/CODE_MAP.md` for the tiered contract.

---

## Risks

- **The tier may not survive the model.** The extract model may emit a `## Core objectives` section
  that is just the first 6 bullets of the flat list. T2→T7 measures whether it helped; if core-learned
  does not move, the answer is shape (3) *chunk long lessons*, and T7's data says so cleanly.
- **Prioritising is a deliberate coverage trade.** Peripheral facts get less drill by design. The
  `coverage_thin` lint will likely fire more often — that is the intended trade, not a regression,
  and it stays warn-only.
- **n is small (4 specimens).** Bounded by the money rule. The deterministic script re-runs at $0
  over the whole corpus post-change and is the wide check.
- **Literature (`adabiyot`) remains untested** (n<3 done jobs) — the other fact-dense subject.
  Out of scope here; stays on the wishlist.
