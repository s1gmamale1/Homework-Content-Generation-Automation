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
  that it is the distracting datum (the "red herring") — when you name it in the
  summary, name it in the OUTPUT LANGUAGE (Uzbek "chalg'ituvchi ma'lumot",
  Russian «отвлекающий факт»), never the bare English "red herring".
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
  - **Wrong feedback** — MUST open with a gentle "not yet" opener in the OUTPUT
    LANGUAGE (Uzbek «Hali emas», Russian «Пока нет», English «Not yet») — never a
    flat "wrong" (Uzbek "Noto'g'ri", Russian «Неправильно»); re-aim with a
    guiding question, not the answer.
- **Color the feedback by the confidence + correctness pattern** (this is steering
  the *tone*, not a points rubric): when the student was **Sure but wrong**, the
  Wrong feedback should gently name it as a confident misconception — the case
  most worth correcting, so be direct about the faulty belief, not just the call.
  When the student was **Guess but right**, don't over-praise: treat it as lucky
  recall, name the concept that *should* have driven it, and invite them to redo
  the reasoning so it sticks.
- **Final summary** — what an expert would have done, what strong reasoning looks
  like (concept/rule/formula applied, not numbers plugged blindly), the likely
  misses, and (G7+) which datum was the distracting one (the "red herring", named
  in the output language) and why it didn't matter.

The summary must close the loop back to the named concept. Compact shape (Biology):
the student, as a clinic nurse, sees a patient with bluish lips → reads it as low
blood oxygen → ties that to the cellular-respiration concept from the lesson.
Strong reasoning names that chain; weak reasoning only lists symptoms without
linking to the mechanism the lesson taught.

## Non-negotiables

- The student is the expert: first person, named role, specific decisions — never
  generic "What do you think?" role-play.
- Prediction checkpoint, Why justification, and confidence rating fire on every
  decision. None optional.
- Distractors are genuine {{SUBJECT}} misconceptions, not filler.
- **No within-scenario branching** — the same decision sequence for all students.
- Feedback is in-character senior-expert voice, not a rubric read aloud.
- Decoration is not learning evidence — color, mood, and animation are UI polish,
  never a substitute for the reasoning the decision tests.
- For math/geometry lessons, every decision option, explanation, and feedback
  line must pass a factual sanity check: verify algebraic identities by expansion
  or substitution; cancel only common multiplicative factors; preserve original
  domain restrictions for rational expressions; do not reverse theorem
  implications; remember that a square inherits both rectangle and rhombus
  properties; and do not reject `(n-2)*180°` for a simple concave polygon.

## Visuals

If a decision genuinely needs a diagram, describe it as a placeholder (see Output
format) in the relevant context or decision section — never emit `<svg>`. Add one
only when the diagram carries the decision; default to none.

## Output format

Respond in **Markdown only** (no JSON, no code-fenced JSON). Use `#` for the phase
title and `##`/`###` for the sections/decisions described above, in order. For
visuals: do NOT emit `<svg>` or any image/HTML markup. For ANY visual (diagram OR
photo), emit a described placeholder instead — never the visual itself:
`![visual: <diagram|photo> — <what to depict, with every label, value, and axis> — image gen required](placeholder)`
The description must name the medium and every label/value/axis so the visual can be
produced from the text alone. Never output raw `<svg>`, never fabricate an image,
never invent an image URL.

## Language

{{LANGUAGE_RULES}}
