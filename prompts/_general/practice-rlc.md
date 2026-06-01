# Prompt: Real-Life Challenge — first-person expert decision game — {{SUBJECT}}

You are generating the **Real-Life Challenge** for this {{SUBJECT}} lesson. The
student is NOT answering questions *about* a scenario — they ARE the expert
inside it. They predict, decide, and justify; the system evaluates whether their
reasoning would hold up if a real practitioner read it. Build ONE realistic
decision scenario grounded only in this session's {{SUBJECT}} content, with a
named expert role appropriate to {{SUBJECT}}. Emit the structured form the
response schema requests.

## What to produce

One scenario object with these fields:

- `scenario_id` — short stable slug, e.g. `rlc_{{SUBJECT}}_<concept>_001` (optional).
- `concept_ids` — the lesson concept(s) tested. Use the **source concept IDs
  from the lesson's source map when provided**; otherwise short kebab-case slugs.
  At least one. Pick concepts the student met earlier this session so
  mistake-repair is possible.
- `role` — a named, specific expert identity appropriate to this {{SUBJECT}}
  lesson, derived from its `lesson_context` and source map (e.g. a practitioner,
  analyst, or specialist who would really make this call). Never a generic "Siz
  olimsiz" — give a concrete professional role.
- `task` — one sentence naming the decision the student was called in to make.
- `grade_band` / `pisa` — e.g. `"G7-9"` / `"L4"` (optional but set them).
- `context` — 2–4 sentences: the situation, the constraints, and the
  information/readings available. Include exact numbers, units, and formulas as
  given when the {{SUBJECT}} lesson involves them.
- `prediction_prompt` — "Hisoblashdan oldin, natija qanday chiqishini kutyapsiz?"
  Mandatory — the student predicts the outcome before deciding.
- `decisions` — **2 to 4** decision objects (3 for G7-9). Each has:
  - `question` — the call, applying the concept/rule/formula.
  - `options` — 3–4 actions. The wrong ones are **real {{SUBJECT}} misconceptions
    students actually hold** (drawn from the lesson's typical confusions), not
    nonsense.
  - `correct_option` — 0-based index into `options`.
  - `why_required` — `true`. Student justifies in 1–2 sentences.
  - `confidence_required` — `true`. Student rates Sure / Maybe / Guess.
  - `expected_reasoning` — keyword list the Why text should hit.
  - `correct_feedback` — senior-expert voice affirming the reasoning.
  - `partial_feedback` — right action, names the weak link (e.g. a missed step,
    units, a skipped factor).
  - `wrong_feedback` — MUST open with **"Hali emas"** (never "Noto'g'ri"); re-aim
    with a guiding question, not the answer.
- `red_herring` — for G7+ include ONE irrelevant datum in the context the student
  must dismiss, named here. Lower grades: `null`.
- `final_summary` — what an expert would have done, what strong reasoning looks
  like (concept/rule/formula applied, not numbers plugged blindly), likely misses.

## Non-negotiables

- **Strip Test:** remove the lesson concept and the scenario must STOP working.
  If everyday intuition answers it, regenerate.
- The student is the expert: first person, named role, specific decisions — never
  generic "What do you think?" role-play.
- Prediction checkpoint, Why justification, and confidence rating fire on every
  decision. None optional.
- Distractors are genuine {{SUBJECT}} misconceptions, not filler.
- **No within-scenario branching** — same decision sequence for all students.
- Feedback is in-character senior-expert voice, not a rubric read aloud.

## Visuals

If a decision needs a diagram, embed an inline SVG inside the relevant `context`
or `question` text, following the universal SVG rules injected by the runtime (do
not specify size or colors here). Add it only when the diagram carries the decision.

## Language

All student-facing text in natural, formal Uzbek ("Siz", never "sen"). Preserve
every formula, number, unit, and symbol exactly.
