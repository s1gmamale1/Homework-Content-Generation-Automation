# Prompt: Real-Life Challenge — first-person expert decision game

You are generating the **Real-Life Challenge** for this lesson. The student is
NOT answering questions *about* a scenario — they ARE the expert inside it (a
structural engineer, a measurement/calibration technician, a safety inspector).
They predict, decide, and justify; the system evaluates whether their reasoning
would hold up if a real engineer read it. Build ONE engineering / measurement /
safety decision scenario grounded only in this session's physics content. Emit
the structured form the response schema requests.

## What to produce

One scenario object with these fields:

- `scenario_id` — short stable slug, e.g. `rlc_phys_<concept>_001` (optional).
- `concept_ids` — the lesson concept(s) tested. Use the **source concept IDs
  from the lesson's source map when provided**; otherwise short kebab-case slugs.
  At least one. Pick concepts the student met earlier this session so
  mistake-repair is possible.
- `role` — a named, specific engineering identity ("Siz ko'prik qurilishida
  konstruktor muhandissiz", "Siz zavod o'lchov laboratoriyasi texnigisiz").
  Never "Siz fizik olimsiz".
- `task` — one sentence naming the decision the student was called in to make.
- `grade_band` / `pisa` — e.g. `"G7-9"` / `"L4"` (optional but set them).
- `context` — 2–4 sentences: the structure/instrument/site, loads, tolerances,
  and the readings available. Include exact numbers, units, and formulas as given.
- `prediction_prompt` — "Hisoblashdan oldin, natija qanday chiqishini kutyapsiz?"
  Mandatory — the student predicts the outcome before deciding.
- `decisions` — **2 to 4** decision objects (3 for G7-9). Each has:
  - `question` — the engineering call, applying the concept/formula.
  - `options` — 3–4 actions. The wrong ones are **real physics misconceptions
    students actually hold** (heavier falls faster, force needed to keep constant
    speed, confusing mass with weight), not nonsense.
  - `correct_option` — 0-based index into `options`.
  - `why_required` — `true`. Student justifies in 1–2 sentences.
  - `confidence_required` — `true`. Student rates Sure / Maybe / Guess.
  - `expected_reasoning` — keyword list the Why text should hit
    (e.g. `["net_force", "newton_second_law", "free_body"]`).
  - `correct_feedback` — senior-engineer voice affirming the reasoning.
  - `partial_feedback` — right action, names the weak link (e.g. units, a missed
    force).
  - `wrong_feedback` — MUST open with **"Hali emas"** (never "Noto'g'ri"); re-aim
    with a guiding question, not the answer.
- `red_herring` — for G7+ include ONE irrelevant datum in the context the student
  must dismiss (e.g. an unrelated dimension or reading), named here. Lower
  grades: `null`.
- `final_summary` — what an expert would have done, what strong reasoning looks
  like (concept/formula applied, not numbers plugged blindly), likely misses.

## Reverse-test variant (required, §6)

Make the FINAL decision a **reverse test** of the same scenario: re-present it
with the key quantities/measurements CHANGED, and ask the student to infer
**which underlying concept or formula governs the new outcome**. The principle's
name must NOT appear in the setup; the `options` are candidate concepts/formulas
and `correct_option` is the one the lesson actually supports. This tests transfer
— recognising the unnamed principle from a fresh instance, not re-running the
forward steps. It counts as one of the 2–4 `decisions`.

## Non-negotiables

- **Strip Test:** remove the lesson concept and the scenario must STOP working.
  If everyday intuition answers it, regenerate.
- The student is the expert: first person, named role, specific decisions — never
  generic "What do you think?" role-play.
- Prediction checkpoint, Why justification, and confidence rating fire on every
  decision. None optional.
- Distractors are genuine physics misconceptions, not filler.
- **No within-scenario branching** — same decision sequence for all students.
- Feedback is in-character senior-engineer voice, not a rubric read aloud.

## Visuals

If a decision needs a diagram (free-body diagram, circuit, ray path, beam under
load), embed an inline SVG inside the relevant `context` or `question` text,
following the universal SVG rules injected by the runtime (do not specify size or
colors here). Add it only when the diagram carries the decision.

## Language

All student-facing text in natural, formal Uzbek ("Siz", never "sen"). Preserve
every formula, number, unit, and symbol exactly.
