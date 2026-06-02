# Design — CBP Completeness (Flow v2 content reshape, workstream 1)

**Date:** 2026-05-30
**Branch:** Nggaev-v2
**Status:** Approved design → ready for implementation plan
**Scope:** content-generation only (no student-app / runtime concerns)

---

## 1. Goal

Make generated **Case-Based Preview (CBP)** content match the NETS CBP Generation Standard's required structure. Today our CBP runs `setup → C1 → C2 → C3 → DPE → simulation → feedback` — it **omits the two Learning Blocks** the standard mandates between checkpoints. The student is quizzed three times with no teaching in between.

Target structure (standard §5 / output template lines 293–303, 785–795):

```
1. Case setup
2. Checkpoint 1  (Identify)
3. Learning Block 1   ← ADD (short explanation of the concept)
4. Checkpoint 2  (Decide)
5. Learning Block 2   ← ADD (show the method)
6. Checkpoint 3  (Justify / avoid mistake)
7. Decision Process Explanation (DPE)
8. Final consequence / simulation
9. Feedback summary
10. Completion rules
```

Plus one small content-fidelity rule from the standard: the simulation's **`why_wrong_fails` becomes required** (today it can be blank, but the standard requires the wrong path to explain *why* it fails).

This is workstream 1 of a 5-part reshape (the others: flashcards 8-field shape, memory-check rigor, practice-game payloads, Uzbek language contract — each its own spec later).

## 2. Non-goals (explicitly out of scope here)

- `mistake_provenance` (source|inferred tagging) — **deferred** to a dedicated content-fidelity pass. As a defaulted field it would enforce nothing; doing it properly (required + prompted + validated) is its own small workstream.
- DPE "2–4 sentences" wording polish (currently proxied by `min_chars=60`).
- Practice-game *interaction payloads* (pairs/grid/pieces/chips) — workstream 4.
- Any student-app / runtime / scoring behavior.

## 3. Schema changes — `app/schemas/flow_v2.py`

**New model:**
```python
class LearningBlock(BaseModel):
    """Short teaching explanation between checkpoints (CBP standard §5, slots 3 & 5).
    LB1 explains the concept after Checkpoint 1; LB2 shows the method after Checkpoint 2.
    Text-first: visual_svg is optional and used only when a tiny diagram is essential."""
    explanation: str = Field(min_length=1)      # the short teaching text, grounded in the textbook
    title: Optional[str] = None
    visual_svg: Optional[str] = None             # optional inline SVG; prompt-discouraged (see Risks)
    source_concept_id: Optional[str] = None      # link to the SourceMap concept it teaches
```

**`CaseBasedPreview`** — add the two blocks between `checkpoints` and `decision_process_explanation` (field order chosen for readability; interleave order is enforced by prompt + renderer, not the type):
```python
checkpoints: list[CaseCheckpoint] = Field(min_length=3, max_length=3)
learning_block_1: LearningBlock                  # required
learning_block_2: LearningBlock                  # required
decision_process_explanation: DecisionProcessExplanation
final_simulation: CaseSimulation
...
```
Both **required** — the standard mandates exactly 2. `CbpModeGame(CaseBasedPreview)` inherits them automatically; this is correct because the four CBP-mode game specs (memory-match/jigsaw/sentence/tictactoe) also mandate LB1/LB2 in the same positions.

**`CaseSimulation.why_wrong_fails`** — change `= ""` to **required**:
```python
why_wrong_fails: str = Field(min_length=1)
```

## 4. Concept-fidelity collector — `app/services/pipeline.py`

Extend `_emitted_concept_ids(parsed)` so the existing source-fidelity check (plan §10) also sees the learning blocks' concept links:
```python
for lb_attr in ("learning_block_1", "learning_block_2"):
    lb = getattr(parsed, lb_attr, None)
    cid = getattr(lb, "source_concept_id", None) if lb else None
    if cid:
        ids.append(cid)
```
So an invented `learning_block.source_concept_id` is flagged like any other.

## 5. Synth render — `app/services/pipeline.py _synth_md_for_structured`

In **both** the `case-based-preview` branch and the `_CBP_MODE_PHASES` branch, interleave the blocks so the rendered markdown reads in standard order:
```
Checkpoint 1 → Learning Block 1 → Checkpoint 2 → Learning Block 2 → Checkpoint 3 → DPE → simulation → feedback
```
Render each block as `**Learning Block N** [title] explanation` and inline `visual_svg` when present. (Schema guarantees exactly 3 checkpoints + 2 blocks, so the interleave is deterministic.)

## 6. Prompt changes — 18 files

Each must (a) add Learning Block 1 (after C1) and Learning Block 2 (after C2) to its structure block + output instructions + embedded-JSON field guidance, and (b) ensure `why_wrong_fails` is instructed (now required).

- **7 CBP prompts:** `prompts/{biology,english,geometriya-g7-11,history,kimyo-g7-11,math-algebra,physics}/case-based-preview.md`
- **11 CBP-mode game prompts:** `prompts/biology/practice-memory-match.md`, `prompts/english/practice-memory-match.md`, `prompts/english/practice-sentence.md`, `prompts/geometriya-g7-11/practice-jigsaw.md`, `prompts/geometriya-g7-11/practice-tictactoe.md`, `prompts/history/practice-jigsaw.md`, `prompts/history/practice-memory-match.md`, `prompts/kimyo-g7-11/practice-tictactoe.md`, `prompts/math-algebra/practice-jigsaw.md`, `prompts/math-algebra/practice-tictactoe.md`, `prompts/physics/practice-tictactoe.md`

Prompt guidance for blocks: keep them **short and text-first**. `visual_svg` only if a tiny diagram is essential and not already shown — prefer referencing the existing case diagram. (Protects the token ceiling — see Risks.)

## 7. Testing (TDD)

**New tests:**
- `learning_block_1` / `learning_block_2` required → missing ⇒ `ValidationError`.
- `LearningBlock.explanation` required (empty ⇒ error); optional fields default `None`.
- `CaseSimulation.why_wrong_fails` required (empty ⇒ error).
- `_unknown_concept_ids` flags an invented `learning_block.source_concept_id`.
- Synth (both branches) renders Learning Block 1 & 2 in the correct interleaved order.

**Updated tests (13 construction sites):** every `CaseBasedPreview(...)` (6) and `CbpModeGame(...)` (7) across `tests/schemas/test_flow_v2_schemas.py`, `tests/schemas/test_practice_games_schemas.py`, `tests/services/test_practice_arc_synth.py` — add the two blocks (and a non-empty `why_wrong_fails` where simulations are built) to their fixtures/helpers (`_cbp`, `_cbp_mode`, etc.).

## 8. Backward compatibility

None required. Persisted `cbp_json` is write-only (stored via `model_dump`; read back as raw JSON by the download endpoint / frontend), never re-validated through the Pydantic schema. Old jobs won't error; they simply lack blocks. Regenerate to get the new structure. Pre-production.

## 9. Risks & mitigations

| Risk | Mitigation |
|---|---|
| **claude's 32k output-token ceiling.** CBP is the richest phase; we already hit this ceiling on flashcards this session. Adding required content risks re-tripping it. | Learning blocks text-first/short; `visual_svg` optional + prompt-discouraged (reference existing diagrams). Keep prompt additions lean. If CBP still trips it later, address via output-cap tuning in a separate change. |
| **`why_wrong_fails` now required across 18 prompts.** Any prompt that doesn't instruct it ⇒ generation fails. | Fail-loud (validation error → retry-once → job fails, not silent). Prompt rollout explicitly covers all 18; verify per file. |
| **13 test construction sites break.** | Sweep all 13; update fixtures in the same change so the suite stays green. |
| **Inherited required fields force all 4 CBP-mode games to emit blocks.** | Intended & spec-aligned (game specs mandate LB1/LB2). Prompt rollout covers all 11 game prompts. |

## 10. Acceptance criteria

- Schema: `CaseBasedPreview` and `CbpModeGame` require both learning blocks + non-empty `why_wrong_fails`; `LearningBlock` requires `explanation`.
- Fidelity collector includes learning-block concept links.
- Synth renders the interleaved 10-slot order in both branches.
- All 18 prompts instruct LB1/LB2 + `why_wrong_fails`.
- Full test suite green (new tests + 13 updated sites).
- A real CBP generation (geometry, gemini-extract/claude-content) produces both learning blocks without tripping the 32k ceiling.
