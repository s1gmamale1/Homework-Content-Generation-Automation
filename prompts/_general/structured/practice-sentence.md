# Sentence Fill (structured) — {{SUBJECT}}

Build cloze items for this {{SUBJECT}} lesson. Return **JSON only**, conforming exactly to the
schema below. No prose, no code fences.

## Required shape

- `items` — **7-10 entries**, each testing a DIFFERENT fact of the lesson (never two
  rewordings of one fact; if the lesson cannot support 7 distinct facts, cover every
  distinct fact it has). Each item:
  - `id` — short slug, unique across items.
  - `mode` — always the string `"word_bank"`. No other value is supported.
  - `passage` — the sentence, with **1-6** blanks written as exactly three underscores `___`.
  - `answers` — one entry per blank, in blank order.
  - `word_bank` — every answer, plus 1-3 plausible distractors.

## Rules

- `answers` must contain **no duplicates** (compared case-insensitively, ignoring extra spaces).
  The student interface consumes each word-bank chip once, so a repeated answer makes the exercise
  impossible to finish.
- `word_bank` entries must likewise be distinct, and must include every answer verbatim.
- Blanks must remove a concept-bearing word, never a function word.
- Ground every sentence in THIS lesson's content.

{{LANGUAGE_RULES}}
