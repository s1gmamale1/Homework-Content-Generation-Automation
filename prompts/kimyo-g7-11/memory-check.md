# Prompt: Memory Check — Kimyo

You are building a **Memory Check** after the student has reviewed the Flash Cards. The Memory Check tests whether the student can recall the key terms and formulas from the cards — not whether they can solve problems.

## Purpose

Retrieval practice: "Do you know the key cards?" Not "Can you apply the concept?"

## Input

- Lesson context (textbook section)
- Flash Cards output from the `flashcards` phase (REQUIRED — contains card IDs)

## Output

8–12 items. Each item MUST reference a flashcard by its `flashcard_id`.

## Supported item kinds — EXACTLY 3 (no others)

`multiple_choice`, `fill_blank`, `choose_correct_explanation`.

## Item format

Every item references the `flashcard_id` of the card it tests, and a `kind`.

- **multiple_choice / choose_correct_explanation** — `options` is EXACTLY 4 objects, each `{text, is_correct, reason}`. Exactly ONE option has `is_correct: true`. Every WRONG option's `reason` names the real misconception it represents (for choose_correct_explanation, `reason` is the flawed reasoning that makes it tempting). Keep options similar in length/style so formatting never gives away the answer. No `blanks`.
- **fill_blank** — put `_____` in the `prompt`; provide `blanks` = one or more `{answer, accepted_variations}` where `accepted_variations` lists other spellings/phrasings that should also count as correct. No `options`.
- `why_prompt` + `expected_reasoning_keywords` — REQUIRED for science (biology / physics / chemistry); optional otherwise.
- `correct_feedback` / `wrong_feedback` — short, encouraged.
- Distractors are real misconceptions, never joke/filler answers. Each item must trace to a card the student studied; keep the kinds balanced (no more than ~60% one kind). Pass gate stays 60%.

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
      "kind": "multiple_choice",
      "prompt": "Suv molekulasining kimyoviy formulasi qaysi?",
      "options": [
        { "text": "H₂O", "is_correct": true, "reason": "Suvda ikkita vodorod atomi va bitta kislorod atomi mavjud." },
        { "text": "H₂O₂", "is_correct": false, "reason": "H₂O₂ — vodorod peroksid, suv emas; ikkita kislorod atomini adashtirish keng tarqalgan." },
        { "text": "HO", "is_correct": false, "reason": "HO — gidroksi radikali, to'liq suv molekulasi emas." },
        { "text": "H₃O", "is_correct": false, "reason": "H₃O⁺ — gidroniy ion eritmada, lekin bu suvning formulasi emas." }
      ],
      "why_prompt": "Nega suvda aynan ikkita vodorod va bitta kislorod atomi bor?",
      "expected_reasoning_keywords": ["valentlik", "bog'", "elektron", "kovalent"],
      "correct_feedback": "To'g'ri! H₂O — suvning kimyoviy formulasi.",
      "wrong_feedback": "Eslab qoling: H₂O — ikkita H va bitta O."
    },
    {
      "flashcard_id": "card_2",
      "kind": "fill_blank",
      "prompt": "Davriy jadvalda elementlar _____ bo'yicha joylashtirilgan.",
      "blanks": [
        { "answer": "atom raqami", "accepted_variations": ["protonlar soni", "tartib raqami", "Z"] }
      ],
      "why_prompt": "Nega elementlar atom massasi emas, atom raqami bo'yicha tartiblanadi?",
      "expected_reasoning_keywords": ["proton", "yadro", "zaryad", "kimyoviy xossalar"],
      "correct_feedback": "To'g'ri! Elementlar atom raqami (protonlar soni) bo'yicha joylashadi.",
      "wrong_feedback": "Eslab qoling: tartib atom raqami (Z) bo'yicha, massa bo'yicha emas."
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
5. ✓ At least 2 of the 3 kinds represented? No kind exceeds ~60%?
6. ✓ Every `multiple_choice` / `choose_correct_explanation` item has exactly 4 option objects with exactly one `is_correct: true` and no `blanks`?
7. ✓ Every `fill_blank` item has `_____` in `prompt`, a `blanks` array, and no `options`?
8. ✓ Every item has `why_prompt` and `expected_reasoning_keywords` (REQUIRED for chemistry)?
