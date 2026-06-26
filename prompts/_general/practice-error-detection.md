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
- **The blocks** — listed in order. Block count is **per block type** and by
  grade band: equation steps — 3 (G1–4) / 4–5 (G5–8) / 5–6 (G9–11); sentence
  blocks — 3–4 (G1–4) / 4–5 (G5–8) / 5–6 (G9–11); diagram labels — 4 (G1–4) /
  5–6 (G5–8) / 6–8 (G9–11). Present each block's content clearly. **EXACTLY ONE
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
  11 − 5 is a real slip. Grammar parallel: "She have been to Tashkent last year"
  → "went" is a real taught-pattern tense slip, not a vocabulary trick.)
- **No auto-reveal.** No correct content in the hint or any pre-reveal feedback.
- **Strip Test must pass.** The {{SUBJECT}} concept must be the reason the task is
  solvable: remove it and the task collapses to "tap a block, type something" —
  no answer leakage, no way to find the error from format alone. If a student
  could solve it without the lesson concept, regenerate.
- **No time pressure.** Don't score for speed or impose a timer — this is
  recognition + construction, not a race.
- **Don't over-reject the correction.** Accept any substantively-correct fix
  even when it isn't an exact string match (spacing, word order, equivalent
  phrasing) — pair with the accepted-wordings note above.
- Test something already shown in correct form earlier in the session.

## Visuals

If you chose the labelled-diagram type (or any block needs a figure), describe the
diagram as a **placeholder** (see Output format) — either in the relevant block's
text or in a leading non-error block — never emit `<svg>`. Exactly one block is wrong.

## Output format

Respond in **Markdown only** (no JSON, no code-fenced JSON). Use `#` for the phase
title and `##`/`###` for the sections/items described above, in order. For visuals: do NOT emit `<svg>` or any image/HTML markup. For ANY visual (diagram
OR photo), emit a described placeholder instead — never the visual itself:
`![visual: <diagram|photo> — <what to depict, with every label, value, and axis> — image gen required](placeholder)`
The description must be self-sufficient: name the medium and every label/value/axis
so the visual can be produced from the text alone. Never output raw `<svg>`, never
fabricate an image, never invent an image URL.

## Language

{{LANGUAGE_RULES}}
