# English Target-Language Handling — Design Spec

**Status:** Approved design (brainstormed 2026-06-01), reviewer pushes folded in. Ready for an implementation plan.
**Motivation (live defect):** after the Path A general-prompts MVP, every `_general` prompt hardcodes formal Uzbek ("Siz"). An `english` (L2) lesson therefore generates **Uzbek prose about English** — pedagogically wrong. Path A §3.3 deliberately deferred English-target handling ("acceptable as-is for the MVP"); that judgment was wrong for English and is now being fixed.
**Scope:** prompts + resolver + tests. **No schema change, no flow change.** Smaller than Path A.
**Live anchors:** `app/services/prompts.py` (`get_prompt` does the `{{SUBJECT}}` replace at ~line 67; `SUBJECT_LABELS`), the 7 `prompts/_general/*.md`. Proven model source: the untouched `prompts/english/*` subject prompts ("student-facing English; UZ bridge uses formal 'Siz'"; "keep language at the unit's CEFR level").

---

## 1. Goal & non-goals

**Goal.** Make `english` lessons generate **English target-language content with an Uzbek scaffolding/bridge, CEFR-leveled**, while every other subject keeps formal-Uzbek output — via a loader-side `{{LANGUAGE_RULES}}` substitution (no model-side `if subject==English`).

**Non-goals.**
- No schema or flow change. No new phases. No re-adding the English `reading` phase (see §8).
- No language *detection* subsystem and no CEFR field on the job (we removed `classify`); CEFR is inferred from the source per §4.
- Does not touch `prompts/<subject>/*` (read-only references).

---

## 2. Decision (locked)

Loader-side `{{LANGUAGE_RULES}}` substitution-time block, parallel to `{{SUBJECT}}`. Two blocks: an Uzbek default (all non-English subjects, unchanged behavior) and an English-target block. The **language split** — **English for the thing being learned, Uzbek ("Siz") for everything that helps learn it** — is the proven model from the existing `prompts/english/*` prompts, lifted verbatim (`english/flashcards.md:90,94`, `english/case-based-preview.md:57`). The **CEFR leveling is retained in spirit, but its grade→CEFR derivation is NEW** — the old pipeline detected CEFR via the `classify` phase, which Path A removed — so the ladder in §4 is an assumption to validate, not inherited fact (the §6 smoke is its gate).

---

## 3. Mechanism (`app/services/prompts.py`)

Add the blocks + a map, and one substitution line in `get_prompt` (mirrors the existing `{{SUBJECT}}` replace):

```python
LANGUAGE_RULES = {"english": _LANG_ENGLISH, "_default": _LANG_UZBEK}

# inside get_prompt(subject, phase_name, provider_suffix=""), after the {{SUBJECT}} replace:
body = body.replace("{{LANGUAGE_RULES}}",
                    LANGUAGE_RULES.get(subject, LANGUAGE_RULES["_default"]))
```

`get_prompt_hash` is unaffected (it hashes the raw file before substitution — provenance only). No signature change.

---

## 4. The two language blocks (constants in `prompts.py`)

**`_LANG_UZBEK` (default — every non-English subject; same as today):**
> All student-facing text in natural, formal Uzbek ("Siz", never "sen"). Preserve every term, formula, number, unit, and symbol exactly as in the source. Modern professional (non-bazaar) contexts.

**`_LANG_ENGLISH` (the `english` subject):**
> This is an **English (L2)** lesson for native-Uzbek learners.
> **Governing principle: the thing being LEARNED is in English; everything that HELPS them learn it is in Uzbek ("Siz").**
> - In **English**: the target vocabulary, example sentences, passages/texts, collocations, grammar items, and the actual things the learner must read/produce.
> - In **formal Uzbek ("Siz")**: all scaffolding — task instructions, framing, hints, explanations, feedback, and the DPE/reasoning prompts (the "UZ bridge" that lets a beginner follow).
> - **CEFR leveling (A1–B2):** if the source/lesson reference shows a grade (e.g. "Grade 7"), level the English to it via grade→CEFR — G5→A1, G6→A1+, G7→A2, G8→A2+, G9→B1, G10→B1+, G11→B2; otherwise infer the level from the source's own complexity. CEFR controls sentence length, tenses, and vocabulary range. Do not exceed the level (no B2 vocabulary in an A1/G5 lesson).

> **⚠ The grade→CEFR ladder above is a NEW derivation, not inherited.** The old english prompts detected CEFR via `classify.md` (removed in Path A); this replaces that detection. The 7 values reuse the exact CEFR levels the old prompts used (A1·A1+·A2·A2+·B1·B1+·B2), mapped one-per-grade G5–G11 (monotonic, no plateau). Treat the values as an **assumption to validate** — the §6 smoke's CEFR check is the gate, and the mapping is worth a sanity-pass with whoever owns the curriculum before it's relied on.

These are guidance strings injected verbatim; keep them tight (a few lines each) to protect the token budget.

---

## 5. Prompt edits (`prompts/_general/*.md`, 7 files)

Replace each prompt's hardcoded language directive with the `{{LANGUAGE_RULES}}` token. **Per-file, not one find/replace:** 4 prompts (`case-based-preview`, `boss-arena`, `practice-error-detection`, `practice-rlc`) carry a `## Language` block / sentence; 3 (`flashcards`, `memory-check`, `reflection`) carry a one-line `- Language: …` bullet. Replace whichever form with `{{LANGUAGE_RULES}}` (keep the surrounding heading where one exists).

**Per-phase hint for the genuinely ambiguous case — flashcards.** The governing principle resolves most phases, but "which side is English" on a card is ambiguous. Add one English-conditional line to `prompts/_general/flashcards.md`:
> For an English (L2) lesson: the card **front** is the English target item (word / phrase / structure); the **back**, `hint`, and `explanation` are the Uzbek bridge (gloss / meaning / usage note). For all other subjects, both sides follow `{{LANGUAGE_RULES}}` (Uzbek).

No other phase needs a per-phase hint (the governing principle covers them: memory-check → English options/items, Uzbek prompt+reason+why; CBP → English being tested, Uzbek case/feedback; boss-arena → Uzbek Why/How/What reasoning about the English point; rlc/error-detection → analogous). 

**Embedded Uzbek example strings stay Uzbek.** `practice-rlc` (`prediction_prompt`/role examples) and `reflection` (literal Uzbek output examples) contain hardcoded Uzbek — those are *scaffolding examples* and remain Uzbek even for English. The `{{LANGUAGE_RULES}}` token governs only the output-language policy statement; it does not conflict with them.

**Path B note:** when Path B authors the 4 CBP-mode game prompts, they must include the `{{LANGUAGE_RULES}}` token too (this workstream sets the convention).

---

## 6. Tests

- **Resolver** (`tests/services/test_prompts_resolver.py`, extend): `get_prompt("english", phase)` → contains the English block markers (e.g. "English (L2)", "Uzbek bridge"/"Siz", "CEFR") and NO `{{LANGUAGE_RULES}}`; `get_prompt("physics", phase)` → contains the Uzbek block and NO English-target markers; `{{LANGUAGE_RULES}}` substituted in both.
- **Coverage** (`tests/services/test_prompt_coverage.py` or `test_general_flow.py`): every `prompts/_general/*.md` contains the literal `{{LANGUAGE_RULES}}` token (so substitution always fires) — analogous to the existing `{{SUBJECT}}` presence guard.
- **Smoke (the gate) — ≥2 structurally-different `english` phases.** Real `claude` CLI on **`english` flashcards AND `english` memory-check** (or CBP), each `model_validate`-clean, and a human/grep check that (a) the **English/Uzbek split is correct for that phase** (flashcards: English front + Uzbek back/explanation; memory-check: English items/options with Uzbek prompt+reason), and (b) the **English is CEFR-appropriate** for the lesson's grade (not B2 vocabulary in an A1/G5 lesson). One phase passing does NOT close this — both must.
- Full suite green.

---

## 7. Doc fix
Update the committed Path A spec `docs/nets_general_prompts_mvp_design.md` §3.3: replace the "English-as-Uzbek … acceptable as-is for the MVP" line with a pointer to this workstream (English-target handling via `{{LANGUAGE_RULES}}`), so the next reader isn't misled that Uzbek-for-English was an accepted end state.

---

## 8. Out of scope / adjacent (noted deliberately)
- **English `reading` phase:** Path A dropped `reading` from the flow. For L2, reading comprehension matters, but re-adding it is a **flow decision**, separate from this language-handling fix. Not done here; flagged for a future English-flow workstream.
- **Path B** (CBP-mode game lightening) is unaffected and resumes after this; its new game prompts adopt `{{LANGUAGE_RULES}}`.
- **Worklog note (when the override layer revives):** the inert `prompts/english/*` subject prompts still reference the deleted `classify.md` for CEFR detection. Dead until `USE_SUBJECT_PROMPTS` flips — out of scope here, but the worklog should flag that those stale `classify.md` references need fixing whenever subject-specific prompts are revived.

---

## 9. Acceptance
An `english` job produces homework whose target content (vocab, sentences, passages, practiced items) is in **English**, with Uzbek ("Siz") instructions/feedback/scaffolding, leveled to the lesson's CEFR; every other subject is unchanged (formal Uzbek); both English smoke phases are schema-valid with the correct per-phase split and appropriate CEFR level; full suite green; subject prompt files untouched.

---

## 10. Execution notes
On `Nggaev-v2` directly (per the project owner; no worktree). Build order: (1) `prompts.py` blocks + `{{LANGUAGE_RULES}}` substitution + resolver test, (2) swap the token into the 7 prompts + flashcards per-phase hint + coverage test, (3) full suite + the 2-phase english smoke (split + CEFR checks), (4) doc fix + worklog. Subagent-driven, two-stage review per task, like Path A.
