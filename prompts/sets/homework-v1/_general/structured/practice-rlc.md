# Real-Life Challenge (structured) — {{SUBJECT}}

Build ONE real-life challenge for this {{SUBJECT}} lesson. Return **JSON only**, conforming exactly
to the schema below. No prose, no code fences.

## Required shape

- `id` — short slug, non-empty.
- `title` — the challenge name.
- `intro` — 1-3 sentences setting the scene.
- `expert_role` — EXACTLY one of: `fire_inspector`, `structural_engineer`, `business_consultant`,
  `medical_diagnostician`, `agronomist`, `teacher`, `lawyer`, `city_planner`, `epidemiologist`,
  `ethicist`, `historian`, `general`. Choose the closest fit for the lesson.
- `steps` — EXACTLY 5, in this order and no other:
  1. `kind: "decision"` — `options`, 2-4 entries, exactly one `is_correct: true`
  2. `kind: "info_request"` — `options`, 2-4 entries, exactly one `is_correct: true`
  3. `kind: "final_decision"` — `options`, 2-4 entries, exactly one `is_correct: true`
  4. `kind: "concept_select"` — `concept_chips`, 2-4 entries, exactly one `is_correct: true`
  5. `kind: "reasoning"` — `min_chars`, an integer between 20 and 1000 (use 80 unless the lesson
     warrants otherwise)

Every step needs `id`, `title`, `prompt`. Every option and chip needs `id` and `label`.

## Rules

- Option and chip labels must be **distinct** from each other within a step (compared
  case-insensitively, ignoring extra spaces) and non-empty.
- **Never reveal which option is correct in the visible text.** Do not write "(correct)",
  "правильный ответ", "to'g'ri javob" or any equivalent inside a `label`, `title` or `prompt`. The
  `is_correct` flag is the only place correctness is expressed, and it is stripped before the
  student sees the exercise.
- Ground every step in THIS lesson's content. Do not invent facts.
- All student-visible text is in the output language.

{{LANGUAGE_RULES}}
