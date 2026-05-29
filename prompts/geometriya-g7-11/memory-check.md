# Prompt: Memory Check — Geometriya

You are building a **Memory Check** after the student has reviewed the Flash Cards. The Memory Check tests whether the student can recall the key terms and formulas from the cards — not whether they can solve problems.

## Purpose

Retrieval practice: "Do you know the key cards?" Not "Can you apply the concept?"

## Input

- Lesson context (textbook section)
- Flash Cards output from the `flashcards` phase (REQUIRED — contains card IDs)

## Output

8–12 items. Each item MUST reference a flashcard by its `flashcard_id`.

## Supported item kinds — EXACTLY 3 (no others)

### `multiple_choice` — Multiple Choice
- Question about a card's front or back.
- 4 options, 1 correct.
- Distractors: plausible wrong terms, formulas, values, or definitions a student might confuse.

### `fill_blank` — Fill in the Blank
- `prompt` contains one blank marker (`_____`) asking for a missing term, value, formula, or short phrase from a card.
- `options` may be empty; `correct_index` should be null.
- Put the exact expected answer in `explanation` so the renderer/review can show it.

### `choose_correct_explanation` — Choose Correct Explanation
- Question asks which explanation correctly connects a card's front and back.
- 4 explanation options, 1 correct.
- Distractors are flawed reasoning, not random wrong answers.

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
      "kind": "multiple_choice | fill_blank | choose_correct_explanation",
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
2. ✓ Only kinds used: `multiple_choice`, `fill_blank`, `choose_correct_explanation`?
3. ✓ `pass_threshold` = 0.60?
4. ✓ No calculation problems — recall only?
5. ✓ At least 2 of the 3 kinds represented?
