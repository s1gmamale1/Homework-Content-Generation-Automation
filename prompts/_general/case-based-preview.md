# Prompt: Case-Based Preview — {{SUBJECT}}

You are building a **Case-Based Preview** (CBP) for a {{SUBJECT}} homework session: a
short guided learning case that turns this textbook section into a student-facing
decision situation. The student is a decision-maker — solver, observer, advisor,
writer, or analyst appropriate to {{SUBJECT}} — never a passive reader. Derive the
specific role from this lesson's content.

**Stakes:** low-to-medium. This is meaning-building, not final mastery.

## Textbook authority (NON-NEGOTIABLE)

NETS does not replace the textbook. Do NOT invent textbook facts, formulas,
definitions, dates, or lesson claims. The case may be fictional, but the lesson
concept must stay source-aligned: never change a formula, rule, fact, process,
chronology, or answer logic. If a common mistake is not stated in the textbook,
present it as an inferred typical error, not as a textbook claim.

A "dragon needs algebra to open a gate" case is forbidden — strip the fantasy out
and the {{SUBJECT}} concept must still be the load-bearing reason the decision
succeeds or fails. Prefer adapting a real-life example or diagram the textbook
already gives; create a plausible case only when none exists.

## CBP canonical structure (NON-NEGOTIABLE)

Emit these sections in exactly this order:

```
1. Case setup          — student role, narrative, clear task. The narrative states
                         SYMPTOMS, not the diagnosis: describe what the student
                         observes (events, tensions, facts on the ground) WITHOUT
                         naming the underlying cause, concept, or method that the
                         checkpoints will ask them to identify.
2. Checkpoint 1        — Identify: which concept/structure/rule is involved?
3. Learning Block 1    — short, textbook-grounded explanation of the concept just identified
4. Checkpoint 2        — Decide: which method/factor/action drives the outcome?
5. Learning Block 2    — short explanation showing the method/relationship to apply
6. Checkpoint 3        — Justify or Avoid Mistake: explain why correct works or why the common mistake fails
7. Decision Process Explanation (DPE) — after Checkpoint 3, BEFORE the final simulation
8. Final simulation    — correct path + wrong path + why wrong fails
9. Feedback summary
10. Completion rules
```

## Checkpoint rules

- **Exactly 3** checkpoints, in order: Identify → Decide → Justify-or-avoid-mistake.
- Each is a multiple-choice or simple-choice recognition question — low-friction
  recognition only. Deep reasoning belongs in the DPE, not here.
- Each checkpoint states its question, its answer choices, the correct choice, and
  feedback that explains why that choice fits the case.
- Each checkpoint has **3–4 options, exactly one correct**; **at least one distractor
  must be the lesson's common mistake**. Keep all options similar in length and format
  so the answer cannot be guessed from shape (anti-leak).

## Grade-band reasoning load

Scale the case to the lesson's grade (the pipeline supplies it in context — there is
no grade template variable, so read it from the surrounding material):
- **Lower grades** — one concrete, familiar context; obvious distractors; a short,
  guided DPE.
- **Upper grades** — layered context; subtle distractors that require *applying* the
  rule, not keyword-spotting; a fuller DPE that explicitly weighs the rejected option.

Difficulty scales the **reasoning load only** — never the numbers, formulas, dates, or
source facts, which stay exactly as the textbook states them at every grade.

## Learning Blocks (sections 3 & 5)

Two short teaching moments — call them Learning Block 1 and Learning Block 2.
- **Learning Block 1** (after Checkpoint 1): a 1–3 sentence explanation of the
  concept the student just identified, grounded in the textbook.
- **Learning Block 2** (after Checkpoint 2): a 1–3 sentence explanation that shows
  the method or relationship to apply.
- Keep them text-first and short. Describe a visual as a placeholder (see Output
  format) only if a diagram is essential and not already shown in the case — never
  emit `<svg>`; a brief `[Diagram: …]` note is preferred. This protects the budget.
- Do NOT name the governing method/formula in Learning Block 1 if Checkpoint 2 still
  expects the student to commit to it first.

## Decision Process Explanation (section 7 — non-negotiable)

After Checkpoint 3 and **before** the final simulation, present ONE open-ended
reasoning prompt — never a fourth multiple-choice question, never any answer choices.
The student writes 2–4 sentences answering all three of:

1. Which {{SUBJECT}} concept/structure did you spot in the situation?
2. Why did you pick this method over the alternatives?
3. What wrong interpretation would the common mistake have caused?

**Expected components:** concept · method · mistake. Score the answer **Full** (all
three present), **Partial** (one or two present), or **Retry** (none present) — partial
credit is allowed.

**Required closing line (non-negotiable):** the DPE section MUST end with one
explicit evaluation note, written in the output language, stating that this answer
is NOT auto-passed — it is evaluated by reading the student's reasoning for the
concept, the method, and the mistake. Omitting this note violates the contract.
Placing the DPE AFTER the consequence is forbidden (the student would rationalise
backwards).

## Final simulation rules

Show both paths so the consequence reveals the {{SUBJECT}} content, not just a verdict:
- **Correct path** — walk through the successful outcome when the student's decision is applied.
- **Wrong path** — show what the common wrong choice produces instead.
- **Why the wrong path fails** — one required sentence on why it cannot be correct.

## Feedback summary

Close with a feedback section in exactly four parts:
1. **What the student understood** — the concept(s) they handled correctly.
2. **What mistake appeared** — the error seen across checkpoints and the DPE (if any).
3. **What to review** — the specific textbook point to revisit.
4. **Completion status** — `passed` or `Needs Retry` (never a bare "Not Completed").

## Source concept rule

Every checkpoint, learning block, and the simulation must trace to a real concept in
THIS lesson. Do not invent concepts. Name the case type and student role you derived,
and note which textbook concept the case is built on.

## Visual & case framing (family-specific)

{{FAMILY_RULES}}

## Language

{{LANGUAGE_RULES}}

## Output format

Respond in **Markdown only** (no JSON, no code-fenced JSON). Use `#` for the phase
title and `##`/`###` for the sections/items described above, in order. For visuals: do NOT emit `<svg>` or any image/HTML markup. For ANY visual (diagram
OR photo), emit a described placeholder instead — never the visual itself:
`![visual: <diagram|photo> — <what to depict, with every label, value, and axis> — image gen required](placeholder)`
The description must be self-sufficient: name the medium and every label/value/axis
so the visual can be produced from the text alone. Never output raw `<svg>`, never
fabricate an image, never invent an image URL.

**Setup-visual no-spoiler rule (overrides label-completeness for the setup visual
only):** the case setup's visual placeholder is PART of the setup — self-check #6
applies to its caption. Describe the scene only: place, actors, time, atmosphere.
Never depict or name the cause→effect chain, the concept, the method, or any answer
a checkpoint tests. If describing the visual completely would reveal a checkpoint
answer, depict less instead.

## Self-check

1. ✓ Exactly 3 checkpoints, in order Identify → Decide → Justify/Avoid-mistake?
2. ✓ Both Learning Block 1 and Learning Block 2 present and short?
3. ✓ DPE is open-ended with no answer choices?
4. ✓ DPE placed after Checkpoint 3 and before the final simulation?
5. ✓ Final simulation shows correct path, wrong path, and why wrong fails?
6. ✓ {{SUBJECT}} concept/method NOT named in the setup or checkpoints — including
   the setup visual's caption (it must not depict the tested cause→effect or concept)?
7. ✓ Every concept traces to this lesson; no invented textbook facts?
8. ✓ Visuals follow the family policy above (right medium, no fabricated URLs)?
9. ✓ DPE ends with the required not-auto-passed evaluation note?
10. ✓ Each checkpoint has 3–4 same-shape options, exactly one correct, with ≥1 distractor being the common mistake (anti-leak)?
11. ✓ DPE names its expected components (concept · method · mistake) and a Full/Partial/Retry score?
12. ✓ Feedback summary has all four parts and a `passed`/`Needs Retry` status (never bare "Not Completed")?
