# Prompt: Practice Game — History — Jigsaw Matching (Case-Based Preview interaction mode: Jigsaw Matching)

You are building the **Jigsaw Matching** practice game for a History homework session. It is a Case-Based Preview (CBP) interaction mode: the student plays a historian / source checker / analyst who decides **which two source-supported pieces fit together**, **which assembly relationship type connects them**, and **why the tempting wrong combination cannot fit**.

**Emit `interaction_mode = "jigsaw"` on every generated object. This literal is non-negotiable.**

## The mechanic (read before generating)

- Matching is only the ENTRY point. The learning target is choosing the source-supported RELATIONSHIP between two pieces, not surface resemblance.
- An assembly is valid ONLY if the relationship type is supported by the lesson source. Do NOT invent a historical relationship because it "sounds right."
- Surface similarity (same era, same region, similar-sounding name) is NOT a valid connection.
- Relationship DIRECTION matters: a cause leads to an effect, evidence supports a claim, an event leads to a consequence — not the reverse. A reversed assembly is wrong.
- History assembly (piece pair) types — use a MAXIMUM of 3 per round, drawn from: **cause ↔ effect** (an event/condition and what it brought about), **evidence ↔ claim** (a source fact and the claim it supports), **event ↔ consequence** (an event and its later outcome).
- Distractors must be CLOSE: a surface-related but unsupported pair, a correct piece joined to a wrong piece, a reversed/too-broad relationship label — never random or silly options.
- Key terms MUST align with the lesson's Flashcards. Preserve every name, date, chronology, cause, and evidence meaning exactly.

## What to produce — JSON matching the `CbpModeGame` schema

Emit the structured form the response schema requests. Fields, exactly:

- `interaction_mode`: the literal `"jigsaw"`.
- `title`: names the lesson's concept as a relationship-assembly case.
- `student_role`: e.g. `"historian"`, `"source_checker"`, `"analyst"`.
- `case_type`: e.g. `"relationship_reasoning"`.
- `source_concept_ids`: list of ≥1 SourceMap concept IDs from THIS lesson. Every piece and every relationship must trace back to one. Do not invent IDs.
- `case_setup`: `{ narrative, student_role, task }` — 2-4 sentences. The role must create a real need to assemble the correct piece pair to make a historical judgement. Present a piece set of 3-6 source items (events, causes, effects, evidence, claims, consequences).
- `checkpoints`: EXACTLY 3 objects, in order `identify` → `decide` → `justify_or_avoid_mistake`. Each: `{ intent, form, question, options, correct_index, feedback }`.
  1. `identify` (`mcq`): Which PAIR of pieces truly fits together in the source? Options = one correct source-supported pair + a surface-related unsupported pair + a (correct piece + wrong piece) pair + an irrelevant/reversed pair.
  2. `decide` (`mcq`): Which ASSEMBLY TYPE LABEL correctly connects that pair — `cause ↔ effect`, `evidence ↔ claim`, or `event ↔ consequence` (max 3 labels in this round)? Options = correct label + related-but-wrong label + too-broad label + opposite/reversed label.
  3. `justify_or_avoid_mistake` (`mcq`): Which explanation PROVES the relationship and rejects the tempting wrong label? Wrong options include a correct-pair-but-wrong-relationship explanation, a surface-similarity explanation, and an unsupported/reversed explanation. **Checkpoint 3 stays MCQ — it is NOT the open reasoning.**

## Interaction payload (required)

Emit `interaction_payload` = `{ "pieces": [ {id, content}, … ], "allowed_assembly_types": [ … ] }` with **3–6 pieces** and **1–3** assembly-relationship labels (the relationship types the student may use to connect pieces). Pieces and relationships must come from the lesson's source concepts.

## Learning Blocks (required — slots 3 & 5)

Emit `learning_block_1` (after Checkpoint 1) and `learning_block_2` (after Checkpoint 2): each a 1–3 sentence, textbook-grounded teaching moment for this game's mechanic. Set `source_concept_id`. Keep them short and text-first; use `visual_svg` only if a tiny diagram is essential and not already shown.

- `decision_process_explanation` (DPE): comes AFTER checkpoint 3 and BEFORE the final simulation.
  - `prompt`: ask the student to walk through (1) which source concept/event they spotted, (2) why this assembly relationship label is correct (and its direction — e.g. this cause leads to that effect, not the reverse), (3) what mistake would occur with the wrong label or a weak/surface pair.
  - `expected_components`: `["concept", "method", "mistake"]` — concept · assembly relationship label · mistake.
  - `rubric`: `{ "concept": 1, "method": 1, "mistake": 1 }`.
  - `sample_acceptable_answer`: a model 2-4 sentence answer covering all three.
  - `eval_mode`: `"ai"`. `min_chars`: `60`. `options`: **MUST be `null`** — the DPE is never an MCQ and never auto-passes.
- `final_simulation`: `{ correct_path, wrong_path, why_wrong_fails }`. Correct path = `[Piece A] — [assembly relationship] → [Piece B]`, a solid/locked line because the relationship is source-supported. Wrong path = wrong pair or wrong/reversed/too-broad relationship, a dotted/crossed line because it is unsupported, reversed, or too broad. `why_wrong_fails` names exactly which of those failed.
  - `why_wrong_fails`: one sentence on why the wrong path cannot be correct (REQUIRED).
- `feedback_summary`: `{ understood, mistake, review }`.
- `completion_rules`: `{ pass_condition, retry_condition }`. Pass = valid source pair + correct assembly type + relationship explained before the consequence. Retry = pieces matched without relationship reasoning, an unsupported relationship chosen, the direction reversed, or the DPE skipped.

## Visuals

Low-asset and usually text pieces: piece cards, assembly slots, type-label chips, a result puzzle. History needs no diagrams — text cards carrying the source event, evidence, cause, or consequence are sufficient. Use SVG only if a source-carrying relationship map genuinely helps, and then follow the runtime universal SVG rules — no size, no colour, no decoration. Do not copy textbook images. Visuals must carry the actual source relationship, never decorate it.

## Language

All student-facing text in formal Uzbek ("Siz"). Preserve names, dates, chronology, causes, and evidence meaning exactly.

## Self-check

1. ✓ `interaction_mode == "jigsaw"`?
2. ✓ Exactly 3 checkpoints, order identify → decide → justify_or_avoid_mistake?
3. ✓ C1 = which pair fits, C2 = which assembly label (≤3 labels), C3 = MCQ proving the relationship?
4. ✓ DPE after checkpoint 3, before final_simulation, `options: null`, asks concept · assembly relationship label · mistake?
5. ✓ Relationship source-supported, direction correct (cause→effect, evidence→claim, event→consequence), surface similarity rejected?
6. ✓ Final simulation: correct = solid/locked supported relation, wrong = dotted/crossed unsupported/reversed/too-broad?
7. ✓ Key terms align with Flashcards; `source_concept_ids` trace to the source map?
