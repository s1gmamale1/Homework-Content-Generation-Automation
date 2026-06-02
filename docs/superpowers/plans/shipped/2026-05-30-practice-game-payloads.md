# Practice-Game Payloads Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Give each CBP-mode game a required, typed `interaction_payload` (pairs / pieces+types / sentence+chips / 3×3 grid) so it's fully renderable, validated to match `interaction_mode`.

**Architecture:** 4 payload models + a shared `GameChoice` in `practice_games.py`; `CbpModeGame` gains a required `interaction_payload` union + a mode-match validator; synth renders each payload; 11 game prompts instruct it. Schema + synth + fixture land together.

**Tech Stack:** Python 3.13, Pydantic v2, pytest (`.venv/Scripts/python.exe -m pytest`).

**Spec:** `docs/superpowers/specs/2026-05-30-practice-game-payloads-design.md`

---

### Task 1: Schema + synth + fixtures (one green commit)

**Files:**
- Modify: `app/schemas/practice_games.py`
- Modify: `app/services/pipeline.py` (`_synth_md_for_structured`, `_CBP_MODE_PHASES` branch)
- Modify: `tests/schemas/test_practice_games_schemas.py` (`_cbp_mode` fixture + new tests)

- [ ] **Step 1: Add payload models + the union/validator.** In `app/schemas/practice_games.py`, ensure `from typing import Literal, Optional, Union` and `from pydantic import BaseModel, Field, model_validator` (add `Union`/`model_validator` if missing). Just ABOVE `class CbpModeGame`, add:

```python
class GameChoice(BaseModel):
    label: str = Field(min_length=1)
    is_correct: bool = False
    reason: Optional[str] = None


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
    sentence: str = Field(min_length=1)
    chips: list[GameChoice] = Field(min_length=3)

    @model_validator(mode="after")
    def _one_correct_chip(self) -> "SentenceFillPayload":
        if sum(1 for c in self.chips if c.is_correct) != 1:
            raise ValueError("sentence_fill needs exactly one correct chip")
        return self


class TicTacToePayload(BaseModel):
    cells: list[GameChoice] = Field(min_length=9, max_length=9)

    @model_validator(mode="after")
    def _at_least_one_correct(self) -> "TicTacToePayload":
        if not any(c.is_correct for c in self.cells):
            raise ValueError("tictactoe needs at least one correct cell")
        return self


_PAYLOAD_TYPE_FOR_MODE = {
    "memory_match": MemoryMatchPayload,
    "jigsaw": JigsawPayload,
    "sentence_fill": SentenceFillPayload,
    "tictactoe": TicTacToePayload,
}
```

- [ ] **Step 2: Add the field + validator to `CbpModeGame`.** Replace the `CbpModeGame` class body with:
```python
class CbpModeGame(CaseBasedPreview):
    """A Case-Based Preview interaction-mode game. (docstring kept as-is above/below.)"""

    interaction_mode: PracticeInteractionMode
    interaction_payload: Union[
        MemoryMatchPayload, JigsawPayload, SentenceFillPayload, TicTacToePayload
    ]

    @model_validator(mode="after")
    def _payload_matches_mode(self) -> "CbpModeGame":
        expected = _PAYLOAD_TYPE_FOR_MODE[self.interaction_mode]
        if not isinstance(self.interaction_payload, expected):
            raise ValueError(
                f"interaction_mode={self.interaction_mode} requires a "
                f"{expected.__name__}, got {type(self.interaction_payload).__name__}"
            )
        return self
```
(Keep the existing `interaction_mode` docstring/comment; just add the payload field + validator.)

- [ ] **Step 3: Update the `_cbp_mode` fixture.** In `tests/schemas/test_practice_games_schemas.py`, add a payload map near the top of the CBP-mode section and make `_cbp_mode` `setdefault` a mode-appropriate payload. Add:
```python
_PAYLOAD_FOR = {
    "memory_match": dict(pairs=[
        dict(left="Parallelogramm", right="qarama-qarshi tomonlari parallel"),
        dict(left="Romb", right="hamma tomonlari teng"),
        dict(left="Kvadrat", right="hamma tomon teng + to'g'ri burchak"),
        dict(left="Trapetsiya", right="bir juft parallel tomon"),
    ]),
    "jigsaw": dict(
        pieces=[dict(id="p1", content="qarama-qarshi tomonlar"),
                dict(id="p2", content="teng"),
                dict(id="p3", content="parallel")],
        allowed_assembly_types=["xossa", "ta'rif"],
    ),
    "sentence_fill": dict(
        sentence="Parallelogrammning qarama-qarshi tomonlari _____.",
        chips=[dict(label="teng va parallel", is_correct=True),
               dict(label="faqat teng", is_correct=False, reason="parallellik ham shart"),
               dict(label="perpendikular", is_correct=False, reason="bu noto'g'ri munosabat")],
    ),
    "tictactoe": dict(cells=[
        dict(label="qarama-qarshi tomonlar teng", is_correct=True),
        *[dict(label=f"chalg'ituvchi {i}", is_correct=False, reason="mavzuga aloqasiz") for i in range(8)],
    ]),
}
```
Then in `_cbp_mode`, after `base.update(over)` and before `return base`, add:
```python
    base.setdefault("interaction_payload", _PAYLOAD_FOR[base["interaction_mode"]])
```

- [ ] **Step 4: Rewrite the synth `_CBP_MODE_PHASES` branch.** In `app/services/pipeline.py`, find the `if phase_name in _CBP_MODE_PHASES:` branch. Keep its existing skeleton render (the `_render_checkpoints_and_blocks`, DPE, sim lines). Just before `return "\n".join(out)`, insert the payload render:
```python
        payload = getattr(parsed, "interaction_payload", None)
        mode = getattr(parsed, "interaction_mode", "")
        if payload is not None:
            if mode == "memory_match":
                out.append("\n**Pairs to match:**")
                for p in payload.pairs:
                    out.append(f"   - {p.left} ↔ {p.right}")
            elif mode == "jigsaw":
                out.append(f"\n**Pieces** (assemble via: {', '.join(payload.allowed_assembly_types)}):")
                for pc in payload.pieces:
                    out.append(f"   - `{pc.id}` {pc.content}")
            elif mode == "sentence_fill":
                out.append(f"\n**Sentence:** {payload.sentence}")
                for j, ch in enumerate(payload.chips):
                    out.append(f"   - {chr(97 + j)}) {ch.label}")
                correct = next((c for c in payload.chips if c.is_correct), None)
                if correct is not None:
                    out.append(f"   - {_teacher(f'Correct chip: {correct.label}')}")
                for ch in payload.chips:
                    if not ch.is_correct and ch.reason:
                        out.append(f"   - {_teacher(f'{ch.label} — {ch.reason}')}")
            elif mode == "tictactoe":
                out.append("\n**Decision grid (3×3):**")
                for r in range(3):
                    row = payload.cells[r * 3:r * 3 + 3]
                    out.append("   | " + " | ".join(c.label for c in row) + " |")
                for c in payload.cells:
                    if c.is_correct:
                        out.append(f"   - {_teacher(f'Correct cell: {c.label}')}")
                    elif c.reason:
                        out.append(f"   - {_teacher(f'{c.label} — {c.reason}')}")
        return "\n".join(out)
```
(If the branch already ends in `return "\n".join(out)`, replace that final return with the block above.)

- [ ] **Step 5: Add tests.** Append to `tests/schemas/test_practice_games_schemas.py`:
```python
def test_cbp_mode_requires_payload_matching_mode() -> None:
    # jigsaw mode with a memory_match payload must fail
    with pytest.raises(ValidationError):
        CbpModeGame(**_cbp_mode(interaction_mode="jigsaw",
                                interaction_payload=_PAYLOAD_FOR["memory_match"]))


def test_memory_match_payload_pair_count() -> None:
    from app.schemas.practice_games import MemoryMatchPayload
    with pytest.raises(ValidationError):
        MemoryMatchPayload(pairs=[dict(left="a", right="b")])  # only 1, need 4-8


def test_sentence_fill_requires_exactly_one_correct_chip() -> None:
    from app.schemas.practice_games import SentenceFillPayload
    with pytest.raises(ValidationError):
        SentenceFillPayload(sentence="x _____", chips=[
            dict(label="a", is_correct=True), dict(label="b", is_correct=True),
            dict(label="c", is_correct=False)])


def test_tictactoe_requires_nine_cells_and_a_correct() -> None:
    from app.schemas.practice_games import TicTacToePayload
    with pytest.raises(ValidationError):
        TicTacToePayload(cells=[dict(label=f"c{i}", is_correct=False) for i in range(9)])  # none correct
    with pytest.raises(ValidationError):
        TicTacToePayload(cells=[dict(label=f"c{i}", is_correct=(i == 0)) for i in range(4)])  # not 9


def test_cbp_mode_valid_with_each_mode_payload() -> None:
    for mode in ("memory_match", "jigsaw", "sentence_fill", "tictactoe"):
        g = CbpModeGame(**_cbp_mode(interaction_mode=mode))
        assert g.interaction_mode == mode
        assert g.interaction_payload is not None
```
Ensure `ValidationError` + `CbpModeGame` are imported in the file (they are).

- [ ] **Step 6: Run the full suite.** `.venv/Scripts/python.exe -m pytest tests/ -q` → PASS. (The existing parametrized `_cbp_mode` mode tests + synth tests must stay green — they now get matching payloads.)

- [ ] **Step 7: Commit.**
```bash
git add app/schemas/practice_games.py app/services/pipeline.py tests/schemas/test_practice_games_schemas.py
git commit -m "feat(practice-games): required typed interaction_payload per CBP-mode game"
```

---

### Task 2: Instruct the payload in the 11 CBP-mode game prompts

**Files (modify each):**
`prompts/biology/practice-memory-match.md`, `prompts/english/practice-memory-match.md`, `prompts/history/practice-memory-match.md`, `prompts/english/practice-sentence.md`, `prompts/geometriya-g7-11/practice-jigsaw.md`, `prompts/history/practice-jigsaw.md`, `prompts/math-algebra/practice-jigsaw.md`, `prompts/geometriya-g7-11/practice-tictactoe.md`, `prompts/kimyo-g7-11/practice-tictactoe.md`, `prompts/math-algebra/practice-tictactoe.md`, `prompts/physics/practice-tictactoe.md`

- [ ] **Step 1: Add an `## Interaction payload (required)` section to each file**, matching the file's game. Preserve existing content (CBP skeleton/learning-blocks/why_wrong_fails instructions).

memory-match files:
```markdown
## Interaction payload (required)

Emit `interaction_payload` = `{ "pairs": [ {left, right}, … ] }` with **4–8 pairs**. Each pair is a valid match drawn from the lesson (e.g. term ↔ meaning). Pairs must be matchable only by understanding the source concept, not by surface cues.
```

jigsaw files:
```markdown
## Interaction payload (required)

Emit `interaction_payload` = `{ "pieces": [ {id, content}, … ], "allowed_assembly_types": [ … ] }` with **3–6 pieces** and **1–3** assembly-relationship labels (the relationship types the student may use to connect pieces). Pieces and relationships must come from the lesson's source concepts.
```

sentence file (english/practice-sentence):
```markdown
## Interaction payload (required)

Emit `interaction_payload` = `{ "sentence": "...", "chips": [ {label, is_correct, reason}, … ] }`. The `sentence` contains the broken/blank span. Provide **≥3 chips**, EXACTLY ONE `is_correct: true`; each wrong chip's `reason` names why it fails (grammatically possible but wrong meaning/register, too broad/narrow, irrelevant).
```

tictactoe files:
```markdown
## Interaction payload (required)

Emit `interaction_payload` = `{ "cells": [ {label, is_correct, reason}, … ] }` with **exactly 9 cells** (a 3×3 decision grid). Each cell is a candidate action/answer; at least one `is_correct: true`. Every wrong cell's `reason` names why it fails. Cells must be solvable only through the lesson concept, not general intuition.
```

- [ ] **Step 2: Sanity-check prompts load.** `.venv/Scripts/python.exe -m pytest tests/ -k "prompt_coverage or practice_arc_flow or flow" -q` → PASS.

- [ ] **Step 3: Commit.**
```bash
git add prompts/biology/practice-memory-match.md prompts/english/practice-memory-match.md prompts/history/practice-memory-match.md prompts/english/practice-sentence.md prompts/geometriya-g7-11/practice-jigsaw.md prompts/history/practice-jigsaw.md prompts/math-algebra/practice-jigsaw.md prompts/geometriya-g7-11/practice-tictactoe.md prompts/kimyo-g7-11/practice-tictactoe.md prompts/math-algebra/practice-tictactoe.md prompts/physics/practice-tictactoe.md
git commit -m "feat(practice-games): instruct interaction_payload in 11 CBP-mode game prompts"
```

---

### Task 3: Live smoke + worklog + push

**Files:**
- Temporary (throwaway, `rm` after): `verify_game.py`
- Modify: `docs/memory/MASTER_MEMORY.md`, `docs/memory/INDEX.md`

- [ ] **Step 1: Full suite green.** `.venv/Scripts/python.exe -m pytest tests/ -q` → PASS.

- [ ] **Step 2: Live game smoke.** Throwaway `verify_game.py` mirroring prior smokes: lesson_context from job `c6457a80` extract `phase_output` id `57e55ade-f0b3-4384-a9a8-a78f056900ad`; `agent.run_phase_prompt_structured(provider="claude", model="claude-sonnet-4-6", phase_prompt=get_prompt("geometriya-g7-11","practice-jigsaw"), response_schema=CbpModeGame, lesson_context=..., prior_outputs={}, difficulty="hard", phase_name="practice-jigsaw", homework_job_id=JOB)`. Print: interaction_mode, payload type, the payload contents (pieces + allowed types), output_tokens, and whether it validated. Run with PYTHONUTF8=1 + the standard env. Expected: a `JigsawPayload` matching mode, valid, no exception, and note the output_tokens/duration (watch the heaviness risk). If it trips the 32k ceiling, record it and flag for a follow-up (lighter game skeleton).

- [ ] **Step 3: Remove the throwaway.** `rm verify_game.py`

- [ ] **Step 4: Worklog 0018.** Append `## [0018]` to `docs/memory/MASTER_MEMORY.md` (payloads added, schema+synth+11 prompts, smoke result incl. token/duration) + one-line pointer in `docs/memory/INDEX.md`. Note WS5 (language contract) is the last remaining.

- [ ] **Step 5: Commit + push.**
```bash
git add docs/memory/MASTER_MEMORY.md docs/memory/INDEX.md
git commit -m "docs: worklog 0018 — practice-game interaction payloads"
git push origin Nggaev-v2
```

---

## Self-Review

**Spec coverage:** §3 schema → Task 1 Steps 1-2,5. §4 synth → Task 1 Step 4. §5 prompts → Task 2. §6 tests → Task 1 Steps 3,5,6. §7 back-compat → no task. §8 risk (heaviness) → Task 3 Step 2 smoke is the gate. §9 acceptance → Task 3. Covered.

**Placeholder scan:** No TBD/TODO. Task 2 gives exact per-game sections. Task 3 smoke described by behavior + ids + command.

**Type consistency:** `GameChoice` (`label`/`is_correct`/`reason`), `MemoryMatchPair` (`left`/`right`), `JigsawPiece` (`id`/`content`), payload field names (`pairs`/`pieces`/`allowed_assembly_types`/`sentence`/`chips`/`cells`) and `_PAYLOAD_TYPE_FOR_MODE`/`_PAYLOAD_FOR` keys match across schema (Task 1.1-2), synth (Task 1.4: `payload.pairs`, `payload.pieces`, `payload.allowed_assembly_types`, `payload.sentence`, `payload.chips`, `payload.cells`), fixture (Task 1.3), tests (Task 1.5), and the smoke (Task 3).
