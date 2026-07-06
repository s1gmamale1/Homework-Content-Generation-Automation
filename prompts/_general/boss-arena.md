# Prompt: Boss Arena — Why → How → What reasoning boss — {{SUBJECT}}

You are generating the **Boss Arena** for this {{SUBJECT}} lesson — the mastery
peak of the session. It is a set of high-stakes **reasoning** questions, NOT a
quiz with HP painted on it. Every question forces the student to explain **Why**
a concept applies, **How** to use it, and **What** the result means.

## Adapt to the student's weak spots

Boss Arena is the moment where earlier mistakes get repaired. It does NOT hit
random topics — it **hunts the concepts the student struggled with earlier in
the session**: the ideas they got wrong, the ones they needed hints for, the
skills flagged as weak in earlier phases (flashcards, memory check, practice).
Aim each Boss question at one of those weak spots so that answering it well is a
real repair of an earlier stumble. Stay grounded in this session's lesson
content — never test something the student didn't practice today.

## What to produce

A set of **4–6 questions**, mixing difficulty tiers, each grounded ONLY in this
session's lesson content and aimed at a weak spot from earlier in the session.
You may not know the exact grade — as a soft steer, lean toward **4** questions
for early grades and **6** for senior grades, staying within the 4–6 range.
Write each question as a Markdown section with these parts:

- **Scenario** — a short, concrete, self-contained situation that sets up the
  problem (a real or plausible context for this lesson's concept).
- **The three-part question** — Why, How, and What, each spelled out:
  - **Why** — why does the relevant concept/rule apply here? (conceptual understanding)
  - **How** — how do you use it to reach the answer? (process / application)
  - **What** — what does the result mean, or what follows from it — including the
    counterfactual, *what would change if* a key condition were different? (interpretation / transfer)
- **Concepts tested** — the lesson concept(s) this question checks; name each as a
  short concept slug. At least one per question, and prefer a concept the student
  was weak on earlier.
- **Difficulty** — `easy`, `medium`, or `hard`. Aim for a mix (e.g. for 5
  questions: 2 easy, 2 medium, 1 hard). Easy questions ask the student to apply,
  medium to analyze, hard to evaluate or create — harder questions are worth more
  and demand deeper reasoning.
- **Hints** — up to 3 **probing** hints that nudge the student toward what they're
  missing. Each hint asks a *smaller question*; it must NEVER state the answer
  or a fill-in-the-blank skeleton of it. Hint 1 probes the **Why**, Hint 2 the
  **How**, Hint 3 pushes toward synthesis — still without giving the answer.
- **Feedback lines** — three of them:
  - **Correct** — affirms strong reasoning (names what they did well).
  - **Partial** — names which part of the chain was weak and points back to it.
  - **Wrong** — opens with a gentle "not yet" opener in the OUTPUT LANGUAGE
    (Uzbek «Hali emas», Russian «Пока нет», English «Not yet»); never a flat
    "wrong" (Uzbek "Noto'g'ri", Russian «Неправильно»). Re-point the student with
    a guiding question, not the answer.

## The Why → How → What rule (non-negotiable)

Every Boss question MUST include **all three** parts — a Why, a How, and a What.
Not one. Not two. **Three.** Skipping any one of them means it is not a Boss
question:

- A question that only asks "what is the answer" is a quiz question.
- A question that asks "why does it matter" without "how do you do it" is a
  discussion question.
- A question that asks how to compute but never what the result means stops short
  of mastery.

The full Why → How → What chain must fire on **every** question. If a question is
missing any of the three parts, rewrite it or drop it.

**Worked example of the phrasing** (Pythagorean theorem): *A ladder leans against a
wall; its base is 5 m out and the ladder is 13 m long.* — **Why** does the Pythagorean
theorem apply to this situation? **How** do you set up the equation to find the height?
**What** does the answer mean for whether the ladder is safe — and what would change if
the base were moved farther out?

## What NOT to do

- No multiple-choice / pick-an-option questions — Boss Arena is open reasoning.
- No pure-recall questions answerable from memory with no reasoning (that is a
  Memory Check item, not a Boss question).
- No questions outside this session's lesson content.
- No question that skips any part of the Why → How → What chain.
- No hint that reveals the answer or its skeleton.
- Do not invent facts, formulas, dates, or claims the lesson does not support.
- For math/geometry questions, re-check the actual answer logic before writing
  the scenario, hints, and feedback: verify algebraic identities by expansion or
  substitution; cancel only common multiplicative factors; preserve original
  domain restrictions for rational expressions; do not reverse theorem
  implications; remember that a square inherits both rectangle and rhombus
  properties; and do not reject `(n-2)*180°` for a simple concave polygon.

## Visuals

If a question genuinely needs a diagram (a geometry figure, force diagram, cell
structure, timeline, etc.), describe it as a placeholder (see Output format) in the
scenario — never emit `<svg>`. Add one only when it carries the question; default to none.

## Output format

Respond in **Markdown only** (no JSON, no code-fenced JSON). Use `#` for the phase
title and `##`/`###` for the sections/items described above, in order. For visuals: do NOT emit `<svg>` or any image/HTML markup. For ANY visual (diagram
OR photo), emit a described placeholder instead — never the visual itself:
`![visual: <diagram|photo> — <what to depict, with every label, value, and axis> — image gen required](placeholder)`
The description must be self-sufficient: name the medium and every label/value/axis
so the visual can be produced from the text alone. Never output raw `<svg>`, never
fabricate an image, never invent an image URL.

## Language

{{LANGUAGE_RULES}}
