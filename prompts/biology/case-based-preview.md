# Prompt: Case-Based Preview — Biology

You are building a **Case-Based Preview** (CBP) for a Biology homework session. The student plays the role of a researcher or ecologist who must make an observation-based decision before seeing the consequence.

## CBP canonical structure (NON-NEGOTIABLE)

EXACTLY 3 checkpoints + DPE in slot 7 (before final simulation).

```
1. Case setup          — student role (researcher/ecologist/medic), narrative, task
2. Checkpoint 1        — Identify: which biological system/structure is involved?
3. Checkpoint 2        — Decide: which factor/mechanism drives the outcome?
4. Checkpoint 3        — Justify or Avoid Mistake: predict consequence or rule out wrong mechanism
5. Decision Process Explanation (DPE) — slot 7, OPEN-ENDED, options = null
6. Final simulation    — correct path + wrong path + why wrong fails
7. Feedback summary
8. Completion rules
```

## Checkpoint rules

- **Exactly 3** — intents: `identify` → `decide` → `justify_or_avoid_mistake`
- Forms: `mcq` or `choice` only for biology cases
- Low-friction recognition only. Deep reasoning belongs in the DPE.

## DPE — slot 7 rules (non-negotiable)

- `options: null` — NEVER add answer choices
- `expected_components: ["concept", "method", "mistake"]`
- Prompt must ask: (1) Which biological concept/structure? (2) Why this mechanism? (3) What wrong interpretation was avoided?
- `min_chars: 60`, `eval_mode: "ai"`

## Biology case types

- **Observation → mechanism**: student identifies what's happening at the cellular/organism level
- **Ecological prediction**: student predicts population/ecosystem change from a trigger
- **Comparative anatomy**: student decides which structure serves a given function

## SVG rule

Use SVG for: cell diagrams, food chains, organism structures, cross-sections. Use image descriptions for real-life scenes (forest, lab, ecosystem).

## Source concept rule

`source_concept_ids` must map to concepts in this lesson. Do not invent.

## Output format — JSON matching CaseBasedPreview schema

```json
{
  "title": "...",
  "student_role": "researcher | ecologist | lab_assistant",
  "case_type": "observation_mechanism | ecological_prediction | comparative_anatomy",
  "source_concept_ids": ["..."],
  "case_setup": { "narrative": "...", "student_role": "...", "task": "..." },
  "checkpoints": [
    { "intent": "identify", "form": "mcq", "question": "...", "options": [...], "correct_index": 0, "feedback": "..." },
    { "intent": "decide", ... },
    { "intent": "justify_or_avoid_mistake", ... }
  ],
  "decision_process_explanation": {
    "prompt": "Walk through your reasoning: (1) Which biological concept did you spot? (2) Why this mechanism over alternatives? (3) What wrong interpretation was avoided?",
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
4. ✓ Biological concept name NOT given in setup or checkpoints?
5. ✓ `source_concept_ids` traceable to this lesson?
