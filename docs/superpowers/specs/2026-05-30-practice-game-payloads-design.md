# Design — Practice-Game Payloads (Flow v2 content reshape, workstream 4)

**Date:** 2026-05-30
**Branch:** Nggaev-v2
**Status:** Approved design → ready for implementation plan
**Scope:** content-generation only (static game data; runtime feedback/scoring/meters = student app)

---

## 1. Goal

The 4 CBP-mode games (`memory_match, jigsaw, sentence_fill, tictactoe`) share `CbpModeGame` (the Case-Based Preview skeleton + `interaction_mode`). That captures the reasoning flow but **not the structured data each game needs to render**, which today is crammed into prose/checkpoint strings. Add a typed, **required, per-mode `interaction_payload`** so a downstream renderer can draw the real widget.

Workstream 4 of the reshape (after CBP=0015, Flashcards=0016, Memory Check=0017).

## 2. Non-goals (runtime — student app)

Recall classification (Recalled/Guessed/Missed), state-meter values, meaning-panel pass/fail verdicts, scoring, the result/animation panels. We generate the static game data only (pairs, pieces, sentence+chips, grid cells + which are correct + why).

## 3. Schema — `app/schemas/practice_games.py`

```python
class GameChoice(BaseModel):              # reused for sentence chips + tictactoe cells
    label: str = Field(min_length=1)
    is_correct: bool = False
    reason: Optional[str] = None          # why this choice is right/wrong


class MemoryMatchPair(BaseModel):
    left: str = Field(min_length=1)
    right: str = Field(min_length=1)


class MemoryMatchPayload(BaseModel):
    pairs: list[MemoryMatchPair] = Field(min_length=4, max_length=8)


class JigsawPiece(BaseModel):
    id: str = Field(min_length=1)
    content: str = Field(min_length=1)


class JigsawPayload(BaseModel):
    pieces: list[JigsawPiece] = Field(min_length=3, max_length=6)
    allowed_assembly_types: list[str] = Field(min_length=1, max_length=3)


class SentenceFillPayload(BaseModel):
    sentence: str = Field(min_length=1)   # contains the broken/blank span
    chips: list[GameChoice] = Field(min_length=3)

    @model_validator(mode="after")
    def _one_correct_chip(self):
        if sum(1 for c in self.chips if c.is_correct) != 1:
            raise ValueError("sentence_fill needs exactly one correct chip")
        return self


class TicTacToePayload(BaseModel):
    cells: list[GameChoice] = Field(min_length=9, max_length=9)   # 3x3

    @model_validator(mode="after")
    def _at_least_one_correct(self):
        if not any(c.is_correct for c in self.cells):
            raise ValueError("tictactoe needs at least one correct cell")
        return self
```

`CbpModeGame` gains:
```python
_PAYLOAD_TYPE_FOR_MODE = {
    "memory_match": MemoryMatchPayload,
    "jigsaw": JigsawPayload,
    "sentence_fill": SentenceFillPayload,
    "tictactoe": TicTacToePayload,
}

class CbpModeGame(CaseBasedPreview):
    interaction_mode: PracticeInteractionMode
    interaction_payload: Union[MemoryMatchPayload, JigsawPayload, SentenceFillPayload, TicTacToePayload]

    @model_validator(mode="after")
    def _payload_matches_mode(self) -> "CbpModeGame":
        expected = _PAYLOAD_TYPE_FOR_MODE[self.interaction_mode]
        if not isinstance(self.interaction_payload, expected):
            raise ValueError(
                f"interaction_mode={self.interaction_mode} requires a {expected.__name__}, "
                f"got {type(self.interaction_payload).__name__}"
            )
        return self
```
(Pydantic v2 smart-union picks the right payload by structure; the validator guarantees it matches the declared mode.)

## 4. Synth render — `app/services/pipeline.py` (`_CBP_MODE_PHASES` branch)

After the existing CBP-skeleton render (checkpoints+blocks, DPE, sim), append a per-mode block from `interaction_payload`:
- **memory_match:** list the pairs `left ↔ right` (student-visible — matching is the task).
- **jigsaw:** list the pieces + the `allowed_assembly_types` (student-visible).
- **sentence_fill:** show the `sentence`, then the chips as lettered options (student-visible); a `🔑 TEACHER NOTE` names the correct chip + each wrong chip's reason.
- **tictactoe:** render the 9 cells as a 3×3 grid of labels (student-visible); a `🔑 TEACHER NOTE` names the correct cell(s) + reasons.

## 5. Prompts — 11 CBP-mode game prompts

`prompts/*/practice-memory-match.md` (biology, english, history), `practice-jigsaw.md` (geometriya, history, math-algebra), `practice-sentence.md` (english), `practice-tictactoe.md` (geometriya, kimyo, math-algebra, physics).

Each instructs its `interaction_payload`:
- memory-match: `pairs` (4–8 `{left, right}`).
- jigsaw: `pieces` (3–6 `{id, content}`) + `allowed_assembly_types` (1–3 relationship labels).
- sentence: `sentence` (with the broken/blank span) + `chips` (≥3 `{label, is_correct, reason}`, exactly one correct; distractors are real wrong choices with a `reason`).
- tictactoe: exactly 9 `cells` (`{label, is_correct, reason}`, ≥1 correct; cells are candidate actions, the wrong ones tagged with why).
Keep each game's existing source-fidelity + learning-block + why_wrong_fails instructions.

## 6. Tests (TDD)

- Each payload validator (pair count 4–8; pieces 3–6 + assembly types 1–3; sentence chips ≥3 with exactly-one-correct; cells exactly 9 with ≥1 correct).
- `CbpModeGame` mode-match validator: a `jigsaw` mode with a `MemoryMatchPayload` ⇒ `ValidationError`; each mode with its matching payload ⇒ valid.
- Update the `_cbp_mode` fixture so it builds a **mode-appropriate** payload (a `_PAYLOAD_FOR[mode]` map; `_cbp_mode` `setdefault`s the payload from the final `interaction_mode`) — the parametrized "all 4 modes" tests (`test_cbp_mode_accepts_all_four_interaction_modes`, the synth-mode test) must pass for every mode.
- Synth: each mode renders its payload (pairs/pieces/sentence+chips/grid); chip & cell correctness only under a teacher note.

## 7. Backward compatibility

None — `practice_*_json` columns are write-only, never re-validated.

## 8. Risks & mitigations

| Risk | Mitigation |
|---|---|
| **Heaviest output in the system.** CbpModeGame = full CBP skeleton + 2 learning blocks + payload. Plain CBP already ~46k tok/~12 min; a game could be heavier/slower, possibly near claude limits. | Payloads are small (short strings). Live smoke is the gate — run one game generation; if it trips the ceiling or is too slow, escalate to a follow-up (a lighter game skeleton is a separate architectural question, not this WS). |
| Union mis-parse (Pydantic picks wrong payload). | Field sets are distinct (pairs vs pieces+types vs sentence+chips vs cells); the `_payload_matches_mode` validator catches any mismatch loudly. |
| Parametrized `_cbp_mode` tests break (one payload no longer fits all modes). | Fixture builds a per-mode payload via `_PAYLOAD_FOR[mode]`. |

## 9. Acceptance criteria

- `CbpModeGame` requires an `interaction_payload` whose type matches `interaction_mode` (validator enforced).
- Each payload validates its structural rules.
- Synth renders each game's widget data, with correctness teacher-noted.
- All 11 CBP-mode game prompts instruct their payload.
- Full suite green (new tests + `_cbp_mode` fixture updated).
- A real CBP-mode game generation (e.g. geometry jigsaw or tictactoe, claude) produces a valid payload matching its mode, and the generation completes without tripping the output ceiling.
