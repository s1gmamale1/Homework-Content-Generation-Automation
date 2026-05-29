# Prompt: Error Detection — spot the broken chemistry step, type the fix

You are generating an **Error Detection** task for this lesson — a worked
chemistry step (a balancing/calculation) or a labelled apparatus/structure, with
**exactly one** error. The student finds the broken block, then **types the
correction themselves**. The system does NOT auto-reveal; producing the fix is
the load-bearing cognitive event.

Use `pattern: "math_equation"` for equation balancing and mole/mass
calculations. Use `pattern: "science_diagram"` for labelling apparatus or a
molecular/structure figure.

## What to produce

One Error Detection task, emitted in the structured form the response schema
requests. Fill every field:

- `task_id` — short stable slug (e.g. `err_chem_g8_balancing_001`); optional.
- `pattern` — `"math_equation"` or `"science_diagram"` (see above).
- `concept_ids` — the lesson concept(s). Use the **source concept IDs from the
  lesson's source map when provided**; otherwise short kebab-case slugs. >=1.
- `grade_band` / `difficulty` — e.g. `"G9-11"`, `"medium"`; set them.
- `blocks` — **3 or more**. For `math_equation`: steps of one balancing or
  calculation (5–6 for G9–11). For `science_diagram`: the labels (6–8 for G9–11)
  with the diagram embedded as inline SVG (see Visuals). Each block: `id`,
  `content`, `is_error`. **EXACTLY ONE block has `is_error: true`**; surrounding
  blocks stay correct so the slip is subtle.
- `correct_answer_for_error_block` — correct version of the broken block only.
- `accepted_variants` — formatting variants that must pass (spacing, `H2O` vs
  `H₂O`, coefficient spacing, `->` vs `→`) so a right answer is never rejected on
  form. Preserve the chemical meaning exactly.
- `common_mistake_source` — the real student mistake (e.g. "coefficient chosen so
  the equation isn't balanced", "changed a subscript instead of a coefficient",
  "wrong molar mass used", "forgot a diatomic element"). For diagrams, a real
  apparatus/structure mislabel.
- `hint` — ONE probing hint at what to re-check; NEVER reveals the corrected block.
- `why_prompt` — **MANDATORY** (non-empty) for both `math_equation` and
  `science_diagram`. e.g. "Asl tenglama/yorliq nega noto'g'ri edi?"
- `expected_reasoning_keywords` — terms a sound explanation should reference
  (conservation of mass, the right coefficient, the correct structure/role).
- `correct_feedback` — affirms they spotted and fixed it themselves.
- `wrong_correction_feedback` — **encouraging**, offers the hint. NOT "Noto'g'ri".
- `reveal_feedback` — shown only after the second wrong attempt: corrected block
  plus the one-line reason.

## Non-negotiables

- **Exactly one error.** Any other count is rejected by the validator.
- **Real mistake, not nonsense.** Use genuine chemistry slips (unbalanced
  coefficient, subscript-vs-coefficient confusion, wrong molar mass, missing
  diatomic, mislabelled apparatus), not absurd content nobody would write.
- **No auto-reveal.** No correct answer in the hint or any pre-reveal feedback.
- **Strip Test must pass:** without the chemistry, the task is just "tap a block,
  type the fix" — no answer leakage.
- Test a concept already shown in correct form earlier in the session.

## Visuals

For `science_diagram` (apparatus, molecular structure), embed an inline SVG — in
the relevant block's `content`, or a leading non-error block — following the
universal SVG rules injected by the runtime. Do NOT specify size or colours here.
The labels are the blocks; exactly one label is wrong.

## Language

Student-facing text (`hint`, `why_prompt`, feedback) in natural, formal Uzbek
("Siz"). Preserve every formula, subscript, coefficient, and unit exactly inside
`blocks`, `correct_answer_for_error_block`, and `accepted_variants`.
