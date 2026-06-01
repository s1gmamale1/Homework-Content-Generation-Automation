# CBP-Mode Game Lightening (Path B) — Design Spec

**Status:** Approved design (brainstormed 2026-06-01). Ready for an implementation plan.
**Follows:** the General Prompts MVP / Path A (worklog 0019, `docs/nets_general_prompts_mvp_design.md`).
**Motivation:** worklog 0018 — a single CBP-mode game (`practice-jigsaw`) ran >21 min and was killed, because `CbpModeGame` inherits the *entire* `CaseBasedPreview` shell. This makes the 4 CBP-mode games un-shippable. Path A shipped without them (RLC + Error Detection only); Path B brings them back in a generatable form.
**Live anchors:** `app/schemas/practice_games.py` (`CbpModeGame`), `app/schemas/flow_v2.py` (`DecisionProcessExplanation`), `app/services/flows.py` (`GENERAL_FLOW`/`flow_for`), `app/services/pipeline.py` (`_synth_md_for_structured`, `_CBP_MODE_PHASES`), `app/services/agent.py` (`STRUCTURED_PHASE_SCHEMAS`).

---

## 1. Goal & non-goals

**Goal.** Make the 4 CBP-mode games (Memory Match, TicTacToe, Jigsaw, Sentence Fill) **fast to generate** by replacing the inherited full-CBP shell with a compact standalone schema (**Payload + DPE**), then re-add **one subject-matched game** to the flow and author the 4 general prompts.

**Non-goals.**
- No change to the 2 standalone games (`RealLifeChallenge`, `ErrorDetection`) — they're already light and shipped in Path A.
- No DB migration (the `practice_*_json` columns are JSONB; only the stored shape changes, and no real CBP-mode rows exist yet).
- No new phases, no easy/hard, no classify (Path A invariants hold).
- No frontend.

---

## 2. Decision (locked in brainstorming)

`CbpModeGame` keeps: `title`, `source_concept_ids`, `interaction_mode`, `interaction_payload`, a brief `instruction`, and **one `decision_process_explanation` (DPE)**. It drops the full CBP shell. The DPE is retained deliberately: the game board alone is recognition/matching (which New_Flow forbids as "fake interaction / memorization-focused"); the DPE is the one production-reasoning step that keeps the game a legitimate practice. Per-choice feedback already lives in `GameChoice.reason`, so no separate feedback object.

---

## 3. Schema (`app/schemas/practice_games.py`)

`CbpModeGame` stops inheriting `CaseBasedPreview` and becomes standalone. The payload types (`GameChoice`, `MemoryMatchPayload`, `JigsawPayload`, `SentenceFillPayload`, `TicTacToePayload`), `PracticeInteractionMode`, `_PAYLOAD_TYPE_FOR_MODE`, and the `_payload_matches_mode` validator are **unchanged**.

```python
from app.schemas.flow_v2 import DecisionProcessExplanation  # reuse, do not redefine

class CbpModeGame(BaseModel):
    """A compact interaction-mode practice game (Path B). NOT a full Case-Based
    Preview — the standalone `case-based-preview` phase already delivers the
    learning case in the flow. Here: the game board + one open reasoning step.
    """
    title: str = Field(min_length=1)
    source_concept_ids: list[str] = Field(min_length=1)   # source fidelity
    interaction_mode: PracticeInteractionMode
    instruction: str = Field(min_length=1)                # 1–2 sentences: the task
    interaction_payload: Union[
        MemoryMatchPayload, JigsawPayload, SentenceFillPayload, TicTacToePayload
    ]
    decision_process_explanation: DecisionProcessExplanation

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

Dropped fields (were inherited from `CaseBasedPreview`): `student_role`, `case_type`, `case_setup`, `checkpoints`, `learning_block_1`, `learning_block_2`, `final_simulation`, `feedback_summary`, `completion_rules`.

**REASONING-STEP REVISION (supersedes "DPE" everywhere below):** per the reviewer's heaviness finding + decision, the game's reasoning step is a **lightweight `why_prompt: str` (+ optional `expected_reasoning_keywords`)** — the same field RLC/Error-Detection use — NOT the full `DecisionProcessExplanation`. This avoids the prime remaining heaviness slot and the duplicate-DPE-per-packet problem. **Wherever §3/§5/§6/§7/§9 say "DPE"/`decision_process_explanation`, read `why_prompt`.** The plan `docs/superpowers/plans/2026-06-01-cbp-mode-game-lightening.md` is authoritative on the final shape.

**Also remove the now-dead import:** once `CbpModeGame(CaseBasedPreview)` → `(BaseModel)`, the existing `from app.schemas.flow_v2 import CaseBasedPreview` in `practice_games.py` is unused (only `CbpModeGame` referenced it) — delete it. (No `DecisionProcessExplanation` import is needed, since the reasoning step is now a plain `why_prompt`.)

`STRUCTURED_PHASE_SCHEMAS` registration for the 4 phases is unchanged (still → `CbpModeGame`, new shape).

---

## 4. Flow (`app/services/flows.py`)

Re-introduce subject variation for the 3rd practice game. Build the 8-phase sequence from a base list (made private — see the directive below); add a `SUBJECT_GAME` map; `flow_for` inserts the subject's game.

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

def flow_for(subject: str) -> list[str]:
    if subject not in SUBJECTS:
        raise KeyError(f"Unsupported subject: {subject}")
    return [
        "case-based-preview", "flashcards", "memory-check",
        "practice-rlc", "practice-error-detection", SUBJECT_GAME[subject],
        "boss-arena", "reflection",
    ]
```

Restore the 4 games' `PHASE_DEPS` (they depend on the learning sections):
```python
"practice-memory-match":  ["flashcards", "memory-check"],
"practice-tictactoe":     ["case-based-preview", "flashcards"],
"practice-jigsaw":        ["case-based-preview", "flashcards"],
"practice-sentence":      ["case-based-preview", "flashcards"],
```
(Add these to the existing `PHASE_DEPS`; keep the Path A entries.) **Directive (not implementer's choice): do NOT leave a public `GENERAL_FLOW` constant that no longer matches any runnable flow.** Once `flow_for` inserts `SUBJECT_GAME`, the 7-phase `GENERAL_FLOW` is what *no* subject runs — a readability trap. Either inline the base list into `flow_for` or rename it to a private `_BASE_PHASES`, so nothing imports `GENERAL_FLOW` thinking it's the runnable flow. `flow_for` must return the 8-phase sequence above, and every `SUBJECT_GAME` value must be a real prompt + registered schema.

---

## 5. Prompts (`prompts/_general/`, 4 new files)

Author `practice-memory-match.md`, `practice-tictactoe.md`, `practice-jigsaw.md`, `practice-sentence.md` to the **compact** shape. Seed by generalizing the existing subject prompts for these games (read-only; e.g. `prompts/biology/practice-memory-match.md`) but **strip the full-CBP scaffolding** (case_setup / 3 checkpoints / learning blocks / simulation / feedback_summary / completion_rules) — keep only: title, `source_concept_ids`, `interaction_mode` literal, a brief `instruction`, the `interaction_payload` (mode-correct shape + constraints), and the `decision_process_explanation` (options:null, concept·method·mistake, min_chars 60). `{{SUBJECT}}`-parameterized, and **use the `{{LANGUAGE_RULES}}` token for the language directive** (shipped in worklog 0020 — do NOT hardcode "formal Uzbek"; this makes the `english`→`practice-sentence` game emit English-target content with an Uzbek bridge automatically, while other subjects stay Uzbek). Each must state its `interaction_mode` literal and the payload constraints (memory_match 4–8 pairs · jigsaw 3–6 pieces + 1–3 assembly types · sentence ≥3 chips exactly-one-correct · tictactoe exactly 9 cells ≥1 correct). Cross-check against the `Gamified Practices/*` specs for content rules, but emit the compact shape, not the full CBP.

---

## 6. Synth render (`app/services/pipeline.py`)

Update the `_CBP_MODE_PHASES` branch of `_synth_md_for_structured` to render the compact game: a `## {title} _(mode)_` heading, the `instruction`, the payload rendered per mode (pairs table / jigsaw pieces + assembly types / sentence + chips with the correct one marked / 3×3 grid of cells), the `source_concept_ids`, and the DPE prompt. It must no longer reference `checkpoints`/`learning_block`/`final_simulation`/`completion_rules`. **Correction:** the current branch reads these via `getattr(parsed, "checkpoints", None)` with defaults, so after the fields are dropped it would NOT raise — it would silently produce a degraded render (0 checkpoints, empty role). Rewrite it for correctness/cleanliness, not to avoid a crash.

---

## 7. Tests

- **Schema** (`tests/schemas/test_practice_games_schemas.py`): rewrite the `CbpModeGame` cases for the new shape — valid compact game per mode; `source_concept_ids` ≥1 enforced; `instruction` required; DPE required; `_payload_matches_mode` still rejects a payload/mode mismatch. Remove assertions that built the old full-CBP `CbpModeGame`.
- **Flow** (`tests/services/test_general_flow.py`): `flow_for(subject)` now returns the 8-phase sequence with the right `SUBJECT_GAME` per subject; the 4 game phases are in `PHASE_DEPS`; every `SUBJECT_GAME` value is registered in `STRUCTURED_PHASE_SCHEMAS` and has a `prompts/_general/` file. **Two Path-A tests in this file INVERT and must be updated:** (a) `test_flow_is_identical_for_every_subject` is no longer true — replace with a per-subject assertion (8 phases, the right `SUBJECT_GAME` inserted at position 5); (b) `test_phase_deps_have_no_reading_or_cbp_mode_games` — keep the "no `reading`" half, but the CBP-mode games are now *expected* in `PHASE_DEPS`, so drop/invert that half.
- **Coverage** (`tests/services/test_prompt_coverage.py`): now iterates the 8-phase `flow_for(subject)` (the 4 game prompts must resolve in `_general`).
- **Synth** (`tests/services/test_practice_arc_synth.py` if it covers CBP-mode): update to the compact render.
- **Smoke (the acceptance gate):** real `claude`-CLI generation of one CBP-mode game (e.g. **physics → practice-tictactoe**) → `model_validate`s against the new `CbpModeGame` AND completes in **practical wall-clock (target ≲ ~5 min, definitely not >20)**. This is the proof the lightening worked.
- Full suite green.

---

## 8. Risks & open items
- **Deliberate spec deviation:** Gamified Practices specs define these as full CBPs; we compact them on purpose (full-CBP-per-game proven impractical, worklog 0018). The standalone `case-based-preview` phase still provides the full learning case in the flow.
- **Old-shape data:** none exists (no CBP-mode job ever completed), so no migration/back-compat concern; the synth only needs to handle the new shape.
- **DPE is now the prime remaining heaviness suspect** — the one non-trivial slot left (requires `expected_components`, `rubric: dict`, `sample_acceptable_answer`, all no-default). Treat the ≲5-min target as **measure-first via the smoke, NOT an assumption**: if the compact game is still slow, fall back to "Payload only" (drop the DPE) — a one-field schema change — but only if the smoke shows it's needed.
- **Content note (awareness, not a defect):** after this lands, a packet has TWO open-ended DPE production prompts — one in `case-based-preview`, one in the game — plus RLC justifications and the boss Why→How→What. The game's DPE is justified (it keeps the game a real practice, not recognition), but note the DPE now appears twice per packet.

---

## 9. Acceptance
A job on each subject runs the 8-phase flow including its subject-matched CBP-mode game; that game generates schema-valid `CbpModeGame` (compact shape: instruction + payload + DPE + source_concept_ids) in practical wall-clock; the full suite is green; subject prompt files remain untouched; the 4 new prompts live in `prompts/_general/`.

---

## 10. Execution notes
On `Nggaev-v2` directly (per the project owner; no worktree). Build order: (1) schema lighten + schema tests, (2) the 4 `_general` prompts, (3) flows `SUBJECT_GAME` + `flow_for` + PHASE_DEPS + flow/coverage tests, (4) synth render + synth test, (5) full suite + real CBP-mode smoke, (6) worklog entry. Subagent-driven, two-stage review per task, like Path A.
