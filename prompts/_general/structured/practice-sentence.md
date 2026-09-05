# Sentence Fill (structured) — {{SUBJECT}}

Build cloze items for this {{SUBJECT}} lesson. Return **JSON only**, conforming exactly to the
schema below. No prose, no code fences.

## Required shape

- `items` — **7-10 entries** (use 7 for grades 1–6), each testing a DIFFERENT
  fact of the lesson (never two
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
- Ground every sentence in THIS lesson's content. Use fewer items when there
  are fewer distinct facts; never pad with rephrasings.
- Every bank has exactly one defensible entry per blank: no synonyms, equivalent
  forms, subset answers, overlapping meanings, or grammar/style-only cues.
- Never blank a chemical formula fragment or corrupt a formula as a distractor.
  In mathematics test the actual taught value, operator, concept or relation.
  Preserve approximate, modal, geographic and chronological qualifiers.
- This schema has no reflection field: do not add one or refer to absent visuals.
- Keep notation inside JSON string values and escape backslashes as JSON requires.
  Answers are word-bank entries, not typed free text: repeat the bank entry verbatim,
  including math delimiters where applicable. A blank stays outside any math span.

{{LANGUAGE_RULES}}

{{NOTATION_RULES}}
