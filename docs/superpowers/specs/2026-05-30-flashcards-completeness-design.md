# Design — Flashcards Completeness (Flow v2 content reshape, workstream 2)

**Date:** 2026-05-30
**Branch:** Nggaev-v2
**Status:** Approved design → ready for implementation plan
**Scope:** content-generation only (the card deck; NOT the runtime study games)

---

## 1. Goal

Upgrade generated flashcards from the current 3-field "reference tool" to the NETS spec's **8-field active-recall card**. Today a card has `id, front, back, hint (+ a non-spec cluster)`. The spec card is `front, back, hint, explanation, example, misconception, type, difficulty`, with word limits and a per-subject `type` vocabulary.

This is workstream 2 of the Flow v2 reshape (after CBP completeness, worklog 0015).

## 2. Non-goals

- The 5 runtime **study games** (Match / Write-Spell / Learn / Test / Memory Sprint) — those are how the deck is *used* (student-app/runtime), and quizzing overlaps the Memory Check workstream (next). Out of scope.
- **Word-limit enforcement in the schema** — word-count validators are brittle (what is a "word"? a hard cap can reject valid cards). Word limits live in the prompt as a quality rule.
- **Card-count enforcement in the schema** — count (6–12; easy 5–8 / hard 8–12) stays prompt-governed; difficulty-dependent ranges don't map cleanly to one schema bound.

## 3. Schema changes — `app/schemas/flashcards.py`

**New enums:**
```python
FlashcardType = Literal[
    "definition", "term_to_meaning", "formula", "process_step",
    "question_answer", "misconception", "image_label", "vocabulary",
    "grammar", "example",
]
FlashcardDifficulty = Literal["easy", "medium", "hard"]
```
(One superset `type` covering all subject families; each subject's prompt scopes the subset it uses.)

**`Flashcard`** becomes:
```python
class Flashcard(BaseModel):
    id: str = Field(min_length=1)        # kept
    front: str = Field(min_length=1)
    back: str = Field(min_length=1)
    type: FlashcardType                  # NEW, required
    difficulty: FlashcardDifficulty      # NEW, required
    hint: Optional[str] = None
    explanation: Optional[str] = None    # NEW, optional (prompt-encouraged)
    example: Optional[str] = None        # NEW, optional
    misconception: Optional[str] = None  # NEW, optional (prompt-required for trap cards)
    cluster: Optional[str] = None        # kept (harmless; avoids breaking old frontend types)
```
`FlashcardsPack` (cards list + `_ids_must_be_unique` validator) is unchanged. `front`/`back` gain `min_length=1` (they were unconstrained strings).

## 4. Prompts — 7 subject `flashcards.md`

`prompts/{biology,english,geometriya-g7-11,history,kimyo-g7-11,math-algebra,physics}/flashcards.md`

Rewrite each from the current 3-field card to the 8-field card. Each prompt must specify:
- The 8 fields and which are required (`front, back, type, difficulty`) vs optional-but-encouraged (`hint, explanation, example, misconception`).
- **Word limits:** front 3–14 words; back 5–22, never >25 (formula/process exception); hint ≤12; explanation/example/misconception ≤1 short sentence each.
- The subject's allowed `type` values (e.g. sciences: definition/term_to_meaning/formula/process_step/question_answer/misconception/image_label; languages: + vocabulary/grammar; math: + example; humanities: + process_step) and `difficulty`.
- **`misconception` is required (by prompt) for trap / false-friend cards.**
- Keep the existing **bracket `[Diagram: ...]` / no raw inline `<svg>`** rule (from worklog 0013) — flashcards stay SVG-free.
- Keep stable sequential `card_1, card_2, …` ids and the 6–12 (easy 5–8 / hard 8–12) count guidance.

## 5. Synth render — `app/services/pipeline.py` (`_synth_md_for_structured`, flashcards branch)

Extend the per-card line to show a `type · difficulty` tag and surface `explanation` / `example` / `misconception` when present. **No teacher-note wrapping** — a flashcard is a flip-to-study reference (both sides are student-facing), not an answer key to hide.

## 6. Tests (TDD)

- `type` and `difficulty` required → omitting either ⇒ `ValidationError`.
- `type`/`difficulty` reject unknown enum values.
- Optional fields (`explanation`, `example`, `misconception`, `hint`) default `None`.
- `front`/`back` empty ⇒ `ValidationError`.
- Existing id-uniqueness validator still passes.
- Synth: a card with the new fields renders the `type·difficulty` tag + the present optional fields.
- Sweep every test that constructs a `Flashcard(...)` / `FlashcardsPack(...)` and add `type`/`difficulty` to the fixtures so the suite stays green.

## 7. Backward compatibility

None needed. `flashcards_json` is write-only (persisted via `model_dump`, read back as raw JSON by the download endpoint / frontend), never re-validated through the schema. Old decks won't error; regenerate to get the richer cards.

## 8. Risks & mitigations

| Risk | Mitigation |
|---|---|
| `type`/`difficulty` required on every card (deck of ~12) raises validation-fail odds. | Only 2 new required fields, both simple closed enums (trivial for the model to fill on each card). Far lighter than CBP's added required objects. Fail-loud (retry-once), not silent. |
| 7 prompt rewrites are real content work (current prompts are minimal). | Mechanical per-file: same 8-field template, only the subject's `type` subset + examples differ. |
| No 32k output risk. | Flashcards are already SVG-free (worklog 0013); text cards are small. |

## 9. Acceptance criteria

- `Flashcard` requires `front, back, type, difficulty` (+ unique `id`); `type`/`difficulty` are closed enums; the four optional fields default `None`.
- All 7 flashcards prompts instruct the 8-field card with word limits + the subject's `type` subset.
- Synth renders the `type·difficulty` tag + optional fields.
- Full suite green (new tests + swept fixtures).
- A real flashcards generation (geometry, claude) produces 8-field cards with `type`/`difficulty` set and no raw `<svg>`.
