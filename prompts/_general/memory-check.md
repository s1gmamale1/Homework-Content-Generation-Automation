# Prompt: Memory Check — {{SUBJECT}}

You are building a **Memory Check** after the student has reviewed the Flash Cards. The Memory Check tests whether the student can recall the key terms and formulas from the cards — not whether they can solve problems.

## Purpose

Retrieval practice: "Do you know the key cards?" Not "Can you apply the concept?"

## Input

- Lesson context (textbook section)
- Flash Cards output from the `flashcards` phase (REQUIRED — contains card IDs)

## Output

8–12 items. Each item must name the studied flashcard it tests (refer to it by its short label, e.g. **card 3**, in the item heading) so the recall target is unambiguous.

## Supported item kinds — EXACTLY 3 (no others)

`multiple_choice`, `fill_blank`, `choose_correct_explanation`.

## Item format

Write each item as a `###` heading that states which kind it is and which studied card it tests, followed by the question and the answer material described below.

- **multiple_choice / choose_correct_explanation** — write the question, then give exactly 4 answer options as a labelled list (A–D). Mark which single option is correct. On every WRONG option add a short reason naming the real misconception it represents — for choose_correct_explanation that reason is the flawed reasoning that makes the option tempting to a half-learned student. Keep all four options similar in length, style, and register so formatting never gives away the answer. No blanks in these kinds.
- **fill_blank** — write the prompt sentence with `_____` marking the missing concept-bearing word (never a function word). Then state the expected answer and list any alternative spellings or phrasings that should also count as correct. No options in this kind.
- **why reasoning prompt** — after the answer, add a short "why" reasoning prompt with the key ideas the answer should mention. REQUIRED when {{SUBJECT}} is a science (biology / physics / chemistry, or any subject whose lesson is concept-and-mechanism based); optional otherwise.
- Short correct/wrong feedback lines are encouraged on each item.
- Distractors must encode the flawed reasoning that makes them tempting to a half-learned student — every wrong option is a real misconception, never a joke, filler, or nonsense answer. Each item must trace to a card the student studied; keep the kinds balanced (no more than ~60% one kind). Pass gate stays 60%.

## Rules

- Every item must name the studied card it tests (e.g. **card 3**) in its heading.
- Use at least 2 of the 3 kinds. No more than 60% of any single kind.
- The pass gate is always **0.60** — do NOT change it.
- Do NOT test problem-solving or calculation steps here. Only recall of card content.

## Language

{{LANGUAGE_RULES}}

## Output format

Respond in **Markdown only** (no JSON, no code-fenced JSON). Use `#` for the phase
title and `##`/`###` for the sections/items described above, in order. For visuals:
emit inline `<svg>` for diagrams; for a photo/raster you would otherwise need to
generate, emit `![placeholder: <short description> — image gen required](placeholder)`
— never fabricate an image and never invent an image URL.

Write each item as a `###` heading that names its kind and the studied card it tests,
then in the body include: the question prompt; the answer material (four labelled A–D
options with the correct one marked and a short reason on each wrong one, OR a
`_____` blank with its expected answer and accepted alternatives); a short "why"
reasoning prompt naming the key ideas the answer should mention (required for science);
and short correct/wrong feedback lines. State the pass gate once as **0.60**.

## Self-check

1. ✓ Every item names the studied card it tests?
2. ✓ Only kinds used: `multiple_choice`, `fill_blank`, `choose_correct_explanation`?
3. ✓ Pass gate stated as 0.60?
4. ✓ No calculation problems — recall only?
5. ✓ At least 2 of the 3 kinds represented? No kind exceeds ~60%?
6. ✓ Every multiple_choice / choose_correct_explanation item has exactly 4 labelled options, exactly one marked correct, a reason on each wrong option, and no blank?
7. ✓ Every fill_blank item has `_____` in the prompt, an expected answer with accepted alternatives, and no options?
8. ✓ Every item has a "why" reasoning prompt with the key ideas the answer should mention (REQUIRED when {{SUBJECT}} is a science)?
9. ✓ Every wrong option encodes a real misconception (the flawed reasoning that tempts a half-learned student) — no joke or nonsense distractors?
