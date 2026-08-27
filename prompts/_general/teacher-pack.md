# Prompt: Teacher Pack — {{SUBJECT}}

You are building the **Teacher Pack** for a {{SUBJECT}} lesson: a **slide deck**
the teacher holds while delivering the lesson **at school**, before the student
opens that lesson's homework on the platform.

**This is a presentation, not a document.** Each slide becomes one section
(*bo'lim*) in the platform's library. A teacher does not read this — they
**project it and glance at it**. Every rule below about length exists because a
slide that has to be read is a slide that gets skipped.

**What it is for.** Akademiya homework is **post-lesson revision** — the teacher
taught the lesson, then the system opens that lesson's homework. Every homework
phase assumes something was taught. This deck is what makes that assumption true.

## Input

You receive:
1. The lesson's **coverage contract** (the extract) — the lesson's ground truth.
2. The **already-generated homework phases** for this lesson, in full.

This phase runs **last**. You are the only phase that sees the whole packet.

## THE SLIDE BUDGET (NON-NEGOTIABLE)

Measured across 81 slides of four real teaching decks:

| | words per slide |
|---|---|
| median | **32** |
| 76% of slides | **under 60** |
| 92% of slides | **under 90** |

**Your budget per slide: 25–60 words of body text. 90 is a hard ceiling.**
Title and breadcrumb do not count toward it.

A slide over 90 words is not a slide. **Split it into two.** Splitting is always
the correct move — the reference decks spend *ten* slides on one topic rather
than one dense slide listing ten things.

**A slide may be almost empty.** A three-word slide and a diagram is a real
slide; one of the reference decks carries 15 slides and no text at all.
Whitespace is not waste.

## Slide anatomy (every slide, no exceptions)

```
## <n>. <Title — the claim or the task, never a category label>
<breadcrumb — optional, repeated across one arc, e.g. "Doskada · 2/5">

<body: ONE of the four shapes below>

<closing line: "Diqqat:" or "Tekshiruv:" — one sentence, optional>
```

**The body is one of exactly four shapes. Never a stack of paragraphs.**

1. **Bullets** — 3–5 of them, each under 12 words.
2. **Labelled mini-blocks** — 2–4 blocks (`A` / `B` / `C`), 2–3 short lines each.
3. **A worked block** — the steps of one calculation or one sentence analysis.
4. **One short paragraph** — 40–50 words max, and only when the slide is
   carried by a figure or a single quotation.

**Marker pills.** Where a slide is an activity, open it with a pill in square
brackets: `[TAXTADA]` (at the board) · `[SINF ISHLAYDI]` (class works) ·
`[5 daqiqa]` (timebox) · `[AGAR XATO]` (the corrective branch).

**Titles are claims, not labels.** ✓ *"Kelajak savollari faqat Will bilan"*
✗ *"Grammatika qoidasi"*. A title a teacher can read aloud as a sentence is
doing its job.

## Textbook authority (NON-NEGOTIABLE)

Do NOT invent textbook facts, formulas, definitions, dates or claims. Every
factual claim traces to the coverage contract. Where the deck states something
the contract does not carry, mark that line `[manbada yo'q]` — the teacher will
say it out loud to a class, and an unmarked invention becomes a taught falsehood.

**Every rule carries the conditions under which it holds** — even when the
source omits them. Supplying a missing bound is the teaching we owe on top of
the book; state it and mark whose it is: *"darslik buni aytmaydi, biz aytamiz."*

## Relevance to the homework (NON-NEGOTIABLE)

The deck is **derived from the generated homework, not written alongside it.**

- **Quote, never paraphrase.** Reproduce a term, checkpoint, card or item
  **character-for-character** from the phase it came from.
- **Every concept the homework tests appears in this deck.**
- **Nothing in this deck that the homework does not use.**
- **Name the source phase** on every cross-reference, e.g. `(Keys: 2-kartochka)`.

## About the STUDENT — the hard prohibition

This deck is authored **before any student has done anything.** It **MUST NOT**
contain a single statement about a particular learner, nor any template
inviting one.

Banned outright: *"O'quvchi nimani tushundi"* · *"Qanday xato paydo bo'ldi"* ·
any strengths-and-weaknesses line · any characterisation (*"e'tiborsiz"*,
*"qobiliyatsiz"*).

Write **properties of the lesson** instead:
- ✓ *"Bu dars tekshiradigan tushunchalar: …"*
- ✗ *"O'quvchi bu xatoni qiladi."*

Per-student characterisation is produced at runtime by the AI Tutor, never
authored into a page.

## THE DECK (NON-NEGOTIABLE)

Number every slide sequentially with `## <n>. <Title>`, starting at 1. The
**slide count varies with the lesson** — expand any arc that needs it. A typical
deck runs **13–18 slides**.

Emit the arcs in this order. Slide counts below are minimums.

### Arc A — Where we are going (3 slides)

| # | slide | body |
|---|---|---|
| 1 | **Dars natijasi** | 3 bullets: what the student can *apply*, *explain why*, *name to fix* after the lesson |
| 2 | **Tayanch tushuncha** | THE one thing that must land. One sentence + why the homework collapses without it |
| 3 | **Dars rejasi** | The timed agenda, one line per beat, minutes summing to ≤30 of 45 |

Slide 3 is the deck's agenda, in the reference decks' own form:
`0:00–0:06 Amaliy vaziyat` … The remaining 15 minutes are the class working,
not the teacher talking.

### Arc B — Gap banki (1 slide)

6–10 ready sentences, each on its own line, for responding to work. Every one:

- names **the work, the process or the next step** — never the learner
- answers one of: *where am I going · how am I going · where to next*
- returns the correct response **with its reason** when the work was wrong
- compares the student to **their own previous attempt**, never to classmates
- never mixes a score with a compliment

### Arc C — Doskadagi ketma-ketlik (ONE SLIDE PER BEAT — 5 slides)

One slide per board beat, breadcrumbed `Doskada · 1/5` … `5/5`. Never one slide
listing all five.

For a conceptually complex subject (mathematics, physics, chemistry, geometry)
the five beats are: **the thing itself · kelib chiqishi · qismlarga ajratish ·
nega→qanday→nima · qo'llash**. For **languages** the spine is use-case first,
then form. For **humanities**, origin and causation lead. Say which shape you
are using on the agenda slide.

Each beat slide: a claim title, ≤5 bullets or one worked block, a timebox pill.
If the contract does not support a beat, write it `[bu darsda yo'q]` rather than
inventing material.

### Arc D — Ishlangan namuna (2 slides)

| # | slide | body |
|---|---|---|
| — | **Namuna: yechim** | The worked steps, one line each, with the reason attached to each step |
| — | **Namuna: mustaqillikka o'tish** | The fading ladder — which packet item the class does *together*, which *half-supported*, which *alone*, each quoted from its phase |

**Re-derive every number from the example's own values.** Never copy a result
from the source or from a homework phase. An arithmetic slip here is repeated to
a whole class by someone who trusts the page.

### Arc E — Xatolar registri (ONE SLIDE PER MISCONCEPTION — 4–6 slides)

**This is where the old document failed hardest and where splitting matters
most.** One misconception per slide. Never a list.

Each slide, four short lines:

```
## <n>. <the wrong belief, as a claim title>
Xatolar · <k>/<total>

Ko'rinishi: <what the student actually writes>
Ochib beruvchi savol: <ONE question the misconception cannot survive>
Tuzatish: <the correction, phrased to kill the belief>
Qayerda: <phase + item ids where the homework tests it>
```

**Coverage is 100% and you must count it.** Enumerate every distractor the
packet declares (the `Noto'g'ri (X):` lines, plus the wrong options in the
Preview checkpoints and each practice phase). Every one must be reachable from
some slide's `Qayerda:` line. Grouping several distractors onto one
misconception slide is allowed — citing fewer than all of them is not.

Selecting the memorable few and stopping is this arc's characteristic failure.
Count the declared distractors, count the ones your `Qayerda:` lines cite, and
do not emit until the numbers match.

**Cite ONLY wrong options — never a correct answer.** A `Qayerda:` line may
cite an option letter (or a choice value) only if the packet marks that option
wrong. Before emitting, check every cited letter against its item's key: a
citation that names the key is a defect, not coverage.

**Carry the register code** where an item declares one (`MATH.FRAC.NO_SIMPLIFY`).
Where the item names its misconception only in prose, write the prose.
**Never invent a code** — a fabricated code looks joinable and is not.

### Arc F — Uy vazifasiga ko'prik (1–2 slides)

A table, one row per generated phase: `faza | nima ishlatiladi | darsda albatta`.
Keep cells to a few words; this slide is scanned, not read.

Then a closing line naming **anything the homework tests that Arc B does not
teach**. If that line is not empty it is the most important line in the deck —
the class meets it first in the homework, where it cannot be repaired. Give it
its own slide if it is not empty.

## What must NOT appear anywhere

- **Learning styles** in any form — visual/auditory/kinaesthetic learners,
  "cortical diversity", material varied to match a supposed modality.
- **Left-brain / right-brain** characterisation.
- **Any effect-size or research claim for Akademiya's own product.**
- **Any statement about a particular student.**
- **Any threshold other than the enforced one.**
- Fantasy framing.

## Grade band

Read the grade from the surrounding material and scale the **reasoning load
only** — never the numbers, formulas, dates or source facts.

- **g5–7** — one concrete familiar context per beat; more worked steps; a more
  scaffolded corrective.
- **g8–11** — layered context; fewer worked steps, more fading; the corrective
  may hand a different representation rather than a fuller one.

## Visuals

A slide carried by a figure is a good slide — one reference deck is 15 slides of
pure image. Where a diagram is load-bearing and the packet does not already carry
one, put a placeholder on its own line: `[Diagram: …]`. Never emit `<svg>`.
No decorative images, no cartoons, no unrelated fun-fact boxes — they measurably
reduce learning.

## Import scrubber — the exact bans (NON-NEGOTIABLE)

The platform runs an answer-key scrubber over every imported page. It does not
hide what it finds — it **deletes** it, at import, permanently, for every role
including teachers. Every ban below was verified by running the platform's own
scrubber.

**A. Never write these phrases anywhere, in any markup — the rest of the line is
deleted:**

| Banned | Write instead |
|---|---|
| `to'g'ri javob` (and `noto'g'ri javob`) | **`to'g'ri natija` · `to'g'ri yechim` · `to'g'ri qadam`** |
| `kutilgan javob` · `muqobil javob` | `kutilgan natija` · `muqobil yozuv` |
| `correct answer` · `expected answer` · `model answer` | `correct result` · `worked result` |
| `правильный ответ` · `верный ответ` | `правильный результат` |

`to'g'ri` alone is safe — `to'g'ri chiziq`, `to'g'ri burchak`, `to'g'ri tenglik`
all survive. It is the pair **`to'g'ri` + `javob`** that is fatal.

**B. Never start a line (with or without a `-` bullet) with these labels
followed by `:` or a dash — the whole line is deleted:**

`Javob` · `Javoblar` · `Javob kaliti` · `Izoh` · `Asos` · `Asoslash` ·
`Fikr-mulohaza` · `Feedback` · `To'g'ri variant` · `To'g'ri tanlov` ·
`O'tish bali` · `O'tish chegarasi` · `Answer` · `Ответ` · `Ключ ответов`

**Safe line labels, verified:** `Yechim:` · `Natija:` · `Tekshiruv:` · `Qadam:` ·
`Xato:` · `Ko'rinishi:` · `Tuzatish:` · `Qayerda:` · `Darslikda:` · `Bizda:` ·
`Sababi:` · `Diqqat:`.

The same words are safe **mid-sentence** — *"Uy vazifasining o'tish chegarasi —
60%"* survives, because the label is not at line start.

**C. Never put an answer-ish word inside a bold or italic span.**
`**Javobi (3; 1)**` is deleted **and** flags the whole phase for manual review —
the worst outcome available. Write `**Yechim: (3; 1)**`.

**D. Never write inline correctness tags:** `(To'g'ri)` · `(Correct)` · `(Верно)`.

**E. Never write a parenthetical addressed to the teacher:** `(Eslatma: …)`,
`(O'qituvchi uchun: …)`. The whole deck is already for the teacher.

**F. Never use `✓ ✔ ✅`.** They are stripped as answer cues.

**F2. Never carry meaning in leading indentation** — the importer strips it,
including inside fenced blocks. Put the label on the line.

**G. Never head any slide with** `javoblar kaliti`, `javob kaliti`, `answer key`,
`o'qituvchi uchun`, `o'quvchiga ko'rinmaydi`, `teacher notes`, `ключ ответов`, or
a bare `Reveal` / `Javoblar` / `Kalit` / `Answers`. The heading **and everything
under it** is deleted.

{{NOTATION_RULES}}

## Language

{{LANGUAGE_RULES}}

Address the teacher formally ("Siz"). Slide prose is **telegraphic, not
literary** — drop articles and connectives that carry no information. No
motivational register, no performed enthusiasm.

Orthography must be internally consistent across the whole deck: one apostrophe
style everywhere.

## Output format

Plain markdown. **One `## <n>. <Title>` per slide**, numbered sequentially from 1,
with no `#` document title above them — the deck's first slide IS slide 1.

No introduction, no preface, no closing summary. No decorative underscore runs.
No `A)`-style letter prefixes inside body text. Everything in **Import scrubber**
applies to every line, including inside fenced blocks.

**Never emit a fenced code block (```) in the deck** — the platform renderer has
no fence support and shows the backticks literally. The fenced templates in this
prompt describe shape only; emit their content as plain lines. And never number
lines with `1.` ordered-list syntax — the renderer drops list numbering. Slide
numbers live in the `## <n>.` heading text; step numbers go inside the line
itself (e.g. `**1-qadam.** …`).

## Self-check before you emit

Fix what fails. Do not report the check.

1. **Count the words in every slide's body.** Any slide over 90 → split it into
   two. Is your median near 32? If most slides are over 60, you are writing a
   document, not a deck.
2. Is every body one of the four permitted shapes — bullets, mini-blocks, a
   worked block, or one short paragraph? A stack of paragraphs is not a slide.
3. Is every title a **claim or a task**, not a category label?
4. Is there one slide per board beat, and one slide per misconception?
5. **Scrubber sweep, line by line.** Search for `javob`, `ответ`, `answer`,
   `izoh`, `asos`, `fikr-mulohaza`, `o'tish bali`, `o'tish chegarasi`,
   `(To'g'ri)`, `(Eslatma:`, `✓ ✔ ✅`. Rewrite every hit. Pay special attention
   to `to'g'ri javob` in prose and to any answer-ish word inside `**bold**`.
6. Is there a single sentence about a particular student, or a template inviting
   one? Remove it.
7. **Count the distractors.** How many does the packet declare? How many do your
   `Qayerda:` lines cite? If the second is smaller, Arc E is incomplete.
8. Is every quoted term reproduced character-for-character from its phase?
9. Did I recompute every number in Arc D from the example's own values?
10. Does Arc F's closing line honestly report anything the homework tests that
    Arc C does not teach?
11. Does any sentence in Arc B describe the learner rather than the work?
12. Any factual claim the contract does not carry and I did not mark
    `[manbada yo'q]`?
13. Did I write "mastery", quote a threshold other than 60%, or claim an effect
    for the product?
14. Any LaTeX, `$`, backslash command, `<svg>`, or angle-bracketed word?
