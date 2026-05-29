# Prompt: Case-Based Preview — Kimyo (Chemistry)

You are building a **Case-Based Preview** (CBP) for a Chemistry homework session. The student plays a lab assistant or safety officer who must make a decision about a chemical process or safety procedure.

## CBP canonical structure (NON-NEGOTIABLE)

EXACTLY 3 checkpoints + DPE in slot 7 (before final simulation).

```
1. Case setup          — student role (lab_assistant/safety_officer/analyst), scenario, task
2. Checkpoint 1        — Identify: which substance/process/reaction type is present?
3. Checkpoint 2        — Decide: which method/procedure is safe and correct?
4. Checkpoint 3        — Justify or Avoid Mistake: explain consequence of wrong choice
5. Decision Process Explanation (DPE) — slot 7, OPEN-ENDED, options = null
6. Final simulation    — correct procedure + wrong procedure + why wrong causes harm/failure
7. Feedback summary
8. Completion rules
```

## Checkpoint rules

- **Exactly 3** — intents: `identify` → `decide` → `justify_or_avoid_mistake`
- Forms: `mcq` or `choice`
- Low-friction recognition only.

## DPE — slot 7 rules (non-negotiable)

- `options: null` — NO answer choices
- `expected_components: ["concept", "method", "mistake"]`
- Prompt asks: (1) Which chemical concept/property? (2) Why this method/procedure? (3) What hazard or failure was avoided?
- `min_chars: 60`, `eval_mode: "ai"`

## Chemistry case types

- **Lab safety**: safety_officer identifying risk and selecting safe procedure
- **Reaction observation**: analyst identifying a reaction type from observable signs
- **Substance property**: lab_assistant choosing correct handling based on substance properties

## SVG rule

Use SVG for: chemical formulas, reaction equations, lab apparatus, periodic table excerpts.

## Source concept rule

`source_concept_ids` must name concepts from this lesson's textbook section.

## Output format — JSON matching CaseBasedPreview schema

```json
{
  "title": "...",
  "student_role": "lab_assistant | safety_officer | analyst",
  "case_type": "lab_safety | reaction_observation | substance_property",
  "source_concept_ids": ["..."],
  "case_setup": { "narrative": "...", "student_role": "...", "task": "..." },
  "checkpoints": [
    { "intent": "identify", "form": "mcq", "question": "...", "options": [...], "correct_index": 0, "feedback": "..." },
    { "intent": "decide", ... },
    { "intent": "justify_or_avoid_mistake", ... }
  ],
  "decision_process_explanation": {
    "prompt": "Walk through your reasoning: (1) Which chemical concept did you spot? (2) Why this procedure? (3) What hazard or failure was avoided?",
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
4. ✓ Reaction/substance name NOT named as method in setup?
5. ✓ Both correct_path and wrong_path present?
