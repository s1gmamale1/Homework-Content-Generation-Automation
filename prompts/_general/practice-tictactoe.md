# Prompt: Practice Game — Tic-Tac-Toe Decision Grid — {{SUBJECT}}

You are generating a **Tic-Tac-Toe Decision Grid** practice game for a {{SUBJECT}}
homework session. The student sees a 3×3 board of candidate actions or answers and
must identify which cells are correct applications of this lesson's concept — and
which are wrong. The grid must be solvable **only through the lesson concept**, never
by general intuition or test-taking logic.

Derive all cells from the lesson's `lesson_context` and source map.
Set `interaction_mode` to `tictactoe` (include this as a labelled field in your output — non-negotiable).

## What to produce

One compact Tic-Tac-Toe game. Fill every field below; invent no extra fields.

- `title` — short, names the concept + decision-grid framing.
- `source_concept_ids` — array of ≥1 concept IDs taken directly from the provided
  source map. **Use real IDs from the source; do NOT invent.**
- `interaction_mode` — the literal `tictactoe`.
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

Respond in **Markdown only** (no JSON, no code-fenced JSON). Use `#` for the phase
title and `##`/`###` for the sections/items described above, in order. For visuals:
emit inline `<svg>` for diagrams; for a photo/raster you would otherwise need to
generate, emit `![placeholder: <short description> — image gen required](placeholder)`
— never fabricate an image and never invent an image URL.

Include these labelled fields in order: `title`, `source_concept_ids`,
`interaction_mode` (value: `tictactoe`), `instruction`, then the 9 cells each with
`label`, `is_correct`, and optional `reason`, followed by `why_prompt` and
`expected_reasoning_keywords`.

## Language

{{LANGUAGE_RULES}}
