# Prompt: Error Detection — spot the broken block, type the correction — {{SUBJECT}}

You are generating an **Error Detection** task for this {{SUBJECT}} lesson — a set
of blocks (a labelled diagram, a worked equation, a sentence, a sequence of
steps) in which **exactly one** block is wrong. The student finds the broken
block, then **types the correction themselves**. The system does NOT auto-reveal;
producing the fix is the load-bearing cognitive event.

Choose the `pattern` that fits this {{SUBJECT}} lesson — exactly one of the
literals `math_equation`, `grammar_sentence`, or `science_diagram`. Derive which
one applies, and what the blocks are, from the lesson's `lesson_context` and
source map. For `science_diagram` the blocks are the **labels** of the diagram;
for `math_equation` they are the lines/steps of a worked solution; for
`grammar_sentence` they are the words/phrases of a sentence.

## What to produce

One Error Detection task. Fill every field:

- `task_id` — short stable slug (e.g. `err_topic_g8_001`); optional.
- `pattern` — exactly one of `math_equation`, `grammar_sentence`, `science_diagram`.
- `concept_ids` — the lesson concept(s). Use the **source concept IDs from the
  lesson's source map when provided**; otherwise short kebab-case slugs. >=1.
- `grade_band` / `difficulty` — e.g. `"G5-8"`, `"medium"`; set them.
- `blocks` — **3 or more** blocks (4 for G1–4, 5–6 for G5–8, 6–8 for G9–11).
  Each block: `id`, `content` (the block text), `is_error`.
  **EXACTLY ONE block has `is_error: true`** — that block names the wrong thing
  or applies the wrong step/role for the part it represents. The other blocks
  must be correct so the slip is subtle, not glaring.
- `correct_answer_for_error_block` — the correct content / the real role of that block.
- `accepted_variants` — accepted phrasings so a substantively-right answer is
  never rejected on wording.
- `common_mistake_source` — the real student mix-up this error encodes.
- `hint` — ONE probing hint. It must NEVER reveal the corrected block.
- `why_prompt` — **MANDATORY** (non-empty) for `math_equation` and
  `science_diagram` patterns; optional for `grammar_sentence`. e.g.
  "Bu blok nega noto'g'ri edi?"
- `expected_reasoning_keywords` — terms a sound explanation should reference
  (the correct content, its real role, where the wrong step/process actually applies).
- `correct_feedback` — affirms they spotted and fixed it themselves.
- `wrong_correction_feedback` — **encouraging**, offers the hint. NOT "Noto'g'ri".
- `reveal_feedback` — shown only after the second wrong attempt: the correct block
  plus the one-line reason.

## Non-negotiables

- **Exactly one error.** Any other count is rejected by the validator.
- **Real mistake, not nonsense.** Break the block with a genuine, common confusion
  a {{SUBJECT}} student actually makes, not an absurd thing every student spots
  instantly.
- **No auto-reveal.** No correct content in the hint or any pre-reveal feedback.
- **Strip Test must pass:** without the {{SUBJECT}} content, the task is just "tap
  a block, type the fix" — no answer leakage.
- Test something already shown in correct form earlier in the session.

## Visuals

If the chosen `pattern` is `science_diagram` (or any block needs a figure), embed
the diagram as **inline `<svg>`** — either in the relevant block's text or in a
leading non-error block — following the universal SVG rules injected by the
runtime. Do NOT specify size or colours here. Exactly one block is wrong.

## Output format

Respond in **Markdown only** (no JSON, no code-fenced JSON). Use `#` for the phase
title and `##`/`###` for the sections/items described above, in order. For visuals:
emit inline `<svg>` for diagrams; for a photo/raster you would otherwise need to
generate, emit `![placeholder: <short description> — image gen required](placeholder)`
— never fabricate an image and never invent an image URL.

## Language

{{LANGUAGE_RULES}}
