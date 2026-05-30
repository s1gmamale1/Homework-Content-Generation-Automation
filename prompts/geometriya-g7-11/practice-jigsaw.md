# Prompt: Practice Game — Geometriya (Geometry) — Jigsaw Matching (Case-Based Preview interaction mode: Jigsaw Matching)

You are building the **Jigsaw Matching** practice game for a Geometry homework session. It is a Case-Based Preview (CBP) interaction mode: the student plays a puzzle assembler / source checker / analyst who decides **which two source-supported pieces fit together**, **which assembly relationship type connects them**, and **why the tempting wrong combination cannot fit**.

**Emit `interaction_mode = "jigsaw"` on every generated object. This literal is non-negotiable.**

## The mechanic (read before generating)

- Matching is only the ENTRY point. The learning target is choosing the source-supported RELATIONSHIP between two pieces, not surface resemblance.
- An assembly is valid ONLY if the relationship type is supported by the lesson source. Do NOT invent a relationship because it "sounds right."
- Surface similarity (same figure, similar label, look-alike angle) is NOT a valid connection.
- Relationship DIRECTION matters: a condition enables a theorem, given data leads to a conclusion, a step yields a result — not the reverse. A reversed assembly is wrong.
- Geometry assembly (piece pair) types — use a MAXIMUM of 3 per round, drawn from: **theorem ↔ condition** (a theorem and the condition under which it holds), **given ↔ conclusion** (given data and the conclusion it justifies), **step ↔ result** (a construction/proof step and the value or fact it yields).
- Distractors must be CLOSE: a surface-related but unsupported pair, a correct piece joined to a wrong piece, a reversed/too-broad relationship label — never random or silly options.
- Key terms MUST align with the lesson's Flashcards. Preserve every theorem name, formula, measurement, and number exactly.

## What to produce — JSON matching the `CbpModeGame` schema

Emit the structured form the response schema requests. Fields, exactly:

- `interaction_mode`: the literal `"jigsaw"`.
- `title`: names the lesson's concept as a relationship-assembly case.
- `student_role`: e.g. `"assembler"`, `"source_checker"`, `"analyst"`.
- `case_type`: e.g. `"relationship_reasoning"`.
- `source_concept_ids`: list of ≥1 SourceMap concept IDs from THIS lesson. Every piece and every relationship must trace back to one. Do not invent IDs.
- `case_setup`: `{ narrative, student_role, task }` — 2-4 sentences. The role must create a real need to assemble the correct piece pair to make a geometric decision. Present a piece set of 3-6 source items (theorems, conditions, given data, conclusions, steps, results).
- `checkpoints`: EXACTLY 3 objects, in order `identify` → `decide` → `justify_or_avoid_mistake`. Each: `{ intent, form, question, options, correct_index, feedback }`.
  1. `identify` (`mcq`): Which PAIR of pieces truly fits together in the source? Options = one correct source-supported pair + a surface-related unsupported pair + a (correct piece + wrong piece) pair + an irrelevant/reversed pair.
  2. `decide` (`mcq`): Which ASSEMBLY TYPE LABEL correctly connects that pair — `theorem ↔ condition`, `given ↔ conclusion`, or `step ↔ result` (max 3 labels in this round)? Options = correct label + related-but-wrong label + too-broad label + opposite/reversed label.
  3. `justify_or_avoid_mistake` (`mcq`): Which explanation PROVES the relationship and rejects the tempting wrong label? Wrong options include a correct-pair-but-wrong-relationship explanation, a surface-similarity explanation, and an unsupported/reversed explanation. **Checkpoint 3 stays MCQ — it is NOT the open reasoning.**

## Interaction payload (required)

Emit `interaction_payload` = `{ "pieces": [ {id, content}, … ], "allowed_assembly_types": [ … ] }` with **3–6 pieces** and **1–3** assembly-relationship labels (the relationship types the student may use to connect pieces). Pieces and relationships must come from the lesson's source concepts.

## Learning Blocks (required — slots 3 & 5)

Emit `learning_block_1` (after Checkpoint 1) and `learning_block_2` (after Checkpoint 2): each a 1–3 sentence, textbook-grounded teaching moment for this game's mechanic. Set `source_concept_id`. Keep them short and text-first; use `visual_svg` only if a tiny diagram is essential and not already shown.

- `decision_process_explanation` (DPE): comes AFTER checkpoint 3 and BEFORE the final simulation.
  - `prompt`: ask the student to walk through (1) which source concept/theorem they spotted, (2) why this assembly relationship label is correct (and its direction — e.g. the condition enables the theorem, not the reverse), (3) what mistake would occur with the wrong label or a weak/surface pair.
  - `expected_components`: `["concept", "method", "mistake"]` — concept · assembly relationship label · mistake.
  - `rubric`: `{ "concept": 1, "method": 1, "mistake": 1 }`.
  - `sample_acceptable_answer`: a model 2-4 sentence answer covering all three.
  - `eval_mode`: `"ai"`. `min_chars`: `60`. `options`: **MUST be `null`** — the DPE is never an MCQ and never auto-passes.
- `final_simulation`: `{ correct_path, wrong_path, why_wrong_fails }`. Correct path = `[Piece A] — [assembly relationship] → [Piece B]`, a solid/locked line because the relationship is source-supported. Wrong path = wrong pair or wrong/reversed/too-broad relationship, a dotted/crossed line because it is unsupported, reversed, or too broad. `why_wrong_fails` names exactly which of those failed.
  - `why_wrong_fails`: one sentence on why the wrong path cannot be correct (REQUIRED).
- `feedback_summary`: `{ understood, mistake, review }`.
- `completion_rules`: `{ pass_condition, retry_condition }`. Pass = valid source pair + correct assembly type + relationship explained before the consequence. Retry = pieces matched without relationship reasoning, an unsupported relationship chosen, the direction reversed, or the DPE skipped.

## Visuals

Low-asset: piece cards, assembly slots, type-label chips, a result puzzle. ALWAYS embed a source-carrying inline SVG for the figure, given data, theorem condition, or proof relationship, and follow the runtime universal SVG rules — no size, no colour, no decoration. Never render a figure or formula as a raster image. SVG must carry the actual source relationship, never decorate it.

## Language

All student-facing text in formal Uzbek ("Siz"). Preserve theorem names, formulas, measurements, and numbers exactly.

## Self-check

1. ✓ `interaction_mode == "jigsaw"`?
2. ✓ Exactly 3 checkpoints, order identify → decide → justify_or_avoid_mistake?
3. ✓ C1 = which pair fits, C2 = which assembly label (≤3 labels), C3 = MCQ proving the relationship?
4. ✓ DPE after checkpoint 3, before final_simulation, `options: null`, asks concept · assembly relationship label · mistake?
5. ✓ Relationship source-supported, direction correct (condition→theorem, given→conclusion), surface similarity rejected?
6. ✓ Final simulation: correct = solid/locked supported relation, wrong = dotted/crossed unsupported/reversed/too-broad?
7. ✓ Key terms align with Flashcards; `source_concept_ids` trace to the source map?
