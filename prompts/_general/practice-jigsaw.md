# Prompt: Practice Game — Jigsaw Assembly — {{SUBJECT}}

You are generating ONE compact **Jigsaw Assembly** practice game for this {{SUBJECT}}
lesson. The student is given a set of concept pieces and must assemble them according
to the source-supported relationships between them — concept ↔ definition, formula ↔
variable, cause ↔ effect, evidence ↔ claim, step ↔ result, or term ↔ example. Surface
similarity is never enough; every connection — and its direction — must be traceable
to this lesson.

> **Where jigsaw content lands.** There is no `jigsaw_config` block on the platform;
> a jigsaw phase is imported as a **`tile_match_config`** — each directed connection
> becomes one left↔right match pair (source piece on the left, target piece on the
> right). For that import to succeed the output must obey the tile-match contract:
> pieces in the exact `- **P1:** …` form below, and **exactly 3** directed pairs —
> 6 pieces total (the platform validator rejects fewer than 3 pairs; the content
> standard caps the board at 6 pieces, so 3 pairs is the one size satisfying
> both — never emit more). Follow the shapes here precisely, or
> the phase folds to plain study text instead of a playable board.

Keep this a single short game. Do NOT expand it into a multi-step case with learning
blocks, MCQ checkpoints, or a final consequence panel.

## Primary rules

- **Draw every piece and link from this lesson.** Each piece must trace to a concept
  actually taught in this session's {{SUBJECT}} content, and each valid connection
  must be one the lesson supports. Do not invent relationships because they "sound
  right," and do not contradict the lesson's Flashcards terminology.
- **Direction matters.** A reversed connection is wrong — the condition enables the
  theorem, not the reverse; the cause leads to the effect, not the reverse. The
  student must assemble pieces in the source-supported direction.
- **Math/geometry sanity check.** For algebra, verify identities by expansion or
  substitution, cancel only common multiplicative factors, and preserve original
  domain restrictions for rational expressions. For geometry, do not reverse
  theorem implications, do not assign rectangle/rhombus/square properties to a
  general parallelogram unless the condition is stated, remember that a square
  inherits both rectangle and rhombus properties, and do not reject `(n-2)*180°`
  for a simple concave polygon.
- **Anti-leak.** Correct partners must not be inferable from piece length, wording, or
  ordering — only from the relationship. Tempting wrong pairings come in three
  flavors: surface-related-but-unsupported, one-correct-node-plus-one-wrong-node,
  and reversed-or-irrelevant pair — so a guesser is drawn to them.
- **History assembles pairings, never causal claims.** When {{SUBJECT}} is a
  history lesson, a directed link is a pairing the lesson states outright, never
  a contested causal claim — an assembly game rehearses its links as settled
  fact, and a causal claim must be reasoned in the scenario and boss phases,
  never fixed as a puzzle edge (the history constraint under Relationship types
  below follows from this).
- **Biology pieces carry names; relations carry mechanism.** When {{SUBJECT}} is
  a biology lesson, pieces may carry names, structures, and levels; a visual
  placeholder cues nomenclature, never a process explanation — mechanism belongs
  in the directed relation (step→result), never inside a piece's text as a
  mini-lecture.
- **Chemistry pieces pair on the criterion.** When {{SUBJECT}} is a chemistry
  lesson, a substance piece pairs to its deciding observation or criterion,
  never its category noun; and a formula-bearing piece must be verbatim-correct
  — no corrupted-formula pieces as tempting wrong nodes.
- For any other subject, keep the general rules above.

## What to produce

Write the game as Markdown sections, in this order:

- **Title** — short; names the concept set and the assembly framing.
- **How to play** — 1–2 sentences: the student joins each piece to its partner using
  only the relationship types below; pieces connect only where the lesson supports it.
- **Relationship types** — name the **1–3** relationship types this round uses (chosen
  from concept↔definition, formula↔variable, cause↔effect, evidence↔claim,
  step↔result, term↔example), keeping only the types the lesson's content actually
  exhibits. When {{SUBJECT}} is a history lesson, exclude cause↔effect from the
  round's chosen types, use evidence↔claim only when the textbook states the
  support relation in so many words, and default history rounds to
  concept↔definition / term↔example / step↔result; other subjects choose from
  the full list.
- **Pieces** — **6–12** pieces, each a short labelled item (theorem, condition, given
  data, conclusion, step, result, evidence, or claim) drawn from the lesson. Write each
  piece as its own bullet in EXACTLY this form so it can be matched to its partner:
  `- **P1:** <piece content>` (bold `P`-tag, a colon, then the content). Number the
  tags P1, P2, P3, … in order.
- **Correct assembly** — list the source-supported connections as **at least 3**
  directed pairs, one per line, each in the form `P1 → P3 (cause ↔ effect)` (source
  tag, an arrow `→`, target tag, then the relationship in parentheses). Each pair
  becomes one tile-match pair, so give **3–6** pairs and make every tag resolve to a
  piece listed above. Fewer than three pairs cannot be imported.
- **Why prompt** — for math/science lessons this is **mandatory**; otherwise include
  it whenever the assembly turns on reasoning. ONE open question asking the student to
  explain which source concept/theorem they identified, why the assembly direction is
  correct, and what mistake would occur if pieces were joined by surface similarity or
  in the reversed direction. A short note (to yourself) of the concept words a sound
  answer should reach is fine, but keep it to a single open prompt.

## Non-negotiables

- 6–12 source-supported pieces written as `- **P1:** …` bullets; at most 3 relationship types.
- **At least 3** (up to 6) directed `P1 → P3` pairs — a board with fewer cannot be imported.
- Connection direction is enforced — a reversed link is wrong.
- Tempting wrong pairings are surface-related but unsupported — never random filler.
- Terminology aligns with the lesson's Flashcards.
- Stay compact: one game, no MCQ checkpoints, no learning blocks, no consequence panel.

## Output format

Respond in **Markdown only** (no JSON, no code-fenced JSON). Use `#` for the phase
title and `##`/`###` for the sections described above, in order. For visuals: do NOT emit `<svg>` or any image/HTML markup. For ANY visual (diagram
OR photo), emit a described placeholder instead — never the visual itself:
`![visual: <diagram|photo> — <what to depict, with every label, value, and axis> — image gen required](placeholder)`
The description must be self-sufficient: name the medium and every label/value/axis
so the visual can be produced from the text alone. Never output raw `<svg>`, never
fabricate an image, never invent an image URL.

## Language

{{LANGUAGE_RULES}}

{{NOTATION_RULES}}
