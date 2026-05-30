# Prompt: Case-Based Preview — English

You are building a **Case-Based Preview** (CBP) for an English homework session. The student plays a communicator (speaker, writer, or editor) who must make a language choice in a real-world scenario.

## CBP canonical structure (NON-NEGOTIABLE)

EXACTLY 3 checkpoints with a Learning Block after the first two, then the DPE — placed after Checkpoint 3 and before the final simulation (this is the canonical CBP "slot 7").

```
1. Case setup          — student role (speaker/writer/editor), communication scenario, task
2. Checkpoint 1        — Identify: which language goal/register applies here?
3. Learning Block 1    — short, textbook-grounded explanation of the concept just identified
4. Checkpoint 2        — Decide: which grammar/vocabulary/structure fits?
5. Learning Block 2    — short explanation showing the method/relationship to apply
6. Checkpoint 3        — Justify or Avoid Mistake: why does the alternative fail?
7. Decision Process Explanation (DPE) — after Checkpoint 3, before the final simulation (canonical CBP slot 7); OPEN-ENDED, options = null
8. Final simulation    — correct version + wrong version + why wrong fails
9. Feedback summary
10. Completion rules
```

## Checkpoint rules

- **Exactly 3** — intents: `identify` → `decide` → `justify_or_avoid_mistake`
- Forms: `mcq` or `choice`
- Recognition only (choose from 4 options). Production reasoning belongs in the DPE.

## Learning Blocks (slots 3 & 5)

Two short teaching moments, emitted as `learning_block_1` and `learning_block_2`.
- **learning_block_1** (after Checkpoint 1): a 1–3 sentence explanation of the concept the student just identified, grounded in the textbook. Set `source_concept_id` to the SourceMap concept it teaches.
- **learning_block_2** (after Checkpoint 2): a 1–3 sentence explanation that shows the method/relationship to apply. Set `source_concept_id`.
- Keep them **text-first and short**. Use `visual_svg` ONLY if a tiny diagram is essential AND not already shown in the case — otherwise omit it (a `[Diagram: ...]` note in the text is preferred). This protects the output-token budget.
- Do NOT name the method in `learning_block_1` if the case still expects the student to commit at Checkpoint 2 first.

## DPE — slot 7 rules (non-negotiable)

- `options: null` — NO answer choices ever
- `expected_components: ["concept", "method", "mistake"]`
- Prompt asks: (1) Which grammar/vocab concept did you spot? (2) Why this form over alternatives? (3) What communicative mistake was avoided?
- `min_chars: 60`, `eval_mode: "ai"`

## Final simulation rules

- `correct_path`: walk through the successful outcome when the student's decision is applied.
- `wrong_path`: show what happens when the common wrong answer is applied instead.
- `why_wrong_fails`: one sentence on why the wrong path cannot be correct (REQUIRED).

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
