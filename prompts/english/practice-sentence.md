# Prompt: Sentence Filling (Case-Based Preview interaction mode: English Fill-in-the-Blanks)

You are generating ONE **Sentence Filling** case for this lesson — a Case-Based
Preview run as an English Fill-in-the-Blanks error-repair case. **This is NOT a
cloze exercise. There is NO empty blank.** The student is shown a *mostly fluent*
English sentence with one broken word/phrase, must **diagnose the broken part
first**, choose the source-aligned repair, justify it, then explain the decision
before seeing the meaning test.

Emit `interaction_mode = "sentence_fill"` (literal). The structure is the CBP
schema (`CbpModeGame extends CaseBasedPreview`): exactly 3 MCQ checkpoints, then
an open-ended Decision Process Explanation (DPE), then the final simulation.

## What to produce

One case, emitted in the structured form the response schema requests. Fill
every field — do not invent fields.

- `interaction_mode` — exactly `"sentence_fill"`.
- `title` — short, names the wording skill being repaired.
- `student_role` — an editor / message checker / source checker / proofreader /
  translator-checker / explanation improver who must repair wording without
  changing the textbook meaning.
- `case_type` — `communication_case | error_detection_case | source_aligned_wording_case`.
- `source_concept_ids` — list (>=1) of source-map concept IDs from THIS lesson.
  Do not invent; key terms must align with Flashcards.
- `case_setup` — `{ narrative, student_role, task }`. 2–4 sentence role-based
  narrative that creates a real need to repair the sentence. Put the broken
  English sentence (mostly fluent, one tempting wrong phrase) inside `task`. The
  error must fail because of the **LESSON concept**, not random grammar weirdness.
- `checkpoints` — EXACTLY 3, in this order, each
  `{ intent, form, question, options, correct_index, feedback }`:
  - **C1 `identify` (mcq)** — "Which word/phrase does NOT match the textbook
    meaning?" Options are phrase chunks of the broken sentence: the broken phrase
    (correct), a correct surrounding phrase, a tempting-but-not-main phrase, an
    irrelevant phrase. The student finds the broken part — this is NOT a blank.
  - **C2 `decide` (mcq)** — "Which replacement makes the sentence match the
    source meaning?" Options are replacement chips: (a) correct repair,
    (b) grammatically possible but conceptually wrong, (c) conceptually close but
    too broad/too narrow, (d) irrelevant or wrong-register replacement. Chips must
    NOT all be arguably correct — exactly one preserves the source meaning.
  - **C3 `justify_or_avoid_mistake` (mcq)** — "Which explanation best shows why
    the old phrase is wrong and the new one right?" Options: (a) correct
    explanation using the source concept and repaired meaning, (b) fluent but
    source-changing explanation, (c) grammar/register-only, missing the concept,
    (d) wrong/unsupported. C3 stays MCQ; do NOT turn it into the open-ended DPE.
  - Set `correct_index` to the right option; `feedback` is a short formal-Uzbek note.

## Interaction payload (required)

Emit `interaction_payload` = `{ "sentence": "...", "chips": [ {label, is_correct, reason}, … ] }`. The `sentence` contains the broken/blank span. Provide **≥3 chips**, EXACTLY ONE with `is_correct: true`; each wrong chip's `reason` names why it fails (grammatically possible but wrong meaning/register, too broad/narrow, or irrelevant).

## Learning Blocks (required — slots 3 & 5)

Emit `learning_block_1` (after Checkpoint 1) and `learning_block_2` (after Checkpoint 2): each a 1–3 sentence, textbook-grounded teaching moment for this game's mechanic. Set `source_concept_id`. Keep them short and text-first; use `visual_svg` only if a tiny diagram is essential and not already shown.

- `decision_process_explanation` — comes AFTER C3, BEFORE the final simulation:
  - `prompt` — ask for all three: (1) which concept/rule you spotted,
    (2) why the replacement preserves the source meaning, (3) what wording mistake
    would happen if the old/tempting phrase stayed.
  - `expected_components` — `["concept", "method", "mistake"]`.
  - `rubric` — object, e.g. `{ "concept": 1, "method": 1, "mistake": 1 }`.
  - `sample_acceptable_answer` — references all three components in 2–4 sentences.
  - `eval_mode` — `"ai"`. `min_chars` — `60`. `options` — MUST be `null`.
    Never auto-pass the DPE.
- `final_simulation` — the meaning test panel, `{ correct_path, wrong_path,
  why_wrong_fails }`. Correct path: meaning accurate · wording/register clear ·
  source concept preserved (all pass). Wrong/weak path: grammar may sound fine but
  source concept changed and/or explanation missing — `why_wrong_fails` says which
  check fails. Never skip the weak-path meaning test. Do not treat fluent grammar
  as correct if the source concept changed.
  - `why_wrong_fails`: one sentence on why the wrong path cannot be correct (REQUIRED).
- `feedback_summary` — `{ understood, mistake, review }`, formal Uzbek.
- `completion_rules` — `{ pass_condition, retry_condition }`. Pass: broken phrase
  found, source-aligned replacement chosen, repair explained before the meaning
  test. Retry: guesses chips, changes source meaning, picks fluent-but-wrong
  phrase, comments only on grammar when concept is the issue, or skips the DPE.

## Non-negotiables

- **No empty blank** — the student diagnoses the broken phrase first.
- The broken sentence is **mostly fluent** and the error **tempting, not nonsense**.
- The sentence fails because of the **lesson concept**, not random grammar.
- Replacement chips must NOT all be arguably correct — exactly one is the repair.
- **Exactly 3 MCQ checkpoints**; C3 is MCQ, never the open-ended explanation.
- DPE sits after C3 and before the consequence; `options: null`; never auto-pass.
- Final simulation always shows correct AND weak path meaning checks.
- Key terms must align with Flashcards; `source_concept_ids` trace to the source map.

## Language (Uzbek guardrails)

When the surrounding/instruction language is `uz`: use formal **"Siz"**, never
`sen`/`san`. The **English sentence being repaired stays in English** (it is the
content under test); all narrative, questions, options, feedback, DPE, and panels
are in natural, formal Uzbek. Preserve meaning, terms, register, formulas, units,
names, and chronology. A register-task error must be wrong because it violates the
target rule/register, not because it merely sounds unusual.

## Visuals

Low-asset / text-based: a broken-sentence card (selectable phrase chunks), the
replacement chips (C2), and the meaning test panel (final simulation). No raster
asset is needed for language cases.
