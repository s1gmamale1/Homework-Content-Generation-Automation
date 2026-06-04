# Prompt: Practice Game — Tic-Tac-Toe Decision Grid — {{SUBJECT}}

You are generating ONE compact **Tic-Tac-Toe Decision Grid** practice game for this
{{SUBJECT}} lesson. The student sees a 3×3 board of candidate actions or answers and
must identify which cells correctly apply this lesson's concept and which do not. The
board must be solvable **only through the lesson concept**, never by general intuition
or test-taking logic.

Keep this a single short game. Do NOT expand it into a multi-step case with learning
blocks, MCQ checkpoints, state meters, or a final consequence panel.

## Primary rules

- **Draw every cell from this lesson.** All nine cells must trace to a concept, rule,
  formula, or mechanism actually taught in this session's {{SUBJECT}} content. No
  generic filler, and no terminology that conflicts with the lesson's Flashcards.
- **Solvable only by the concept.** A student should not be able to mark the correct
  cells by common sense or test-taking habits. Removing the lesson concept must make
  the grid unsolvable.
- **Anti-leak.** Correct cells must not stand out by length, phrasing, or position.
  Wrong cells must be plausible and tempting — a fast-but-unsupported action, an
  overly broad action, a wrong-order action, or a common-mistake distractor — never
  obviously silly.

## What to produce

Write the game as Markdown sections, in this order:

- **Title** — short; names the concept and the decision-grid framing.
- **How to play** — 1–2 sentences: the student selects every cell on the 3×3 board
  that correctly applies the lesson concept.
- **Board** — **exactly 9 cells** laid out as a 3×3 grid (a markdown table is fine).
  Number or letter the cells so the answer can be referenced. At least one cell is a
  correct application (typically 1–3 correct cells); the rest are tempting wrong
  applications. For each wrong cell, give a brief note of why it fails (it may be
  hidden in an answer-key section below the board, not on the cell face).
- **Why prompt** — **mandatory** (this is a math/science-style decision game). ONE
  open question asking the student to explain which concept they applied to mark the
  correct cell(s), why those cells satisfy the concept while the others do not, and
  what mistake a student relying on intuition rather than the concept would make. A
  short note (to yourself) of the concept words a sound answer should reach is fine,
  but keep it to a single open prompt.

## Non-negotiables

- Exactly 9 cells; at least one correct; all derived from lesson content.
- Wrong cells are plausible, tempting near-misses — never random filler.
- Terminology aligns with the lesson's Flashcards; numbers, formulas, and units unchanged.
- Stay compact: one game, no MCQ checkpoints, no learning blocks, no consequence panel.

## Output format

Respond in **Markdown only** (no JSON, no code-fenced JSON). Use `#` for the phase
title and `##`/`###` for the sections described above, in order. For visuals:
emit inline `<svg>` for diagrams; for a photo/raster you would otherwise need to
generate, emit `![placeholder: <short description> — image gen required](placeholder)`
— never fabricate an image and never invent an image URL.

## Language

{{LANGUAGE_RULES}}
