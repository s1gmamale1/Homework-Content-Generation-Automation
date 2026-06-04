# Prompt: Error Detection — spot the broken block, type the correction — {{SUBJECT}}

You are generating an **Error Detection** task for this {{SUBJECT}} lesson — a set
of blocks (a labelled diagram, a worked equation, a sentence, a sequence of
steps) in which **exactly one** block is wrong. The student finds the broken
block, then **types the correction themselves**. The system does NOT auto-reveal;
producing the fix is the load-bearing cognitive event.

Pick the block type that fits this {{SUBJECT}} lesson — derive it from the
lesson content:

- **Worked equation** — the blocks are the lines/steps of a worked solution.
- **Sentence** — the blocks are the words or phrases of a sentence (for English,
  the error must be a grammar pattern the student has been taught — not a
  vocabulary trick or an idiom they wouldn't know).
- **Labelled diagram** — the blocks are the labels on the diagram.

## What to produce

Write the task as Markdown sections, in this order:

- **Concepts** — the lesson concept(s) the task tests. Test something the student
  already saw in correct form earlier in the session, so they have a reference
  point.
- **The blocks** — **3 or more** blocks (4 for G1–4, 5–6 for G5–8, 6–8 for
  G9–11), listed in order. Present each block's content clearly. **EXACTLY ONE
  block is wrong** — it names the wrong thing or applies the wrong step/role for
  the part it represents. Every other block must be correct so the slip is
  subtle, not glaring. Make clear (to the reader of this output, not to the
  student) which block is the broken one.
- **The correct version** — the right content / real role of the broken block,
  and a note on the wordings that should count as substantively correct so a
  right answer is never rejected on phrasing alone.
- **The real mistake** — name the genuine student mix-up this error encodes.
- **Hint** — ONE probing hint. It must NEVER reveal the corrected block.
- **Why prompt** — **MANDATORY** (non-empty) for worked-equation and
  labelled-diagram tasks (math/science); optional for sentence/grammar tasks.
  e.g. "Bu blok nega noto'g'ri edi?" Name the reasoning a sound explanation
  should reach (the correct content, its real role, where the wrong step or
  process actually applies).
- **Correct feedback** — affirms they spotted and fixed it themselves.
- **Wrong-correction feedback** — **encouraging**, offers the hint. NOT "Noto'g'ri".
- **Reveal** — shown only after the second wrong attempt: the correct block plus
  the one-line reason.

## Non-negotiables

- **Exactly one error.** Any other count is rejected by the validator.
- **Real mistake, not nonsense.** The broken block must be a genuine, common
  confusion a {{SUBJECT}} student actually makes — drawn from real student error
  patterns — NOT an absurd error every student spots instantly. ("3x = 999" as
  the wrong version of "3x = 6" is too obvious; "3x = 16" from miscomputing
  11 − 5 is a real slip.)
- **No auto-reveal.** No correct content in the hint or any pre-reveal feedback.
- **Strip Test must pass.** The {{SUBJECT}} concept must be the reason the task is
  solvable: remove it and the task collapses to "tap a block, type something" —
  no answer leakage, no way to find the error from format alone. If a student
  could solve it without the lesson concept, regenerate.
- Test something already shown in correct form earlier in the session.

## Visuals

If you chose the labelled-diagram type (or any block needs a figure), embed the
diagram as **inline `<svg>`** — either in the relevant block's text or in a
leading non-error block — following the universal SVG rules injected by the
runtime. Do NOT specify size or colours here. Exactly one block is wrong.

## Output format

Respond in **Markdown only** (no JSON, no code-fenced JSON). Use `#` for the phase
title and `##`/`###` for the sections/items described above, in order. For visuals:
emit inline `<svg>` for diagrams; for a photo/raster you would otherwise need to
generate, emit `![placeholder: <short description> — image gen required](placeholder)`
— never fabricate an image and never invent an image URL.

## Language

{{LANGUAGE_RULES}}
