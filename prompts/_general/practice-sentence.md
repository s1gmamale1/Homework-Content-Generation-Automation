# Prompt: Practice Game — Sentence Fill — {{SUBJECT}}

You are generating a **Sentence Fill** practice game for a {{SUBJECT}} homework
session. The student sees a sentence with one blank (marked `____`) and a set of
word/phrase chips. They must choose the chip that correctly completes the sentence
according to the lesson concept — exactly one chip is correct; the others are
plausible distractors that fail for concept-level reasons.

Derive the sentence and chips from the lesson's `lesson_context` and source map.
Emit `interaction_mode = "sentence_fill"` (the literal string — non-negotiable).

## What to produce

One compact Sentence Fill game, emitted in the structured form the response schema
requests. Fill every field below; invent no extra fields.

- `title` — short, names the concept or skill being tested.
- `source_concept_ids` — array of ≥1 concept IDs taken directly from the provided
  source map. **Use real IDs from the source; do NOT invent.**
- `interaction_mode` — the literal `"sentence_fill"`.
- `instruction` — 1–2 sentences: what the student does (read the sentence, choose the
  one chip that fills the blank correctly according to the lesson).
- `interaction_payload` — object with two fields:
  - `"sentence"` — the sentence string containing exactly one blank marked as `____`.
    The sentence must be meaningful with the blank in place and must test a real
    lesson concept, not a trivial detail.
  - `"chips"` — array of **≥3** chip objects, each `{"label": "...", "is_correct": true/false, "reason": "..."}`.
    **EXACTLY ONE chip** must have `"is_correct": true`. Every wrong chip's `reason`
    names specifically why it fails (too broad, too narrow, conceptually reversed,
    wrong register, plausible-but-incorrect). All chips must be non-empty strings.
- `why_prompt` — ONE open reasoning question (non-empty). Ask the student to explain:
  which concept from the lesson makes the correct chip the right choice, why the other
  chips fail (pointing to the concept, not just grammar), and what mistake a student
  guessing by surface similarity or word length would make. Keep it to a single open
  prompt.
- `expected_reasoning_keywords` — optional array of a few concept words a sound
  answer would contain.

## Non-negotiables

- The blank is clearly marked `____` in the sentence string.
- Exactly one chip is correct — wrong chips must be incorrect for concept-level
  reasons, not arbitrary ones.
- Wrong chips must be tempting — close in meaning or wording, never obviously silly.
- `source_concept_ids` must trace to real concepts in this lesson's source map.
- This is the compact game schema. Do NOT add full-CBP fields
  (no multi-step scaffolding, no open-ended DPE, no simulation panels).

## Output format

Emit exactly one JSON object. Example (generic — replace with real lesson content):

```json
{
  "title": "Photosynthesis — Reactants and Products",
  "source_concept_ids": ["concept_photosynthesis"],
  "interaction_mode": "sentence_fill",
  "instruction": "Choose the one chip that correctly completes the sentence based on the lesson. Only one chip matches the source concept.",
  "interaction_payload": {
    "sentence": "During photosynthesis, plants use sunlight to convert carbon dioxide and water into glucose and ____.",
    "chips": [
      { "label": "oxygen", "is_correct": true, "reason": "" },
      { "label": "nitrogen", "is_correct": false, "reason": "Nitrogen is not a product of photosynthesis; it is fixed by bacteria." },
      { "label": "carbon monoxide", "is_correct": false, "reason": "Carbon monoxide is not produced; oxygen gas is released." },
      { "label": "water vapour only", "is_correct": false, "reason": "Water vapour may transpire but is not the primary by-product of the light reactions." }
    ]
  },
  "why_prompt": "Explain which concept from the lesson tells you why 'oxygen' is the correct answer, why each of the other chips fails at the concept level (not just because they sound wrong), and what error a student guessing by elimination alone would be likely to make.",
  "expected_reasoning_keywords": ["photosynthesis", "light reactions", "by-product", "oxygen", "carbon dioxide"]
}
```

## Language

{{LANGUAGE_RULES}}
