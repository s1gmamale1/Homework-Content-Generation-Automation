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

`multiple_choice`, `fill_blank`, `choose_correct_explanation`.

## Item format

Every item references the `flashcard_id` of the card it tests, and a `kind`.

- **multiple_choice / choose_correct_explanation** — `options` is EXACTLY 4 objects, each `{text, is_correct, reason}`. Exactly ONE option has `is_correct: true`. Every WRONG option's `reason` names the real misconception it represents (for choose_correct_explanation, `reason` is the flawed reasoning that makes it tempting). Keep options similar in length/style so formatting never gives away the answer. No `blanks`.
- **fill_blank** — put `_____` in the `prompt`; provide `blanks` = one or more `{answer, accepted_variations}` where `accepted_variations` lists other spellings/phrasings that should also count as correct. No `options`.
- `why_prompt` + `expected_reasoning_keywords` — optional for history.
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
      "prompt": "O'zbekiston mustaqilligini qaysi sana e'lon qildi?",
      "options": [
        { "text": "1991-yil 1-sentabr", "is_correct": true, "reason": "O'zbekiston mustaqilligini 1991-yil 1-sentabrda e'lon qildi." },
        { "text": "1991-yil 31-avgust", "is_correct": false, "reason": "31-avgust — sessiya o'tkazilgan kun, lekin rasmiy e'lon 1-sentabrda bo'ldi." },
        { "text": "1990-yil 20-iyun", "is_correct": false, "reason": "20-iyun 1990 — suverenitet deklaratsiyasi, to'liq mustaqillik emas." },
        { "text": "1992-yil 8-dekabr", "is_correct": false, "reason": "Bu SSSR rasman tarqatib yuborilgan sana, O'zbekiston mustaqilligini e'lon qilgan sana emas." }
      ],
      "correct_feedback": "To'g'ri! Mustaqillik 1991-yil 1-sentabrda e'lon qilindi.",
      "wrong_feedback": "Eslab qoling: 1-sentabr — Mustaqillik kuni."
    },
    {
      "flashcard_id": "card_2",
      "kind": "fill_blank",
      "prompt": "Ikkinchi Jahon urushi _____ yilda tugadi.",
      "blanks": [
        { "answer": "1945", "accepted_variations": ["1945-yil", "mil. 1945"] }
      ],
      "correct_feedback": "To'g'ri! Ikkinchi Jahon urushi 1945-yilda tugadi.",
      "wrong_feedback": "Eslab qoling: urush 1945-yilda, Yaponiyaning taslim bo'lishi bilan tugadi."
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
