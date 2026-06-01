# Prompt: Practice Game — Tic-Tac-Toe Decision Grid — {{SUBJECT}}

You are generating a **Tic-Tac-Toe Decision Grid** practice game for a {{SUBJECT}}
homework session. The student sees a 3×3 board of candidate actions or answers and
must identify which cells are correct applications of this lesson's concept — and
which are wrong. The grid must be solvable **only through the lesson concept**, never
by general intuition or test-taking logic.

Derive all cells from the lesson's `lesson_context` and source map.
Emit `interaction_mode = "tictactoe"` (the literal string — non-negotiable).

## What to produce

One compact Tic-Tac-Toe game, emitted in the structured form the response schema
requests. Fill every field below; invent no extra fields.

- `title` — short, names the concept + decision-grid framing.
- `source_concept_ids` — array of ≥1 concept IDs taken directly from the provided
  source map. **Use real IDs from the source; do NOT invent.**
- `interaction_mode` — the literal `"tictactoe"`.
- `instruction` — 1–2 sentences: what the student does (select all correct cells on the
  3×3 board; a cell is correct only when it applies the lesson concept correctly).
- `interaction_payload` — `{ "cells": [ {"label": "...", "is_correct": true/false, "reason": "..."}, … ] }`.
  Produce **exactly 9 cells** (a 3×3 board). At least one cell must have
  `is_correct: true`. `reason` is optional per cell but recommended for wrong cells —
  it names why that cell fails. Cells must be distinguishable only by applying the
  lesson concept, not by common sense.
- `why_prompt` — ONE open reasoning question (non-empty). Ask the student to explain:
  which concept they applied to mark the correct cell(s), why those cells satisfy the
  concept while the others do not, and what mistake a student relying on intuition
  rather than the concept would make. Keep it to a single open prompt.
- `expected_reasoning_keywords` — optional array of a few concept words a sound
  answer would contain.

## Non-negotiables

- All 9 cells must be derived from lesson content — no generic filler.
- Wrong cells must be plausible and tempting, never obviously silly.
- At least one correct cell; typically 1–3 correct cells on the board.
- `source_concept_ids` must trace to real concepts in this lesson's source map.
- This is the compact game schema. Do NOT add full-CBP fields
  (no multi-step scaffolding, no open-ended DPE, no simulation panels).

## Output format

Emit exactly one JSON object. Example (generic — replace with real lesson content):

```json
{
  "title": "Newton's Second Law — Decision Grid",
  "source_concept_ids": ["concept_newtons_second_law"],
  "interaction_mode": "tictactoe",
  "instruction": "Select all cells that correctly apply Newton's Second Law. A cell is correct only if the action follows directly from F = ma.",
  "interaction_payload": {
    "cells": [
      { "label": "Double the force → double the acceleration (mass constant)", "is_correct": true },
      { "label": "Double the mass → double the acceleration (force constant)", "is_correct": false, "reason": "Doubling mass halves acceleration when force is constant." },
      { "label": "Halve the mass → double the acceleration (force constant)", "is_correct": true },
      { "label": "Force and acceleration are independent", "is_correct": false, "reason": "F = ma shows they are directly proportional." },
      { "label": "Larger mass requires less force for same acceleration", "is_correct": false, "reason": "More mass requires MORE force for the same acceleration." },
      { "label": "Net force zero → acceleration zero", "is_correct": true },
      { "label": "Acceleration depends on speed, not force", "is_correct": false, "reason": "Acceleration depends on net force and mass, not on current speed." },
      { "label": "Force and velocity always point in the same direction", "is_correct": false, "reason": "Force gives acceleration; velocity direction is independent." },
      { "label": "Tripling both force and mass leaves acceleration unchanged", "is_correct": true }
    ]
  },
  "why_prompt": "Pick one correct cell and one wrong cell. Explain which concept from the lesson makes the correct cell right, why the wrong cell fails that same concept, and what error a student making an intuitive guess would fall into.",
  "expected_reasoning_keywords": ["net force", "mass", "acceleration", "proportional"]
}
```

## Language

{{LANGUAGE_RULES}}
