# Language-fidelity mechanism probe — BEFORE run

**Date:** 2026-08-07 · **Plan:** `docs/superpowers/plans/2026-08-07-language-fidelity-judge.md`
**Instrument:** `docs/research/2026-08-07-language-fidelity-probe.py` · **Data:** `…-probe-data-before.json`
**Spend:** **$0.8425** (16 judge calls `gemini-3.5-flash` $0.7120 + 3 extracts `gemini-3.5-flash-lite` $0.1305).
The plan estimated ~$0.35 — it under-counted judge OUTPUT tokens by ~2.4× (66k output across 16 calls, ~4.1k each; the judge quotes long evidence blocks). Budget was ≤$2, so this stayed inside it.

## Headline

| limb | verdict |
|---|---|
| **1 — judge caps absent-and-false at `minor`** | **DISPROVEN. Task 1 is SKIPPED per gate rule 3.** |
| **2 — extract starves language lessons** | **CONFIRMED, with a corrected mechanism.** Task 2 and 3 proceed. |

---

## Gate rules, in the plan's prescribed order

### Rule 1 — confound check (`control` arm): PASS, specimen is clean

`major=False` in 3/3 replays. The only findings were `minor`: an untagged misconception provenance (all 3 replays), English headings left un-localized (2 of 3), the `### Mode: Hard` line (2 of 3), and one "don't bundle two phrasal verbs on one card" nit. No pre-existing major, so no arm is confounded and no specimen swap was needed.

**Bonus 0159-p2 signal, as the plan predicted:** card_9's claim that rescue dogs find people under rubble — absent from the extract, true of rescue dogs generally — was **not** flagged at any severity in any replay. The absent-but-TRUE exemption is working.

### Rule 2 — instrument check (`contradiction` arm): PASS

`major=True` in 3/3, and every one cites the source contradiction explicitly, quoting the lesson context back: *"This directly contradicts the LESSON CONTEXT: 'The connectors when (before past simple) and while (before past continuous) are practiced.'"* Replay #1 raised a second major for the example sentence inverting the source's own example pattern.

**Impurity check (plan's known-impurity #1):** the failure text cites the **source contradiction**, not intra-deck inconsistency with surviving cards 1 and 3. The feared channel did not fire. The instrument measures what it claims.

### Rule 3 — limb 1 (`absent_false` arm): FIRES 3/3 → **SKIP TASK 1**

The duck card — glossing *duck* as a burrowing rodent, a word the extract names in its animal list but never defines — was majored in **3 of 3 replays**, under the **current, unmodified `_FIDELITY_RULE`**. The failure text asserts the gloss is false on world knowledge, exactly the definition gate rule 3 requires:

- replay #0: *"A duck is a waterbird (o'rdak in Uzbek), not a burrowing rodent."*
- replay #1: quotes the duck card under the source-fidelity heading.
- replay #2: *"A duck is defined as a small burrowing rodent, which is factually incorrect (it is a bird)."*

None of the three is the excluded omission-of-`turn on / turn off` major — that concept is never mentioned. The exclusion clause added after review round 2 was necessary to make this judgement possible, and it cleanly separated the cases.

**Therefore the plan's headline premise is false as stated.** `_FIDELITY_RULE` does say a source-absent, non-contradicted world claim is "at most `minor` — never `major`" (`phase_judge.py:82-84`), and read literally that covers a wrong gloss. But the live judge does not follow the text that way: `_INSTRUCTIONS` §4's `major` = "wrong or missing content" (`phase_judge.py:61-65`) dominates in practice, and the model reaches for it — twice explicitly invoking real-world falsity, once stretching the contradiction clause to cover it. **Fabricated content that is demonstrably false is NOT structurally un-regenerable.** Reading a prompt is not the same as measuring a model; this is what the gate existed to catch.

What remains true and unmeasured: a fabrication that is *plausible* rather than *demonstrably false* — an invented example sentence a native speaker would not say, a subtly-off register, a gloss that is defensible but not the source's — stays `minor` by the same rule, and the probe did not test that class. It is also the class the extract limb prevents rather than detects.

### Rule 4 — regression baseline: clean

`math-g11-prizma` and `geo-g10-braziliya`: `major=False` in 3/3 each. Findings were `minor` apostrophe-consistency and markdown-header nits. This is the `before` half of the 0159-preservation measurement; with Task 1 skipped, no judge change ships, so there is nothing for the `after` half to regress against. Recorded for the file.

### Rule 5 — limb 2 (extract): CONFIRMED, mechanism corrected

| specimen | headings emitted | chars |
|---|---|---|
| `english-g8-families` | Concepts & terms · Rules & theorems · Worked-example types · Key facts | 1,407 |
| `english-g8-vocab-list` | Concepts & terms · Key facts | 5,558 |
| `adabiyot-g9-alpomish` | Concepts & terms · Key facts | 1,496 |

**The plan said the extract has "nothing to build from but invention". That is too strong and is now corrected.** What actually happens:

1. **The words survive; the meanings do not.** "Families" emits `aunt, cousin, grandchild, granddaughter, grandfather, grandmother, grandparent, grandson, nephew, niece, uncle` under `## Concepts & terms` — a bare word list with **no glosses**. A generator whose contract demands `vocabulary` cards as "L2 word → L1 meaning" (`prompts.py:372`, `_FC_LANGUAGES`) receives the left-hand side and must invent every right-hand side. Same for adabiyot, whose archaic terms (`Shabgir tortish`, `Zinkiyib`, `Dabgiridan adashib`) are precisely the words a student needs defined.
2. **Source sentences vanish entirely.** No specimen emitted any verbatim model sentence. The pre-0119 free-form extract for this same lesson carried four ("Jana plays the piano really well," etc.); the coverage-contract extract carries none. So `_CBP_LANGUAGES`'s "do not author a fresh passage when the textbook has one" is unenforceable — the textbook's passage never reaches the generator.
3. **The coverage-contract extract is a regression for language lessons.** Old free-form "Families" extract ≈ 3.0k chars including glossed vocabulary, adjective lists and example sentences; the current one is 1,407 chars and drops the appearance-adjective set (`beautiful, blonde, dark, fair, good-looking, old, pretty, short, slim, tall, young`) altogether.
4. **Lexis is landing in the wrong heading.** All three specimens dump vocabulary into `## Concepts & terms`, which is supposed to carry the ideas the lesson explains. The plan's disambiguation line ("Concepts & terms carries the IDEAS; Vocabulary carries what the student must be able to USE, with meanings") is therefore load-bearing, not decorative.

Rule 5's flag condition — "if all three specimens already carry a **full glossed** vocabulary inventory, flag before Task 2" — is **not** met: the inventories are unglossed. Task 2 proceeds.

**Extract-growth watch (Approach bullet's decision rule).** `english-g8-vocab-list` is already **5,558 chars before any change** — a reference appendix listing 400+ words across every unit. Adding a gloss per item could multiply it several-fold, against a 12,000-char threshold. This specimen is the plan's designated worst case and is now expected to be the binding constraint in the `after` run.

---

## Consequences for the plan

- **Task 1 (judge rule): SKIPPED.** Gate rule 3, 3/3. No change to `_FIDELITY_RULE` ships.
- **Task 5 Step 0 now applies:** `scripts/smoke_judge_fidelity.py` is still stale (pre-0159 expectation, retired cli transport) and must be re-anchored there, since Task 1 is not running.
- **Tasks 2 and 3 proceed unchanged**, with the mechanism statement corrected from "nothing to build from" to "the words without their meanings, and never the source sentences".
- **Task 4's judge criteria drop**; its regression gate is moot. What remains is the extract before/after and the live language packet.
- **The un-probed class stands as the honest residual:** plausible-but-invented content (idiomatically odd example sentences, defensible-but-not-source glosses) is still `minor`-only. The extract limb addresses it preventively; nothing detects it. This strengthens the case for the filed `language-drill-solver-gap-1`.

---

# AFTER run — extract limb

Judge arms were not re-run (`--extract-only`): no judge change shipped, so they cannot move. The `before` judge rows are carried forward in the JSON and the file records `extract_only: true`.

## Result: acceptance criterion 1 MET

| specimen | before | after | headings gained |
|---|---|---|---|
| `english-g8-families` | 1,407 ch | 2,274 ch | Vocabulary & set phrases · Source sentences & passages |
| `english-g8-vocab-list` | 5,558 ch | 1,720 ch | Vocabulary & set phrases (and now capped) |
| `adabiyot-g9-alpomish` | 1,496 ch | 4,963 ch | Vocabulary & set phrases · Source sentences & passages |

What the generator now receives that it did not before: a **glossed** family-vocabulary list *including the appearance adjectives the coverage contract had been silently dropping*, the reading text's actual sentences, and for the *doston* lesson the textbook's own footnote glossary plus verbatim verse lines.

## Three defects the after-run exposed, all fixed and re-measured

1. **Uncapped growth.** The end-of-book reference word-list produced **34,104 chars** — over the plan's 12,000 decision threshold, and injected ~22× per job. Capped at 40 bullets + `(+N further items in the source list)` → **1,720 chars**. Note the honest cost: on that specimen the "meanings" degrade to part-of-speech labels, because the source genuinely has none. A `Vocabulary List` TOC row is a reference appendix, not a lesson; this is a degenerate specimen and the cap is the right trade.
2. **Duplicated glosses.** `item — meaning (uz. *meaning*)` doubled the section for no information. Format is now exact and the repeat forbidden.
3. **Unmarked supplied meanings — the one that matters.** The model supplied dictionary definitions without marking them. This **inverts the fidelity guard**: `_FIDELITY_RULE` makes the judge treat LESSON CONTEXT as ground truth, so a gloss the *extract* invented is enforced against the generator as if the textbook had said it. The contract now requires `[not in source]`. Verified discriminating correctly on adabiyot — real textbook footnote glosses unmarked, the one supplied term marked. A live example of why this matters: the after-run glossed `dark` as *"reflect a lot of light"* (backwards). Marked, so it reads as supplied rather than textbook-authoritative.

## A regression this lane introduced, caught by measurement

The two new headings carried forceful `REQUIRED whenever` clauses; `## Key facts` carried none. On the literature specimen the model spent its budget on vocabulary + verse and **dropped `## Key facts` entirely — 3/3 runs, i.e. not variance**, losing the Alpomish lesson's dates, genealogy and the terms of the plot: its examinable content. `## Key facts` now carries the same REQUIRED clause; 3/3 runs restored it.

Worth naming as a method point: with n=1 per condition I could not have told this from noise, and the natural reading ("the change also made adabiyot shorter and tighter") was wrong. Three repeats cost ~$0.08 and turned a guess into a fact.

## Spend

**$2.0727 for the whole lane** — 32 judge calls $1.4265 + 18 extract calls $0.6462. Over the plan's ~$0.35-per-run estimate: judge output tokens were ~2.4× what I assumed, and the extract contract needed three measured iterations rather than one shot.

**Attribution caveat:** a naive "last 95 minutes" query over `agent_usages` also picks up `lesson.extract.coverage` rows emitting an `ExtractCoverageVerdict` schema that exists in **neither this worktree nor the main checkout** — another session is running its own lane against the same `edu_copy` database. Those rows are excluded above. Anyone re-deriving this figure must filter by operation, not by time window alone.
