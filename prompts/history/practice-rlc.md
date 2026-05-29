# Prompt: Real-Life Challenge — first-person expert decision game

You are generating the **Real-Life Challenge** for this lesson. The student is
NOT answering questions *about* a scenario — they ARE the expert inside it: an
advisor at a historical decision point, or a historian evaluating sources. They
predict, decide, and justify; the system evaluates whether their reasoning would
hold up if a real historian read it. Build ONE advisor-to-a-historical-decision
or source-evaluation scenario grounded only in this session's history content.
Emit the structured form the response schema requests.

## What to produce

One scenario object with these fields:

- `scenario_id` — short stable slug, e.g. `rlc_hist_<concept>_001` (optional).
- `concept_ids` — the lesson concept(s) tested. Use the **source concept IDs
  from the lesson's source map when provided**; otherwise short kebab-case slugs.
  At least one. Pick concepts the student met earlier this session so
  mistake-repair is possible.
- `role` — a named, specific historical identity ("Siz hukmdorning maslahatchisi
  sifatida xizmat qilyapsiz", "Siz tarixchi sifatida hujjatni baholayapsiz").
  Never "Siz tarixchisiz" without a concrete task.
- `task` — one sentence naming the advice or judgement the student must give.
- `grade_band` / `pisa` — e.g. `"G7-9"` / `"L4"` (optional but set them).
- `context` — 2–4 sentences: the period, the parties, the sources, and the
  constraints in play. Preserve every date, name, place, and source exactly.
- `prediction_prompt` — history is not physical prediction; frame it as "Qaror
  qilishdan oldin, bu qarorning oqibati / bu manba nimani ko'rsatishini
  kutyapsiz?" Mandatory — the student forecasts the consequence or what the
  source will reveal before deciding.
- `decisions` — **2 to 4** decision objects (3 for G7-9). Each has:
  - `question` — the advisory call or source judgement, applying the concept
    (cause/effect, bias, reliability, context).
  - `options` — 3–4 actions/interpretations. The wrong ones are **real historical
    reasoning errors students actually make** (presentism, taking a source at
    face value, single-cause thinking), not nonsense.
  - `correct_option` — 0-based index into `options`.
  - `why_required` — `true`. Student justifies in 1–2 sentences.
  - `confidence_required` — `true`. Student rates Sure / Maybe / Guess.
  - `expected_reasoning` — keyword list the Why text should hit
    (e.g. `["source_bias", "author_intent", "corroboration"]`).
  - `correct_feedback` — senior-historian voice affirming the reasoning.
  - `partial_feedback` — right judgement, names the weak link (e.g. missed the
    author's motive).
  - `wrong_feedback` — MUST open with **"Hali emas"** (never "Noto'g'ri"); re-aim
    with a guiding question, not the answer.
- `red_herring` — for G7+ include ONE irrelevant detail in the context the
  student must dismiss (e.g. a vivid but immaterial fact), named here. Lower
  grades: `null`.
- `final_summary` — what a historian would have concluded, what strong reasoning
  looks like (source/cause reasoned, not asserted), likely misses.

## Non-negotiables

- **Strip Test:** remove the lesson concept and the scenario must STOP working.
  If everyday opinion answers it without the chapter's concept, regenerate.
- The student is the expert: first person, named role, specific decisions — never
  generic "What do you think?" role-play.
- Prediction checkpoint, Why justification, and confidence rating fire on every
  decision. None optional.
- Distractors are genuine historical-reasoning errors, not filler.
- **No within-scenario branching** — same decision sequence for all students.
- Feedback is in-character senior-historian voice, not a rubric read aloud.

## Visuals

Visuals are usually unnecessary for history. Only if a decision genuinely needs
one (a map, a timeline) embed an inline SVG inside the relevant `context` or
`question` text, following the universal SVG rules injected by the runtime (do
not specify size or colors here).

## Language

All student-facing text in natural, formal Uzbek ("Siz", never "sen"). Preserve
every date, name, place, and source meaning exactly.
