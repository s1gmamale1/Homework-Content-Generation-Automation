# Prompt: Case-Based Preview — History (O'zbekiston Tarixi + Jahon Tarixi)

You are building a **Case-Based Preview** (CBP) for a History homework session. The student plays an advisor or historian who must evaluate historical evidence and make a decision under the same constraints as the historical actor.

## CBP canonical structure (NON-NEGOTIABLE)

EXACTLY 3 checkpoints, then the DPE — placed after Checkpoint 3 and before the final simulation (this is the canonical CBP "slot 7").

```
1. Case setup          — student role (advisor/historian/witness), historical situation, task
2. Checkpoint 1        — Identify: which historical factor/force is driving this event?
3. Checkpoint 2        — Decide: which decision/interpretation fits the evidence best?
4. Checkpoint 3        — Justify or Avoid Mistake: why is the alternative interpretation wrong?
5. Decision Process Explanation (DPE) — after Checkpoint 3, before the final simulation (canonical CBP slot 7); OPEN-ENDED, options = null
6. Final simulation    — correct historical interpretation + wrong interpretation + consequence
7. Feedback summary
8. Completion rules
```

## Checkpoint rules

- **Exactly 3** — intents: `identify` → `decide` → `justify_or_avoid_mistake`
- Forms: `mcq` or `choice`
- Recognition only (choose the factor/interpretation).

## DPE — slot 7 rules (non-negotiable)

- `options: null` — NO answer choices
- `expected_components: ["concept", "method", "mistake"]`
- Prompt asks: (1) Which historical concept/force did you identify? (2) Why this interpretation over alternatives? (3) What historical misreading was avoided?
- `min_chars: 60`, `eval_mode: "ai"`

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
