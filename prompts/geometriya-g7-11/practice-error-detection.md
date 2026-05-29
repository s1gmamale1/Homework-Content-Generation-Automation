# Prompt: Error Detection — spot the broken geometry step, type the fix

You are generating an **Error Detection** task for this lesson — a worked
geometry derivation or proof with **exactly one** broken step. The student finds
the broken block, then **types the correction themselves**. The system does NOT
auto-reveal; producing the fix is the load-bearing cognitive event.

Use `pattern: "math_equation"`. The blocks are **steps of one derivation or
short proof** (e.g. applying the Pythagorean theorem, an angle-sum, a similarity
ratio, an area/volume formula).

## What to produce

One Error Detection task, emitted in the structured form the response schema
requests. Fill every field:

- `task_id` — short stable slug (e.g. `err_geo_g8_pythagor_001`); optional.
- `pattern` — exactly `"math_equation"`.
- `concept_ids` — the lesson concept(s). Use the **source concept IDs from the
  lesson's source map when provided**; otherwise short kebab-case slugs. >=1.
- `grade_band` / `difficulty` — e.g. `"G9-11"`, `"medium"`; set them.
- `blocks` — **3 or more** steps of one derivation/proof (5–6 for G9–11). Each
  block: `id`, `content` (one step: a formula instance, substitution, or proof
  line), `is_error`. **EXACTLY ONE block has `is_error: true`**, and the
  surrounding steps stay correct so the slip is subtle.
- `correct_answer_for_error_block` — the correct version of the broken step only.
- `accepted_variants` — formatting variants that must pass (spacing, equivalent
  ordering, `√` vs `sqrt`, etc.) so a right answer is never rejected on form.
- `common_mistake_source` — the real student mistake the broken step reflects
  (e.g. "added a^2 + b^2 wrongly", "used diameter instead of radius", "forgot to
  halve in the triangle-area formula", "mixed up the angle-sum value").
- `hint` — ONE probing hint at what to re-check; NEVER reveals the corrected step.
- `why_prompt` — **MANDATORY** (non-empty) for `math_equation`. e.g.
  "Bu qadam nega noto'g'ri edi?"
- `expected_reasoning_keywords` — terms a sound explanation should reference
  (the theorem name, the right quantity, the corrected value).
- `correct_feedback` — affirms they spotted and fixed it themselves.
- `wrong_correction_feedback` — **encouraging**, offers the hint. NOT "Noto'g'ri".
- `reveal_feedback` — shown only after the second wrong attempt: corrected step
  plus the one-line reason.

## Non-negotiables

- **Exactly one error.** Any other count is rejected by the validator.
- **Real mistake, not nonsense.** Use genuine geometry slips (wrong formula
  variant, radius/diameter confusion, dropped factor, miscomputed angle sum),
  not absurd values nobody would write.
- **No auto-reveal.** No correct answer in the hint or any pre-reveal feedback.
- **Strip Test must pass:** without the geometry, the task is just "tap a block,
  type the fix" — no answer leakage.
- Test a concept already shown in correct form earlier in the session.

## Visuals

If the derivation needs a figure (triangle, circle, labelled setup), embed an
inline SVG inside the relevant block's `content`, following the universal SVG
rules injected by the runtime — do NOT specify size or colours here.

## Language

Student-facing text (`hint`, `why_prompt`, feedback) in natural, formal Uzbek
("Siz"). Preserve every formula, number, symbol, and unit exactly inside
`blocks`, `correct_answer_for_error_block`, and `accepted_variants`.
