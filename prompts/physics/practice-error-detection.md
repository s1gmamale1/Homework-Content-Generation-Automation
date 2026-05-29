# Prompt: Error Detection — spot the broken physics step, type the fix

You are generating an **Error Detection** task for this lesson — a worked physics
derivation/calculation (or a labelled setup) with **exactly one** error. The
student finds the broken block, then **types the correction themselves**. The
system does NOT auto-reveal; producing the fix is the load-bearing cognitive event.

Default to `pattern: "math_equation"` for derivations and numeric calculations.
Use `pattern: "science_diagram"` only when the task is about labelling a setup
(forces on a body, a circuit, an optics ray diagram).

## What to produce

One Error Detection task, emitted in the structured form the response schema
requests. Fill every field:

- `task_id` — short stable slug (e.g. `err_phys_g9_kinematics_001`); optional.
- `pattern` — `"math_equation"` or `"science_diagram"` (see above).
- `concept_ids` — the lesson concept(s). Use the **source concept IDs from the
  lesson's source map when provided**; otherwise short kebab-case slugs. >=1.
- `grade_band` / `difficulty` — e.g. `"G9-11"`, `"medium"`; set them.
- `blocks` — **3 or more**. For `math_equation`: steps of one calculation (5–6
  for G9–11). For `science_diagram`: the labels (6–8 for G9–11), with the diagram
  embedded as inline SVG (see Visuals). Each block: `id`, `content`, `is_error`.
  **EXACTLY ONE block has `is_error: true`**; surrounding steps/labels stay
  correct so the slip is subtle.
- `correct_answer_for_error_block` — correct version of the broken block only.
- `accepted_variants` — formatting variants that must pass (spacing, unit forms
  like `m/s` vs `m s^-1`, `9.8` vs `9,8`) so a right answer is never rejected on
  form. Keep the **unit** required where physics demands it.
- `common_mistake_source` — the real student mistake (e.g. "forgot to convert km
  to m", "used g = 10 then mixed with 9.8", "dropped the unit", "wrong sign on
  acceleration", "mislabelled the normal force as weight").
- `hint` — ONE probing hint at what to re-check; NEVER reveals the corrected block.
- `why_prompt` — **MANDATORY** (non-empty) for both `math_equation` and
  `science_diagram`. e.g. "Asl natija nega noto'g'ri edi?"
- `expected_reasoning_keywords` — terms a sound explanation should reference
  (the law/quantity, the right unit, the corrected value).
- `correct_feedback` — affirms they spotted and fixed it themselves.
- `wrong_correction_feedback` — **encouraging**, offers the hint. NOT "Noto'g'ri".
- `reveal_feedback` — shown only after the second wrong attempt: corrected block
  plus the one-line reason.

## Non-negotiables

- **Exactly one error.** Any other count is rejected by the validator.
- **Real mistake, not nonsense.** Use genuine physics slips (unit-conversion
  miss, dropped unit, sign error, wrong formula variant, mislabelled force), not
  absurd values nobody would write.
- **No auto-reveal.** No correct answer in the hint or any pre-reveal feedback.
- **Strip Test must pass:** without the physics, the task is just "tap a block,
  type the fix" — no answer leakage.
- Test a concept already shown in correct form earlier in the session.

## Visuals

For `science_diagram` (and any derivation that needs a figure), embed an inline
SVG — in the relevant block's `content`, or a leading non-error block — following
the universal SVG rules injected by the runtime. Do NOT specify size or colours
here. The labels are the blocks; exactly one label is wrong.

## Language

Student-facing text (`hint`, `why_prompt`, feedback) in natural, formal Uzbek
("Siz"). Preserve every formula, number, symbol, and unit exactly inside
`blocks`, `correct_answer_for_error_block`, and `accepted_variants`.
