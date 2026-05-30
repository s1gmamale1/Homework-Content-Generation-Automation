# Prompt: Case-Based Preview — History (O'zbekiston Tarixi + Jahon Tarixi)

You are building a **Case-Based Preview** (CBP) for a History homework session. The student plays an advisor or historian who must evaluate historical evidence and make a decision under the same constraints as the historical actor.

## CBP canonical structure (NON-NEGOTIABLE)

EXACTLY 3 checkpoints with a Learning Block after the first two, then the DPE — placed after Checkpoint 3 and before the final simulation (this is the canonical CBP "slot 7").

```
1. Case setup          — student role (advisor/historian/witness), historical situation, task
2. Checkpoint 1        — Identify: which historical factor/force is driving this event?
3. Learning Block 1    — short, textbook-grounded explanation of the concept just identified
4. Checkpoint 2        — Decide: which decision/interpretation fits the evidence best?
5. Learning Block 2    — short explanation showing the method/relationship to apply
6. Checkpoint 3        — Justify or Avoid Mistake: why is the alternative interpretation wrong?
7. Decision Process Explanation (DPE) — after Checkpoint 3, before the final simulation (canonical CBP slot 7); OPEN-ENDED, options = null
8. Final simulation    — correct historical interpretation + wrong interpretation + consequence
9. Feedback summary
10. Completion rules
```

## Checkpoint rules

- **Exactly 3** — intents: `identify` → `decide` → `justify_or_avoid_mistake`
- Forms: `mcq` or `choice`
- Recognition only (choose the factor/interpretation).

## Learning Blocks (slots 3 & 5)

Two short teaching moments, emitted as `learning_block_1` and `learning_block_2`.
- **learning_block_1** (after Checkpoint 1): a 1–3 sentence explanation of the concept the student just identified, grounded in the textbook. Set `source_concept_id` to the SourceMap concept it teaches.
- **learning_block_2** (after Checkpoint 2): a 1–3 sentence explanation that shows the method/relationship to apply. Set `source_concept_id`.
- Keep them **text-first and short**. Use `visual_svg` ONLY if a tiny diagram is essential AND not already shown in the case — otherwise omit it (a `[Diagram: ...]` note in the text is preferred). This protects the output-token budget.
- Do NOT name the method in `learning_block_1` if the case still expects the student to commit at Checkpoint 2 first.

## DPE — slot 7 rules (non-negotiable)

- `options: null` — NO answer choices
- `expected_components: ["concept", "method", "mistake"]`
- Prompt asks: (1) Which historical concept/force did you identify? (2) Why this interpretation over alternatives? (3) What historical misreading was avoided?
- `min_chars: 60`, `eval_mode: "ai"`

## Final simulation rules

- `correct_path`: walk through the successful outcome when the student's decision is applied.
- `wrong_path`: show what happens when the common wrong answer is applied instead.
- `why_wrong_fails`: one sentence on why the wrong path cannot be correct (REQUIRED).

## History case types

- **Historical decision**: advisor evaluates a ruler/leader's choice from available evidence
- **Source analysis**: historian weighs a primary source claim against context
- **Cause identification**: student identifies the root cause of a historical event

## Milliylik rule

When the lesson covers Uzbekistan history: the scenario must be grounded in Uzbekistan's specific context (Silk Road, Timurids, Soviet era, independence). Do NOT generalize to generic "Central Asian history".

## Source concept rule

`source_concept_ids` must reference specific events, figures, or frameworks from this lesson.

## Output format — JSON matching CaseBasedPreview schema

```json
{
  "title": "...",
  "student_role": "advisor | historian | witness",
  "case_type": "historical_decision | source_analysis | cause_identification",
  "source_concept_ids": ["..."],
  "case_setup": { "narrative": "...", "student_role": "...", "task": "..." },
  "checkpoints": [
    { "intent": "identify", "form": "mcq", "question": "...", "options": [...], "correct_index": 0, "feedback": "..." },
    { "intent": "decide", ... },
    { "intent": "justify_or_avoid_mistake", ... }
  ],
  "decision_process_explanation": {
    "prompt": "Walk through your reasoning: (1) Which historical concept/force did you identify? (2) Why this interpretation? (3) What misreading was avoided?",
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
2. ✓ DPE `options` is null?
3. ✓ DPE after checkpoint 3, before final_simulation?
4. ✓ Historical conclusion NOT given in case setup?
5. ✓ Both correct_path and wrong_path present?
