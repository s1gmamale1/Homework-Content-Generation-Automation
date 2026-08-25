# Prompt: Practice Game — Memory Match — {{SUBJECT}}

You are generating ONE compact **Memory Match** practice game for this {{SUBJECT}}
lesson. The student flips cards to find matching pairs drawn from this lesson's
concepts. Each match must be meaningful — a term paired with its definition, a
structure paired with its function, a step paired with its result, or a cause paired
with its effect — never a surface-similarity guess and never card position.

Keep this a single short game. Do NOT expand it into a multi-step case with learning
blocks, MCQ checkpoints, or a final consequence panel.

## Primary rules

- **Draw every pair from this lesson.** Each card pair must trace to a concept,
  relationship, or term actually taught in this session's {{SUBJECT}} content. Do
  not invent terms the lesson never covered, and do not contradict the lesson's
  Flashcards terminology.
- **Anti-leak.** The correct partner must not be inferable from formatting, length,
  word order, or card position — only from understanding the relationship. If a
  student could pair the cards by surface cues alone, rework them.
- **Close distractors.** When two pairs share a confusable term or near-miss
  function, that is good — the wrong partners should be tempting near-misses, never
  random or silly.
- **Math/geometry sanity check.** For math lessons, verify identities by expansion
  or substitution, cancel only common multiplicative factors, and preserve
  original domain restrictions for rational expressions. For geometry lessons,
  do not reverse theorem implications, do not treat a general parallelogram as if
  it has all rectangle/rhombus/square properties, remember that a square inherits
  both rectangle and rhombus properties, and do not reject `(n-2)*180°` for a
  simple concave polygon.
- **History pairs are pairings, never claims.** When {{SUBJECT}} is a history
  lesson, a pair is a PAIRING, never a causal claim — term ↔ meaning, event ↔
  date, figure ↔ role; never event ↔ "because…". For history the cause ↔ effect
  and historical event ↔ consequence pair types are off the menu: a matching
  game rehearses its pairs as settled fact, and a contested causal claim must be
  reasoned (in the scenario and boss phases), never memorized as a card flip.
  Other subjects keep the full menu.
- **Biology imagery anchors names.** When {{SUBJECT}} is a biology lesson, a
  visual placeholder on a card may cue a NAME (structure ↔ name), never explain
  a process — imagery anchors nomenclature, never mechanism, and mechanism
  content stays out of card text.
- **Chemistry pairs match on the criterion.** When {{SUBJECT}} is a chemistry
  lesson, pairs match on the criterion, never the noun — a substance pairs with
  its deciding observation ("lakmusni qizartiradi"), never with its bare
  category label.
- For any other subject, keep the general pairing rules above.

## What to produce

Write the game as Markdown sections, in this order:

- **Title** — short; names the concept set being matched.
- **How to play** — 1–2 sentences telling the student to flip cards and join each
  card to its true partner by meaning, not by position.
- **Pairs** — **4–8** pairs. Present each pair as one list item showing both sides,
  e.g. `**<left card>** ↔ **<right card>**`. Every side must be non-empty and
  source-supported. Across the set the pairs should be distinguishable only by
  understanding the relationship (term ↔ meaning, part ↔ function, step ↔ result,
  cause ↔ effect, symbol ↔ rule, word ↔ correct usage, historical event ↔
  consequence, formula part ↔ quantity). When {{SUBJECT}} is a history lesson,
  the cause ↔ effect and historical event ↔ consequence types are off this
  menu; other subjects keep the full menu.
- **Why prompt** — for math/science lessons this is **mandatory**; for other
  subjects include it whenever a pair turns on reasoning. ONE open question asking
  the student to explain which concept connects a chosen pair, why those two sides
  belong together, and what mistake a student relying on guessing or surface
  similarity would make. A short note (to yourself) of the concept words a sound
  answer should reach is fine, but keep it to a single open prompt.

## Non-negotiables

- 4–8 source-supported pairs; both sides of every pair non-empty.
- Wrong partners are close, meaningful near-misses — never random filler.
- Terminology aligns with the lesson's Flashcards.
- Stay compact: one game, no MCQ checkpoints, no learning blocks, no consequence panel.

## Parser contract (non-negotiable)

The platform importer extracts ONLY the pair lines; everything else (title,
how-to-play, why prompt) is not shown inside the game. Its pair rules are fixed:

- One pair per bulleted line, exactly `- **<left>** ↔ **<right>**`, using the `↔`
  separator.
- No dash or arrow characters inside card text: `-`, `--`, `–`, `—`, `→`, `<->` are
  all read as pair separators, and the importer splits each pair line at the FIRST
  one it finds. Uzbek ordinal forms like "3-qonun" silently corrupt the pair — write
  them out ("uchinchi qonun").
- Bulleted lines are reserved for pairs. Write Title, How to play, and the Why prompt
  as plain prose (no list bullets) — a bulleted prose line containing a dash would be
  mis-read as a pair.

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
