# Prompt: Case-Based Preview — English

You are building a **Case-Based Preview** (CBP) for an English homework session. The student plays a communicator (speaker, writer, or editor) who must make a language choice in a real-world scenario.

## CBP canonical structure (NON-NEGOTIABLE)

EXACTLY 3 checkpoints + DPE in slot 7 (before final simulation).

```
1. Case setup          — student role (speaker/writer/editor), communication scenario, task
2. Checkpoint 1        — Identify: which language goal/register applies here?
3. Checkpoint 2        — Decide: which grammar/vocabulary/structure fits?
4. Checkpoint 3        — Justify or Avoid Mistake: why does the alternative fail?
5. Decision Process Explanation (DPE) — slot 7, OPEN-ENDED, options = null
6. Final simulation    — correct version + wrong version + why wrong fails
7. Feedback summary
8. Completion rules
```

## Checkpoint rules

- **Exactly 3** — intents: `identify` → `decide` → `justify_or_avoid_mistake`
- Forms: `mcq` or `choice`
- Recognition only (choose from 4 options). Production reasoning belongs in the DPE.

## DPE — slot 7 rules (non-negotiable)

- `options: null` — NO answer choices ever
- `expected_components: ["concept", "method", "mistake"]`
- Prompt asks: (1) Which grammar/vocab concept did you spot? (2) Why this form over alternatives? (3) What communicative mistake was avoided?
- `min_chars: 60`, `eval_mode: "ai"`

## English case types

- **Communication scenario**: speaker/writer choosing correct tense, register, collocation
- **Error editing**: editor identifying and explaining why a sentence fails
- **Vocabulary selection**: writer picking the precise word vs a near-miss

## CEFR rule

Keep language at the unit's CEFR level (injected in lesson_context). Do not simplify grammar items from the unit.

## Source concept rule

`source_concept_ids` must reference the unit's target grammar/vocabulary items.

## Output format — JSON matching CaseBasedPreview schema

```json
{
  "title": "...",
  "student_role": "speaker | writer | editor",
  "case_type": "communication_scenario | error_editing | vocabulary_selection",
  "source_concept_ids": ["..."],
  "case_setup": { "narrative": "...", "student_role": "...", "task": "..." },
  "checkpoints": [
    { "intent": "identify", "form": "mcq", "question": "...", "options": [...], "correct_index": 0, "feedback": "..." },
    { "intent": "decide", ... },
    { "intent": "justify_or_avoid_mistake", ... }
  ],
  "decision_process_explanation": {
    "prompt": "Walk through your reasoning: (1) Which grammar/vocabulary concept did you spot? (2) Why this form over alternatives? (3) What communicative mistake was avoided?",
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
4. ✓ Grammar/vocabulary concept NOT named upfront in setup?
5. ✓ Both correct_path and wrong_path in final_simulation?
