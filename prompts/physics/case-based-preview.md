# Prompt: Case-Based Preview — Physics

You are building a **Case-Based Preview** (CBP) for a Physics homework session. The student plays an observer or engineer who must predict a physical outcome before seeing it.

## CBP canonical structure (NON-NEGOTIABLE)

EXACTLY 3 checkpoints, then the DPE — placed after Checkpoint 3 and before the final simulation (this is the canonical CBP "slot 7").

```
1. Case setup          — student role (observer/engineer/technician), physical scenario, task
2. Checkpoint 1        — Identify: which physical quantity/concept is changing?
3. Checkpoint 2        — Decide: which law/formula applies?
4. Checkpoint 3        — Justify or Avoid Mistake: predict effect or rule out incorrect formula
5. Decision Process Explanation (DPE) — after Checkpoint 3, before the final simulation (canonical CBP slot 7); OPEN-ENDED, options = null
6. Final simulation    — correct result + wrong result + why wrong result is impossible
7. Feedback summary
8. Completion rules
```

## Checkpoint rules

- **Exactly 3** — intents: `identify` → `decide` → `justify_or_avoid_mistake`
- Forms: `mcq` or `choice`
- Recognition only. DPE holds all production reasoning.

## DPE — slot 7 rules (non-negotiable)

- `options: null` — NEVER include answer choices
- `expected_components: ["concept", "method", "mistake"]`
- Prompt asks: (1) Which physical concept/quantity did you spot? (2) Why this law/formula over alternatives? (3) What physical mistake was avoided?
- `min_chars: 60`, `eval_mode: "ai"`

## Physics case types

- **Phenomenon → prediction**: observer watches what happens and predicts the outcome
- **Engineering decision**: engineer selects the correct formula for a design constraint
- **Measurement interpretation**: technician decides what a measurement tells them

## SVG rule

Use SVG for: force diagrams, circuit diagrams, motion graphs, vector arrows, formulas. Use image descriptions for real-world scenes.

## Law/formula visibility

Do NOT name the law (e.g. "Ohm's law", "Newton's second law") in the case setup or checkpoints. Student commits to it in the DPE.

## Source concept rule

`source_concept_ids` must reference concepts from this lesson only.

## Output format — JSON matching CaseBasedPreview schema

```json
{
  "title": "...",
  "student_role": "observer | engineer | technician",
  "case_type": "phenomenon_prediction | engineering_decision | measurement_interpretation",
  "source_concept_ids": ["..."],
  "case_setup": { "narrative": "...", "student_role": "...", "task": "..." },
  "checkpoints": [
    { "intent": "identify", "form": "mcq", "question": "...", "options": [...], "correct_index": 0, "feedback": "..." },
    { "intent": "decide", ... },
    { "intent": "justify_or_avoid_mistake", ... }
  ],
  "decision_process_explanation": {
    "prompt": "Walk through your reasoning: (1) Which physical concept/quantity did you spot? (2) Why this law/formula? (3) What physical mistake was avoided?",
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
4. ✓ Law/formula name NOT in setup or checkpoints?
5. ✓ Both correct_path and wrong_path present?
