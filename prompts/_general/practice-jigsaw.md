# Prompt: Practice Game — Jigsaw Assembly — {{SUBJECT}}

You are generating ONE compact **Jigsaw Assembly** practice game for this {{SUBJECT}}
lesson. The student is given a set of concept pieces and must assemble them according
to the source-supported relationships between them — concept ↔ definition, formula ↔
variable, cause ↔ effect, evidence ↔ claim, step ↔ result, or term ↔ example. Surface
similarity is never enough; every connection — and its direction — must be traceable
to this lesson.

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
- **Anti-leak.** Correct partners must not be inferable from piece length, wording, or
  ordering — only from the relationship. Tempting wrong pairings come in three
  flavors: surface-related-but-unsupported, one-correct-node-plus-one-wrong-node,
  and reversed-or-irrelevant pair — so a guesser is drawn to them.

## What to produce

Write the game as Markdown sections, in this order:

- **Title** — short; names the concept set and the assembly framing.
- **How to play** — 1–2 sentences: the student joins each piece to its partner using
  only the relationship types below; pieces connect only where the lesson supports it.
- **Relationship types** — name the **1–3** relationship types this round uses (chosen
  from concept↔definition, formula↔variable, cause↔effect, evidence↔claim,
  step↔result, term↔example), keeping only the types the lesson's content actually
  exhibits.
- **Pieces** — **3–6** pieces, each a short labelled item (theorem, condition, given
  data, conclusion, step, result, evidence, or claim) drawn from the lesson. Give each
  a short tag (e.g. P1, P2) so the assembly can be stated.
- **Correct assembly** — list the source-supported connections as directed pairs (e.g.
  `P1 → P3 (cause ↔ effect)`), so the intended solution is unambiguous.
- **Why prompt** — for math/science lessons this is **mandatory**; otherwise include
  it whenever the assembly turns on reasoning. ONE open question asking the student to
  explain which source concept/theorem they identified, why the assembly direction is
  correct, and what mistake would occur if pieces were joined by surface similarity or
  in the reversed direction. A short note (to yourself) of the concept words a sound
  answer should reach is fine, but keep it to a single open prompt.

## Non-negotiables

- 3–6 source-supported pieces; at most 3 relationship types.
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
