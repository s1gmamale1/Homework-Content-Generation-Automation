# Prompt: Case-Based Preview — {{SUBJECT}}

You are building a **Case-Based Preview** (CBP) for a {{SUBJECT}} homework session. The student plays the role of a practitioner appropriate to {{SUBJECT}} who must make an observation-based decision before seeing the consequence. Derive the specific role from this lesson's `lesson_context` and source map.

## CBP canonical structure (NON-NEGOTIABLE)

EXACTLY 3 checkpoints with a Learning Block after the first two, then the DPE — placed after Checkpoint 3 and before the final simulation (this is the canonical CBP "slot 7").

```
1. Case setup          — student role (derive from the {{SUBJECT}} lesson), narrative, task
2. Checkpoint 1        — Identify: which system/structure/concept is involved?
3. Learning Block 1    — short, textbook-grounded explanation of the concept just identified
4. Checkpoint 2        — Decide: which factor/mechanism drives the outcome?
5. Learning Block 2    — short explanation showing the method/relationship to apply
6. Checkpoint 3        — Justify or Avoid Mistake: predict consequence or rule out wrong mechanism
7. Decision Process Explanation (DPE) — after Checkpoint 3, before the final simulation (canonical CBP slot 7); OPEN-ENDED, options = null
8. Final simulation    — correct path + wrong path + why wrong fails
9. Feedback summary
10. Completion rules
```

## Checkpoint rules

- **Exactly 3** — intents: `identify` → `decide` → `justify_or_avoid_mistake`
- Forms: `mcq` or `choice` only
- Low-friction recognition only. Deep reasoning belongs in the DPE.

## Learning Blocks (slots 3 & 5)

Two short teaching moments, emitted as `learning_block_1` and `learning_block_2`.
- **learning_block_1** (after Checkpoint 1): a 1–3 sentence explanation of the concept the student just identified, grounded in the textbook. Set `source_concept_id` to the SourceMap concept it teaches.
- **learning_block_2** (after Checkpoint 2): a 1–3 sentence explanation that shows the method/relationship to apply. Set `source_concept_id`.
- Keep them **text-first and short**. Use `visual_svg` ONLY if a tiny diagram is essential AND not already shown in the case — otherwise omit it (a `[Diagram: ...]` note in the text is preferred). This protects the output-token budget.
- Do NOT name the method in `learning_block_1` if the case still expects the student to commit at Checkpoint 2 first.

## DPE — slot 7 rules (non-negotiable)

- `options: null` — NEVER add answer choices
- `expected_components: ["concept", "method", "mistake"]`
- Prompt must ask: (1) Which {{SUBJECT}} concept/structure? (2) Why this mechanism? (3) What wrong interpretation was avoided?
- `min_chars: 60`, `eval_mode: "ai"`

## Final simulation rules

- `correct_path`: walk through the successful outcome when the student's decision is applied.
- `wrong_path`: show what happens when the common wrong answer is applied instead.
- `why_wrong_fails`: one sentence on why the wrong path cannot be correct (REQUIRED).

## {{SUBJECT}} case types

Derive 2–3 case types from this lesson's `lesson_context` and source map — the kinds of real situations in which a {{SUBJECT}} practitioner identifies what is happening, decides which factor drives the outcome, and rules out a wrong interpretation. Use these to shape `case_type`, the role, and the checkpoints. Do NOT name the governing method/formula in the case body — the student must commit at the checkpoints first.

## SVG rule

Use SVG for genuine diagrams (structures, processes, figures, charts) that carry the decision. Use image descriptions for real-life scenes. Follow the universal SVG rules injected by the runtime.

## Source concept rule

`source_concept_ids` must map to concepts in this lesson. Do not invent. At least one.

## Output format — JSON matching CaseBasedPreview schema

```json
{
  "title": "...",
  "student_role": "...",
  "case_type": "...",
  "source_concept_ids": ["..."],
  "case_setup": { "narrative": "...", "student_role": "...", "task": "..." },
  "checkpoints": [
    { "intent": "identify", "form": "mcq", "question": "...", "options": [...], "correct_index": 0, "feedback": "..." },
    { "intent": "decide", ... },
    { "intent": "justify_or_avoid_mistake", ... }
  ],
  "learning_block_1": { "explanation": "...", "title": "...", "visual_svg": null, "source_concept_id": "..." },
  "learning_block_2": { "explanation": "...", "title": "...", "visual_svg": null, "source_concept_id": "..." },
  "decision_process_explanation": {
    "prompt": "Walk through your reasoning: (1) Which concept did you spot? (2) Why this mechanism over alternatives? (3) What wrong interpretation was avoided?",
    "expected_components": ["concept", "method", "mistake"],
    "rubric": { "concept": 1, "method": 1, "mistake": 1 },
    "sample_acceptable_answer": "...",
    "eval_mode": "ai",
    "min_chars": 60,
    "options": null
  },
  "final_simulation": { "correct_path": "...", "wrong_path": "...", "why_wrong_fails": "..." },
  "feedback_summary": { "understood": "...", "mistake": "...", "review": "..." },
  "completion_rules": {
    "pass_condition": "≥2/3 checkpoints correct + DPE attempted",
    "retry_condition": "Fewer than 2 correct checkpoints — retry the case"
  }
}
```

## Self-check

1. ✓ Exactly 3 checkpoints?
2. ✓ Both `learning_block_1` and `learning_block_2` present?
3. ✓ DPE `options` is null?
4. ✓ DPE after checkpoint 3, before final_simulation?
5. ✓ {{SUBJECT}} concept name NOT given in setup or checkpoints?
6. ✓ `source_concept_ids` traceable to this lesson?
