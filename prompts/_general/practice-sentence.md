# Prompt: Practice Game — Sentence Fill — {{SUBJECT}}

You are generating the **Sentence Fill** practice set for this {{SUBJECT}} lesson:
**7–10 fill-in items** (use 7 for grades 1–6; fewer for a narrower lesson). Each item is a short passage with blanks (marked `____`) and
its own word bank; the student fills every blank from that item's bank. Wrong bank
entries are plausible distractors that fail for concept-level reasons — wrong term,
wrong cause/effect, reversed/opposite cause-effect connector, too broad, too narrow,
reversed meaning, opposite meaning, wrong register, a changed formula/unit/variable
meaning, or an unsupported evidence claim. The passage stays fluent either way and
wrong choices are tempting, not nonsense.

Keep every item a compact fill game. Do NOT expand any item into a multi-step case
with learning blocks, MCQ checkpoints, or a final consequence panel.

## Primary rules

- **7–10 items, each testing a DIFFERENT fact of the lesson.** Spread the set across
  the lesson's distinct concepts, rules, definitions, values, and relationships —
  never two rewordings of the same fact. If the lesson genuinely cannot support 7
  distinct facts, cover every distinct fact it has and stop (never pad with
  rephrasings). Use 7 for grades 1–6 when there are enough distinct facts.
- **Draw every passage and bank from this lesson.** Each item must test a real
  concept, rule, definition, or relationship taught in this session's {{SUBJECT}}
  content — not a trivial detail — and must not contradict the lesson's Flashcards
  terminology.
- **Per item: 1–6 blanks.** Most items use 1–2 blanks; use more only when the
  passage naturally carries them. Every blank removes a concept-bearing word or
  value — never a function word.
- **Per item: its own word bank** = every answer (verbatim) plus 1–3 plausible
  distractors. All bank entries distinct; the answers themselves distinct
  (case-insensitive, ignoring extra spaces) — the interface consumes each bank chip
  once, so a repeated answer makes the item unfinishable.
- **Concept-level distractors.** Each wrong entry is wrong for a concept reason
  (too broad, too narrow, conceptually reversed, wrong register, or
  plausible-but-incorrect), never an arbitrary one.
- **Anti-leak.** Correct entries must not stand out by length, grammar fit, or word
  similarity — only by meaning. A bank where two entries could be defended as
  correct for the same blank is a broken item.
- **Judged on meaning, not well-formedness.** An entry that keeps the passage
  grammatical is still wrong if it changes the source concept. Never smuggle a
  changed formula, unit, chronology, term, or safety rule in as a "wrong" entry.
- **What the blank IS for this {{SUBJECT}}.** When {{SUBJECT}} is a mathematics
  lesson, blanks test its taught values, operators, concepts or relationships
  (not incidental story words), and the surrounding text keeps the source's
  numbers and glyphs. When {{SUBJECT}} is a
  chemistry lesson, a blank is never a formula fragment — a partial formula exposes
  a corrupted formula as a candidate string. When {{SUBJECT}} is a history lesson,
  blanks are terms, dates, or sequence members — never causal connectors that
  smuggle a claim. For other subjects, keep the general rules above.

## What to produce

Write the set as Markdown sections, in this order:

- **Title** (`#`) — short; names the lesson skill the set tests.
- **How to play** (`##`) — once, 1–2 sentences: read each passage, fill every blank
  from that item's word bank.
- **The items, numbered.** Each item, in order:
  - An `##` heading carrying the item number: `## 1-gap` … `## 10-gap` (uz) /
    `## Sentence 1` … (en) / `## Предложение 1` … (ru).
  - The passage: one short paragraph containing that item's blanks, each marked
    `____`, meaningful with the blanks in place.
  - `### Variantlar` (uz) / `### Choices` (en) / `### Варианты` (ru) — the item's
    word bank as a plain bulleted list (`- <entry>`), answers and distractors mixed.
    Mark every ANSWER entry by ending its line with the tag `(To'g'ri)` — Russian
    «(Верно)», English `(Correct)`; tagged entries = number of blanks, in any bank
    order (the platform maps them to blanks by meaning at import). The tag is
    stripped before the student sees the bank.
- **Why prompt** (`##`) — optional, once after the last item, when it adds
  useful reasoning. For grades 1–6 use one brief concrete question. It must use
  actual supplied facts; never ask about an absent map, chart or source. Do not
  add this prose as an unsupported field in the structured schema.
- **Answer key** (`###`) — one final section headed exactly
  `### Javoblar kaliti (O'quvchiga ko'rinmaydi)` (or `### Answer key (Hidden from
  the student)` in English output): for each item number, one line per wrong bank
  entry naming why it fails at the concept level. Never repeat the correctness tag
  inside the key. Inside the key, name items with a BOLD line (`**1-gap:**`,
  `**2-gap:**`), NEVER another `##` heading — the `## N-gap` headings are the
  item splitter, they appear exactly once per item in the student text, and a
  repeated one inside the key would split the key into phantom items.

## Non-negotiables

- 7–10 items (7 for grades 1–6, fewer if the lesson has fewer distinct facts),
  each on a different lesson fact; per item 1–6 blanks (`____`), a bank
  of every answer + 1–3 distractors, all entries distinct, answers distinct
  case-insensitively.
- Wrong entries fail for concept-level reasons and are tempting near-misses — never
  random filler.
- Terminology aligns with the lesson's Flashcards.
- Items stay compact: no MCQ checkpoints, no learning blocks, no consequence panels.

## Parser contract (non-negotiable)

The platform importer reads this set with fixed rules; violating any of them makes
an item silently drop or corrupt:

- A run of 3+ underscores appears ONLY inside passage lines (the paragraph directly
  under an item heading). Never write such a run in the title, how-to-play, banks,
  why prompt, or key.
- A `____` blank NEVER sits inside a `$…$` math span — structure the sentence so
  the blank stays in plain text (the formula around it may be `$…$`-wrapped, or the
  whole formula is itself the blanked-out bank entry). Bank entries that are
  formulas follow the notation contract (`$…$`-wrapped, KaTeX-safe) like any other
  displayed math.
- Every item heading is an `##` heading containing the item's number; the passage is
  the text between that heading and the item's bank heading.
- Bank entries are a plain bulleted list (`- <entry>`). NEVER letter them (`A)`,
  `B)` …) — lettered lines are not recognized and the item is dropped.
- Within one item's bank, the number of entries tagged `(To'g'ri)` / «(Верно)» /
  `(Correct)` equals that item's blank count. The tag sits on the bank line itself —
  a marking that lives only in the key is not read.
- The key section's heading contains the words `Javoblar kaliti` or `Answer key`;
  everything under it is removed from all student-facing text.
- Write the machine-read label strings — `(To'g'ri)`, `Javoblar kaliti`,
  `(O'quvchiga ko'rinmaydi)` — with the plain ASCII apostrophe `'` exactly as
  shown, whatever apostrophe style the surrounding prose uses.

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
