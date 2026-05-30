# Prompt: Practice Game — History (O'zbekiston Tarixi + Jahon Tarixi) — Memory Matching (Case-Based Preview interaction mode: Memory Matching)

You are building the **Memory Matching** practice game for a History homework session. It is a Case-Based Preview (CBP) interaction mode: the student plays a source sorter / historian / archivist who first finds a valid card pair, then — after the cards are hidden — must reconstruct what the matched items MEAN, not where they sat on the board.

**Emit `interaction_mode = "memory_match"` on every generated object. This literal is non-negotiable.**

## The mechanic (read before generating)

- Matching is only the ENTRY point. The learning target is reconstructing the source MEANING after visual support is hidden — never card position, colour, or decoration.
- History pair types: **event ↔ consequence** and **evidence ↔ claim** (e.g. a reform ↔ what it led to, a primary source line ↔ the conclusion it supports). Pairs must come from the lesson source.
- Distractors must be CLOSE and meaningful — a plausible-but-wrong consequence or a claim drawn from the wrong evidence — never random or silly answers.
- Key terms MUST align with the lesson's Flashcards. Preserve chronology, names, dates, and terms exactly.
- When the lesson covers Uzbekistan history, ground cards in its specific context (Silk Road, Timurids, Soviet era, independence); do not generalize to generic "Central Asian history".

## What to produce — JSON matching the `CbpModeGame` schema

Emit the structured form the response schema requests. Fields, exactly:

- `interaction_mode`: the literal `"memory_match"`.
- `title`: names the lesson's concept set as a memory-reconstruction case.
- `student_role`: e.g. `"source_sorter"`, `"historian"`, `"concept_archivist"`.
- `case_type`: e.g. `"memory_reconstruction"`.
- `source_concept_ids`: list of ≥1 SourceMap concept IDs from THIS lesson (specific events, figures, frameworks). Every card pair must trace back to one. Do not invent IDs.
- `case_setup`: `{ narrative, student_role, task }` — 2-4 sentences. The role must create a need to organize 4-8 source cards now and reconstruct their meaning after they are hidden.
- `checkpoints`: EXACTLY 3 objects, in order `identify` → `decide` → `justify_or_avoid_mistake`. Each: `{ intent, form, question, options, correct_index, feedback }`.
  1. `identify` (`mcq`): Which two cards form the same source-supported pair? Options = one correct event↔consequence / evidence↔claim pair plus close, surface-similar, and one irrelevant pair.
  2. `decide` (`mcq`): After the cards are hidden, which consequence/claim belonged to the event/evidence? Options = correct reconstructed meaning + close distractor + surface-similar + irrelevant.
  3. `justify_or_avoid_mistake` (`mcq`): Which explanation shows WHY this pairing is correct? Wrong options include a position-memory-only explanation and a surface-similarity explanation. **Checkpoint 3 stays MCQ — it is NOT the open reasoning.**

## Learning Blocks (required — slots 3 & 5)

Emit `learning_block_1` (after Checkpoint 1) and `learning_block_2` (after Checkpoint 2): each a 1–3 sentence, textbook-grounded teaching moment for this game's mechanic. Set `source_concept_id`. Keep them short and text-first; use `visual_svg` only if a tiny diagram is essential and not already shown.

- `decision_process_explanation` (DPE): comes AFTER checkpoint 3 and BEFORE the final simulation.
  - `prompt`: ask the student to walk through (1) which concept they reconstructed, (2) why this consequence/claim belongs to that event/evidence (the reconstructed meaning), (3) what memory mistake would occur if they relied only on card position or a close distractor.
  - `expected_components`: `["concept", "method", "mistake"]`.
  - `rubric`: `{ "concept": 1, "method": 1, "mistake": 1 }`.
  - `sample_acceptable_answer`: a model 2-4 sentence answer covering all three.
  - `eval_mode`: `"ai"`. `min_chars`: `60`. `options`: **MUST be `null`** — the DPE is never an MCQ and never auto-passes.
- `final_simulation`: `{ correct_path, wrong_path, why_wrong_fails }`. Separate the outcomes: correct reconstruction → **Recalled**; matched by location but failed reconstruction → **Position Memory Only / Guessed / Missed** and item returns to review.
  - `why_wrong_fails`: one sentence on why the wrong path cannot be correct (REQUIRED).
- `feedback_summary`: `{ understood, mistake, review }`.
- `completion_rules`: `{ pass_condition, retry_condition }`. Pass = valid pair + correct reconstruction + relationship explained before the consequence. Retry = card-flipping done but meaning not reconstructed, position relied on, close distractor confused, or DPE skipped.

## Visuals

Low-asset: text cards, chips, reconstruction slots, simple result labels. Text cards are normally sufficient for History. Use SVG ONLY where it carries source meaning (e.g. a timeline strip whose order IS the content) and follow the runtime universal SVG rules — no size, no colour, no decoration. Do not test colour, position, or art.

## Language

All student-facing text in formal Uzbek ("Siz"). Preserve chronology, names, dates, and historical terms exactly.

## Self-check

1. ✓ `interaction_mode == "memory_match"`?
2. ✓ Exactly 3 checkpoints, order identify → decide → justify_or_avoid_mistake?
3. ✓ Checkpoint 3 is MCQ, not the open reasoning?
4. ✓ DPE after checkpoint 3, before final_simulation, `options: null`, asks concept · reconstructed meaning · memory mistake?
5. ✓ Distractors close/meaningful; pairs are event↔consequence / evidence↔claim from the source?
6. ✓ Final simulation separates Recalled / Guessed / Missed / Position Memory Only?
7. ✓ Key terms align with Flashcards; `source_concept_ids` trace to the source map?
