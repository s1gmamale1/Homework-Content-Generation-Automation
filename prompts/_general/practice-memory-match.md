# Prompt: Practice Game — Memory Match — {{SUBJECT}}

You are generating a **Memory Match** practice game for a {{SUBJECT}} homework session.
The student flips cards to find matching pairs drawn from this lesson's concepts.
The match must be meaningful — a term paired with its definition, a structure paired
with its function, or a cause paired with its effect — never a surface-similarity guess.

Derive all pairs from the lesson's `lesson_context` and source map.
Set `interaction_mode` to `memory_match` (include this as a labelled field in your output — non-negotiable).

## What to produce

One compact Memory Match game. Fill every field below; invent no extra fields.

- `title` — short, names the concept set being matched.
- `source_concept_ids` — array of ≥1 concept IDs taken directly from the provided
  source map. **Use real IDs from the source; do NOT invent.**
- `interaction_mode` — the literal `memory_match`.
- `instruction` — 1–2 sentences: what the student does (flip cards, find the matching
  pair based on meaning, not position).
- `interaction_payload` — `{ "pairs": [ {"left": "...", "right": "..."}, … ] }`.
  Produce **4–8 pairs**. Each pair is a valid source-supported match (term ↔ meaning,
  structure ↔ role, cause ↔ effect). Both `left` and `right` must be non-empty.
  Pairs must be distinguishable by understanding, not by surface cues or card position.
- `why_prompt` — ONE open reasoning question (non-empty). Ask the student to explain:
  which concept connects this pair, why these two sides belong together (the
  relationship), and what mistake a student relying on guessing or surface similarity
  would make. Keep it to a single open prompt.
- `expected_reasoning_keywords` — optional array of a few concept words a sound
  answer would contain.

## Non-negotiables

- Distractors (wrong sides) must be close and meaningful — a confusable term or
  near-miss function — never random or silly.
- Key terms must align with the lesson's Flashcards. Do not introduce terminology
  that conflicts with them.
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
`interaction_mode` (value: `memory_match`), `instruction`, then the pairs listed
as `left` / `right` entries, followed by `why_prompt` and
`expected_reasoning_keywords`.

## Language

{{LANGUAGE_RULES}}
