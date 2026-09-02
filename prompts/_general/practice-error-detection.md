# Prompt: Error Detection — spot the broken block, type the correction — {{SUBJECT}}

You are generating an **Error Detection** game for this {{SUBJECT}} lesson — a set
of blocks forming ONE coherent student solution (a worked equation, a sentence, a
labelled diagram, a sequence of steps) in which **exactly one** block is wrong. The
student finds the broken block, then **types the correction themselves**, then
explains why. The system does NOT auto-reveal; producing the fix is the
load-bearing cognitive event. The platform machine-parses this output into a
playable game — the section contract below is FIXED.

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
  correction restores the hedge or the term, and the accepted variants list
  hedged wordings so a synonymous hedge is never rejected.
- When {{SUBJECT}} is a language lesson (English, Russian, Ona tili,
  literature), the error is a grammar pattern the student has been taught —
  never a vocabulary trick — preferring the common mistake this lesson itself
  treats; the acceptance policy lives in the accepted-variants list.
- For other subjects, keep the general rule above: any real, source-anchored
  mistake a {{SUBJECT}} student actually makes.

## FIXED section contract (machine-parsed — exact headings, exact order)

After the `#` phase title, the output has EXACTLY these nine `##` sections, in
this order, with these EXACT heading strings — in EVERY output language (they are
machine-read keys like `Javoblar kaliti`; never translate them, never use a
synonym, never add or drop a section):

1. `## Tushunchalar`
2. `## Bloklar`
3. `## To'g'ri versiya`
4. `## Haqiqiy xato`
5. `## Maslahat`
6. `## "Nega" savoli`
7. `## To'g'ri javob fikr-mulohazasi`
8. `## Noto'g'ri tuzatish fikr-mulohazasi`
9. `## Ochish`

Write every heading apostrophe as the plain ASCII `'` exactly as shown, and the
quotes in `"Nega" savoli` as straight ASCII quotes — whatever punctuation style
the surrounding prose uses. Heading drift (e.g. "Sabab savoli",
"To'g'ri javob qaytarilishi", "Qayta urinish mulohazasi", "Oshkor") is a
contract violation: the game fails to import.

What each section contains:

- **`## Tushunchalar`** — the lesson concept(s) the game tests. Test something the
  student already saw in correct form earlier in the session, so they have a
  reference point.
- **`## Bloklar`** — **4–6 numbered blocks** (`1.` … `6.`, a plain ordered list,
  one block per line) forming ONE coherent student solution read top to bottom.
  **EXACTLY ONE block is wrong**, and its error embodies precisely the
  misconception named in `## Haqiqiy xato` — subtle enough to be plausible, never
  a glaring absurdity. Every other block must be correct. Do NOT mark the broken
  block inside the list — no "(XATO)"-style labels, no bold/emphasis tells, no
  wording hints. The list must read clean, exactly as the student will see it.
- **`## To'g'ri versiya`** — MUST open by naming the broken block as `N-blok`
  (e.g. `3-blok`), then give the corrected content and its real role. It MUST
  contain the line `Qabul qilinadigan variantlar:` followed by **3–7 accepted
  correction strings, EACH wrapped in backticks** on one or more lines (e.g.
  `` `x = 5` ``, `` `x=5` ``, `` `5` ``). These backticked strings are
  machine-parsed as the acceptance set — prose acceptance notes ("any equivalent
  phrasing is fine") are a contract violation. Cover spacing/word-order/synonym
  variants a correct student would actually type, so a right answer is never
  rejected on phrasing.
- **`## Haqiqiy xato`** — the genuine student mix-up this error encodes.
- **`## Maslahat`** — ONE probing hint. It must NEVER reveal the corrected block.
- **`## "Nega" savoli`** — exactly ONE question paragraph (the open why-question),
  then the exact line
  `Asoslovchi tushuntirish quyidagi fikrlarni o'z ichiga olishi lozim:`
  followed by **2–4 bullets** naming the reasoning points a sound
  explanation must reach (the correct content, its real role, where the wrong
  step or process actually applies).
- **`## To'g'ri javob fikr-mulohazasi`** — affirms the student spotted and fixed
  it themselves.
- **`## Noto'g'ri tuzatish fikr-mulohazasi`** — **encouraging**, offers the hint
  again. NOT a bare "Noto'g'ri".
- **`## Ochish`** — shown only after the second wrong attempt: opens by naming
  the broken block as `N-blok`, plus exactly ONE sentence beginning `Sababi:`
  giving the one-line reason. Nothing else.

## Non-negotiables

- **The wrong value propagates.** When the broken block sits mid-chain, every
  later block continues FROM the planted wrong value, so the chain stays
  internally consistent and the wrong path reaches its wrong end result. A later
  block that silently uses the corrected value destroys the exercise — the
  correct chain exists only in `## To'g'ri versiya`.
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
- **Don't over-reject the correction.** The backticked accepted-variants set IS
  the acceptance policy — populate it with every substantively-correct wording
  (spacing, word order, equivalent phrasing) a right answer could take.
- Test something already shown in correct form earlier in the session.
- **Feedback consistency.** `## To'g'ri javob fikr-mulohazasi`,
  `## Noto'g'ri tuzatish fikr-mulohazasi`, and `## Ochish` must all use the same
  correction stated in `## To'g'ri versiya`; never silently switch to a newly
  re-derived or corrected expression later.
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
  `## Tushunchalar`, `## Maslahat`, `## "Nega" savoli`, or the pre-reveal
  feedback may name or point to the broken block — it is identified only in
  `## To'g'ri versiya` and `## Ochish` (as `N-blok`).
- The backticks in the accepted-variants lines are load-bearing markup — never
  use backticks anywhere else in this output, and never replace them with quotes
  or apostrophes.

## Visuals

If you chose the labelled-diagram type (or any block needs a figure), describe the
diagram as a **placeholder** (see Output format) — either in the relevant block's
text or in a leading non-error block — never emit `<svg>`. Exactly one block is wrong.

## Output format

Respond in **Markdown only** (no JSON, no code-fenced JSON). Use `#` for the phase
title, then EXACTLY the nine `##` sections of the FIXED contract above, in order.
For visuals: do NOT emit `<svg>` or any image/HTML markup. For ANY visual (diagram
OR photo), emit a described placeholder instead — never the visual itself:
`![visual: <diagram|photo> — <what to depict, with every label, value, and axis> — image gen required](placeholder)`
The description must be self-sufficient: name the medium and every label/value/axis
so the visual can be produced from the text alone. Never output raw `<svg>`, never
fabricate an image, never invent an image URL.

The nine section headings stay in their exact forms above in EVERY output
language; section BODIES are written in the output language as usual. Inside
`## To'g'ri versiya` and `## Ochish` name the broken block as `N-blok`
(e.g. `3-blok`).

## Language

{{LANGUAGE_RULES}}

{{NOTATION_RULES}}
