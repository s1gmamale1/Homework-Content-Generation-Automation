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

What the planted error IS for this {{SUBJECT}} — condition the slip on the
subject, and keep it demonstrably wrong for its slot in every case (a block a
well-taught student could defend as correct under another reading is a solver
failure, not extra difficulty):

- When {{SUBJECT}} is a mathematics lesson (algebra and geometry included), the
  error is the wrong path EXECUTED TO A VALUE the student must catch — never a
  described or restated mistake. `3/5 × 3 = 9/5` sitting where `3/5 ÷ 3 = 1/5`
  belongs is an error item; a sentence describing a mistake is not.
- When {{SUBJECT}} is a history lesson, the error is a TRUE fact offered as
  evidence for a claim it does not bear on — a relevance error, never a false
  fact. Prefer the sentence / sequence-of-steps shape, with the blocks forming
  a short claim-and-evidence chain in which exactly one block is a
  true-but-irrelevant "evidence"; its correct version states what the block
  should have offered (relevant evidence).
- When {{SUBJECT}} is a chemistry lesson, the error is a classification made
  from appearance instead of the deciding criterion.
- When {{SUBJECT}} is a biology lesson, the error is a fluent explanation at
  the wrong level ("the heart puts oxygen into the blood" — fluent, confident,
  wrong) or purpose language presented as fact ("…uchun hosil qildi") — never a
  misspelled organelle name.
- When {{SUBJECT}} is a physics lesson, the blocks describe a situation and the
  qualitative claim (which quantity changes, which does not) is where the error
  lives; if a worked equation is used, the error sits in the physical setup
  line, not the arithmetic.
- When {{SUBJECT}} is an economics lesson, the error is a modal-verb upgrade
  ("kamayishi mumkin" → "kamayadi"), a missing standpoint, or an
  everyday/technical conflation (narx ≠ qiymat, foyda ≠ daromad); the
  correction restores the hedge or the term, and the accepted-wordings note in
  **The correct version** lists hedged variants so a synonymous hedge is never
  rejected.
- When {{SUBJECT}} is a language lesson (English, Russian, Ona tili,
  literature), the error is a grammar pattern the student has been taught —
  never a vocabulary trick — preferring the common mistake this lesson itself
  treats; state the acceptance policy once in **The correct version**.
- For other subjects, keep the general rule above: any real, source-anchored
  mistake a {{SUBJECT}} student actually makes.

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
  subtle, not glaring. Do NOT mark the broken block inside the blocks list — no
  "(XATO)"-style labels, no bold/emphasis tells, no wording hints. The list must
  read clean, exactly as the student will see it. Identify the broken block
  ONLY in **The correct version** and **Reveal** sections below, naming it by
  its number there.
- **The correct version** — Open by naming the broken block's number, then give
  the right content / real role of the broken block, and a note on the wordings
  that should count as substantively correct so a right answer is never
  rejected on phrasing alone.
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

- **The wrong value propagates.** When the broken block sits mid-chain, every
  later block continues FROM the planted wrong value, so the chain stays
  internally consistent and the wrong path reaches its wrong end result. A later
  block that silently uses the corrected value destroys the exercise — the
  correct chain exists only in "The correct version".
- **No inline answer marker.** The blocks list is student-visible; any marker,
  label, or typographic tell identifying the broken block inside it defeats the
  entire exercise.
- **Exactly one error.** Any other count is rejected by the validator.
- **Verify every non-broken block.** Before finishing, re-derive every block that
  is meant to be correct. For worked equations, re-compute arithmetic and algebra
  instead of copying it forward; for geometry, name each property by its standard
  condition. If any non-broken block fails this check, regenerate the task.
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
- **Feedback consistency.** The Correct feedback, Wrong-correction feedback, and
  Reveal must all use the same correction stated in **The correct version**; never
  silently switch to a newly re-derived or corrected expression later.
- **Math/geometry sanity check.** For algebra, verify identities by expansion or
  substitution, cancel only common multiplicative factors, and preserve original
  domain restrictions for rational expressions. For geometry, do not reverse
  theorem implications, do not assign rectangle/rhombus/square properties to a
  general parallelogram unless the condition is stated, remember that a square
  inherits both rectangle and rhombus properties, and do not reject `(n-2)*180°`
  for a simple concave polygon.
- **A corrupted chemical formula is never the planted error.** A wrong
  subscript names a different substance, and displaying it teaches it; formula
  slips belong to typed-repair contexts, never to a find-the-broken-block list.
  Every formula-bearing non-broken block must be verbatim-correct.
- **Never name the broken block outside the answer sections.** No prose in
  Concepts, the Hint, the Why prompt, or the pre-reveal feedback may name or
  point to the broken block — it is identified only in **The correct version**
  and **Reveal**.

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

Write the section headings in the output language, keeping the heading
vocabulary the platform recognizes: **The correct version** — Uzbek
"To'g'ri versiya", Russian «Правильная версия»; **Reveal** — Uzbek "Ochish"
or "Oshkor", Russian «Раскрытие». Inside those answer sections name the broken
block by its block id — "Blok N" / "N-blok" (e.g. "Blok 3", "3-blok").

## Language

{{LANGUAGE_RULES}}

{{NOTATION_RULES}}
