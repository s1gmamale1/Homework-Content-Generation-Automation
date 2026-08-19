# Prompt: Real-Life Challenge — first-person expert decision game — {{SUBJECT}}

You are generating the **Real-Life Challenge** for this {{SUBJECT}} lesson. The
student is NOT answering questions *about* a scenario — they ARE the expert
inside it. They predict, decide, gather information, commit, name the governing
concept, and justify their reasoning. Build ONE realistic decision scenario
grounded only in this session's {{SUBJECT}} content, with a named expert role
appropriate to {{SUBJECT}}.

> **Why the shape below is exact.** This phase targets the platform's
> `rlc_config` block, which renders as a five-step game only when the five steps
> arrive in one fixed order — `decision → info_request → final_decision →
> concept_select → reasoning` — each with the fields its kind requires. Anything
> looser cannot be recovered from the markdown and the phase silently folds to
> plain study text instead of a game. Emit the five steps below, in order, every
> time.

## Primary rules

- **Draw the concepts from this lesson.** Every decision must turn on a concept,
  rule, or formula taught in this session's {{SUBJECT}} content. Pick concepts
  the student already met earlier this session so mistake-repair is possible.
  Do not invent concepts the lesson never covered.
- **Strip Test (load-bearing concept).** The lesson concept must be the reason
  the decision succeeds. Remove that concept and the scenario must STOP working
  — the student should be unable to decide correctly. If everyday intuition (or
  swapping a local place name for a generic one) answers it, regenerate.

## What to produce

Write the scenario as Markdown sections, in this order.

### Header sections (before the steps)

- **Role** — put the machine-readable expert role on its own line as
  `**Role (expert_role):** <value> — <named human role>`, where `<value>` is
  EXACTLY one of: `fire_inspector`, `structural_engineer`, `business_consultant`,
  `medical_diagnostician`, `agronomist`, `teacher`, `lawyer`, `city_planner`,
  `epidemiologist`, `ethicist`, `historian`, `general`. Choose the closest fit,
  and give a concrete named professional after the dash (never a generic "Siz
  olimsiz").
- **Task** — one sentence naming the decision the student was called in to make.
- **Context** — 2–4 sentences: the situation, the constraints, and the
  information/readings available. Include exact numbers, units, and formulas as
  given when the {{SUBJECT}} lesson involves them. For G7+ include ONE irrelevant
  datum the student must dismiss, and note (to yourself, in the final summary)
  that it is the distracting datum (the "red herring") — when you name it in the
  summary, name it in the OUTPUT LANGUAGE (Uzbek "chalg'ituvchi ma'lumot",
  Russian «отвлекающий факт»), never the bare English "red herring".
- **Prediction** — a mandatory prompt asking the student what they expect to
  find or happen *before* deciding, e.g. "Hisoblashdan oldin, natija qanday
  chiqishini kutyapsiz?"

### The five steps (EXACTLY five, in this order)

Each step is its own `###` subsection whose heading names the step kind exactly
as written below. **Mark the one correct option/chip by appending a correctness
tag in the OUTPUT LANGUAGE at the end of its line** — Uzbek «(To'g'ri)», Russian
«(Верно)», English «(Correct)». That tag is the ONLY place correctness is
expressed; the platform strips it before the student sees the step, so never
reveal the answer by option length, position, or wording, and never write
"to'g'ri javob"/"правильный ответ" inside an option label itself.

- `### Step 1 — Decision (kind: decision)` — the first call the expert must make.
  - **3–4 options** as a bulleted list. Exactly ONE carries the correctness tag.
    The wrong ones are **real {{SUBJECT}} misconceptions students actually hold**
    (drawn from the lesson's typical confusions), not nonsense.
  - A mandatory **Why** prompt — the student justifies the call in 1–2 sentences.
    Name the reasoning a sound Why should reach.
  - A mandatory **confidence** prompt — the student rates Sure / Maybe / Guess.
- `### Step 2 — Information request (kind: info_request)` — before committing,
  what should the expert check, measure, or ask for next?
  - **3–4 options** (candidate readings / tests / questions). Exactly ONE carries
    the correctness tag — the piece of information that actually resolves the
    call. The distractors are plausible-but-uninformative checks.
  - A mandatory **Why** prompt and **confidence** prompt, as in Step 1.
- `### Step 3 — Final decision (kind: final_decision)` — the committed call now
  that the information is in hand.
  - **3–4 options**; exactly ONE carries the correctness tag.
  - A mandatory **Why** prompt and **confidence** prompt.
- `### Step 4 — Governing concept (kind: concept_select)` — which lesson concept
  actually drove the correct decision?
  - **3–4 concept chips** as a bulleted list (the governing concept plus close
    lesson concepts as distractors). Exactly ONE carries the correctness tag.
- `### Step 5 — Reasoning (kind: reasoning)` — a free-text justification.
  - One open prompt asking the student to tie the concept named in Step 4 to the
    final decision in Step 3, in their own words.
  - Put the minimum length on its own line as `**Minimum length (min_chars):**
    80` (an integer, 20–1000; use 80 unless the lesson warrants otherwise).

### Feedback (per decision step)

For Steps 1–3 provide, after the options:

- **Correct feedback** — senior-expert voice affirming the reasoning.
- **Partial feedback** — for the right action with a weak link (a missed step,
  units, a skipped factor).
- **Wrong feedback** — MUST open with a gentle "not yet" opener in the OUTPUT
  LANGUAGE (Uzbek «Hali emas», Russian «Пока нет», English «Not yet») — never a
  flat "wrong" (Uzbek "Noto'g'ri", Russian «Неправильно»); re-aim with a
  guiding question, not the answer.
- **Color the feedback by the confidence + correctness pattern** (this steers the
  *tone*, not a points rubric): when the student was **Sure but wrong**, gently
  name it as a confident misconception — the case most worth correcting, so be
  direct about the faulty belief, not just the call. When the student was **Guess
  but right**, don't over-praise: treat it as lucky recall, name the concept that
  *should* have driven it, and invite them to redo the reasoning so it sticks.

### Final summary

- **Final summary** — what an expert would have done, what strong reasoning looks
  like (concept/rule/formula applied, not numbers plugged blindly), the likely
  misses, and (G7+) which datum was the distracting one (named in the output
  language) and why it didn't matter.

The summary must close the loop back to the concept named in Step 4. Compact
shape (Biology): the student, as a clinic nurse, sees a patient with bluish lips
→ reads it as low blood oxygen → ties that to the cellular-respiration concept
from the lesson. Strong reasoning names that chain; weak reasoning only lists
symptoms without linking to the mechanism the lesson taught.

## Non-negotiables

- **Exactly five steps, in the order `decision → info_request → final_decision →
  concept_select → reasoning`.** Never fewer, never more, never reordered.
- Steps 1–3 and Step 4 each have exactly ONE tagged-correct entry; the correctness
  tag is the only correctness signal and is stripped before the student sees it.
- The student is the expert: first person, named role, specific decisions — never
  generic "What do you think?" role-play.
- Prediction checkpoint, Why justification, and confidence rating fire on every
  decision step (1–3). None optional.
- Distractors are genuine {{SUBJECT}} misconceptions, not filler.
- **No within-scenario branching** — the same step sequence for all students.
- Feedback is in-character senior-expert voice, not a rubric read aloud.
- Decoration is not learning evidence — color, mood, and animation are UI polish,
  never a substitute for the reasoning the decision tests.
- For math/geometry lessons, every decision option, chip, explanation, and
  feedback line must pass a factual sanity check: verify algebraic identities by
  expansion or substitution; cancel only common multiplicative factors; preserve
  original domain restrictions for rational expressions; do not reverse theorem
  implications; remember that a square inherits both rectangle and rhombus
  properties; and do not reject `(n-2)*180°` for a simple concave polygon.

## Visuals

If a step genuinely needs a diagram, describe it as a placeholder (see Output
format) in the relevant context or step section — never emit `<svg>`. Add one
only when the diagram carries the decision; default to none.

## Output format

Respond in **Markdown only** (no JSON, no code-fenced JSON). Use `#` for the phase
title and `##`/`###` for the sections/steps described above, in order. For
visuals: do NOT emit `<svg>` or any image/HTML markup. For ANY visual (diagram OR
photo), emit a described placeholder instead — never the visual itself:
`![visual: <diagram|photo> — <what to depict, with every label, value, and axis> — image gen required](placeholder)`
The description must name the medium and every label/value/axis so the visual can be
produced from the text alone. Never output raw `<svg>`, never fabricate an image,
never invent an image URL.

## Language

{{LANGUAGE_RULES}}
