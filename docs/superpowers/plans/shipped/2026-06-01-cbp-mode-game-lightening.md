# CBP-Mode Game Lightening (Path B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the 4 CBP-mode games (memory-match/tictactoe/jigsaw/sentence) fast to generate by replacing the inherited full Case-Based-Preview shell with a compact standalone `CbpModeGame` (instruction + typed payload + a lightweight `why_prompt`), then re-add one subject-matched game to the flow.

**Architecture:** `CbpModeGame` stops inheriting `CaseBasedPreview` and becomes a small `BaseModel` keeping only the game board + a single open reasoning prompt. `flows.flow_for` inserts a per-subject game (`SUBJECT_GAME` map) as the 8th… (6th) practice phase. The 4 game prompts are authored compact in `prompts/_general/` (using the shipped `{{LANGUAGE_RULES}}` token). No DB migration (JSONB; no CBP-mode rows exist).

**Tech Stack:** Python 3.13, Pydantic, pytest, `uv`. Spec: `docs/superpowers/specs/2026-06-01-cbp-mode-game-lightening-design.md` (NOTE: the spec says "DPE"; this plan supersedes that with the lighter `why_prompt` per the approved reasoning-step revision).

---

## Conventions
- Tests: `"C:/Users/Recruiter/AppData/Roaming/Python/Python314/Scripts/uv.exe" run python -m pytest <args>`.
- Branch: `Nggaev-v2` directly (no worktree; another session shares the repo).
- Baseline before starting: `uv run python -m pytest tests/ -q` green (was 226 after the English workstream; re-confirm).
- **Never edit `prompts/<subject>/*`** (read-only references). Only `prompts/_general/*`.

## File Structure
- Modify: `app/schemas/practice_games.py` (lighten `CbpModeGame`)
- Modify: `app/services/flows.py` (`SUBJECT_GAME` + 8-phase `flow_for`; restore game `PHASE_DEPS`; drop public `GENERAL_FLOW`)
- Modify: `app/services/pipeline.py` (`_CBP_MODE_PHASES` synth render → compact shape)
- Create: 4× `prompts/_general/practice-{memory-match,tictactoe,jigsaw,sentence}.md`
- Modify tests: `tests/schemas/test_practice_games_schemas.py`, `tests/services/test_general_flow.py`, `tests/services/test_prompt_coverage.py`, `tests/services/test_practice_arc_synth.py`

---

## Task 1: Lighten the `CbpModeGame` schema

**Files:**
- Modify: `app/schemas/practice_games.py`
- Test: `tests/schemas/test_practice_games_schemas.py`

- [ ] **Step 1: Write the failing tests** — REPLACE the existing `CbpModeGame` test cases in `tests/schemas/test_practice_games_schemas.py` with these (delete any test that builds the OLD full-CBP `CbpModeGame` with `case_setup`/`checkpoints`/`decision_process_explanation`):

```python
from app.schemas.practice_games import (
    CbpModeGame, MemoryMatchPayload, MemoryMatchPair, TicTacToePayload, GameChoice,
)


def _ttt_cells():
    return [GameChoice(label=f"c{i}", is_correct=(i == 0)) for i in range(9)]


def test_cbp_mode_game_compact_valid():
    g = CbpModeGame(
        title="Match the terms",
        source_concept_ids=["c1"],
        interaction_mode="memory_match",
        instruction="Match each term to its meaning.",
        interaction_payload=MemoryMatchPayload(
            pairs=[MemoryMatchPair(left=f"L{i}", right=f"R{i}") for i in range(4)]),
        why_prompt="Explain why these pair up — the concept, the link, the trap.",
    )
    assert g.interaction_mode == "memory_match"
    assert g.why_prompt
    # No full-CBP fields anymore:
    assert not hasattr(g, "checkpoints") and not hasattr(g, "case_setup")
    assert not hasattr(g, "decision_process_explanation")


def test_cbp_mode_game_requires_concept_ids_instruction_why():
    import pytest
    base = dict(title="T", interaction_mode="tictactoe",
                interaction_payload=TicTacToePayload(cells=_ttt_cells()))
    with pytest.raises(Exception):
        CbpModeGame(**base, source_concept_ids=[], instruction="i", why_prompt="w")  # empty ids
    with pytest.raises(Exception):
        CbpModeGame(**base, source_concept_ids=["c1"], instruction="", why_prompt="w")  # empty instruction
    with pytest.raises(Exception):
        CbpModeGame(**base, source_concept_ids=["c1"], instruction="i", why_prompt="")  # empty why


def test_cbp_mode_payload_must_match_mode():
    import pytest
    with pytest.raises(Exception):
        CbpModeGame(
            title="T", source_concept_ids=["c1"], interaction_mode="tictactoe",
            instruction="i", why_prompt="w",
            interaction_payload=MemoryMatchPayload(
                pairs=[MemoryMatchPair(left=f"L{i}", right=f"R{i}") for i in range(4)]),
        )
```

- [ ] **Step 2: Run, verify FAIL** — `uv run python -m pytest tests/schemas/test_practice_games_schemas.py -v` → fails (old `CbpModeGame` still inherits CBP, requires `case_setup` etc.; `why_prompt` not a field).

- [ ] **Step 3: Rewrite `CbpModeGame` in `app/schemas/practice_games.py`.**
Find `class CbpModeGame(CaseBasedPreview):` (currently ~line 211) and replace the whole class with:

```python
class CbpModeGame(BaseModel):
    """Compact interaction-mode practice game (Path B). NOT a full Case-Based
    Preview — the standalone `case-based-preview` phase delivers the learning
    case in the flow. Here: the game board + one lightweight reasoning prompt.
    """
    title: str = Field(min_length=1)
    source_concept_ids: list[str] = Field(min_length=1)   # source fidelity
    interaction_mode: PracticeInteractionMode
    instruction: str = Field(min_length=1)                # 1–2 sentences: the task
    interaction_payload: Union[
        MemoryMatchPayload, JigsawPayload, SentenceFillPayload, TicTacToePayload
    ]
    why_prompt: str = Field(min_length=1)                 # open "explain your reasoning" step
    expected_reasoning_keywords: list[str] = Field(default_factory=list)

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

Then DELETE the now-unused import at the top of the file: `from app.schemas.flow_v2 import CaseBasedPreview` (run `grep -n "CaseBasedPreview" app/schemas/practice_games.py` — after the class rewrite there should be ZERO references; if so, delete the import line). Do NOT add a `DecisionProcessExplanation` import — `why_prompt` is a plain str.

- [ ] **Step 4: Run, verify PASS** — `uv run python -m pytest tests/schemas/test_practice_games_schemas.py -v` → all pass.

- [ ] **Step 5: Check for breakage** — `uv run python -c "from app.schemas.practice_games import CbpModeGame; print('OK')"` and `uv run python -m pytest tests/ -q 2>&1 | tail -8`. Some `test_practice_arc_synth.py` / flow tests may now fail (they assume the old shape / old flow) — note which; they're fixed in Tasks 3–4. Report any failure NOT in those files.

- [ ] **Step 6: Commit**
```
git add app/schemas/practice_games.py tests/schemas/test_practice_games_schemas.py
git commit -m "feat(flow-v2): lighten CbpModeGame off full CBP (payload + why_prompt)"
```

---

## Task 2: Author the 4 compact game prompts

**Files:** Create `prompts/_general/practice-{memory-match,tictactoe,jigsaw,sentence}.md`.

For EACH: read the existing subject prompt for that game (read-only, e.g. `prompts/biology/practice-memory-match.md`) as a reference for the mechanic, but author the COMPACT shape. Each prompt instructs the model to emit a `CbpModeGame`:
- `title`, `source_concept_ids` (≥1, from the source map; "do not invent"), the `interaction_mode` LITERAL for this file, a 1–2 sentence `instruction`, the typed `interaction_payload`, a `why_prompt` (one open question: "explain your reasoning — the concept, the method/link, the mistake avoided"), and optional `expected_reasoning_keywords`.
- **Do NOT** instruct case_setup / 3 checkpoints / learning blocks / final_simulation / feedback_summary / completion_rules (those fields are gone).
- Payload constraints per mode: memory_match → `pairs` 4–8 `{left,right}`; jigsaw → `pieces` 3–6 `{id,content}` + `allowed_assembly_types` 1–3; sentence_fill → `sentence` + `chips` ≥3 (exactly one `is_correct`); tictactoe → `cells` exactly 9 (≥1 `is_correct`). Each `GameChoice` may carry a `reason`.
- `{{SUBJECT}}`-parameterized. **Use the `{{LANGUAGE_RULES}}` token** for the language directive (do NOT hardcode Uzbek) — so `english`→`practice-sentence` emits English-target content automatically.

- [ ] **Step 1: Author the 4 files** per the above (one `## Output format` JSON example each, matching the compact `CbpModeGame`).

- [ ] **Step 2: Verify** — `ls prompts/_general/practice-{memory-match,tictactoe,jigsaw,sentence}.md` (4 files); `grep -L "{{SUBJECT}}" prompts/_general/practice-{memory-match,tictactoe,jigsaw,sentence}.md` and `grep -L "{{LANGUAGE_RULES}}" ...` → both print nothing (all 4 have both tokens); `grep -l "checkpoint\|learning_block\|decision_process_explanation\|case_setup" prompts/_general/practice-{memory-match,tictactoe,jigsaw,sentence}.md` → nothing (no full-CBP leftovers).

- [ ] **Step 3: Commit**
```
git add prompts/_general/practice-memory-match.md prompts/_general/practice-tictactoe.md prompts/_general/practice-jigsaw.md prompts/_general/practice-sentence.md
git commit -m "feat(flow-v2): compact CBP-mode game prompts (payload + why_prompt)"
```

---

## Task 3: Re-add the subject game to the flow

**Files:**
- Modify: `app/services/flows.py`
- Test: `tests/services/test_general_flow.py`, `tests/services/test_prompt_coverage.py`

- [ ] **Step 1: Update the failing tests** in `tests/services/test_general_flow.py` — the two Path-A tests INVERT. Replace `test_flow_is_identical_for_every_subject` and `test_phase_deps_have_no_reading_or_cbp_mode_games` with:

```python
def test_flow_is_8_phases_with_subject_game():
    base = ["case-based-preview", "flashcards", "memory-check",
            "practice-rlc", "practice-error-detection"]
    tail = ["boss-arena", "reflection"]
    for subject in flows.SUPPORTED_SUBJECTS:
        seq = flows.flow_for(subject)
        assert len(seq) == 8
        assert seq[:5] == base and seq[6:] == tail
        assert seq[5] == flows.SUBJECT_GAME[subject]   # subject-matched game at position 5


def test_every_subject_game_is_registered_and_has_prompt():
    import pathlib
    from app.services.agent import STRUCTURED_PHASE_SCHEMAS
    gdir = pathlib.Path(flows.__file__).resolve().parents[2] / "prompts" / "_general"
    for subject, game in flows.SUBJECT_GAME.items():
        assert game in STRUCTURED_PHASE_SCHEMAS, f"{game} not registered"
        assert (gdir / f"{game}.md").is_file(), f"{game}.md missing in _general"


def test_phase_deps_have_no_reading_but_have_games():
    assert "reading" not in flows.PHASE_DEPS
    for game in ("practice-memory-match", "practice-tictactoe",
                 "practice-jigsaw", "practice-sentence"):
        assert game in flows.PHASE_DEPS
```

Also, in `tests/services/test_prompt_coverage.py`, change the pair list from iterating `flows.GENERAL_FLOW` to iterating `flows.flow_for(s)`:
```python
_PAIRS = [(s, p) for s in flows.SUPPORTED_SUBJECTS for p in flows.flow_for(s)]
```

- [ ] **Step 2: Run, verify FAIL** — `uv run python -m pytest tests/services/test_general_flow.py -v` → fails (`SUBJECT_GAME` doesn't exist; `flow_for` returns 7).

- [ ] **Step 3: Edit `app/services/flows.py`.** Remove the public `GENERAL_FLOW` constant (it would no longer match any runnable flow). Add `SUBJECT_GAME` and rewrite `flow_for`:

```python
SUBJECT_GAME = {
    "biology": "practice-memory-match",
    "history": "practice-memory-match",
    "physics": "practice-tictactoe",
    "kimyo-g7-11": "practice-tictactoe",
    "math-algebra": "practice-tictactoe",
    "geometriya-g7-11": "practice-jigsaw",
    "english": "practice-sentence",
}

_BASE_PHASES = ["case-based-preview", "flashcards", "memory-check",
                "practice-rlc", "practice-error-detection"]


def flow_for(subject: str) -> list[str]:
    if subject not in SUBJECTS:
        raise KeyError(f"Unsupported subject: {subject}")
    return [*_BASE_PHASES, SUBJECT_GAME[subject], "boss-arena", "reflection"]
```

Add the 4 game entries to `PHASE_DEPS` (keep the existing entries):
```python
    "practice-memory-match":  ["flashcards", "memory-check"],
    "practice-tictactoe":     ["case-based-preview", "flashcards"],
    "practice-jigsaw":        ["case-based-preview", "flashcards"],
    "practice-sentence":      ["case-based-preview", "flashcards"],
```

- [ ] **Step 4: Run** — `uv run python -m pytest tests/services/test_general_flow.py tests/services/test_prompt_coverage.py -v` → all pass.
Then `grep -rn "GENERAL_FLOW" app/ tests/` → ZERO hits (the constant is gone and nothing references it). If any test/file still imports `GENERAL_FLOW`, update it to `flow_for(...)`.

- [ ] **Step 5: Commit**
```
git add app/services/flows.py tests/services/test_general_flow.py tests/services/test_prompt_coverage.py
git commit -m "feat(flow-v2): re-add subject-matched CBP-mode game to flow (SUBJECT_GAME)"
```

---

## Task 4: Compact synth render

**Files:**
- Modify: `app/services/pipeline.py` (the `if phase_name in _CBP_MODE_PHASES:` branch, ~line 287)
- Test: `tests/services/test_practice_arc_synth.py`

- [ ] **Step 1: Update/replace the synth test** for CBP-mode games in `tests/services/test_practice_arc_synth.py` (find the test that renders a `CbpModeGame`). It should build a compact `CbpModeGame` (as in Task 1's test) and assert the rendered markdown contains the `title`, the `instruction`, a payload marker (e.g. a pair's `left`/`right`, or "3x3"/cells for tictactoe), and the `why_prompt` text — and does NOT crash / does NOT mention "checkpoint"/"learning block".

- [ ] **Step 2: Run, verify FAIL or degraded** — `uv run python -m pytest tests/services/test_practice_arc_synth.py -v`. (The current branch reads `getattr(parsed, "checkpoints", None)` etc. — confirmed at `pipeline.py:287-291` — so with the new shape it won't crash but renders "0 checkpoints", empty role; the updated test asserting the compact content will FAIL.)

- [ ] **Step 3: Rewrite the `_CBP_MODE_PHASES` branch** of `_synth_md_for_structured` (around `pipeline.py:287`). Replace the old (case/checkpoints/DPE) rendering with the compact shape:

```python
    if phase_name in _CBP_MODE_PHASES:
        mode = getattr(parsed, "interaction_mode", "") or ""
        title = getattr(parsed, "title", None) or "Practice Game"
        out = [f"## {title} _(mode: {mode})_", getattr(parsed, "instruction", "") or ""]
        payload = getattr(parsed, "interaction_payload", None)
        if mode == "memory_match" and payload is not None:
            for pr in getattr(payload, "pairs", []):
                out.append(f"- {pr.left} ↔ {pr.right}")
        elif mode == "jigsaw" and payload is not None:
            for pc in getattr(payload, "pieces", []):
                out.append(f"- [{pc.id}] {pc.content}")
            ats = getattr(payload, "allowed_assembly_types", [])
            if ats:
                out.append(f"_Assembly types: {', '.join(ats)}_")
        elif mode == "sentence_fill" and payload is not None:
            out.append(getattr(payload, "sentence", "") or "")
            for ch in getattr(payload, "chips", []):
                mark = " ✓" if getattr(ch, "is_correct", False) else ""
                out.append(f"- {ch.label}{mark}")
        elif mode == "tictactoe" and payload is not None:
            cells = getattr(payload, "cells", [])
            for r in range(0, 9, 3):
                row = cells[r:r + 3]
                out.append(" | ".join(f"{c.label}{'✓' if c.is_correct else ''}" for c in row))
        cids = getattr(parsed, "source_concept_ids", None) or []
        if cids:
            out.append(f"_concepts: {', '.join(cids)}_")
        why = getattr(parsed, "why_prompt", "") or ""
        if why:
            out.append(f"\n**Reasoning:** {why}")
        return "\n".join(out)
```

- [ ] **Step 4: Run, verify PASS** — `uv run python -m pytest tests/services/test_practice_arc_synth.py -v` → pass.

- [ ] **Step 5: Commit**
```
git add app/services/pipeline.py tests/services/test_practice_arc_synth.py
git commit -m "feat(flow-v2): compact CBP-mode game synth render"
```

---

## Task 5: Full verify + real CBP-mode smoke + worklog

**Files:** (verification); `docs/memory/*`

- [ ] **Step 1: Full suite green** — `uv run python -m pytest tests/ -q 2>&1 | tail -5` → 0 failures/errors; record count.

- [ ] **Step 2: Real claude smoke — the acceptance gate (heaviness proof).** Write throwaway `smoke_game.py` at repo root that builds `get_prompt("physics", "practice-tictactoe")`, feeds a small Uzbek physics `lesson_context` (e.g. Nyuton II, F=ma) + a 3-concept source-map digest, runs it through the real `claude` CLI via the structured runner (see `pipeline.py::_execute_phase`; pass `homework_job_id=None`/`phase_output_id=None`), `model_validate`s against `CbpModeGame`, and TIMES it. Run `uv run python smoke_game.py`. Confirm: (a) schema-valid (compact shape: instruction + 9-cell payload + why_prompt + concept_ids), and (b) **wall-clock is practical — target ≲ ~5 min, definitely not >20** (this is the proof the lightening worked). DELETE `smoke_game.py` (do not commit). If still slow, STOP and report — the §8 fallback (drop `why_prompt` → payload-only) is the lever, but only if the smoke shows it's needed.

- [ ] **Step 3: Worklog** — append the next-ID entry to `docs/memory/MASTER_MEMORY.md` + `INDEX.md` row (style per entry 0020). Record: `CbpModeGame` lightened off `CaseBasedPreview` (payload + `why_prompt`, NOT full DPE — the reviewer/decision change); `SUBJECT_GAME` re-added (8-phase flow); 4 compact `_general` game prompts using `{{LANGUAGE_RULES}}`; synth rewrite; commits; suite count + smoke wall-clock result. Note the Gamified-Practices specs define these as full CBPs — this is the deliberate, measured deviation.

- [ ] **Step 4: Commit**
```
git add docs/memory/MASTER_MEMORY.md docs/memory/INDEX.md
git commit -m "docs(memory): worklog — CBP-mode game lightening (Path B) shipped"
```

---

## Self-Review (done while writing)
- **Spec coverage:** schema lighten §3 (with why_prompt revision) → Task 1; flow `SUBJECT_GAME`/`flow_for`/PHASE_DEPS + the 2 inverted Path-A tests + the `GENERAL_FLOW`-removal directive → Task 3; 4 compact prompts w/ `{{LANGUAGE_RULES}}` §5 → Task 2; synth §6 (getattr-degrades correction reflected) → Task 4; tests + real heaviness smoke §7 → Tasks 1–5; deliberate-deviation + dead-import + measure-first-DPE → Tasks 1/5. All covered.
- **Placeholders:** none — schema, flow, synth code shown in full; prompt step gives exact fields/constraints (prose authoring, verified by greps + the smoke).
- **Consistency:** `CbpModeGame`, `SUBJECT_GAME`, `_BASE_PHASES`, `flow_for`, `why_prompt`, `interaction_payload`, `_payload_matches_mode` used identically across tasks. No `decision_process_explanation`/`GENERAL_FLOW` survive.
- **No subject-prompt edits:** Task 2 only reads them.
