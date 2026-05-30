# Prompt: Case-Based Preview — Math + Algebra

You are building a **Case-Based Preview** (CBP) for a Math/Algebra homework session. The student encounters a realistic problem scenario and makes decisions before seeing the answer. This phase teaches through a story — not by explaining a rule first.

## CBP canonical structure (NON-NEGOTIABLE)

Your output must have EXACTLY this shape (3 checkpoints with a Learning Block after the first two, then the DPE):

```
1. Case setup          — student role, narrative, task
2. Checkpoint 1        — Identify (MCQ/choice): which operation/concept applies?
3. Learning Block 1    — short, textbook-grounded explanation of the concept just identified
4. Checkpoint 2        — Decide (MCQ/choice): which formula/method to use?
5. Learning Block 2    — short explanation showing the method/relationship to apply
6. Checkpoint 3        — Justify or Avoid Mistake (MCQ/choice): why is the wrong path wrong?
7. Decision Process Explanation (DPE) — slot 7, OPEN-ENDED, before consequence
8. Final simulation    — correct path + wrong path + why wrong path fails
9. Feedback summary
10. Completion rules
```

## Checkpoint rules

- **Exactly 3 checkpoints** — no more, no fewer.
- Forms allowed: `mcq`, `choice`, `true_false`, `short_select`
- Checkpoints are RECOGNITION only (low friction). Production reasoning belongs ONLY in the DPE.
- Intents must be: `identify` → `decide` → `justify_or_avoid_mistake` (in that order).

## Learning Blocks (slots 3 & 5)

Two short teaching moments, emitted as `learning_block_1` and `learning_block_2`.
- **learning_block_1** (after Checkpoint 1): a 1–3 sentence explanation of the concept the student just identified, grounded in the textbook. Set `source_concept_id` to the SourceMap concept it teaches.
- **learning_block_2** (after Checkpoint 2): a 1–3 sentence explanation that shows the method/relationship to apply. Set `source_concept_id`.
- Keep them **text-first and short**. Use `visual_svg` ONLY if a tiny diagram is essential AND not already shown in the case — otherwise omit it (a `[Diagram: ...]` note in the text is preferred). This protects the output-token budget.
- Do NOT name the method in `learning_block_1` if the case still expects the student to commit at Checkpoint 2 first.

## Decision Process Explanation (DPE) — slot 7

- Placed AFTER checkpoint 3, BEFORE the final simulation.
- **OPEN-ENDED.** The `options` field is always `null`. NEVER add options.
- Must ask the student to address ALL THREE components:
  1. Which concept did you spot in this situation?
  2. Why did you pick this method over alternatives?
  3. What mistake would have happened with the wrong choice?
- `expected_components` = `["concept", "method", "mistake"]`
- `min_chars` = 60
- `eval_mode` = `"ai"`
- `sample_acceptable_answer`: write a real example (2–4 sentences)

## Final simulation rules

- `correct_path`: walk through the successful outcome when the student's decision is applied.
- `wrong_path`: show what happens when the common wrong answer is applied instead.
- `why_wrong_fails`: one sentence on why the wrong path cannot be correct (REQUIRED).

## Math case types

Use one of these patterns depending on the section:
- **Practical problem**: buyer / planner / helper role — quantities, prices, areas, distances
- **Formula selection**: engineer / builder — identify which formula, apply it, explain why alternative fails
- **Calculation shortcut**: student / tutor — spot the pattern, choose the efficient path

## Source concept rule

`source_concept_ids` must name at least one concept ID from the lesson (e.g. `["fraction_division"]`). Do NOT invent concepts not in the textbook section.

## Method/formula visibility rule

Do NOT name the method/formula in the case setup or checkpoint prompts. The student commits to it first (in the DPE), then the simulation reveals the correct path.

## SVG rule

For fractions, formulas, geometric shapes, number lines: embed an inline SVG. For real-life narrative scenes: image description in brackets. Never put formulas as images.

## Output format

Return a **JSON object** matching the `CaseBasedPreview` schema exactly:

```json
{
  "title": "...",
  "student_role": "...",
  "case_type": "practical_problem | formula_selection | calculation_shortcut",
  "source_concept_ids": ["..."],
  "case_setup": { "narrative": "...", "student_role": "...", "task": "..." },
  "checkpoints": [
    {
      "intent": "identify",
      "form": "mcq",
      "question": "...",
      "options": ["A", "B", "C", "D"],
      "correct_index": 0,
      "feedback": "..."
    },
    { "intent": "decide", ... },
    { "intent": "justify_or_avoid_mistake", ... }
  ],
  "decision_process_explanation": {
    "prompt": "Walk through your reasoning: (1) Which concept did you spot? (2) Why this method? (3) What mistake was avoided?",
    "expected_components": ["concept", "method", "mistake"],
    "rubric": { "concept": 1, "method": 1, "mistake": 1 },
    "sample_acceptable_answer": "...",
    "eval_mode": "ai",
    "min_chars": 60,
    "options": null
  },
  "final_simulation": {
    "correct_path": "...",
    "wrong_path": "...",
    "why_wrong_fails": "..."
  },
  "feedback_summary": { "understood": "...", "mistake": "...", "review": "..." },
  "completion_rules": {
    "pass_condition": "≥2/3 checkpoints correct + DPE attempted",
    "retry_condition": "Fewer than 2 correct checkpoints — retry the case"
  }
}
```

## Self-check before output

1. ✓ Exactly 3 checkpoints?
2. ✓ DPE has no `options` field (or `options: null`)?
3. ✓ DPE is in slot 7 — after checkpoint 3, before final_simulation?
4. ✓ DPE prompt asks concept + method + mistake?
5. ✓ Method/formula NOT named in case setup or checkpoint prompts?
6. ✓ All `source_concept_ids` traceable to the textbook section?
7. ✓ `final_simulation` has both `correct_path` and `wrong_path`?
