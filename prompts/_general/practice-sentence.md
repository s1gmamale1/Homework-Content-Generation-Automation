# Prompt: Practice Game — Sentence Fill — {{SUBJECT}}

You are generating ONE compact **Sentence Fill** practice game for this {{SUBJECT}}
lesson. The student sees a sentence with one blank (marked `____`) and a set of
word/phrase choices. Exactly one choice correctly completes the sentence according to
the lesson concept; the others are plausible distractors that fail for concept-level
reasons — wrong term, wrong cause/effect, reversed/opposite cause-effect connector,
too broad, too narrow, reversed meaning, opposite meaning, or wrong register.

Keep this a single short game. Do NOT expand it into a multi-step case with learning
blocks, MCQ checkpoints, or a final consequence panel.

## Primary rules

- **Draw the sentence and choices from this lesson.** The sentence must test a real
  concept, rule, definition, or relationship taught in this session's {{SUBJECT}}
  content — not a trivial detail — and must not contradict the lesson's Flashcards
  terminology.
- **Concept-level distractors.** Each wrong choice must be wrong for a concept reason
  (too broad, too narrow, conceptually reversed, wrong register, or
  plausible-but-incorrect), not an arbitrary one. The sentence must fail with a wrong
  choice because of the lesson concept, not because of random grammar weirdness.
- **Anti-leak.** The correct choice must not stand out by length, grammar fit, or word
  similarity to the sentence — only by meaning. Wrong choices should be tempting: close
  in meaning or wording, never obviously silly.

## What to produce

Write the game as Markdown sections, in this order:

- **Title** — short; names the concept or skill being tested.
- **How to play** — 1–2 sentences: the student reads the sentence and picks the one
  choice that fills the blank correctly according to the lesson.
- **Sentence** — one sentence containing exactly one blank marked `____`, meaningful
  with the blank in place.
- **Choices** — **3 or more** word/phrase choices, one per line as a plain bulleted
  list (`- <choice>`). Mark the correct one (exactly one) by ending its line with the
  tag `(To'g'ri)` — Russian «(Верно)», English `(Correct)`; the tag is stripped
  before the student sees the choice. For each wrong choice, give a brief note of why
  it fails at the concept level in the answer-key section below the choices (never
  beside the choice on its face). Every choice must be a non-empty string.
- **Why prompt** — for math/science lessons this is **mandatory**; for other subjects
  include it whenever the choice turns on reasoning. ONE open question asking the
  student to explain which concept makes the correct choice right, why the others fail
  (pointing to the concept, not just grammar), and what mistake a student guessing by
  surface similarity or word length would make. A short note (to yourself) of the
  concept words a sound answer should reach is fine, but keep it to a single open prompt.

## Non-negotiables

- Exactly one blank, clearly marked `____`; exactly one correct choice; 3+ choices total.
- Wrong choices fail for concept-level reasons and are tempting near-misses — never random filler.
- Terminology aligns with the lesson's Flashcards.
- Stay compact: one game, no MCQ checkpoints, no learning blocks, no consequence panel.

## Parser contract (non-negotiable)

The platform importer reads this game with fixed rules; violating any of them makes
the game silently drop or corrupt:

- The blank marker appears ONLY in the Sentence line. Never write a run of 3+
  underscores anywhere else (title, how-to-play, choices, key section) — the importer
  takes the FIRST line containing such a run as the sentence.
- Choices are a plain bulleted list (`- <choice>`). NEVER letter them (`A)`, `B)` …) —
  lettered lines are not recognized as choices and the whole game is dropped.
- Exactly one choice line ends with the correctness tag `(To'g'ri)` / «(Верно)» /
  `(Correct)`. The tag must sit on the choice line itself — a marking that lives only
  in the answer-key section is not read.
- Put the wrong-choice notes under a final heading containing the words `Javoblar
  kaliti` or `Answer key`, e.g. `### Javoblar kaliti (O'quvchiga ko'rinmaydi)` — the
  whole section under such a heading is removed from all student-facing text.
- Platform note: today the student plays this as a type-the-answer (free-recall) item
  built from the sentence plus the tagged correct choice; the untagged choices are not
  rendered yet. Author the distractors well anyway — the staged word-bank upgrade
  consumes them.

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
