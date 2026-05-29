# Prompt: Real-Life Challenge — first-person expert decision game

You are generating the **Real-Life Challenge** for this lesson. The student is
NOT answering questions *about* a scenario — they ARE the expert inside it (a lab
technician, clinic nurse, field biologist). They predict, decide, and justify;
the system evaluates whether their reasoning would hold up if a real expert read
it. Build ONE clinical / lab / field-health scenario grounded only in this
session's biology content. Emit the structured form the response schema requests.

## What to produce

One scenario object with these fields:

- `scenario_id` — short stable slug, e.g. `rlc_bio_<concept>_001` (optional).
- `concept_ids` — the lesson concept(s) this scenario tests. Use the **source
  concept IDs from the lesson's source map when provided**; otherwise short
  kebab-case slugs. At least one. These must be concepts the student met earlier
  in the session so mistake-repair is possible.
- `role` — a named, specific biology expert identity ("Siz mahalliy klinikada
  hamshira yordamchisisiz", "Siz dala biologisiz"). Never "Siz olimsiz".
- `task` — one sentence naming what the student was called in to do.
- `grade_band` / `pisa` — e.g. `"G7-9"` / `"L4"` (optional but set them).
- `context` — 2–4 sentences: the patient/sample/site, constraints, and the
  information available. Include exact numbers, units, and observations.
- `prediction_prompt` — "Qaror qilishdan oldin, nima topishni / nima yuz
  berishini kutyapsiz?" Mandatory — the student predicts before deciding.
- `decisions` — **2 to 4** decision objects (3 for G7-9). Each has:
  - `question` — the call the expert must make, applying the concept.
  - `options` — 3–4 actions. The wrong ones are **real clinical/biological
    misconceptions students actually hold**, not nonsense (e.g. confusing
    symptom with cause, breathing fast = more oxygen).
  - `correct_option` — 0-based index into `options`.
  - `why_required` — `true`. The student must justify in 1–2 sentences.
  - `confidence_required` — `true`. Student rates Sure / Maybe / Guess.
  - `expected_reasoning` — keyword list the Why text should hit
    (e.g. `["low_oxygen", "cellular_respiration", "cyanosis"]`).
  - `correct_feedback` — senior-expert voice affirming the reasoning.
  - `partial_feedback` — right action, names the weak link in the mechanism.
  - `wrong_feedback` — MUST open with **"Hali emas"** (never "Noto'g'ri"); re-aim
    the student with a guiding question, not the answer.
- `red_herring` — for G7+ include ONE irrelevant detail in the context the
  student must dismiss (e.g. a normal temperature that doesn't bear on the call),
  named here. Lower grades: `null`.
- `final_summary` — what an expert would have done, what strong reasoning looks
  like (concept connected, not just symptoms described), what was likely missed.

## Non-negotiables

- **Strip Test:** remove the lesson concept and the scenario must STOP working.
  If general intuition answers it, regenerate.
- The student is the expert: first person, named role, specific decisions — never
  generic "What do you think?" role-play.
- Prediction checkpoint, Why justification, and confidence rating fire on every
  decision. None optional.
- Distractors are genuine misconceptions, not filler.
- **No within-scenario branching** — the decision sequence is the same for all
  students. Variety lives at scenario selection, not inside.
- Feedback is in-character senior-expert voice, not a grading rubric read aloud.

## Visuals

If a decision needs a diagram (cell structure, gas-exchange path, dilution
series), embed an inline SVG inside the relevant `context` or `question` text,
following the universal SVG rules injected by the runtime (do not specify size or
colors here). Often unnecessary for clinical reasoning — only add if it carries
the decision.

## Language

All student-facing text in natural, formal Uzbek ("Siz", never "sen"). Preserve
every number, unit, name, and measurement exactly.
