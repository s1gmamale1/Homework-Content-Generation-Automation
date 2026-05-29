# Prompt: Memory Check — History

You are building a **Memory Check** after the student has reviewed the Flash Cards. The Memory Check tests whether the student can recall the key terms and formulas from the cards — not whether they can solve problems.

## Purpose

Retrieval practice: "Do you know the key cards?" Not "Can you apply the concept?"

## Input

- Lesson context (textbook section)
- Flash Cards output from the `flashcards` phase (REQUIRED — contains card IDs)

## Output

8–12 items. Each item MUST reference a flashcard by its `flashcard_id`.

## Supported item kinds — EXACTLY 3 (no others)

### `mc` — Multiple Choice
- Question about a card's front or back.
- 4 options, 1 correct.
- Distractors: plausible wrong formulas/values a student might confuse.

### `tf` — True / False
- Statement derived from a card. Student taps True or False.
- Test a rule or common misconception — not trivial recall.

### `tile_match` — Tile Match
- `prompt` = a term/expression from a card's front.
- `options` = list of 4 definitions/values; one matches the card's back.
- `correct_index` = index of the matching definition.

## Rules

- Every item MUST have `flashcard_id` set to the card it tests (e.g. `"card_3"`).
- Use all 3 kinds. No more than 60% of any single kind.
- `pass_threshold` is always 0.60 — do NOT change it.
- Language: Uzbek, "Siz" formal.
- Do NOT test problem-solving or calculation steps here. Only recall of card content.

## Output format — JSON matching MemoryCheckPack schema

```json
{
  "items": [
    {
      "flashcard_id": "card_1",
      "prompt": "...",
      "kind": "mc | tf | tile_match",
      "options": ["A", "B", "C", "D"],
      "correct_index": 0,
      "explanation": "..."
    }
  ],
  "pass_threshold": 0.60
}
```

## Self-check

1. ✓ Every item has a non-empty `flashcard_id`?
2. ✓ Only kinds used: `mc`, `tf`, `tile_match`?
3. ✓ `pass_threshold` = 0.60?
4. ✓ No calculation problems — recall only?
5. ✓ At least 2 of the 3 kinds represented?
