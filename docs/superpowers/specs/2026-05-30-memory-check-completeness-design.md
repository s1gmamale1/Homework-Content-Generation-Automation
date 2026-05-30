# Design — Memory Check Completeness (Flow v2 content reshape, workstream 3)

**Date:** 2026-05-30
**Branch:** Nggaev-v2
**Status:** Approved design → ready for implementation plan
**Scope:** content-generation only (items + answers + reasons; NOT runtime scoring/retry/adaptation)

---

## 1. Goal

Upgrade Memory Check items from a flat `options: list[str]` + `correct_index` model to the spec's richer per-type structure for the 3 Quizlet item kinds (`multiple_choice`, `choose_correct_explanation`, `fill_blank`):
- **MCQ / CCE:** options as objects carrying `is_correct` + a `reason` (the distractor's flaw / misconception); exactly 4 options, exactly one correct.
- **Fill-in-Blank:** no options — `blanks` with an expected `answer` + `accepted_variations`.
- **All kinds:** an optional `why_prompt` + `expected_reasoning_keywords` (the WHY apparatus, prompt-required for science) + per-item feedback.

Workstream 3 of the Flow v2 reshape (after CBP=0015, Flashcards=0016).

## 2. Non-goals (runtime — the student app's job)

- 100-point scoring, pass tiers, retry/hint sequencing, "near-miss" tolerance behavior, case/diacritic-sensitivity *behavior*, adaptive task pools, `supports_visual` rendering. We generate the *content* (items, correct answers, distractor reasons, accepted variations, WHY prompt); the app runs the quiz.
- `accepted_variations` IS kept (it's content — the set of acceptable typed answers). The runtime *matching algorithm* is not ours.

## 3. Schema changes — `app/schemas/memory_check.py`

**New models:**
```python
class MemoryCheckOption(BaseModel):
    text: str = Field(min_length=1)
    is_correct: bool = False
    reason: Optional[str] = None  # why this distractor is wrong (MCQ) / the flawed reasoning (CCE)


class MemoryCheckBlank(BaseModel):
    answer: str = Field(min_length=1)
    accepted_variations: list[str] = Field(default_factory=list)
```

**`MemoryCheckItem`** becomes:
```python
class MemoryCheckItem(BaseModel):
    flashcard_id: str = Field(min_length=1)   # kept — every item traces to a card
    kind: MemoryCheckKind
    prompt: str = Field(min_length=1)
    options: list[MemoryCheckOption] = Field(default_factory=list)   # MCQ + CCE
    blanks: list[MemoryCheckBlank] = Field(default_factory=list)     # fill_blank
    why_prompt: Optional[str] = None
    expected_reasoning_keywords: list[str] = Field(default_factory=list)
    correct_feedback: Optional[str] = None
    wrong_feedback: Optional[str] = None
    explanation: Optional[str] = None
```
(`correct_index` removed — correctness now lives on `MemoryCheckOption.is_correct`.)

**Validator** (`model_validator(mode="after")`, keyed on `kind`):
- `multiple_choice` / `choose_correct_explanation` → **exactly 4 options, exactly one `is_correct`, and `blanks` empty.**
- `fill_blank` → **at least 1 blank, and `options` empty.**

`MemoryCheckPack` unchanged: `items` + `pass_threshold` (locked at 0.60 by the existing field_validator — kept as our deck-level gate metadata).

## 4. Synth render — `app/services/pipeline.py` (`_synth_md_for_structured`, memory-check branch)

Rewrite for the new shape, preserving the §8 teacher-note split (memory-check answers ARE answer keys):
- Per item: `prompt`, then for MCQ/CCE the options rendered **clean and lettered** (a/b/c/d — correctness hidden from the student view), then a `🔑 TEACHER NOTE` with the correct option letter + each distractor's `reason`.
- For fill_blank: the `prompt` (with its `_____`), then a `🔑 TEACHER NOTE` with the accepted answer(s).
- `why_prompt` is student-facing (a reasoning question); `explanation`/feedback go under the teacher note.

## 5. Prompts — 7 `memory-check.md`

`prompts/{biology,english,geometriya-g7-11,history,kimyo-g7-11,math-algebra,physics}/memory-check.md`

Rewrite each to instruct the enriched item:
- Each item references a `flashcard_id`.
- MCQ/CCE: **exactly 4 option objects**, each `{text, is_correct, reason}`, exactly one `is_correct: true`; distractors are real misconceptions (the `reason` names the flaw); anti-leak (options similar length/style).
- Fill-blank: a `prompt` containing `_____` + `blanks` each `{answer, accepted_variations}`.
- `why_prompt` + `expected_reasoning_keywords` — **required for science subjects** (biology/physics/kimyo), optional elsewhere.
- `correct_feedback` / `wrong_feedback` encouraged.
- Keep the 3 kinds, the `flashcard_id` traceability, the "≤60% of items one kind" balance, and the 60% pass gate.

## 6. Tests (TDD)

- Validator: MCQ/CCE with ≠4 options ⇒ error; with 0 or 2 correct ⇒ error; with a blank present ⇒ error. fill_blank with 0 blanks ⇒ error; with options present ⇒ error.
- `MemoryCheckOption.text` / `MemoryCheckBlank.answer` empty ⇒ error.
- Optionals default (`why_prompt` None, `expected_reasoning_keywords` []).
- `pass_threshold` still locked at 0.60.
- Sweep all construction sites: **`tests/services/test_teacher_note_labeling.py`** builds MemoryCheck items in the OLD shape (`options=list[str]`, `correct_index`) — rewrite those fixtures to the new option-objects shape; check `tests/schemas/` for any memory-check builders too.

## 7. Backward compatibility

None needed — `memory_check_json` is write-only (persisted via `model_dump`, read raw by the download endpoint / frontend), never re-validated through the schema.

## 8. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Per-option objects + exactly-4 validator raise the generation bar (≈10 items × 4 option objects each). | Structure is explicit in the prompt; fail-loud (retry-once), not silent. Memory Check items are small text; no token-size concern. |
| Removing `correct_index` breaks the synth + any frontend reading it. | Synth rewritten in this workstream (Task: synth). Frontend is old-flow + out of scope; `memory_check_json` not re-validated. |
| `test_teacher_note_labeling.py` (and possibly others) construct the old shape and will fail. | Sweep + rewrite those fixtures in the schema task so the suite stays green. |
| "Exactly 4 options" is rigid. | Spec-mandated ("more or fewer than 4 options" is forbidden). Accepted. |

## 9. Acceptance criteria

- `MemoryCheckItem` uses `MemoryCheckOption`/`MemoryCheckBlank`; the kind-keyed validator enforces 4-option/one-correct (MCQ/CCE) and blanks-only (fill_blank).
- Synth renders clean student options + a teacher note with the correct answer + distractor reasons (no answer leaked to the student view).
- All 7 memory-check prompts instruct the enriched item with the WHY apparatus (science-required).
- Full suite green (new tests + swept fixtures).
- A real memory-check generation (geometry, claude) yields items with 4 tagged options (one correct) / valid fill-blank blanks, and the teacher note shows the answers.
