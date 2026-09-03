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

**There is NO hard word limit per slide.** The measured reference numbers
above are calibration context — what good decks tend to look like — never a
rule to enforce. The binding rules are these three:

- **One idea per slide.** When a second idea creeps in, split the slide —
  never compress. The reference decks spend *ten* slides on one topic rather
  than one dense slide listing ten things.
- **Length is driven by need.** A slide carries as much explanation and as
  many examples as ITS topic needs — a hard concept may run long, a simple
  term may be two lines; both are correct. Padding and restating stay banned,
  and a rule stated without its explanation is as wrong as a padded slide.
- **Organization must grow with length.** The longer the body, the more
  visible structure it carries — labeled blocks, a table, a ✗-contrast, a
  heading split. A long bare paragraph stack is a fail at any length.
- **The slide is the teacher's CUE CARD, not the spoken explanation.** The
  reference decks put very little text on a slide — the teacher SPEAKS the
  explanation; the slide holds what they GLANCE at: the **definition**, the
  **key terms**, the **rule or hint**, the **worked answer**, the
  **mistake→fix**. Definitions and key facts STAY on the slide; the paragraph
  that *explains* them is teacher talk — cut it. A concept slide = the
  definition (one line) + at most 2 short supporting cues (a key term, an
  example, or a "watch for" hint). No multi-sentence teaching paragraphs.
  The test: if a line reads like something the teacher would SAY rather than
  GLANCE at, cut it. (Reference calibration, non-binding: median ~30 words a
  slide; many slides are a headline + a few words + a picture.)

## Slide anatomy (every slide, no exceptions)

```
## <n>. <Title — a SHORT NOUN LABEL. See Titles below>

<body — a pattern from INFO-PATTERNS.md, not a default bullet list>
```

### Titles — measured against 48 real reference titles

| | reference decks | what we generated before |
|---|---|---|
| median length | **3 words** | 7.5 |
| ≤4 words | **62%** | 0% |
| bare noun phrase naming the topic | **90%** | 0% |
| full sentences / unnumbered imperatives | **0 of 48** | all of them |

**A title is a short NOUN LABEL of 1–4 words** naming what the slide is about:
`Colour Harmony` · `Modal ishonch darajasi` · `White Space` · `Types of Font`.

If a gloss genuinely helps, use `Topic: concrete gloss` — 1–3 words before the
colon, **8 words absolute cap** for the whole title.

**Banned in titles — this is the exact failure the reviewer named:**
- full declarative sentences (*"Modal choice depends entirely on speaker certainty"*)
- unnumbered imperatives built from abstractions (*"Distinguish guaranteed
  technical outcomes from tentative possibilities"*)
- learning-objective language: *distinguish · separate · identify · analyse ·
  transfer · audit* + a pile of abstract nouns
- the word **bank**, and every analyst word: *procedure · interrogative ·
  auxiliary · evidential · formulating · syntax · infinitive · classification*

**The reader test (owner rule): a title must tell a teacher what the slide is
about with ZERO context.** If the title needs the slide to explain it
(*"Technical Forecasting Procedure"*, *"Irrelevant Historical Data"*), it
fails. Fixed names that pass and stay: **Lesson Outcomes · Core Concept ·
Lesson Agenda · Homework Bridge** (mother-tongue equivalents in Uzbek/Russian
decks). The teacher-lines slide is titled **What to Say** (never "Phrase
Bank"). Calibration renames: *Technical Forecasting Procedure →* `Step by
Step: Will or Might` · *Worked Analysis →* `Worked Example` · *Modal Forms
and Questions →* `Will, May and Might`.

Those are **learning objectives pasted into the title slot.** The objectives
belong as bullets on ONE dedicated slide, exactly once per deck — the reference
decks do precisely that and never put an objective in a title.

Number the title only for a genuinely ordered how-to step
(`1. Find high quality images.`).

### Body shape — the mix, not one default

Measured across 81 reference slides:

| shape | share |
|---|---|
| **one short statement / caption, no bullets** | **54%** |
| title-only divider | 9% |
| labelled blocks / definition list | 9% |
| question → pointer | 7% |
| **plain bullet list as the whole body** | **6%** |
| worked/code slide with a closing `Tekshiruv:` line | 6% |

**Only 16% of reference slides contain a bullet at all.** A deck that is all
bullet lists — which is what we produced — matches **none** of the four
reference decks. That is the whole of *"it looks like plain text, there is no
depth"*.

**Your quota, and it is checkable:**
- **At least half the slides carry NO bullets** — one short statement, or a
  contrast, or a table.
- **At most 1 slide in 5 may be a plain bullet list.**
- When you do use bullets: **fragments of 4–10 words**, never full sentences,
  never more than 6, always under a 1–3-word label.

### Choose the shape from the content

**The INFO-PATTERNS catalogue appended at the END of this prompt is part of
this prompt.** It carries 12
named patterns — Headline Claim · Labeled Bins · Rule Card · Key Callout ·
Versus Split · Contrast Table · Term Ledger · Minimal Pairs · Wrong→Right ·
Numbered Path · Decision Fork · Question Before Answer — each with a
copy-pasteable markdown template, when to use it, when not to, and a decision
table mapping content shape → pattern.

**Read it and pick the pattern that fits the content.** Two things being
contrasted want a Versus Split or a Contrast Table, not two bullets. A rule with
exceptions wants a Rule Card. A common error wants Wrong→Right. Reaching for
bullets every time is the failure this section exists to prevent.

**Rhythm: runs, then a break.** The reference decks do NOT alternate shape every
slide. A concept family becomes a **run of identically-framed slides, one member
each** — six colour harmonies became six slides with the same shape — and the run
then **breaks** with a differently-shaped slide: a one-line key idea, a recap, a
table.

So: keep a run consistent (all five lesson-plan steps look alike; all
misconception slides look alike), and make sure each run ENDS against a
different shape. What must not happen is the whole deck being one shape.

Within a section, follow **explain → show → do**: state it, show it worked, then
point at the task — and let those three look different from each other.

### Emphasis — by label and isolation, not by bolding everything

Reference decks emphasise **3–6% of a slide's words**, and mostly by *position*:
a label prefix (`Asosiy fikr:`, `Tekshiruv:`, `Diqqat:`), or by giving the
important sentence a line of its own.

- **A short statement slide usually needs NO emphasis at all.** 44 of the 81
  reference slides carry none whatsoever — the sentence is short enough to be its
  own emphasis.
- **No mid-sentence bolding.** It appears nowhere in the reference corpus.
  Emphasis goes at the START of a line, as a label, or on a line of its own.
- **`**bold**` at most 5 runs per slide, ≤3 words each**, and only on a
  load-bearing term — never a whole line, never a whole sentence.
- **Table cells count toward the bold budget.** A term-ledger that bolds every
  term row blows it — leave cell text plain; the styled header row and the
  zebra rows already carry the structure.
- **Mid-sentence bold exists in exactly one place: the swapped word of a
  Minimal Pairs slide.** Everywhere else — bullets, callouts, example
  sentences — bold sits at the START of a line as a label or lead word.
  The classic violation is bolding the lesson's target terms (*will*, *may*,
  *might*) inside sentences — target terms inside a sentence take *italics*.
- **A Headline Claim is never a whole bolded line.** The claim lives in a `>`
  callout; inside it, bold at most the resolving 1–3 words. Manual step
  numbers (`**1.**`) count toward the budget too — in a worked block, bold
  the numbers and the labels, nothing else, and end with the closing check
  line.
- **Every `Label:` line opener is BOLD** — `**Diqqat:** …`, `**Say:** …`,
  `**Tuzatish:** …`. Three or more unbolded `Label:` openers on one slide is
  a failing slide (the importer audit enforces exactly this). Bold labels
  count toward the budget — which is why a labeled slide rarely needs any
  other bold.
- One `Label:` prefix (`Asosiy fikr:`, `Tekshiruv:`, `Diqqat:`) is worth more
  than six bold words.
- If a sentence is truly central, **give it its own slide** rather than bolding it.

### Closing lines have a job

Where a slide shows a worked example or a task, end it with ONE line that is a
**checkable fact**, not a summary: `**Tekshiruv:** <the result the teacher
should see>` or `**Diqqat:** <the thing that usually breaks>` — and in an
all-English deck those labels are `**Check:**` and `**Watch out:**` (the
deck-language rule binds closing labels too). The reference decks use
`Checkpoint:` / `Gotchas` / `Tip:` exactly this way. A closing line that merely
restates the slide is noise — cut it.

### No breadcrumbs, no counters

Do not print `Doskada · 1/5`, `Xatolar · 3/5`, `· Namuna` or any "part N of M"
line. **0 of 81 reference slides carry one.** The slide number in the heading is
the only position marker; the platform shows a section list beside the slide.

**No marker pills.** Do not prefix lines with `[TAXTADA]`, `[SINF ISHLAYDI]` or
any bracketed tag. If who-does-what matters, make it a table column or a bolded
lead-in word — not a bracket the reader must decode.

**NEVER put a time on anything.** No minute figures, no timeboxes, no clock
ranges. The teacher paces their own room.

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

**Exactly ONE `##` heading per slide — its numbered title. Never a second `#`
or `##` line inside a slide body:** the platform's section importer splits on
`##`, so a nested heading silently breaks the slide in two. Where a pattern
template shows a `#`/`##` line, that line IS the slide's own numbered title
slot — inside the body use `###` labels, bold lead-ins, or plain lines only.

**Pattern names are prompt vocabulary.** *Term Ledger*, *Rule Card*, *Minimal
Pairs*, *Versus Split*, *Decision Fork* and the rest never appear in visible
deck text — the bridge's "nima ishlatiladi" column describes the CONTENT
("10 term–definition pairs", "question-form rule"), never the pattern used to
lay it out.

Emit the arcs in this order. Slide counts below are minimums.

### Arc A — Where we are going (3 slides)

| # | slide | body |
|---|---|---|
| 1 | **Dars natijasi** | 3 bullets: what the student can *apply*, *explain why*, *name to fix* after the lesson |
| 2 | **Tayanch tushuncha** | THE one thing that must land. One sentence + why the homework collapses without it |
| 3 | **Dars rejasi** | The plan: one short line per step, in order. No times |

Slide 3 is the deck's route map — the step names in order, nothing more, so the
teacher can see the shape of the lesson at a glance. **Call them steps, or just
number them. Never "Beat 1", never invented jargon** — plain, logical naming. It also states which
explanation spine this lesson uses (see Arc C). **No minutes anywhere on it.**

### Arc B — Gap banki (1 slide)

Visible slide title: **What to Say** (mother-tongue equivalent in Uzbek /
Russian decks — never "bank", which is banned in titles; "Gap banki" is this
arc's internal name only).

A two-column **When → Say table** — context first, then the ready sentence, so
the teacher sees at a glance WHEN each line is used. At most 6 rows; the Say
cell quotes ≤15 words:

| When | Say |
|---|---|
| <the classroom moment, ≤8 words> | "<the ready sentence>" |

Every row still obeys the feedback principles:

- names **the work, the process or the next step** — never the learner
- answers one of: *where am I going · how am I going · where to next*
- returns the right response **with its reason** when the work was wrong
- compares the student to **their own previous attempt**, never to classmates
- never mixes a score with a compliment

**Scrubber trap in this table:** never write *correct / correctly / incorrect*
(or a `to'g'ri`+`javob` pairing) in a row that also carries italics — the
importer deletes everything between the asterisk marks. Phrase certainty
without the word: "You chose *will* because the clause is certain."

### Arc C — Dars rejasi bosqichlari (ONE SLIDE PER STEP — 5 slides)

One slide per step of the lesson plan. Never one slide listing all five.

**No breadcrumbs, no counters.** Do not print `Doskada · 1/5`, `Xatolar · 3/5`
or any "part N of M" line. The slide number in the heading is the only position
marker the deck gets; the platform already shows a section list beside the slide.

For a conceptually complex subject (mathematics, physics, chemistry, geometry)
the five steps are: **the thing itself · kelib chiqishi · qismlarga ajratish ·
nega→qanday→nima · qo'llash**. For **languages** the spine is use-case first,
then form. For **humanities**, origin and causation lead. Say which shape you
are using on the agenda slide.

Each step slide: a claim title and ≤5 bullets, or one worked block.
If the contract does not support a step, write it `[bu darsda yo'q]` rather than
inventing material.

**When the lesson has a vocabulary phase, the vocabulary step carries EVERY word
that phase carries.** Count them; when the slide budget cannot hold them all,
split the step into two slides — showing five of ten words is this step's
characteristic failure, and every word left off the board meets the class for
the first time in the homework.

### Arc D — Ishlangan namuna (2 slides)

| # | slide | body |
|---|---|---|
| — | **Namuna: yechim** | The worked steps, one line each, with the reason attached to each step |
| — | **Namuna: mustaqillikka o'tish** | The fading ladder — which packet item the class does *together*, which *half-supported*, which *alone*, each quoted from its phase |

**Re-derive every number from the example's own values.** Never copy a result
from the source or from a homework phase. An arithmetic slip here is repeated to
a whole class by someone who trusts the page.

**Every ladder rung names its source phase, in the deck's language per the
phase-name table above** — uz `(Xatoni aniqlash)`, `(Hayotiy vaziyat, 1-qadam)`.
A quoted sentence without its phase name cannot be found during the lesson.

### Arc E — Xatolar registri (ONE SLIDE PER MISCONCEPTION — 4–6 slides)

**This is where the old document failed hardest and where splitting matters
most.** One misconception per slide. Never a list. Every register slide is a
**true Wrong→Right contrast** — the correction is ON the slide, because the
wrong example plus advice without the corrected sentence is not
self-explanatory at a glance.

Each slide (labels in the deck's language — Uzbek / English):

```
## <n>. Mistake: <the wrong belief in 2–4 plain words>

✗ *<the sentence exactly as the mistaken student writes it>*
**To'g'risi:** *<the SAME sentence, minimally corrected>*

**Sababi:** <one line naming the rule that was broken>
**Sinfga savol:** <ONE question the misconception cannot survive>

<!-- QA-WHERE: <phase> <item>: <wrong options>; <phase> <item>: <wrong options> -->
```

English-deck labels: `**Right:**` · `**Why:**` · `**Ask the class:**` — in the
register the corrected line's label is `**To'g'risi:**` / `**Right:**`, never
`**Fix:**` (`Fix:` belongs to the generic Wrong→Right pattern outside this arc).
The ✗ line and the corrected line are the SAME sentence minimally edited —
the diff IS the lesson. (`✓` is stripped at import — the corrected line takes
the bold label, never a check mark.)

**The `Sababi:`/`Why:` line is a SHORT reason, not a lecture** — one compact
sentence naming the rule that was broken. The register keeps its ✗ /
To'g'risi / Sababi / Sinfga savol shape; the spoken elaboration is the
teacher's, not the slide's.

**Every register slide's title is `Mistake: <2–4 plain words>`** (mother-tongue
deck: `Xato: …`) — the mistake named in words a student would use:
`Mistake: Will Without Proof` · `Mistake: May in Questions` ·
`Mistake: Mixed-Up Words` · `Mistake: Extra Facts`.

**No visible citations, no internal codes.** `Qayerda:` / `Where:` lines and
generator QA vocabulary (`MC4`, `CP2`, `RLC Step 1`, `Flashcard 3`) are banned
from slide text — a teacher reading them "does not get the relevance". Each
register slide's citations go in ONE `<!-- QA-WHERE: … -->` HTML comment
immediately after the slide, machine-checked, invisible in the room.

**This holds everywhere in the deck, the bridge table included: phase names in
visible text are always spelled out IN THE DECK'S LANGUAGE** — never an acronym
(*RLC*, "Practice RLC" fail the packet), never a paraphrase, and NEVER the
English phase name inside a uz/ru deck (English phase names are QA-comment
vocabulary only). The canonical visible names, exactly these strings per
language:

| phase | uz deck | ru deck | en deck |
|---|---|---|---|
| vocabulary | Mavzu lug'ati | Тематическая лексика | Topic Vocabulary |
| case-based-preview | Keys bilan tanishuv | Разбор кейса | Case Preview |
| flashcards | Fleshkartalar | Флешкарты | Flashcards |
| memory-check | Xotira tekshiruvi | Проверка памяти | Memory Check |
| practice-rlc | Hayotiy vaziyat | Жизненная задача | Real-Life Challenge |
| practice-error-detection | Xatoni aniqlash | Поиск ошибки | Error Detection |
| practice-sentence | Gap tuzish mashqi | Работа с предложениями | Sentence Practice |

**Coverage is 100% and you must count it.** Enumerate every distractor the
packet declares (the `Noto'g'ri (X):` lines, plus the wrong options in the
Preview checkpoints and each practice phase). Every one must be reachable from
some slide's `QA-WHERE` comment. Grouping several distractors onto one
misconception slide is allowed — citing fewer than all of them is not.

Selecting the memorable few and stopping is this arc's characteristic failure.
Count the declared distractors, count the ones your `QA-WHERE` comments cite,
and do not emit until the numbers match. **Walk the packet item by item:**
every multiple-choice AND choose-explanation card carries three declared
wrongs (fill-blanks carry none), every preview checkpoint carries its wrong
letters, every practice step carries its wrong options — and the concept-chip
step's wrong chips count too. The repeat failure is skipping one mid-packet
card and one chip step; tick items off one by one, never by memory.

**Cite ONLY wrong options — never a correct answer.** A `QA-WHERE` comment may
cite an option letter (or a choice value) only if the packet marks that option
wrong. Before emitting, check every cited letter against its item's key: a
citation that names the key is a defect, not coverage.

**Carry the register code** where an item declares one (`MATH.FRAC.NO_SIMPLIFY`).
Where the item names its misconception only in prose, write the prose.
**Never invent a code** — a fabricated code looks joinable and is not.

### Arc F — Uy vazifasiga ko'prik (1–2 slides)

A table, one row per generated phase: `faza | nima ishlatiladi | darsda albatta`.
Keep cells to a few words; this slide is scanned, not read. Emit a **real
markdown table** (header row + `|---|` separator) — the platform renders
tables, now including inline bold in cells. Never leave a cell deliberately
blank — write a dash (`–`) instead; the importer drops empty cells and shifts
the row.

Then a closing line naming **anything the homework tests that Arc B does not
teach**. If that line is not empty it is the most important line in the deck —
the class meets it first in the homework, where it cannot be repaired. Give it
its own slide if it is not empty.

**The coverage enumeration is a QA artifact, never a slide.** Do NOT emit a
coverage/audit/summary slide — the deck's only visible bridge is the Arc F
table above, and a self-audit is nothing a teacher presents. Instead, AFTER the
last slide, append exactly ONE HTML comment block:

<!-- QA-COVERAGE
<term or concept> -> slide <n>
<term or concept> -> slide <n>
UNTAUGHT: <name each concept the homework uses that no slide teaches — or: none>
-->

Derive it by enumeration: list every term, rule and background fact the
packet's items actually use, and map each to the slide that really teaches it —
a mapping to a slide that does not contain the thing is a fabrication, and this
block is machine-checked line by line. Write `UNTAUGHT: none` only when the
remainder is actually empty; when it is not, the untaught concepts must ALSO
appear visibly per the closing-line rule above (a real gap gets its own slide —
an empty gap gets no slide, no line, no claim). **The comment and the visible
deck must AGREE:** naming a gap visibly while the comment says `none` — or the
reverse — is a contradiction and fails the packet.

## What must NOT appear anywhere

- **Learning styles** in any form — visual/auditory/kinaesthetic learners,
  "cortical diversity", material varied to match a supposed modality.
- **Left-brain / right-brain** characterisation.
- **Any effect-size or research claim for Akademiya's own product.**
- **Any statement about a particular student.**
- **Any threshold other than the enforced one.**
- **AI Boss in any form** — the `ai_boss` builder element is banned from
  teacher material; one occurrence fails the whole packet at import.
- Fantasy framing.

## Grade band

Read the grade from the surrounding material and scale the **reasoning load
only** — never the numbers, formulas, dates or source facts.

- **g5–7** — one concrete familiar context per step; more worked steps; a more
  scaffolded corrective.
- **g8–11** — layered context; fewer worked steps, more fading; the corrective
  may hand a different representation rather than a fuller one.

## Visuals

A slide carried by a figure is a good slide — one reference deck is 15 slides of
pure image. Where a diagram is load-bearing and the packet does not already carry
one, put a placeholder on its own line: `[Diagram: …]`. Never emit `<svg>`.
No decorative images, no cartoons, no unrelated fun-fact boxes — they measurably
reduce learning.

## Builder elements — the interactive layer (optional)

A slide may carry builder elements where they serve the room: an interactive
check, a matching game, or a written exercise. The element never replaces the
slide's text — it is the interactive layer under it. **Most slides carry none;
a deck that turns into a quiz has failed as a presentation.**

**Counts (owner-set):** the deck carries **2–3 interactive items total**. One
item = one matching/memory game, one exercise, or one **test battery**. A test
is always a battery of **3–4 questions**: emit 3–4 consecutive `ELEMENT: test`
fences on the same slide (the importer orders them; the room sees consecutive
question cards). A single lonely test question is not an item. The
≥4-pair-vocabulary matching-game default stands and counts as one item.
Element text follows the student-level word rules — students read it off the
board.

Emit an element as a fenced block anywhere in the slide body — the ONLY fence
form the deck permits. First line names the kind; the rest is ONE JSON object
with the builder's own field names (never invent variants):

```
ELEMENT: test
{"type": "single_choice", "question": "…",
 "options": ["…", "…", "…"],
 "correct_answers": ["…the exact option VALUE, never an index…"],
 "feedback_correct": "…", "feedback_incorrect": "…", "attempts_allowed": 2}
```

- `test` — `type` ∈ single_choice | multiple_choice | true_false | short_text |
  fill_blank | matching | ordering; `question`; `options[]`;
  `correct_answers[]` as VALUES copied from `options`; optional
  `feedback_correct` / `feedback_incorrect`, `attempts_allowed`; matching adds
  `matching_pairs: [{left, right}]`, ordering adds `order_items[]`.
- `game` — `game_type` ∈ matching | memory; `items: [{term, definition}]`
  (at least 2, realistically 4–8); optional `time_limit` (seconds), `shuffle`.
- `exercise` — `exercise_type: "text"`; `prompt`; optional `title`, `rubric`.
- **`ai_boss` — NEVER.** AI Boss is banned from teacher material; a single
  occurrence fails the whole packet at import.

**The code fence is MANDATORY.** An `ELEMENT:` line outside a ``` fence is a
packet failure: the importer only parses fenced blocks, so a bare block leaks
its raw JSON onto the slide as visible text. Open with ```, then `ELEMENT:
<kind>` on the first line inside (canonical; the kind on the fence line is
tolerated), then the JSON, then the closing ```.

### Illustrations — one per teaching slide

**Every teaching slide carries ONE illustration element** — concept slides,
worked examples, vocabulary, register slides. The pure agenda/outcomes lists
skip it unless a picture genuinely helps. Short text + supporting picture is
the slide's shape.

```
ELEMENT: image
{"scene": "<what the picture shows — ONE concrete sentence, in English>",
 "caption": "<short caption in the deck's language>",
 "width": "0.5x"}
```

- **Do NOT include a `data` field** — the pipeline generates the picture from
  your `scene` and injects the bytes automatically. `scene` and `caption` are
  mandatory; `width` is `0.5x` for a supporting picture, `0.75x` or `full`
  for a hero/diagram.
- The `scene` is a PURE METAPHOR for that slide's idea: two crossing paths
  for "one solution", two parallel tracks for "no solution", a balanced
  see-saw for an equation, one character handing a glowing key to another
  for a worked answer, a child checking a worksheet for a register slide.
- **The scene must contain ZERO numbers, equations, or words-to-show — and
  never "a board/screen/sign with something written on it".** A scene that
  implies a calculation makes the model bake garbled text into the picture.
  The math and the words live in the slide TEXT; the picture carries the
  idea only, wordlessly.
- Illustrations do NOT count toward the 2–3 interactive-item budget (that
  budget is games/exercises/test batteries).

Where an element belongs — driven by need, never a quota:
- **game (matching) is the expected DEFAULT under the vocabulary slide** when
  it carries ≥4 term–definition pairs — the class plays the pairing after the
  table is read; omit it only for a concrete reason. **game (memory)** for
  younger grades / flashcard-heavy sets.
- **test** where the deck already asks the class a checkable question — a
  register slide's `Ask the class:` line can become a single_choice test whose
  distractors echo the homework's REAL distractors; or one exit-check question
  on the homework-bridge slide.
- **exercise (text)** on the practice-ladder or worked-example slide — the
  board task as a real prompt.

Element JSON must be builder-valid or the import fails loudly: a known `type`,
every `correct_answers` value present in `options`, ≥2 game pairs, a non-empty
`prompt`. Element language and word choice follow the same deck-language and
grade-band rules as slide text — students read the question the teacher
projects. Scrubber note: element text obeys the same glyph bans as slide text
(no `✓ ✔ ✅` inside JSON strings).

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

**F. Never use `✓ ✔ ✅`.** The scrubber strips them silently as answer cues —
and inside a table cell the strip leaves an empty cell, which the importer then
drops, shifting the whole row. `✗` and `→` survive; mark only the WRONG line
(with `✗`) and lead the correct line with a safe label (`Fix:` / `Right:` /
`Tuzatish:`) instead of a check mark.

**F2. Never carry meaning in leading indentation** — the importer strips it,
including inside fenced blocks. Put the label on the line.

**G. Never head any slide with** `javoblar kaliti`, `javob kaliti`, `answer key`,
`o'qituvchi uchun`, `o'quvchiga ko'rinmaydi`, `teacher notes`, `ключ ответов`, or
a bare `Reveal` / `Javoblar` / `Kalit` / `Answers`. The heading **and everything
under it** is deleted.

{{NOTATION_RULES}}

### Formula-ready across subjects (deck supplement)

The notation contract above applies to **every deck surface**: slide bodies,
titles, teacher-voice lines, table cells, QA comments — and **ELEMENT JSON
text** (test questions and options, game terms and definitions, exercise
prompts). A formula a student meets on the board obeys the same rules as one
in the homework.

**LaTeX inside ELEMENT JSON strings is JSON-escaped: every backslash is
DOUBLED.** A JSON string parses `\` as an escape introducer, so a raw LaTeX
command inside a fence is either invalid JSON (`\oplus` → the whole deck is
rejected as malformed) or silent corruption (`\frac` parses as formfeed +
`rac`, `\tan` as tab + `an`). Write `"$T_{\\oplus}$"`, `"$\\frac{1}{S}$"`,
`"$\\cdot$"` inside every ELEMENT JSON string — and keep the markdown OUTSIDE
the fences single-backslash (`$\frac{1}{S}$`) as usual. The `$` delimiters
need no escaping in either place.

Beyond the base contract ($-wrapped LaTeX for every mathematical expression),
these symbol vocabularies may stay Unicode plain text in prose; inside a
`$…$` span use their commands instead (contract v1):
- **Geometry:** in a span write $\angle ABC = 90^{\circ}$, $\triangle ABC$,
  $AB \parallel CD$, $AB \perp CD$, vectors $\vec{AB}$, segments
  $\overline{AB}$ — one span per statement. Standalone in prose, the Unicode
  marks ∠ABC, 45°, △ABC, AB ∥ CD remain fine; ratios with a colon (3:1);
  absolute value |x| when standalone.
- **Physics symbols & units:** Greek letters directly in prose (α β γ Δ ρ λ ν
  ω μ Ω), Δt; compound units on one line: m/s², kg/m³, N·m, kW·h — units
  always OUTSIDE the math span. Full equations use the span: $v = v_{0} + at$,
  $F = m \cdot a$.
- **Chemistry:** subscript formulas H₂SO₄, Ca(OH)₂; ion charges as trailing
  superscripts SO₄²⁻, Na⁺, Cl⁻; reaction arrows → and equilibrium ⇌; isotopes
  with leading superscript ²³⁵U.
- **Biology:** full equations chem-style: C₆H₁₂O₆ + 6O₂ → 6CO₂ + 6H₂O;
  genetics ratios 9:3:3:1.
- **Informatics:** number bases as subscripts 1011₂, 11₁₀, B₁₆; logic symbols
  ∧ ∨ ¬; comparison signs in code examples keep spaces on both sides
  (a < b) exactly like prose.

BARE LaTeX stays forbidden everywhere (self-check 18): a backslash command or
`_{…}`/`^{…}` fragment OUTSIDE `$…$` delimiters reaches the reader as literal
text — every LaTeX expression sits inside its dollar pair. Answer values
follow their input mode: a `correct_answers` value for a tap/select item still
copies its option text character-for-character (math span included), while any
answer the student must TYPE stays plain keyboard text — no `$`, no backslash
commands.

## Language — the deck follows the homework (NON-NEGOTIABLE)

{{LANGUAGE_RULES}}

**The deck is written in exactly the language the homework packet is written
in.** It is the same lesson; a teacher presenting an all-English homework from
an Uzbek deck is translating live, and a teacher presenting a bilingual homework
from an English-only deck cannot use the scaffolding the students will see.

**For English (L2) lessons the CEFR ladder decides, and B1 is the threshold:**

| grade / level | the homework | so the deck |
|---|---|---|
| **G5–G7 · below B1** (A1, A2) | target language in English, **all scaffolding in the mother tongue** (Uzbek or Russian) | **the same split** — slide titles, instructions, misconception lines and the phrase bank in the mother tongue; only the target items, example sentences and quoted homework text in English |
| **G8–G11 · B1 and up** (B1, B2, C1, C2) | **entirely in English** | **entirely in English** — every slide title, every line label, every sentence |

Read the level from the packet itself, not from an assumption: the vocabulary
phase declares `**CEFR:** <level>`, and the homework's own scaffolding tells you
which side of B1 it sits on. **If the packet's scaffolding is in Uzbek, so is
the deck. If the packet is all-English, so is the deck.**

**For every non-English subject:** the deck is in the packet's language — Uzbek
for an Uzbek packet, Russian for a Russian one.

### Word choice tracks the grade

Not just the language — the LEXIS sits at the student's grade. For English
lessons the CEFR band decides (G5→A1 … G9→B2 … G11→C2): explanation vocabulary
a B2 class reads as *tentative* is *not sure* for an A1 class. Any above-band
term that must appear (a target term of the lesson) gets an in-band gloss right
where it appears. This binds the teacher-voice lines too — `Sinfga savol:` /
`Ask the class:` questions and the When→Say quotes are read ALOUD to students
of that grade. Non-English subjects follow the same principle: word the
explanations for the grade being taught.

**We are teaching students, not linguists (owner rule — the big one).** ALL
visible deck text — titles, bodies, teacher lines, element text — uses only
the world of words the homework itself uses. The terms the homework teaches
(*will, won't, may, might, question, certain, possible*) are fine. **Analyst
vocabulary is BANNED from visible text:** *modal auxiliary · interrogative ·
bare infinitive · evidential · clause · indicator · syntax · procedure ·
formulating · classification*. Say it plainly:

✗ *"Invert subject and will when formulating future interrogatives."*
→ *"To make a question, put Will first: Will homes change?"*
✗ *"Ensure modal auxiliary choice does not contradict the main clause
certainty indicator."*
→ *"Check: does your word match the certainty clue (certain, sure, not sure)?"*

If a technical term is unavoidable because it IS the taught content, gloss it
in plain words right where it appears.

### Localised labels — verified against the import scrubber

Arc and slide names are the concepts, not fixed strings; translate them with the
deck. The **line labels** are not free, because some are deleted at import:

| | mother-tongue deck | all-English deck |
|---|---|---|
| safe | `Ko'rinishi:` `Ochib beruvchi savol:` `Tuzatish:` `To'g'risi:` `Sinfga savol:` `Yechim:` `Natija:` `Tekshiruv:` `Qadam:` `Xato:` `Darslikda:` `Bizda:` `Sababi:` `Diqqat:` | `Looks like:` `Exposing question:` `Fix:` `Right:` `Ask the class:` `Solution:` `Result:` `Check:` `Step:` `Mistake:` `Watch out:` `Why:` `Say:` `Note:` |
| **deleted** | `Javob:` `Izoh:` `Asos:` `Fikr-mulohaza:` `O'tish bali:` | **`Answer:`** · **`Feedback:`** · `Correct answer:` |

**`Feedback:` is the trap in an English deck** — it is the natural word to reach
for and it is on the scrubber's label list, so the whole line vanishes at import.
Use `Say:` or `Note:` instead. Verified by running the platform's own scrubber.

### Register

Address the teacher formally — "Siz" in Uzbek, plain professional English
otherwise. Slide prose is **telegraphic, not literary** — drop articles and
connectives that carry no information. No motivational register, no performed
enthusiasm.

Orthography must be internally consistent across the whole deck: one apostrophe
style everywhere.

## Output format

Plain markdown. **One `## <n>. <Title>` per slide**, numbered sequentially from 1,
with no `#` document title above them — the deck's first slide IS slide 1.

No introduction, no preface, no closing summary. No decorative underscore runs.
No `A)`-style letter prefixes inside body text. Everything in **Import scrubber**
applies to every line, including inside fenced blocks.

**Never emit a fenced code block (```) in the deck — with ONE exception: an
`ELEMENT:` block (see Builder elements above).** For anything else the platform
renderer has no fence support and shows the backticks literally. The fenced
templates in this prompt describe shape only; emit their content as plain lines.

## Self-check before you emit

Fix what fails. Do not report the check.

1. **One idea per slide, cue-card text.** Does any slide carry a second idea?
   Split it. Does any line read like something the teacher would SAY rather
   than GLANCE at (a multi-sentence explaining paragraph)? Cut it — keep the
   definition, the key terms, the hint, the worked answer. Does any long body
   run as a bare paragraph stack? Give it structure.
2. **Count the bullet slides.** At least half your slides must carry NO bullets,
   and at most 1 in 5 may be a plain bullet list. If most slides are bullets,
   go back to INFO-PATTERNS.md and pick the pattern that fits each content
   shape. And count the words in every bullet — over 10 words is a fail;
   tighten the fragment.
3. Does the shape CHANGE between consecutive slides, or does the deck read as
   one undifferentiated wall?
4. **Measure every title.** Median 1–4 words, 8 absolute cap. Is any title a
   full sentence, an unnumbered imperative, or objective-speak
   (*distinguish/separate/identify/transfer* + abstract nouns)? Rewrite it as a
   short noun label.
5. **Count the bold runs per slide** — 5 max, ≤3 words each. Is anything bold
   that is not load-bearing?
6. **Is there a time anywhere?** Search for `daqiqa`, `min`, `:0`, `0:`, and any
   digit followed by a clock or minute mark. There must be none — not on the
   plan slide, not in a pill, not in a bullet.
7. **Is the deck in the same language as the homework packet?** For an English
   lesson: below B1 the scaffolding is in the mother tongue and only the target
   items are in English; at B1 and above **every line is English**. Read the
   level off the packet, do not assume it.
8. Is there one slide per lesson-plan step, and one slide per misconception?
9. **Scrubber sweep, line by line.** Search for `javob`, `ответ`, `answer`,
   `izoh`, `asos`, `fikr-mulohaza`, `o'tish bali`, `o'tish chegarasi`,
   `(To'g'ri)`, `(Eslatma:`, `✓ ✔ ✅`. Rewrite every hit. Pay special attention
   to `to'g'ri javob` in prose and to any answer-ish word inside `**bold**`.
   **In an English deck also search for `Answer:` and `Feedback:` at line
   start** — both are deleted at import; use `Say:` or `Note:`.
10. Is there a single sentence about a particular student, or a template inviting
   one? Remove it.
11. **Count the distractors.** How many does the packet declare? How many do
   your `QA-WHERE` comments cite? If the second is smaller, Arc E is incomplete.
12. Is every quoted term reproduced character-for-character from its phase?
13. Did I recompute every number in Arc D from the example's own values?
14. Is the QA-COVERAGE comment present after the last slide, is every
    `-> slide <n>` mapping TRUE (the slide really contains the thing), and did
    I emit NO coverage/audit slide? An `UNTAUGHT:` entry that is not `none`
    must also appear visibly per Arc F.
15. Does any sentence in Arc B describe the learner rather than the work?
16. Any factual claim the contract does not carry and I did not mark
    `[manbada yo'q]`?
17. Did I write "mastery", quote a threshold other than 60%, or claim an effect
    for the product?
18. Any BARE LaTeX outside `$…$` (a backslash command or `_{…}`/`^{…}` not
    wrapped in dollars), any `$` used as currency or inside an answer the
    student must TYPE, any `<svg>`, or any angle-bracketed word (QA comments
    excepted)?
19. Any visible `Qayerda:` / `Where:` line, or internal QA code (`MC…`, `CP…`,
    `RLC Step …`, `Flashcard …`) in slide text? Move it into the QA-WHERE
    comment. Any slide with 3+ unbolded `Label:` openers? Bold them.
20. **Every ELEMENT block:** does the JSON parse, is the kind one of
    test/game/exercise, is every `correct_answers` value copied verbatim from
    `options`, do games carry ≥2 pairs, is the prompt non-empty, is the
    language and word choice at the deck's grade band — and is there NO
    `ai_boss` anywhere? Count the items: 2–3 per deck, every test a battery
    of 3–4 consecutive question fences, never one lonely question. And is
    EVERY element inside a ``` code fence — no bare `ELEMENT:` line anywhere?
    And is every backslash inside ELEMENT JSON strings DOUBLED (`\\frac`,
    `\\oplus`) — no raw single-backslash LaTeX in any JSON value?
21. **Read every visible line as the student would.** Any analyst word
    (auxiliary, interrogative, infinitive, syntax, procedure, formulating,
    clause, indicator, evidential, classification, bank)? Rewrite it in the
    homework's own words. Is every register title `Mistake:`/`Xato:` + 2–4
    plain words?
22. **Every teaching slide has exactly one `ELEMENT: image`** with `scene` +
    `caption` and NO `data` field. Is each scene concrete, conceptual,
    relevant to its slide — and free of readable text, formulas, letters and
    digits?


---

# INFO-PATTERNS — the pattern catalogue (part of this prompt)

**Two reconciliations with the deck rules above — the deck rules win:**

- **Titles stay noun labels.** Where a pattern's template shows a full-sentence
  or question title (Headline Claim's `# <rule as a sentence>`, the question
  slides of Question Before Answer), that sentence goes on the FIRST BODY LINE
  (or the `>` callout) — the `## <n>.` title above it remains a 1–4 word noun
  label per the Titles rule.
- **Check marks.** `✓ ✔ ✅` are stripped at import (scrubber ban F). The
  templates below use the safe form: `✗` marks the wrong line; the correct line
  carries a safe label (`Fix:` / `Right:` / `Tuzatish:`) or no mark at all.

Companion to `SLIDE-ANATOMY-MEASURED.md` (which sets the **word budget**:
median 32 words, 76% of good slides under 60). This file sets the **shape**.
It exists because the reviewer's verdict on the current decks was: *"plain
text, no depth, no highlights … information organization is completely off"* —
and the one slide they liked was a 3-column table. The diagnosis: every slide
is the same shape (title + flat bullet list), so nothing signals what matters
or how the pieces relate. The cure is a small vocabulary of distinct shapes,
each matched to a content shape.

Every template below uses ONLY the syntax the renderer supports and is
copy-pasteable into a generation prompt as-is.

---

## 1. The renderer — verified facts and traps

Verified against `LessonElementCard.tsx` (`renderMarkdown`, lines 60–148).

**What works, and what it looks like:**

| Syntax | Renders as |
| `# H1` | xl bold, large top margin — slide title |
| `## H2` | lg bold — section head; only slightly smaller than H1 |
| `### H3` | body-size semibold — a LABEL, not a heading |
| `**bold**` | strong |
| `*italic*` | em; bold-inside-italic works |
| `> line` | boxed callout: left accent border, italic, muted colour |
| `- item` | disc bullet list |
| `\| a \| b \|` rows | real table: tinted semibold header row, zebra body |
| blank line | vertical space (newlines become br) |

**Traps — each verified in the code, not guessed:**

1. **Numbered lists lose their numbers.** `1. item` renders as a plain disc
   bullet (the regex keeps only the text). Any sequence that needs visible
   numbers must write them manually: `**1.** First step` as a plain line.
2. **Table cells get NO inline formatting.** `**bold**` inside a cell shows
   literal asterisks. Tables carry *structure*; salience inside a table comes
   from word order, short cells, or a `✗` glyph — never `✓` (the import
   scrubber strips it, leaving an empty cell that shifts the row).
   *(Platform update 2026-08-27: FIXED — `**bold**` inside cells now renders.)*
3. **Empty table cells are dropped and the row shifts left.** The renderer
   filters out empty cells, so `| | will | may |` becomes a 2-cell row under a
   3-cell header. Put something in every cell — a word or `–`.
4. **Each `>` line is its own box.** Two consecutive `>` lines render as two
   stacked callouts, not one. Keep a callout to one line.
5. **No nested bullets.** An indented `  - item` renders as literal text.
   Flatten, and use bold run-in labels or `###` group labels instead.
6. **No** `---` rules, no backtick code, no images, no HTML, no colour.
   Your only "divider" is a heading, a blank line, or a `>` callout. Your
   only "colour" is unicode glyphs (`→ ✓ ✗ ≠`) — ration them (see §5).
7. **H1 and H2 are close in size** (xl vs lg). Hierarchy on a slide comes
   mostly from weight, spacing and shape, not from dramatic size jumps —
   which is exactly why the shape patterns below matter.

One helpful non-standard behaviour: every newline is a real line break
(`<br/>`), so you can compose line-by-line layouts without list syntax.

---

## 2. Why the flat-bullet slide fails — the principles

Each principle below is tagged **[evidence]** (studied, citable) or
**[convention]** (professional practice, not an experimental finding).

**Scanning and the F-pattern [evidence].** Eyetracking shows readers of
unformatted screen text scan in an F: the first line or two get read, then
attention collapses to the left edge ([Nielsen 2006](https://www.nngroup.com/articles/f-shaped-pattern-reading-web-content-discovered/)).
NN/g's own follow-up stresses the F-pattern is what happens when text gives
no cues — formatting (front-loaded keywords, headings, groups) *breaks* the F
([NN/g 2017](https://www.nngroup.com/articles/f-shaped-pattern-reading-web-content/)).
So-what: the first line and the first word of every line carry the slide.
Start bullets with the load-bearing word, never with filler ("It is important
that…"). The Z-pattern for sparse pages is **[convention]** — plausible,
not evidenced the same way.

**Chunking [evidence].** Working memory holds a handful of chunks — 7±2 in
Miller's classic (1956), closer to ~4 in modern estimates (Cowan 2001).
So-what: more than ~5 parallel items is a wall; split into 2–3 *named*
groups of 2–3 (Pattern 2), which also matches the reference decks' measured
anatomy.

**Gestalt grouping [evidence for the phenomena].** Things close together
read as one group (proximity); things that look alike read as the same kind
of thing (similarity); things inside one boundary read as belonging together
(common region — Palmer 1992; the classics from Wertheimer 1923). In this
renderer: proximity = blank lines; similarity = repeating one line-shape for
one kind of content; common region = the only two boxes you have, **table
cells** and **`>` callouts**. Note the trap in similarity: a slide of ten
identical bullets is a similarity signal shouting "these are all the same
kind of thing" — which is precisely the reviewer's complaint.

**Signalling [evidence, with honesty].** Cues that highlight the essential
material and its organisation improve learning — Mayer's own studies show a
medium effect (d ≈ 0.52), and meta-analyses confirm a positive but
moderated effect that shrinks when everything is cued
([Mayer & Fiorella, Cambridge Handbook ch. 17](https://www.cambridge.org/core/books/abs/cambridge-handbook-of-multimedia-learning/signaling-or-cueing-principle-in-multimedia-learning/3972D4ACC628D5B53F7B2B4785DB2B06);
[Alpizar, Adesope & Wong 2020 meta-analysis](https://link.springer.com/article/10.1007/s11423-020-09748-7);
[Richter, Scheiter & Eitel 2016](https://www.sciencedirect.com/science/article/abs/pii/S1747938X15000664)).
So-what: bold the discriminating word — and only it. A slide where nothing
is bold and a slide where everything is bold carry the same zero signal.

**Progressive disclosure [convention, widely accepted].** Show the claim
first, the detail second; pose the question before the answer. On slides
this means: headline-as-claim (Pattern 1), and splitting dense material
across a run of slides instead of compressing it into one — the reference
decks measurably do this (one colour-harmony type per slide, ten slides).

**Contrast by rarity [mechanically true].** Emphasis is a budget. Bold
works because it is rare; each additional bold run devalues the others.
This is a logical property of contrast, not a finding — but it is why §5
sets hard numbers.

The flat-bullet slide fails all five at once: no hierarchy (every line the
same weight), no chunks (one undifferentiated list), no regions, no signal,
everything disclosed at once.

---

## 3. Telegram — doing a lot with very little

Telegram messages have no headings, no font sizes, no tables — just nine-ish
entities: bold, italic, underline, strikethrough, spoiler, monospace/code,
link, blockquote, and expandable blockquote
([core.telegram.org/api/entities](https://core.telegram.org/api/entities)).
Yet a good channel post is instantly scannable. How:

1. **Position substitutes for size.** With no headings, the first line in
   bold IS the headline. Convention assigns hierarchy that markup can't.
2. **One primitive, one job.** Bold = the point; italic = asides and titles;
   quote = another voice or a lifted key line; code = literal strings;
   spoiler = the answer, hidden until tapped. Channels that swap these jobs
   mid-post become unreadable — the meaning of a primitive is its consistency.
3. **Progressive disclosure needs only one primitive.** The spoiler and the
   expandable blockquote are "answer after the question" and "detail folded
   away" as single markup features. Our renderer has neither — so we get the
   same effect with *ordering*: question slide, then answer slide
   (Pattern 12), or claim first, support below (Pattern 1).
4. **Scarcity is the mechanism.** A post with three bold runs reads as
   structured; a post half-bold reads as shouting.

The lesson for us: our renderer is *richer* than Telegram (real headings,
real tables). If Telegram posts can be scannable with less, our slides have
no excuse — provided each primitive keeps exactly one job (§5).

---

## 4. The pattern catalogue

Rules that apply to every pattern:

- One pattern per slide, plus at most one `>` callout at the bottom.
- Respect the word budget: the whole slide fits in 30–60 words.
- Kill the `A · Label:` pseudo-block everywhere — that is what `###` is for.
- Dense content expands into a run of slides; it does not compress into one.

The filled examples all use one real lesson: English future forms —
*will* = certainty, *may/might* = possibility, and future questions take
*Will…?* not *May…?*.

---

### Pattern 1 — Headline Claim

The title states the rule as a full sentence; the body only supports it.

**Use when:** the slide has one big idea and everything else is support;
first slide of a concept arc.
**Not when:** two things are being contrasted (P5/P6) or the content is a
lookup set (P7) — a claim-title over a table buries the table.

Template:

```
# <The rule, stated as a sentence a student could repeat>

- *<supporting example 1>* — <two-word gloss>
- *<supporting example 2>* — <two-word gloss>
```

Filled:

```
# Will means you are sure. May and might mean you are not.

- *The sun will rise at 6:04.* — no doubt
- *It may rain after lunch.* — perhaps
```

---

### Pattern 2 — Labeled Bins

Split a would-be bullet wall into 2–3 named groups of 2–3 items.

**Use when:** 5+ parallel facts that fall into natural categories.
**Not when:** items share the same attributes and invite look-up across
categories — that is a table (P6/P7). Never make a bin of one item.

Template:

```
## <What the groups have in common>

### <Group label A>
- <item>
- <item>

### <Group label B>
- <item>
- <item>
```

Filled:

```
## Talking about tomorrow: two levels of confidence

### Certain — will
- *You will get your results on Friday.*
- *The bus will leave at 9:00.*

### Possible — may / might
- *It may rain after lunch.*
- *We might go to the lake.*
```

---

### Pattern 3 — Rule Card

The rule in a callout box, then why, then its exception.

**Use when:** a rule plus a boundary or exception — exactly the shape of
"future questions take Will…? not May…?".
**Not when:** there is no exception; then P1 or P4 is lighter.

Template:

```
## <Topic>

> <The rule in one line, key word in **bold**>

<One line of why, or the boundary of the rule.>

- *<correct example>*
- ✗ *<wrong example>* — <why it is wrong, briefly>
```

Filled:

```
## Future questions

> Ask about the future with **Will…?** — never **May…?**

*May I…?* exists, but it asks for permission, not about the future.

- *Will she come to the party?*
- ✗ *May she come to the party?* — this asks if she is allowed to
```

---

### Pattern 4 — Key Callout

One `>` line at the bottom of a slide: the single thing to remember.

**Use when:** closing a slide or an arc; the "if you remember one thing"
line. Combines with any other pattern.
**Not when:** the slide already has a `>` rule box (P3) — two boxes cancel
each other; or as a slide on its own with no body above it.

Template:

```
<slide body in some other pattern>

> **Remember:** <the compressed rule, under 10 words>
```

Filled:

```
- *It will rain.* — certain
- *It may rain.* — possible

> **Remember:** will = sure · may / might = maybe
```

---

### Pattern 5 — Versus Split

Two labeled blocks, side by side vertically — richer than a table row,
tighter than two slides.

**Use when:** exactly two things contrasted, each needing a sentence or two
of its own; when you need bold/italic *inside* the comparison (tables
can't render it).
**Not when:** three or more items, or the contrast reduces to one attribute
— then P6 (table) or P8 (minimal pair) is sharper.

Template:

```
## <A> vs <B>

### <A> — <its one-word character>
- *<example>*
- <what the speaker is doing>

### <B> — <its one-word character>
- *<example>*
- <what the speaker is doing>
```

Filled:

```
## will vs may / might

### will — certainty
- *She will be at school tomorrow.*
- The speaker commits: no doubt left.

### may / might — possibility
- *She might be at school tomorrow.*
- The speaker leaves the door open: perhaps yes, perhaps no.
```

---

### Pattern 6 — Contrast Table

Attribute-by-attribute comparison — the shape the reviewer liked.

**Use when:** 2–3 items compared on the SAME 3+ attributes; the reader
will look up "how does X do Y".
**Not when:** items don't share attributes (P7), only one attribute differs
(P8), or cells would exceed ~6 words — a table of sentences is a worse
paragraph. Remember: no bold in cells, no empty cells (use `–`).

Template:

```
## <What is being compared>

| <attribute> | <A> | <B> |
| <question 1> | <A's answer> | <B's answer> |
| <question 2> | <A's answer> | <B's answer> |
| Example | <short example> | <short example> |
```

Filled:

```
## will vs may / might at a glance

| Question | will | may / might |
| How sure? | certain | only possible |
| Typical use | promises, facts | guesses, plans |
| Example | It will rain. | It may rain. |
| In questions? | Will it rain? | – not for future questions |
```

---

### Pattern 7 — Term Ledger

A definition table: term → meaning → example. The renderer's substitute
for a definition list.

**Use when:** introducing 3–6 new words/terms that do NOT share attributes
— each is just "this means that".
**Not when:** only 1–2 terms (use P3 or bold run-in lines), or the terms
invite cross-comparison on shared questions (P6).

Template:

```
## <The word set>

| Word | Meaning | Example |
| <term> | <under 5 words> | <short sentence> |
| <term> | <under 5 words> | <short sentence> |
```

Filled:

```
## Three ways to talk about the future

| Word | Meaning | Example |
| will | sure it happens | She will pass the exam. |
| may | possible, a little formal | It may snow tonight. |
| might | possible, more doubt | We might be late. |
```

---

### Pattern 8 — Minimal Pairs

Identical sentences, one word swapped, glosses attached. The strongest
contrast device in language teaching.

**Use when:** the entire difference between two forms is one word — the
exact shape of will/may.
**Not when:** the two things differ in structure, not in one slot.

Template:

```
## One word changes <what it changes>

*<sentence with **A**>* — <what the speaker means>
*<same sentence with **B**>* — <what the speaker means now>

*<second pair, sentence with **A**>* — <gloss>
*<second pair, sentence with **B**>* — <gloss>
```

Filled:

```
## One word changes how sure you are

*It **will** rain tomorrow.* — I am certain. Cancel the picnic.
*It **may** rain tomorrow.* — Perhaps. Take an umbrella just in case.

*She **will** call you.* — a promise
*She **might** call you.* — don't wait by the phone
```

---

### Pattern 9 — Wrong → Right

A real error, its correction, and one line of why.

**Use when:** a known misconception or frequent student error; homework
review slides.
**Not when:** no one actually makes the error — invented errors teach the
error. One error per slide (the anatomy doc's rule: misconceptions get one
slide each, not six on a page).

Template:

```
## Fix it

✗ *<the wrong sentence>*
**Fix:** *<the corrected sentence>*

**Why:** <one line naming the rule that was broken>
```

Filled:

```
## Fix the question

✗ *May she come tomorrow?*
**Fix:** *Will she come tomorrow?*

**Why:** future questions take Will…?. *May I…?* asks for
permission — *May I leave early?* — not about what happens.
```

---

### Pattern 10 — Numbered Path

An ordered procedure with hand-written bold numbers (the renderer eats
`1.` markers — see §1 trap 1).

**Use when:** steps whose ORDER matters; a thinking recipe the student
applies.
**Not when:** order doesn't matter — fake sequence is noise; use bullets
or bins.

Template:

```
## <The task the steps accomplish>

**1.** <step, load-bearing word first>
**2.** <step>
**3.** <step — a decision step may fork: <cond> → **A** · <cond> → **B**>
```

Filled:

```
## Choose your verb in three steps

**1.** Future? If you are not talking about the future, stop here.
**2.** How sure? Sure → **will** · not sure → **may / might**
**3.** Question? Future questions always take **Will…?**
```

---

### Pattern 11 — Decision Fork

Condition → outcome lines with arrows; a flowchart flattened to text.

**Use when:** the student must CHOOSE between forms based on conditions;
summary slide after P5/P6.
**Not when:** more than ~4 branches (split the slide), or when conditions
need explanation — explain first on earlier slides, fork last.

Template:

```
## <The choice>

- <condition> → **<outcome>** — *<micro-example>*
- <condition> → **<outcome>** — *<micro-example>*
- <condition> → **<outcome>** — *<micro-example>*
```

Filled:

```
## Which verb?

- Sure it happens → **will** — *The bus will leave at 9.*
- Just possible → **may / might** — *The bus might be late.*
- Asking about the future → **Will…?** — *Will the bus be on time?*
```

---

### Pattern 12 — Question Before Answer

The question stands alone on one slide; the answer opens the next.
Progressive disclosure with no markup at all — ordering does the work.

**Use when:** the answer is worth predicting; opening an arc; checking
understanding mid-lesson.
**Not when:** the deck is for self-study reading where the "reveal" is one
scroll away and just irritates; or for trivial questions.

Template:

```
(slide N)
## <The question, genuinely answerable from what came before>

*<option A>* — or — *<option B>*

Decide before the next slide.

(slide N+1)
## <The answer as a claim>

> **<the resolving rule in one line>**

<one line of because>
```

Filled:

```
(slide N)
## The forecast says 100% rain. What do you say?

*It will rain.* — or — *It may rain.*

Decide before the next slide.

(slide N+1)
## It will rain.

> **100% sure → will.** May would claim doubt you don't have.

Certainty is information — will tells your listener to trust it.
```

---

## 5. Emphasis discipline

Emphasis is a budget, and the renderer gives you exactly three currencies:
bold, italic, and the `>` box. Hard numbers, tuned to a 30–60-word slide:

- **Bold: at most 5 runs per slide, at most 3 words per run.** Past that,
  bold stops being a signal and becomes texture. On most slides, 2–3 runs.
- **Bold the load-bearing word, never the sentence.** In
  "*It **will** rain*", the discriminating word is *will* — bolding the
  whole sentence signals nothing, because the sentence isn't what differs.
  Legitimate bold: the contrastive word, a manual step number (`**1.**`),
  a run-in label (`**Why:**`), the resolving word in a callout.
- **Italic is the second voice, never the first.** Example sentences,
  glosses, asides, titles. If the main point is italic, the hierarchy is
  inverted. (The renderer also italicises `>` boxes — one more reason a
  callout should carry a bold key word.)
- **One `#` per slide, at most.** `##` is the workhorse title; `###` is a
  label for a bin, never a title.
- **Glyph budget: `→ ✗` only where they carry meaning** — the wrong-mark
  and causal arrows. (`✓ ✔ ✅` are stripped at import — see scrubber ban F.) They are the only colour-like salience available
  (and the only emphasis that works inside table cells), which is precisely
  why they must stay rare. No decorative emoji.

**Why all-bullets-all-the-time fails:** three principles from §2 at once.
Similarity — ten identically shaped lines assert "these are ten of the same
thing", flattening rule, example, exception, and aside into one category.
Chunking — an unbroken list exceeds working memory with no groups to
rescue it. Signalling — when every line has the same weight, the slide
contains no information about what matters. A bullet list is the correct
shape for one thing only: 3–5 genuinely parallel items of the same kind.
Everything else on this page exists because most slide content is *not* that.

---

## 6. Decision table — from content shape to pattern

| The content is… | Reach for | Because |
| one big idea + support | P1 Headline Claim | claim reads before detail |
| 5+ parallel facts | P2 Labeled Bins | chunks of 3 beat a wall of 8 |
| a rule with an exception | P3 Rule Card | box the rule, list the edge |
| the one thing to remember | P4 Key Callout | common region = memorable |
| two things, richly contrasted | P5 Versus Split | needs bold; tables can't |
| 2–3 things, same 3+ attributes | P6 Contrast Table | lookup across cells |
| new terms and meanings | P7 Term Ledger | no shared attributes to cross |
| a one-word difference | P8 Minimal Pairs | contrast at the exact word |
| a real student error | P9 Wrong → Right | error, fix, and the why |
| ordered steps | P10 Numbered Path | order is the content |
| a choice between forms | P11 Decision Fork | condition → outcome scan |
| an answer worth predicting | P12 Question Before Answer | disclosure by ordering |

Tie-breakers for comparison content, since three patterns compete:

- **Table (P6) beats bullets** the moment the reader might ask "what does B
  do for attribute 2" — bullets make them re-read; a table makes it a cell.
- **Versus Split (P5) beats a table** when there are exactly two items and
  each side needs full sentences or inline emphasis — the renderer's
  plain-text cells make tables bad at nuance.
- **Term Ledger (P7) beats both** when items don't share attributes: forcing
  unlike things into comparison columns manufactures empty or padded cells
  (which this renderer then drops or misaligns).
- **Minimal Pairs (P8) beats all three** when the whole difference is one
  word: the tightest possible contrast is the same sentence twice.

---

Sources: [NN/g F-pattern (2006)](https://www.nngroup.com/articles/f-shaped-pattern-reading-web-content-discovered/) ·
[NN/g F-pattern revisited (2017)](https://www.nngroup.com/articles/f-shaped-pattern-reading-web-content/) ·
[Mayer & Fiorella, signaling ch., Cambridge Handbook](https://www.cambridge.org/core/books/abs/cambridge-handbook-of-multimedia-learning/signaling-or-cueing-principle-in-multimedia-learning/3972D4ACC628D5B53F7B2B4785DB2B06) ·
[Alpizar, Adesope & Wong 2020, signaling meta-analysis](https://link.springer.com/article/10.1007/s11423-020-09748-7) ·
[Richter, Scheiter & Eitel 2016, text-picture signaling meta-analysis](https://www.sciencedirect.com/science/article/abs/pii/S1747938X15000664) ·
[Telegram entities](https://core.telegram.org/api/entities) ·
Miller 1956 (7±2), Cowan 2001 (~4 chunks), Palmer 1992 (common region),
Wertheimer 1923 (grouping) — classic papers, cited from the literature.
