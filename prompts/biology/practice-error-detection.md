# Prompt: Error Detection — spot the mislabeled structure, type the correction

You are generating an **Error Detection** task for this lesson — a labelled
biology diagram (a cell, an organ, a system, a cycle) in which **exactly one**
label is wrong. The student finds the broken label, then **types the correction
themselves**. The system does NOT auto-reveal; producing the fix is the
load-bearing cognitive event.

Use `pattern: "science_diagram"`. The blocks are the **labels** of the diagram.

## What to produce

One Error Detection task, emitted in the structured form the response schema
requests. Fill every field:

- `task_id` — short stable slug (e.g. `err_bio_g8_plantcell_001`); optional.
- `pattern` — exactly `"science_diagram"`.
- `concept_ids` — the lesson concept(s). Use the **source concept IDs from the
  lesson's source map when provided**; otherwise short kebab-case slugs. >=1.
- `grade_band` / `difficulty` — e.g. `"G5-8"`, `"medium"`; set them.
- `blocks` — **3 or more** labels (4 for G1–4, 5–6 for G5–8, 6–8 for G9–11).
  Each block: `id`, `content` (the label text, e.g. "Mitoxondriya"), `is_error`.
  **EXACTLY ONE block has `is_error: true`** — that label names the wrong
  structure or the wrong function for the part it points to. The other labels
  must be correct so the slip is subtle, not glaring.
- `correct_answer_for_error_block` — the correct label / the real role of that
  structure.
- `accepted_variants` — accepted phrasings (e.g. "ATP / energiya ishlab chiqarish",
  "energiya hosil qilish") so a substantively-right answer is never rejected on
  wording.
- `common_mistake_source` — the real student mix-up (e.g. "mitoxondriyani
  fotosintez bilan adashtirish", "hujayra devori va membranani aralashtirish").
- `hint` — ONE probing hint (e.g. "Hujayraning energiya ishlab chiqaruvchisi
  qaysi organella?"). It must NEVER reveal the corrected label.
- `why_prompt` — **MANDATORY** (non-empty) for `science_diagram`. e.g.
  "Bu yorliq nega noto'g'ri edi?"
- `expected_reasoning_keywords` — terms a sound explanation should reference
  (the correct structure, its real function, where the wrong process happens).
- `correct_feedback` — affirms they spotted and fixed it themselves.
- `wrong_correction_feedback` — **encouraging**, offers the hint. NOT "Noto'g'ri".
- `reveal_feedback` — shown only after the second wrong attempt: the correct label
  plus the one-line reason.

## Non-negotiables

- **Exactly one error.** Any other count is rejected by the validator.
- **Real mistake, not nonsense.** Mislabel with a genuine, common confusion
  (mitochondria↔chloroplast, vein↔artery, cell wall↔membrane), not an absurd
  label every student spots instantly.
- **No auto-reveal.** No correct label in the hint or any pre-reveal feedback.
- **Strip Test must pass:** without the biology, the task is just "tap a label,
  type the fix" — no answer leakage.
- Test a structure already shown in correct form earlier in the session.

## Visuals

Embed the diagram as **inline SVG** — either inside the relevant label block's
`content` or in a leading non-error block — following the universal SVG rules
injected by the runtime. Do NOT specify size or colours here. The labels are the
blocks; exactly one of them is wrong.

## Language

Student-facing text (`hint`, `why_prompt`, feedback, label text) in natural,
formal Uzbek ("Siz"). Preserve every scientific term exactly.
