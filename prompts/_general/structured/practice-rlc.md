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
  For grades 1–6 a familiar role is allowed; use `general` for a class helper,
  for example, and use plain role wording in the visible intro.
- `steps` — EXACTLY 5, in this order and no other:
  1. `kind: "decision"` — `options`, 2-4 entries, exactly one `is_correct: true`
  2. `kind: "info_request"` — `options`, 2-4 entries, exactly one `is_correct: true`
  3. `kind: "final_decision"` — `options`, 2-4 entries, exactly one `is_correct: true`
  4. `kind: "concept_select"` — `concept_chips`, 2-4 entries, exactly one `is_correct: true`
  5. `kind: "reasoning"` — `min_chars`, an integer between 20 and 1000; use 20
     for grades 1–6 and ask for one brief explanation (1–2 short sentences).
     Use 80 for older grades unless the lesson warrants otherwise.

Every step needs `id`, `title`, `prompt`. Every option and chip needs `id` and `label`.

## Rules

- Option and chip labels must be **distinct** from each other within a step (compared
  case-insensitively, ignoring extra spaces) and non-empty.
- **Never reveal which option is correct in the visible text.** Do not write "(correct)",
  "правильный ответ", "to'g'ri javob" or any equivalent inside a `label`, `title` or `prompt`. The
  `is_correct` flag is the only place correctness is expressed, and it is stripped before the
  student sees the exercise.
- Ground every step in THIS lesson's content. Do not invent facts.
- Each `prompt` opens with a question. Put necessary shared facts in `intro`,
  and any additional result in the question/prompt before asking for a conclusion.
  Selecting a test/source does not reveal its result: supply the actual result
  to everyone, independent of their earlier choices. Do not rely on hidden keys.
- Use a different situation AND use of knowledge from the preview. Use source
  analysis, frames, levels, composition changes or numeric disputes only when
  taught with prerequisites/data supplied; otherwise use the actual lesson concept.
- For grades 1–6 omit redundant per-choice Why/confidence prose. Keep the final
  reasoning brief and never add unsupported schema fields or steps.
- Follow the language rules for student-visible text, keeping L2 target language
  and scaffolding language distinct where required.
- Keep notation inside JSON string values and escape backslashes as JSON requires;
  the notation rules do not add fields or change the JSON-only output shape.

{{LANGUAGE_RULES}}

{{NOTATION_RULES}}
