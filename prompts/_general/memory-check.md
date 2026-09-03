# Prompt: Memory Check — {{SUBJECT}}

You are building a **Memory Check** after the student has reviewed the Flash Cards. The Memory Check tests whether the student can recall the key terms and formulas from the cards — not whether they can solve problems.

## Purpose

Retrieval practice: "Do you know the key cards?" Not "Can you apply the concept?"

## Input

- Lesson context (textbook section)
- Flash Cards output from the `flashcards` phase (REQUIRED — contains card IDs)

## Output

8–12 items. Each item must name the studied flashcard it tests (refer to it by its short label, e.g. **card 3**, in the item heading) so the recall target is unambiguous.

## Supported item kinds — EXACTLY 3 (no others)

`multiple_choice`, `fill_blank`, `choose_correct_explanation`.

## Item format

Write each item as a `###` heading that states which kind it is and which studied card it tests, followed by the question and the answer material described below.

- **multiple_choice / choose_correct_explanation** — write the question, then give exactly 4 answer options, one per line, each formatted `A) <option>` … `D) <option>` (letter + `)`). After the options, name the correct one on its own line: `**To'g'ri javob:** <letter>` — this line is stripped before the student sees the item. Then give each WRONG option's short reason on its own line, formatted `Noto'g'ri (<letter>): <reason>` — and for AT LEAST ONE wrong option per item the reason must NAME the specific wrong belief that produces it ("...deb o'ylash xatosi" / "the belief that ..."), not merely state why the option is wrong — never inline in the option text (an inline note ships to the student inside the option label and marks the answer by elimination — the em-dash form `A) … — To'g'ri! …` / `… — Noto'g'ri. …` is the canonical example of this violation and is banned), and never starting a line with a bare letter + `.`/`)` (it would be mis-read as a fifth option). For choose_correct_explanation that reason is the flawed reasoning that makes the option tempting to a half-learned student. Keep all four options similar in length, style, and register so formatting never gives away the answer — if exactly one option is capitalized, or exactly one option ends with a period, that is an answer leak. **Distribute the correct letters across the phase:** use at least three different correct letters over the lettered items, never the same correct letter on more than two consecutive items, and never one letter for every item — a deck whose answers are all `A` is solved by pattern, not knowledge. No blanks in these kinds.
- **fill_blank** — write the prompt sentence with `_____` marking the missing concept-bearing word (never a function word). The `_____` blank NEVER sits inside a `$…$` math span — structure the sentence so the blank stays in plain text (close the span before the blank, or make the whole formula element the blanked word). State the expected answer on its own line as `**Kutilayotgan javob:** <answer>` and alternatives as `**Muqobil javoblar:** <list>` — exactly these labels, which are reliably stripped from student-facing text; both are TYPED answers: plain keyboard text only, never `$`, backslash commands, or characters a student cannot type. No options in this kind. *Platform note:* today's importer folds fill_blank items into read-only recall text (not gradable yet) and routes the phase to review — prefer the two MCQ kinds for most items and use fill_blank sparingly, only where the recall target is inherently cloze-shaped.
- **why reasoning prompt** — after the answer, add a short "why" reasoning prompt with the key ideas the answer should mention. REQUIRED when {{SUBJECT}} is a science (biology / physics / chemistry, or any subject whose lesson is concept-and-mechanism based); optional otherwise.
- Short correct/wrong feedback lines are encouraged on each item.
- Distractors must encode the flawed reasoning that makes them tempting to a half-learned student — every wrong option is a real misconception, never a joke, filler, or nonsense answer. Calibrate distractor subtlety to the grade band: **G5–6** plain, obvious mistakes; **G7–8** at least one plausible near-miss; **G9–11** subtle distractors that require knowing the rule, not just recognizing a familiar term — these may use partially-true reasoning that misses the deciding step, each still provably wrong for this question. Each item must trace to a card the student studied; keep the kinds balanced (no more than ~60% one kind). Pass gate stays 60%.
- A wrong option that is correct in another framing of the same fact is banned — every wrong option must be unambiguously wrong for the question asked; an option a well-taught student could defend as correct is a broken item, not extra difficulty. When {{SUBJECT}} is a science whose lesson is mechanism-based (biology above all), choose_correct_explanation wrong options are wrong-LEVEL explanations — fluent, confident, at the wrong level — and no option, right or wrong, carries purpose language ("…uchun", "so that the body can…"). choose_correct_explanation distractors differ in the reasoning, not the terminology — fotosintez / glyukoza sintezi / fotogeneratsiya is a vocabulary trick, not a reasoning test.

## Rules

- Every item must name the studied card it tests (e.g. **card 3**) in its heading.
- Use at least 2 of the 3 kinds. No more than 60% of any single kind. Avoid two consecutive items of the same kind when possible.
- The pass gate is always **0.60** — do NOT change it.
- Do NOT test problem-solving or calculation steps here. Only recall of card content.
- For math/geometry lessons, every recalled rule, formula, option, and
  misconception must pass a factual sanity check: verify algebraic identities by
  expansion or substitution; preserve original domain restrictions for rational
  expressions; do not reverse theorem implications; remember that a square
  inherits both rectangle and rhombus properties; and do not reject
  `(n-2)*180°` for a simple concave polygon (only self-intersecting star figures
  need separate treatment).

## Language

{{LANGUAGE_RULES}}

## Output format

Respond in **Markdown only** (no JSON, no code-fenced JSON). Use `#` for the phase
title and `##`/`###` for the sections/items described above, in order. For visuals: do NOT emit `<svg>` or any image/HTML markup. For ANY visual (diagram
OR photo), emit a described placeholder instead — never the visual itself:
`![visual: <diagram|photo> — <what to depict, with every label, value, and axis> — image gen required](placeholder)`
The description must be self-sufficient: name the medium and every label/value/axis
so the visual can be produced from the text alone. Never output raw `<svg>`, never
fabricate an image, never invent an image URL.

Write each item as a `###` heading that names its kind and the studied card it tests
(the literal token `card N` must appear in the heading — e.g. `### multiple_choice —
card 3` — the importer only recognizes items whose heading carries it), then in the
body include: the question prompt; the answer material (four `A)`–`D)` option lines
followed by a `**To'g'ri javob:** <letter>` line and a `Noto'g'ri (<letter>): <reason>`
line per wrong option — write the machine-read labels `**To'g'ri javob:**` and
`Noto'g'ri (<letter>):` with the plain ASCII apostrophe `'` exactly as shown,
whatever apostrophe style the prose uses, and never let another word interrupt a
label — OR a `_____` blank with its `**Kutilayotgan javob:**` /
`**Muqobil javoblar:**` lines); a short "why" reasoning prompt naming the key ideas
the answer should mention (required for science); and short correct/wrong feedback
lines placed AFTER the options. State the pass gate once as **0.60** (decimal form,
never the integer `60`).

## Self-check

1. ✓ Every item names the studied card it tests?
2. ✓ Only kinds used: `multiple_choice`, `fill_blank`, `choose_correct_explanation`?
3. ✓ Pass gate stated as 0.60?
4. ✓ No calculation problems — recall only?
5. ✓ At least 2 of the 3 kinds represented? No kind exceeds ~60%?
6. ✓ Every multiple_choice / choose_correct_explanation item has exactly 4 labelled options, exactly one marked correct, a reason on each wrong option, and no blank?
7. ✓ Every fill_blank item has `_____` in the prompt, an expected answer with accepted alternatives, and no options?
8. ✓ Every item has a "why" reasoning prompt with the key ideas the answer should mention (REQUIRED when {{SUBJECT}} is a science)?
9. ✓ Every wrong option encodes a real misconception (the flawed reasoning that tempts a half-learned student) — no joke or nonsense distractors?
10. ✓ No option (right or wrong) is correct under another framing, uses purpose language (science), or leaks by formatting?

{{NOTATION_RULES}}
