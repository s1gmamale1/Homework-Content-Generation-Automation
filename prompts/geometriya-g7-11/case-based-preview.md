# Prompt: Case-Based Preview — Geometriya (Geometry)

You are building a **Case-Based Preview** (CBP) for a Geometry homework session. The student plays a planner or builder who must make a geometric decision in a practical scenario.

## CBP canonical structure (NON-NEGOTIABLE)

EXACTLY 3 checkpoints, then the DPE — placed after Checkpoint 3 and before the final simulation (this is the canonical CBP "slot 7").

```
1. Case setup          — student role (planner/builder/designer), geometric scenario, task
2. Checkpoint 1        — Identify: which shape/theorem applies here?
3. Checkpoint 2        — Decide: which formula/property to use?
4. Checkpoint 3        — Justify or Avoid Mistake: why does the alternative formula fail?
5. Decision Process Explanation (DPE) — after Checkpoint 3, before the final simulation (canonical CBP slot 7); OPEN-ENDED, options = null
6. Final simulation    — correct calculation + wrong calculation + why wrong fails
7. Feedback summary
8. Completion rules
```

## Checkpoint rules

- **Exactly 3** — intents: `identify` → `decide` → `justify_or_avoid_mistake`
- Forms: `mcq` or `choice`
- Recognition only. DPE holds production reasoning.

## DPE — slot 7 rules (non-negotiable)

- `options: null` — NO answer choices
- `expected_components: ["concept", "method", "mistake"]`
- Prompt asks: (1) Which geometric concept/theorem? (2) Why this formula over alternatives? (3) What calculation mistake was avoided?
- `min_chars: 60`, `eval_mode: "ai"`

## Geometry case types

- **Practical measurement**: planner needs area/perimeter/volume for real construction
- **Theorem application**: builder identifies which theorem proves a given property
- **Coordinate geometry**: designer finds distances/midpoints/angles on a grid

## SVG rule

ALWAYS include an SVG for geometry cases. Diagrams are essential — shapes, labels, measurements, coordinate grids. Follow the universal SVG rules injected by the runtime.

## Source concept rule

`source_concept_ids` must reference the theorem/formula from this lesson.

## Output format — JSON matching CaseBasedPreview schema

```json
{
  "title": "...",
  "student_role": "planner | builder | designer",
  "case_type": "practical_measurement | theorem_application | coordinate_geometry",
  "source_concept_ids": ["..."],
  "case_setup": { "narrative": "...", "student_role": "...", "task": "..." },
  "checkpoints": [
    { "intent": "identify", "form": "mcq", "question": "...", "options": [...], "correct_index": 0, "feedback": "..." },
    { "intent": "decide", ... },
    { "intent": "justify_or_avoid_mistake", ... }
  ],
  "decision_process_explanation": {
    "prompt": "Walk through your reasoning: (1) Which geometric concept/theorem did you spot? (2) Why this formula over alternatives? (3) What calculation mistake was avoided?",
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
4. ✓ Theorem/formula name NOT given in setup?
5. ✓ SVG included in case_setup narrative?
