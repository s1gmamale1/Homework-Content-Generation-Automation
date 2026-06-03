# Prompt: Practice Game — Jigsaw Assembly — {{SUBJECT}}

You are generating a **Jigsaw Assembly** practice game for a {{SUBJECT}} homework
session. The student is given a set of concept pieces and must assemble them according
to the source-supported relationships between them — theorem ↔ condition, given ↔
conclusion, or step ↔ result. Surface similarity is never enough; the relationship
must be traceable to the lesson source.

Derive all pieces and relationships from the lesson's `lesson_context` and source map.
Set `interaction_mode` to `jigsaw` (include this as a labelled field in your output — non-negotiable).

## What to produce

One compact Jigsaw Assembly game. Fill every field below; invent no extra fields.

- `title` — short, names the concept set and assembly framing.
- `source_concept_ids` — array of ≥1 concept IDs taken directly from the provided
  source map. **Use real IDs from the source; do NOT invent.**
- `interaction_mode` — the literal `jigsaw`.
- `instruction` — 1–2 sentences: what the student does (drag pieces into connected
  pairs using only the relationship types provided; pieces connect only when the source
  supports it).
- `interaction_payload` — object with two fields:
  - `"pieces"` — array of **3–6** objects, each `{"id": "p1", "content": "..."}`.
    Pieces are theorems, conditions, given data, conclusions, steps, or results drawn
    from the lesson.
  - `"allowed_assembly_types"` — array of **1–3** relationship-type strings, drawn
    from: `"theorem ↔ condition"`, `"given ↔ conclusion"`, `"step ↔ result"`.
    Use only the types that apply to this lesson's content.
- `why_prompt` — ONE open reasoning question (non-empty). Ask the student to explain:
  which source concept/theorem they identified, why the assembly relationship
  direction is correct (e.g. the condition enables the theorem, not the reverse), and
  what mistake would occur if pieces were joined by surface similarity or with the
  direction reversed. Keep it to a single open prompt.
- `expected_reasoning_keywords` — optional array of a few concept words a sound
  answer would contain.

## Non-negotiables

- Assembly relationship direction matters — a reversed connection is wrong.
- Every piece must trace back to a real concept in this lesson's source map.
- `allowed_assembly_types` contains only types present in the lesson content (max 3).
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
`interaction_mode` (value: `jigsaw`), `instruction`, then the pieces (each with
`id` and `content`), `allowed_assembly_types`, `solution` (connected pair IDs),
followed by `why_prompt` and `expected_reasoning_keywords`.

## Language

{{LANGUAGE_RULES}}
