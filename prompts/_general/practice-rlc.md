# Prompt: Real-Life Challenge — first-person expert decision game — {{SUBJECT}}

You are generating the **Real-Life Challenge** for this {{SUBJECT}} lesson. The
student makes decisions inside a scenario in a concrete role; a familiar
role is suitable for grades 1–6. They predict, decide, gather information, commit, name the governing
concept, and justify their reasoning. Build ONE realistic decision scenario
grounded only in this session's {{SUBJECT}} content, with a named role
appropriate to the lesson and grade. Use a different situation and a different
use of the lesson knowledge than the preview.

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
- **Condition the scenario on {{SUBJECT}}.** The header sections, the five
  steps, and the feedback keep their exact shape for every subject; what fills
  them follows the subject:
  - When {{SUBJECT}} is an economics lesson, the decision is made under a real
    constraint, in so'm, with instruments the student could actually meet — a
    household budget, a bazaar seller, an informal family loan — never credit
    scores or mortgages. The scenario measures simulated judgement, never
    conduct: every feedback line judges the call inside the fiction, not the
    student's character. Keep the Role line on an existing `expert_role` value
    (e.g. `business_consultant`) — never invent one.
  - History: source analysis applies only when taught and an actual source is
    supplied. Show the identified excerpt and its supplied metadata; ask only
    what it can establish. Otherwise use labelled lesson information cards to
    support one concrete decision about the actual lesson facts. A source name
    alone cannot settle it.
  - Biology: use organizational-level predictions only when those levels and
    mechanisms are taught. Otherwise use the actual classification, observation
    or relation; no wrong-level template is required.
  - Physics: compare named observers before computing only for a taught
    reference-frame method. Otherwise use this lesson's actual concept, relation
    or procedure with all needed readings supplied before the question.
  - Chemistry: use composition-change criteria only when taught and observations
    are supplied; otherwise use the actual taught classification or relation.
  - Mathematics: a numeric dispute applies only to a taught calculation with
    its inputs supplied; other concepts, classifications and procedures are valid.
  - For other subjects, keep the general rules above.
- **Pressure is narrative.** Stakes belong to the in-fiction role ("the stall
  loses the day's profit"), never to the student's clock or score; urgency
  lives in the story, never in a mechanic.
- **Never emit a PISA level or band** — no such level or band label appears
  anywhere in the output.

## What to produce

Write the scenario as Markdown sections, in this order.

### Header sections (before the steps)

**Label language:** the student-read label WORDS — Role, Task, Context, Prediction,
Why, Confidence, and the phase title — render in the OUTPUT language whenever the
lesson's level is below B1 or the medium is Uzbek/Russian: `**Rol (expert_role):**`,
`**Vazifa:**`, `**Vaziyat:**`, `**Bashorat:**`, `**Nega:**`, `**Ishonch:**` (Russian
medium uses its natural equivalents). English labels stay only in B1+ all-English
lessons. The parenthesized machine keys — `expert_role`, `kind: …`,
`min_chars` — and their values stay EXACTLY as this format defines them, in English,
at every level.

**The `#` phase title is the scenario's own title in the OUTPUT language ONLY** —
it NEVER contains the phase name ("Real-Life Challenge", "RLC") or any other
English words in a uz/ru lesson. Write what the case is about:
`# Ko'prik loyihasidagi qaror` — not `# Real-Life Challenge — …`.

**Step headings localize too.** The heading format is
`### <step label> (kind: <machine-key>)`, and the step label — number word AND
label — is written in the output language with EXACTLY these strings:

| kind | uz heading label | ru heading label | en heading label |
|---|---|---|---|
| decision | `1-qadam — Qaror` | `Шаг 1 — Решение` | `Step 1 — Decision` |
| info_request | `2-qadam — Ma'lumot so'rovi` | `Шаг 2 — Запрос информации` | `Step 2 — Information request` |
| final_decision | `3-qadam — Yakuniy qaror` | `Шаг 3 — Окончательное решение` | `Step 3 — Final decision` |
| concept_select | `4-qadam — Asosiy tushuncha` | `Шаг 4 — Ключевое понятие` | `Step 4 — Governing concept` |
| reasoning | `5-qadam — Asoslash` | `Шаг 5 — Обоснование` | `Step 5 — Reasoning` |

The `(kind: …)` tag is the parse anchor and never translates. Never invent a
different label wording — these strings are machine-recognized.

- **Role** — put the machine-readable expert role on its own line as
  `**Role (expert_role):** <value> — <named human role>`, where `<value>` is
  EXACTLY one of: `fire_inspector`, `structural_engineer`, `business_consultant`,
  `medical_diagnostician`, `agronomist`, `teacher`, `lawyer`, `city_planner`,
  `epidemiologist`, `ethicist`, `historian`, `general`. Choose the closest fit,
  and give a concrete role after the dash. For grades 1–6, use a familiar role
  (e.g. a class helper), with `general` when no specialist enum fits; no
  professional title is required. Directly on the NEXT line, add the machine-parsed role label in the
  output language: `**Rol nomi (expert_role_label):** <role name in the lesson
  language>` (uz e.g. `Qurilish muhandisi`; ru `Инженер-строитель`; en lessons
  the plain English role name). The platform shows THIS label to the student —
  the enum value is never student-visible.
- **Task** — one sentence naming the decision the student was called in to make.
- **Context** — 2–4 sentences: the situation, the constraints, and the
  information/readings available. Include exact numbers, units, and formulas as
  given when the {{SUBJECT}} lesson involves them. For G7+ include ONE irrelevant
  datum the student must dismiss. The Context itself NEVER labels or explains
  it — no "(chalg'ituvchi ma'lumot)", "(red herring)", "(eslatma: ...)" or any
  other marker beside it; a labeled distractor is no distractor. It is identified
  only in the Final summary — and there name it in the OUTPUT LANGUAGE (Uzbek
  "chalg'ituvchi ma'lumot", Russian «отвлекающий факт»), never the bare English
  "red herring".
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

**Every step OPENS with its question.** The FIRST line after each step heading is
one plain question sentence — the decision the student faces in that step —
before any options, chips, or metadata lines. This line is MACHINE-PARSED as the
step's prompt: never omit it, never fold it into an option, and never start a
step's body directly with `A)` or a bullet. (Step 5's open prompt already is this
line.) A step with no question line imports as a repaired item flagged for
review — the question line is what makes it import clean.

**Every step stands on its own (NON-NEGOTIABLE).** A step's wording may build
on the CASE, on given data, or on a result the step itself states — NEVER on
what the student answered in an earlier step ("the concept selected in Step 4",
"your final decision from Step 3", "4-qadamda tanlangan…", "3-qadamdagi
qaroringiz…"). The platform tells a wrong student they were wrong but neither
reveals the answer nor offers a retry, so a step leaning on the student's
history becomes unanswerable for exactly the student who most needs it. Name
the concept, the quantity, or the decision itself instead — and never mention
another step by its number anywhere in a step's body.

- `### 1-qadam — Qaror (kind: decision)` (heading label per the language table above) — the first call the expert must make.
  - **3–4 options**, one per line, each formatted `A) <option>` … `D) <option>`
    (capital letter + `)` — the letters are machine-parsed; a leading `-` bullet
    before the letter is allowed but nothing else). Exactly ONE carries the
    correctness tag.
    The wrong ones are **real {{SUBJECT}} misconceptions students actually hold**
    (drawn from the lesson's typical confusions), not nonsense.
  - Grades 1–6: omit per-choice Why and confidence prose. The final reasoning
    step provides the brief justification. For grades 7–11 these prompts are
    optional prose; never add unsupported fields or extra steps.
- `### 2-qadam — Ma'lumot so'rovi (kind: info_request)` (label per the table) — before committing,
  what should the expert check, measure, or ask for next?
  - **3–4 options** (candidate readings / tests / questions). Exactly ONE carries
    the correctness tag — the piece of information that actually resolves the
    call. The distractors are plausible-but-uninformative checks. Selecting
    a check does not supply its result. Before the final decision, put the
    actual result in student-visible context for everyone, regardless of choice.
    Keep each step's first body line a question: needed data can sit in that
    question or in the shared Context, never only in feedback or an answer key.
  - Omit per-choice Why/confidence for grades 1–6; optional prose for grades 7–11.
- `### 3-qadam — Yakuniy qaror (kind: final_decision)` (label per the table) — the committed call now
  that the information is in hand.
  - **3–4 options**; exactly ONE carries the correctness tag.
  - Omit per-choice Why/confidence for grades 1–6; optional prose for grades 7–11.
- `### 4-qadam — Asosiy tushuncha (kind: concept_select)` (label per the table) — which lesson concept
  actually drove the correct decision?
  - **3–4 concept chips** as a bulleted list (the governing concept plus close
    lesson concepts as distractors). Exactly ONE carries the correctness tag.
- `### 5-qadam — Asoslash (kind: reasoning)` (label per the table) — a free-text justification.
  - One open prompt asking the student to justify the correct call in their own
    words by NAMING the governing concept and the committed decision explicitly
    — e.g. "Kvadrat funksiyaning noli tushunchasi va $h(t) = 0$ shartidan
    foydalanib, nima sababdan …". NEVER phrase it through the student's own
    earlier answers — "4-qadamda tanlangan tushunchadan foydalanib…",
    "3-qadamdagi yakuniy qaroringiz bilan bog'lab…" are the banned shape: a
    student who missed Step 4 gets no reveal and no retry, so a backreferencing
    Step 5 is unanswerable for them, while a named concept still measures
    whether they can reason with the right rule.
  - Put the minimum length on its own line as `**Minimum length (min_chars):**
    20` for grades 1–6 (one brief explanation, 1–2 short sentences); use 80
    for older grades unless the lesson warrants otherwise. Keep the integer
    within 20–1000; the required field cannot be omitted.

### Feedback (per decision step)

For Steps 1–3 provide, after the options:

- **Correct feedback** — senior-expert voice affirming the reasoning.
- **Partial feedback** — for the right action with a weak link (a missed step,
  units, a skipped factor).
- **Wrong feedback** — MUST open with a gentle "not yet" opener in the OUTPUT
  LANGUAGE (Uzbek «Hali emas», Russian «Пока нет», English «Not yet») — never a
  flat "wrong" (Uzbek "Noto'g'ri", Russian «Неправильно»); re-aim with a
  guiding question, not the answer.
- **For grades 7–11 only, if confidence prose is included, color feedback by the confidence + correctness pattern** (this steers the
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
- **Every step's first body line is its question sentence** — before options or
  chips, never omitted (see the machine-parsed prompt rule above).
- Steps 1–3 and Step 4 each have exactly ONE tagged-correct entry; the correctness
  tag is the only correctness signal and is stripped before the student sees it.
- The student has a named role and specific decisions; familiar roles suit
  lower grades. Avoid generic "What do you think?" role-play.
- Keep the Prediction prompt and the final reasoning step. For grades 1–6 omit
  redundant per-choice Why/confidence prose and keep reasoning brief.
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

{{NOTATION_RULES}}
