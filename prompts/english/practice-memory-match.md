# Prompt: Practice Game — English — Memory Matching (Case-Based Preview interaction mode: Memory Matching)

You are building the **Memory Matching** practice game for an English homework session. It is a Case-Based Preview (CBP) interaction mode: the student plays an editor / reviewer / concept archivist who first finds a valid card pair, then — after the cards are hidden — must reconstruct what the matched items MEAN, not where they sat on the board.

**Emit `interaction_mode = "memory_match"` on every generated object. This literal is non-negotiable.**

## The mechanic (read before generating)

- Matching is only the ENTRY point. The learning target is reconstructing the source MEANING after visual support is hidden — never card position, colour, or decoration.
- English pair types: **word ↔ correct usage** and **term ↔ meaning** (e.g. a target word ↔ a correct example sentence, a grammar term ↔ its definition). Pairs must come from the unit's target items.
- Distractors must be CLOSE and meaningful — a near-synonym used in the wrong collocation, or a confusable grammar term — never random or silly answers.
- Key terms MUST align with the unit's Flashcards. Keep items at the unit's CEFR level; do not simplify them.
- The target words/usages being tested are in English. All surrounding instruction and student-facing framing is in formal Uzbek; preserve the English target forms exactly.

## What to produce — JSON matching the `CbpModeGame` schema

Emit the structured form the response schema requests. Fields, exactly:

- `interaction_mode`: the literal `"memory_match"`.
- `title`: names the unit's target item set as a memory-reconstruction case.
- `student_role`: e.g. `"editor"`, `"reviewer"`, `"concept_archivist"`.
- `case_type`: e.g. `"memory_reconstruction"`.
- `source_concept_ids`: list of ≥1 SourceMap concept IDs for THIS unit's target grammar/vocabulary items. Every card pair must trace back to one. Do not invent IDs.
- `case_setup`: `{ narrative, student_role, task }` — 2-4 sentences. The role must create a need to organize 4-8 source cards now and reconstruct their meaning after they are hidden.
- `checkpoints`: EXACTLY 3 objects, in order `identify` → `decide` → `justify_or_avoid_mistake`. Each: `{ intent, form, question, options, correct_index, feedback }`.
  1. `identify` (`mcq`): Which two cards form the same source-supported pair? Options = one correct word↔correct-usage / term↔meaning pair plus close, surface-similar, and one irrelevant pair.
  2. `decide` (`mcq`): After the cards are hidden, which usage/meaning belonged to the word/term? Options = correct reconstructed meaning + close distractor (near-synonym / wrong collocation) + surface-similar + irrelevant.
  3. `justify_or_avoid_mistake` (`mcq`): Which explanation shows WHY this pairing is correct? Wrong options include a position-memory-only explanation and a surface-similarity explanation. **Checkpoint 3 stays MCQ — it is NOT the open reasoning.**

## Learning Blocks (required — slots 3 & 5)

Emit `learning_block_1` (after Checkpoint 1) and `learning_block_2` (after Checkpoint 2): each a 1–3 sentence, textbook-grounded teaching moment for this game's mechanic. Set `source_concept_id`. Keep them short and text-first; use `visual_svg` only if a tiny diagram is essential and not already shown.

- `decision_process_explanation` (DPE): comes AFTER checkpoint 3 and BEFORE the final simulation.
  - `prompt`: ask the student to walk through (1) which concept they reconstructed, (2) why this usage/meaning belongs to that word/term (the reconstructed meaning), (3) what memory mistake would occur if they relied only on card position or a close distractor.
  - `expected_components`: `["concept", "method", "mistake"]`.
  - `rubric`: `{ "concept": 1, "method": 1, "mistake": 1 }`.
  - `sample_acceptable_answer`: a model 2-4 sentence answer covering all three (the English target form quoted exactly).
  - `eval_mode`: `"ai"`. `min_chars`: `60`. `options`: **MUST be `null`** — the DPE is never an MCQ and never auto-passes.
- `final_simulation`: `{ correct_path, wrong_path, why_wrong_fails }`. Separate the outcomes: correct reconstruction → **Recalled**; matched by location but failed reconstruction → **Position Memory Only / Guessed / Missed** and item returns to review.
  - `why_wrong_fails`: one sentence on why the wrong path cannot be correct (REQUIRED).
- `feedback_summary`: `{ understood, mistake, review }`.
- `completion_rules`: `{ pass_condition, retry_condition }`. Pass = valid pair + correct reconstruction + relationship explained before the consequence. Retry = card-flipping done but meaning not reconstructed, position relied on, close distractor confused, or DPE skipped.

## Visuals

Low-asset: text cards, chips, reconstruction slots, simple result labels. Text cards are sufficient for English. Use SVG ONLY where it carries source meaning (rare) and follow the runtime universal SVG rules — no size, no colour, no decoration. Do not test colour, position, or art.

## Language

Student-facing framing in formal Uzbek ("Siz"); the English target words/usages being tested stay in English and are preserved exactly. Keep items at the unit's CEFR level.

## Self-check

1. ✓ `interaction_mode == "memory_match"`?
2. ✓ Exactly 3 checkpoints, order identify → decide → justify_or_avoid_mistake?
3. ✓ Checkpoint 3 is MCQ, not the open reasoning?
4. ✓ DPE after checkpoint 3, before final_simulation, `options: null`, asks concept · reconstructed meaning · memory mistake?
5. ✓ Distractors close/meaningful; pairs are word↔correct usage / term↔meaning from the source?
6. ✓ Final simulation separates Recalled / Guessed / Missed / Position Memory Only?
7. ✓ Key terms align with Flashcards; `source_concept_ids` trace to the source map?
