# Prompt: Real-Life Challenge — first-person expert decision game — {{SUBJECT}}

You are generating the **Real-Life Challenge** for this {{SUBJECT}} lesson. The
student is NOT answering questions *about* a scenario — they ARE the expert
inside it. They predict, decide, and justify; the system evaluates whether their
reasoning would hold up if a real practitioner read it. Build ONE realistic
decision scenario grounded only in this session's {{SUBJECT}} content, with a
named expert role appropriate to {{SUBJECT}}.

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

Write the scenario as Markdown sections, in this order:

- **Role** — a named, specific expert identity appropriate to this {{SUBJECT}}
  lesson (a practitioner, analyst, technician, or specialist who would really
  make this call). Never a generic "Siz olimsiz" — give a concrete professional
  role.
- **Task** — one sentence naming the decision the student was called in to make.
- **Context** — 2–4 sentences: the situation, the constraints, and the
  information/readings available. Include exact numbers, units, and formulas as
  given when the {{SUBJECT}} lesson involves them. For G7+ include ONE irrelevant
  datum the student must dismiss, and note (to yourself, in the final summary)
  that it is the red herring.
- **Prediction** — a mandatory prompt asking the student what they expect to
  find or happen *before* deciding, e.g. "Hisoblashdan oldin, natija qanday
  chiqishini kutyapsiz?"
- **Decisions** — **2 to 4** decision points (3 for G7–9). Each decision is its
  own `###` subsection containing:
  - The **call** to make, applying the concept/rule/formula.
  - **3–4 options.** The wrong ones are **real {{SUBJECT}} misconceptions
    students actually hold** (drawn from the lesson's typical confusions), not
    nonsense. Mark which option is correct.
  - A mandatory **Why** prompt — the student justifies the call in 1–2 sentences.
    Name the reasoning a sound Why should reach.
  - A mandatory **confidence** prompt — the student rates Sure / Maybe / Guess.
  - **Correct feedback** — senior-expert voice affirming the reasoning.
  - **Partial feedback** — for the right action with a weak link (a missed step,
    units, a skipped factor).
  - **Wrong feedback** — MUST open with **"Hali emas"** (never "Noto'g'ri");
    re-aim with a guiding question, not the answer.
- **Final summary** — what an expert would have done, what strong reasoning looks
  like (concept/rule/formula applied, not numbers plugged blindly), the likely
  misses, and (G7+) which datum was the red herring and why it didn't matter.

## Non-negotiables

- The student is the expert: first person, named role, specific decisions — never
  generic "What do you think?" role-play.
- Prediction checkpoint, Why justification, and confidence rating fire on every
  decision. None optional.
- Distractors are genuine {{SUBJECT}} misconceptions, not filler.
- **No within-scenario branching** — the same decision sequence for all students.
- Feedback is in-character senior-expert voice, not a rubric read aloud.

## Visuals

If a decision needs a diagram, embed an inline `<svg>` in the relevant context or
decision section, following the universal SVG rules injected by the runtime (do
not specify size or colors here). Add it only when the diagram carries the decision.

## Output format

Respond in **Markdown only** (no JSON, no code-fenced JSON). Use `#` for the phase
title and `##`/`###` for the sections/decisions described above, in order. For
visuals: emit inline `<svg>` for diagrams; for a photo/raster you would otherwise
need to generate, emit `![placeholder: <short description> — image gen required](placeholder)`
— never fabricate an image and never invent an image URL.

## Language

{{LANGUAGE_RULES}}
