# Prompt: Boss Arena — Why → How → What reasoning boss

You are generating the **Boss Arena** for this lesson — the mastery peak of the
session. It is a set of high-stakes **reasoning** questions, NOT a quiz with HP
painted on it. Every question forces the student to explain **Why** a concept
applies, **How** to use it, and **What** the result means.

## What to produce

A set of **4–6 questions**, mixing difficulty tiers, each grounded ONLY in this
session's lesson content. Emit them in the structured form the response schema
requests. For each question:

- `scenario` — a short, concrete, self-contained situation that sets up the
  problem (a real or plausible context for this lesson's concept).
- `why` — why does the relevant concept/rule apply here? (conceptual understanding)
- `how` — how do you use it to reach the answer? (process / application)
- `what` — what does the result mean, or what follows from it? (interpretation / transfer)
- `concept_ids` — the lesson concept(s) this question tests. Use the **source
  concept IDs from the lesson's source map when they are provided**; otherwise
  use short kebab-case slugs naming the concept. At least one per question.
- `difficulty` — `easy`, `medium`, or `hard`. Aim for a mix (e.g. for 5
  questions: 2 easy, 2 medium, 1 hard).
- `base_damage` — easy `10`, medium `20`, hard `30`.
- `bloom_level` / `pisa_level` — the cognitive level (e.g. `"L4"`); optional.
- `hints` — up to 3 **probing** hints that nudge the student toward what they're
  missing. Each hint asks a *smaller question*; it must NEVER state the answer
  or a fill-in-the-blank skeleton of it. Hint 1 probes the **Why**, Hint 2 the
  **How**, Hint 3 pushes toward synthesis — still without giving the answer.
- `correct_feedback` — affirms strong reasoning (names what they did well).
- `partial_feedback` — names which part of the chain was weak and points back to it.
- `wrong_feedback` — opens with **"Hali emas"** (never "Noto'g'ri") and
  re-points the student with a guiding question, not the answer.

## The Why → How → What rule (non-negotiable)

Every question MUST include all three of `why`, `how`, and `what` — none blank.
A question that only asks "what is the answer" is a quiz question. A question
that asks "why does it matter" without "how do you do it" is a discussion
question. The full chain must fire on every question.

## What NOT to do

- No multiple-choice / pick-an-option questions — Boss Arena is open reasoning.
- No pure-recall questions answerable from memory with no reasoning (that is a
  Memory Check item, not a Boss question).
- No questions outside this session's lesson content.
- No hint that reveals the answer or its skeleton.
- Do not invent facts, formulas, dates, or claims the lesson does not support.

## Visuals

If a question needs a diagram (a geometry figure, force diagram, cell structure,
timeline, etc.), embed an inline SVG inside the `scenario`, following the
universal SVG rules injected by the runtime (do not specify size or colors here).

## Language

Write all student-facing text in natural, formal Uzbek ("Siz") — clear and
student-friendly. Preserve every formula, number, unit, and the source meaning
exactly; do not change them.
