# Prompt: Practice Game — Jigsaw Assembly — {{SUBJECT}}

You are generating a **Jigsaw Assembly** practice game for a {{SUBJECT}} homework
session. The student is given a set of concept pieces and must assemble them according
to the source-supported relationships between them — theorem ↔ condition, given ↔
conclusion, or step ↔ result. Surface similarity is never enough; the relationship
must be traceable to the lesson source.

Derive all pieces and relationships from the lesson's `lesson_context` and source map.
Emit `interaction_mode = "jigsaw"` (the literal string — non-negotiable).

## What to produce

One compact Jigsaw Assembly game, emitted in the structured form the response schema
requests. Fill every field below; invent no extra fields.

- `title` — short, names the concept set and assembly framing.
- `source_concept_ids` — array of ≥1 concept IDs taken directly from the provided
  source map. **Use real IDs from the source; do NOT invent.**
- `interaction_mode` — the literal `"jigsaw"`.
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

Emit exactly one JSON object. Example (generic — replace with real lesson content):

```json
{
  "title": "Pythagorean Theorem — Condition and Application Assembly",
  "source_concept_ids": ["concept_pythagorean_theorem"],
  "interaction_mode": "jigsaw",
  "instruction": "Connect each piece to the piece it belongs with, using only the relationship types listed. A connection is valid only when the source supports it — not when pieces look similar.",
  "interaction_payload": {
    "pieces": [
      { "id": "p1", "content": "Triangle ABC has a right angle at C" },
      { "id": "p2", "content": "a² + b² = c²" },
      { "id": "p3", "content": "The hypotenuse is side c, opposite the right angle" },
      { "id": "p4", "content": "AC = 3, BC = 4 → AB = 5" }
    ],
    "allowed_assembly_types": ["theorem ↔ condition", "given ↔ conclusion"],
    "solution": [["p1", "p2"]]
  },
  "why_prompt": "Explain which theorem or concept you identified, why the relationship direction you chose is correct (for example, which piece is the condition and which is the theorem it enables), and what error a student would make by connecting pieces in the wrong direction or by matching on surface similarity alone.",
  "expected_reasoning_keywords": ["right angle", "hypotenuse", "condition", "theorem", "Pythagorean"]
}
```

## Language

{{LANGUAGE_RULES}}
