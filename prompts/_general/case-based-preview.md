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
1. Case setup          — student role, narrative, clear task
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

## Learning Blocks (sections 3 & 5)

Two short teaching moments — call them Learning Block 1 and Learning Block 2.
- **Learning Block 1** (after Checkpoint 1): a 1–3 sentence explanation of the
  concept the student just identified, grounded in the textbook.
- **Learning Block 2** (after Checkpoint 2): a 1–3 sentence explanation that shows
  the method or relationship to apply.
- Keep them text-first and short. Add a small inline `<svg>` only if a diagram is
  essential and not already shown in the case; otherwise a brief `[Diagram: …]` note
  is preferred. This protects the output-token budget.
- Do NOT name the governing method/formula in Learning Block 1 if Checkpoint 2 still
  expects the student to commit to it first.

## Decision Process Explanation (section 7 — non-negotiable)

After Checkpoint 3 and **before** the final simulation, present ONE open-ended
reasoning prompt — never a fourth multiple-choice question, never any answer choices.
The student writes 2–4 sentences answering all three of:

1. Which {{SUBJECT}} concept/structure did you spot in the situation?
2. Why did you pick this method over the alternatives?
3. What wrong interpretation would the common mistake have caused?

State that this is evaluated by reading the student's reasoning for the concept, the
method, and the mistake — it is not auto-passed. Placing it AFTER the consequence is
forbidden (the student would rationalise backwards).

## Final simulation rules

Show both paths so the consequence reveals the {{SUBJECT}} content, not just a verdict:
- **Correct path** — walk through the successful outcome when the student's decision is applied.
- **Wrong path** — show what the common wrong choice produces instead.
- **Why the wrong path fails** — one required sentence on why it cannot be correct.

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
title and `##`/`###` for the sections/items described above, in order. For visuals:
emit inline `<svg>` for diagrams; for a photo/raster you would otherwise need to
generate, emit `![placeholder: <short description> — image gen required](placeholder)`
— never fabricate an image and never invent an image URL.

## Self-check

1. ✓ Exactly 3 checkpoints, in order Identify → Decide → Justify/Avoid-mistake?
2. ✓ Both Learning Block 1 and Learning Block 2 present and short?
3. ✓ DPE is open-ended with no answer choices?
4. ✓ DPE placed after Checkpoint 3 and before the final simulation?
5. ✓ Final simulation shows correct path, wrong path, and why wrong fails?
6. ✓ {{SUBJECT}} concept/method NOT named in the setup or checkpoints?
7. ✓ Every concept traces to this lesson; no invented textbook facts?
8. ✓ Visuals follow the family policy above (right medium, no fabricated URLs)?
