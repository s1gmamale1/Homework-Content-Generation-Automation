# Prompt: Error Detection — spot the broken algebra step, type the fix

You are generating an **Error Detection** task for this lesson — a single piece
of worked algebra that contains **exactly one** error. The student finds the
broken block, then **types the correction themselves**. The system does NOT
auto-reveal the answer; producing the fix is the load-bearing cognitive event.

Use `pattern: "math_equation"`. The blocks are **steps of one worked equation**.

## What to produce

One Error Detection task, emitted in the structured form the response schema
requests. Fill every field:

- `task_id` — short stable slug (e.g. `err_math_g7_linear_001`); optional.
- `pattern` — exactly `"math_equation"`.
- `concept_ids` — the lesson concept(s) this tests. Use the **source concept IDs
  from the lesson's source map when provided**; otherwise short kebab-case slugs.
  At least one.
- `grade_band` / `difficulty` — e.g. `"G5-8"`, `"medium"`; optional but set them.
- `blocks` — **3 or more** steps of one solution (4–5 for G5–8, 5–6 for G9–11).
  Each block: `id` (e.g. `"b1"`), `content` (one equation step), `is_error`.
  **EXACTLY ONE block has `is_error: true`.** The steps before and after the
  broken one must be otherwise correct and follow logically, so the slip is
  subtle, not glaring.
- `correct_answer_for_error_block` — the correct version of the broken step only.
- `accepted_variants` — formatting variants that must pass (e.g. `"3x=6"`,
  `"3x = 6"`, `"3 x = 6"`). Cover spacing and obvious equivalent forms so the
  evaluator never rejects a right answer over whitespace.
- `common_mistake_source` — the real, common student slip the broken step
  reflects (e.g. "11 - 5 miscalculated as 16", "sign dropped when moving term").
- `hint` — ONE probing hint that points at what to re-check (e.g. "What is
  11 minus 5?"). It must NEVER reveal the corrected step.
- `why_prompt` — **MANDATORY** (non-empty) for `math_equation`. e.g.
  "Asl qadam nega noto'g'ri edi?"
- `expected_reasoning_keywords` — terms a sound explanation should mention
  (e.g. `["11 - 5", "6", "ayirish"]`).
- `correct_feedback` — affirms they spotted it and fixed it themselves.
- `wrong_correction_feedback` — **encouraging**, offers the hint. Do NOT write
  "Noto'g'ri". e.g. "Hali emas — ko'rsatmani ko'rib chiqamizmi?"
- `reveal_feedback` — shown only after the second wrong attempt: the correct step
  plus the one-line reason.

## Non-negotiables

- **Exactly one error.** The model validator rejects any other count.
- **Real mistake, not nonsense.** The broken step must be a slip a real student
  makes (arithmetic error, sign flip, wrong inverse operation, division slip).
  Forbidden: absurd values like `3x = 999` that everyone spots instantly.
- **No auto-reveal.** Never put the correct answer in the hint or in any
  pre-reveal feedback.
- **Strip Test must pass:** remove the algebra and only "tap a block, type the
  fix" remains — the task carries no answer leakage.
- The concept must be one already seen in correct form earlier in the session;
  do not invent first-exposure content.

## Language

Student-facing text (`hint`, `why_prompt`, all feedback) in natural, formal
Uzbek ("Siz"). Preserve every formula, number, variable, and unit exactly inside
`blocks`, `correct_answer_for_error_block`, and `accepted_variants` — do not
translate or alter math notation.
