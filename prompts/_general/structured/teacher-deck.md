# Teacher Material Deck (structured) — {{SUBJECT}}

Build ONE coherent teacher lesson-plan deck for this {{SUBJECT}} lesson, from the facts in
the `--- LESSON CONTEXT ---` block below. Return **JSON only**, conforming exactly to the
provided schema. No prose, no code fences, no commentary before or after the JSON.

## Facts discipline (CRITICAL)

Every date, number, name, definition, rule, and causal claim you state about the world MUST
come from the LESSON CONTEXT. Never invent a date, statistic, name, or fact that is not in
the context — if something isn't there, don't state it. This does NOT apply to the teaching
and structure numbers you are asked to set yourself (stage timings, quiz option counts,
rubric points) — those are yours to author per the shape below, not facts you look up.

## Front matter (meta, passport, objectives, core_idea)

Every deck opens with four required blocks, in this order, before the stage-by-stage plan:

- **`meta`** — the deck's header data: `subject_label`, `grade`, `topic_number`, `topic_title`,
  `duration_min` (always **45**), `lesson_type` (a "Yangi bilim berish" — new-knowledge —
  style lesson type), `method` (the lesson's method steps, e.g. Video -> tahlil -> kviz ->
  juftlik), `materials` (what the teacher needs — video, screen, worksheets), and `video_ref`
  (the Akademiya video used in stage 3).
- **`passport`** — the lesson's 6-field passport card (template slide 2): `fan_sinf` (subject +
  grade), `mavzu` (topic), `dars_turi` (lesson type), `metod` (method), `kerakli_vosita`
  (materials), `baholash` (assessment approach).
- **`objectives`** — the lesson's Bloom-style objectives (template slide 3): `bilib_oladi`
  (will know), `qila_oladi` (will be able to do), `tushunadi` (will understand).
- **`core_idea`** — the lesson's single big idea (template slide 4): a one-line `statement`
  plus a short `elaboration` expanding on it. Every later stage, quiz question, and reflection
  should trace back to this idea.

## Lesson map (overview) vs stages (detail)

`lesson_map` is a REQUIRED array, SEPARATE from `stages` — it is the compact minute-by-minute
overview table (template slide 5, "Dars xaritasi"), not the detailed stage-by-stage plan below.
It has exactly 7 entries, one per stage, each with `index`, `title`, a one-line `description`,
and `minutes` — and its `minutes` must sum to **45**, same total as `stages`. Author it as the
short summary version of the `stages` section that follows; keep the two consistent (same 7
stages, same minute split, same order) but `lesson_map`'s `description` stays to one line while
`stages` carries the full detail.

## Stages (detail)

The deck is a **45-minute** lesson plan in exactly **7 stages**, minutes in this order:
**3 + 3 + 9 + 9 + 8 + 9 + 4 = 45**.

`badge: teacher_only` stages never carry `screen_text` (leave it unset); only `badge: ekranga`
stages carry `screen_text` — the line the teacher puts on the screen for the whole class.

- **Stage 1 — Tashkiliy qism (3 min).** `badge: teacher_only`. Roll call, greeting, framing
  today's topic. Teacher-only — no `screen_text`.
- **Stage 2 — Motivatsiya / hook (3 min).** `badge: ekranga`. One on-screen hook question in
  `screen_text` that pulls the student into today's core idea. Displayed to the whole class.
- **Stage 3 — Akademiya video (9 min).** `badge: teacher_only`. The in-class video segment.
  Use `points` to choreograph before/during/after: (1) what the teacher says BEFORE playing
  the video (frame what to watch for), (2) what happens DURING the video (teacher pauses /
  prompts), (3) what happens AFTER the video. Include an observation task tailored to the
  actual lesson content — e.g. "write down 3 names and 3 dates you heard" using names/dates
  that are genuinely in the LESSON CONTEXT (never placeholder names).
- **Stage 4 — Tayanch nuqtalar (9 min).** `badge: teacher_only`. Exactly **three** core
  reference points (tayanch nuqta) from the lesson, each as a `Point` (`title` + `detail`).
- **Stage 5 — Amaliy mashg'ulot (8 min).** `badge: ekranga`. Pair or individual practice tied
  to the tayanch nuqtalar above.
- **Stage 6 — Mustahkamlash / quiz (9 min).** `badge: ekranga`. Where the on-screen quiz runs.
- **Stage 7 — Yakunlash (4 min).** `badge: ekranga`. Wrap-up, reflection questions, homework
  framing.

## Quiz + answer key

Exactly **5 questions**, each with **4 options** (labels `A`-`D`) and exactly one correct
option. The 5 questions must check comprehension of the in-class video (stage 3) and ladder
up toward the lesson's core idea (`core_idea`) — start concrete/recall, end conceptual. Never
leak the correct answer in the option text or the question wording — the flag alone marks it.
The answer key is a SEPARATE, teacher-only list: one entry per quiz question, same numbering,
each with a short explanation of why that option is correct (grounded in the LESSON CONTEXT).

## Pair work, conclusion, rubric

- **Pair work**: a short intro plus one or more tasks students do in pairs, building on the
  tayanch nuqtalar.
- **Conclusion**: reflection questions that ask students to connect the lesson back to the
  core idea — not a restatement, a genuine reflection prompt.
- **Rubric**: exactly **10 points** total, split **5 kviz + 3 amaliy + 2 faollik** (component
  points must sum to 10). Grade bands: **9-10 -> "5"**, **7-8 -> "4"**, **5-6 -> "3"** (below
  5 is left to teacher judgment and does not need its own band).

## Output discipline

Emit clean JSON: integers as bare integers (not quoted strings), strings as strings, no
trailing commentary, and no keys beyond what the schema defines.

{{LANGUAGE_RULES}}
