# CBP Completeness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the two mandated Learning Blocks to Case-Based Preview (`setup → C1 → LB1 → C2 → LB2 → C3 → DPE → simulation → feedback`), make `CaseSimulation.why_wrong_fails` required, and surface invented learning-block concept IDs.

**Architecture:** A small `LearningBlock` model added to `app/schemas/flow_v2.py`; two required fields on `CaseBasedPreview` (inherited by `CbpModeGame`); a required `why_wrong_fails`; an extension to the existing source-fidelity collector; an interleaved synth renderer; and prompt updates across 18 files. Ordering of the 10 slots lives in the prompt + renderer, not the type.

**Tech Stack:** Python 3.13, Pydantic v2, pytest. Run tests with `.venv/Scripts/python.exe -m pytest`.

**Spec:** `docs/superpowers/specs/2026-05-30-cbp-completeness-design.md`

---

### Task 1: Schema — LearningBlock model, required blocks, required why_wrong_fails

**Files:**
- Modify: `app/schemas/flow_v2.py` (add `LearningBlock`; add 2 fields to `CaseBasedPreview`; make `CaseSimulation.why_wrong_fails` required)
- Modify (fixtures + new tests): `tests/schemas/test_flow_v2_schemas.py`
- Modify (fixture): `tests/schemas/test_practice_games_schemas.py`

- [ ] **Step 1: Add the `LearningBlock` model.** In `app/schemas/flow_v2.py`, immediately after the `CaseSimulation` class (the block ending with `why_wrong_fails: str = ""`), insert:

```python
class LearningBlock(BaseModel):
    """Short teaching explanation between checkpoints (CBP standard §5, slots 3 & 5).
    LB1 explains the concept after Checkpoint 1; LB2 shows the method after
    Checkpoint 2. Text-first: ``visual_svg`` is optional and used only when a tiny
    diagram is essential and not already shown in the case."""

    explanation: str = Field(min_length=1)
    title: Optional[str] = None
    visual_svg: Optional[str] = None
    source_concept_id: Optional[str] = None
```

- [ ] **Step 2: Make `why_wrong_fails` required.** In the `CaseSimulation` class, change:

```python
    why_wrong_fails: str = ""
```
to:
```python
    why_wrong_fails: str = Field(min_length=1)
```

- [ ] **Step 3: Add the two blocks to `CaseBasedPreview`.** Change:

```python
    checkpoints: list[CaseCheckpoint] = Field(min_length=3, max_length=3)
    decision_process_explanation: DecisionProcessExplanation
```
to:
```python
    checkpoints: list[CaseCheckpoint] = Field(min_length=3, max_length=3)
    learning_block_1: LearningBlock
    learning_block_2: LearningBlock
    decision_process_explanation: DecisionProcessExplanation
```

- [ ] **Step 4: Update the CBP test fixture + import.** In `tests/schemas/test_flow_v2_schemas.py`, add `CaseSimulation` is already imported; add `LearningBlock` to the `from app.schemas.flow_v2 import (...)` block. Then in `_valid_cbp_kwargs()`, change the `final_simulation=` value to include `why_wrong_fails`, and add the two blocks after `checkpoints=[...]`:

```python
        final_simulation=CaseSimulation(
            correct_path="3/5 ÷ 3 = 1/5 ℓ per cup.",
            wrong_path="3/5 × 3 = 9/5 ℓ, impossible.",
            why_wrong_fails="Multiplying grows the amount, but sharing must shrink each cup.",
        ),
        learning_block_1=LearningBlock(
            explanation="A proper fraction's numerator is smaller than its denominator.",
        ),
        learning_block_2=LearningBlock(
            explanation="To divide a fraction by a whole number, multiply the denominator by it.",
        ),
```

- [ ] **Step 5: Update the CBP-mode game fixture.** In `tests/schemas/test_practice_games_schemas.py`, inside `_cbp_mode()`, change the `final_simulation=dict(...)` to add `why_wrong_fails`, and add the two blocks before `feedback_summary=`:

```python
        final_simulation=dict(
            correct_path="Talaba ma'noni qayta tiklaydi -> Recalled.",
            wrong_path="Talaba faqat joyni eslaydi -> Position Memory Only.",
            why_wrong_fails="Joy xotirasi tez unutiladi; faqat ma'no qayta tiklash qoladi.",
        ),
        learning_block_1=dict(
            explanation="Ikki karta faqat manba ularni bog'laganda juftlik bo'ladi.",
        ),
        learning_block_2=dict(
            explanation="Bog'lanishning yo'nalishi bor: A qism B ni qo'llab-quvvatlaydi.",
        ),
```

- [ ] **Step 6: Add the new failing-behavior tests.** Append to `tests/schemas/test_flow_v2_schemas.py` (after `test_final_simulation_requires_both_paths`):

```python
def test_learning_block_requires_explanation() -> None:
    with pytest.raises(ValidationError):
        LearningBlock(explanation="")


def test_cbp_requires_both_learning_blocks() -> None:
    for missing in ("learning_block_1", "learning_block_2"):
        kwargs = _valid_cbp_kwargs()
        del kwargs[missing]
        with pytest.raises(ValidationError):
            CaseBasedPreview(**kwargs)


def test_cbp_simulation_requires_why_wrong_fails() -> None:
    with pytest.raises(ValidationError):
        CaseSimulation(correct_path="ok", wrong_path="bad", why_wrong_fails="")
```

- [ ] **Step 7: Run the schema tests.**

Run: `.venv/Scripts/python.exe -m pytest tests/schemas/ -q`
Expected: PASS (new tests pass; updated fixtures keep existing tests green).

- [ ] **Step 8: Run the full suite** to catch any other construction site.

Run: `.venv/Scripts/python.exe -m pytest tests/ -q`
Expected: PASS. If any test fails on a missing `learning_block_*`/`why_wrong_fails`, it is another construction site — add the two blocks + `why_wrong_fails` to that fixture and re-run.

- [ ] **Step 9: Commit.**

```bash
git add app/schemas/flow_v2.py tests/schemas/test_flow_v2_schemas.py tests/schemas/test_practice_games_schemas.py
git commit -m "feat(cbp): add required Learning Blocks + required why_wrong_fails to CaseBasedPreview"
```

---

### Task 2: Extend the source-fidelity collector to learning-block concept IDs

**Files:**
- Modify: `app/services/pipeline.py` (`_emitted_concept_ids`)
- Modify: `tests/services/test_concept_id_fidelity.py`

- [ ] **Step 1: Write the failing test.** Append to `tests/services/test_concept_id_fidelity.py`:

```python
def test_flags_invented_learning_block_concept_id():
    parsed = _Obj(
        source_concept_ids=["c1"],
        learning_block_1=_Obj(source_concept_id="ghost"),
        learning_block_2=_Obj(source_concept_id="c1"),
    )
    assert _unknown_concept_ids(parsed, {"c1"}) == ["ghost"]
```

- [ ] **Step 2: Run it to verify it fails.**

Run: `.venv/Scripts/python.exe -m pytest tests/services/test_concept_id_fidelity.py::test_flags_invented_learning_block_concept_id -v`
Expected: FAIL (the collector doesn't look at learning blocks yet, so "ghost" isn't flagged).

- [ ] **Step 3: Extend the collector.** In `app/services/pipeline.py`, in `_emitted_concept_ids`, before `return ids`, add:

```python
    for lb_attr in ("learning_block_1", "learning_block_2"):
        lb = getattr(parsed, lb_attr, None)
        cid = getattr(lb, "source_concept_id", None) if lb is not None else None
        if cid:
            ids.append(cid)
```

- [ ] **Step 4: Run the fidelity tests.**

Run: `.venv/Scripts/python.exe -m pytest tests/services/test_concept_id_fidelity.py -q`
Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add app/services/pipeline.py tests/services/test_concept_id_fidelity.py
git commit -m "feat(cbp): source-fidelity check covers learning-block concept ids"
```

---

### Task 3: Synth renderer — interleave learning blocks between checkpoints

**Files:**
- Modify: `app/services/pipeline.py` (`_synth_md_for_structured`: `case-based-preview` branch + `_CBP_MODE_PHASES` branch; add a shared helper)
- Modify: `tests/services/test_practice_arc_synth.py`

- [ ] **Step 1: Write the failing test.** In `tests/services/test_practice_arc_synth.py`, add an import at the top:

```python
from tests.schemas.test_flow_v2_schemas import _valid_cbp_kwargs
from app.schemas.flow_v2 import CaseBasedPreview
```
and append:

```python
def test_synth_cbp_interleaves_learning_blocks() -> None:
    md = _synth_md_for_structured("case-based-preview", CaseBasedPreview(**_valid_cbp_kwargs()))
    assert "Learning Block 1" in md and "Learning Block 2" in md
    # Order: C1 → LB1 → C2 → LB2 → C3
    pos = {k: md.index(k) for k in (
        "Checkpoint 1", "Learning Block 1", "Checkpoint 2", "Learning Block 2", "Checkpoint 3")}
    assert pos["Checkpoint 1"] < pos["Learning Block 1"] < pos["Checkpoint 2"] < pos["Learning Block 2"] < pos["Checkpoint 3"]
```

- [ ] **Step 2: Run it to verify it fails.**

Run: `.venv/Scripts/python.exe -m pytest tests/services/test_practice_arc_synth.py::test_synth_cbp_interleaves_learning_blocks -v`
Expected: FAIL ("Learning Block 1" not in the rendered output).

- [ ] **Step 3: Add the shared interleave helper.** In `app/services/pipeline.py`, just above `_synth_md_for_structured`, add:

```python
def _render_checkpoints_and_blocks(parsed: Any) -> list[str]:
    """Render the CBP checkpoints with the two learning blocks interleaved
    (C1 → LB1 → C2 → LB2 → C3), per the CBP generation standard §5."""
    cps = getattr(parsed, "checkpoints", None) or []
    blocks = {
        1: getattr(parsed, "learning_block_1", None),
        2: getattr(parsed, "learning_block_2", None),
    }
    out: list[str] = []
    for i, cp in enumerate(cps, 1):
        out.append(f"**Checkpoint {i}** [{cp.intent}] {cp.question}")
        lb = blocks.get(i)
        if lb is not None:
            title = f" — {lb.title}" if getattr(lb, "title", None) else ""
            out.append(f"**Learning Block {i}**{title} {lb.explanation}")
            if getattr(lb, "visual_svg", None):
                out.append(lb.visual_svg)
    return out
```

- [ ] **Step 4: Use it in the `case-based-preview` branch.** In `_synth_md_for_structured`, replace the checkpoint loop in the `case-based-preview` branch:

```python
        for i, cp in enumerate(cps, 1):
            out.append(f"**Checkpoint {i}** [{cp.intent}] {cp.question}")
```
with:
```python
        out.extend(_render_checkpoints_and_blocks(parsed))
```

- [ ] **Step 5: Use it in the `_CBP_MODE_PHASES` branch.** In the `if phase_name in _CBP_MODE_PHASES:` branch, replace the equivalent checkpoint loop:

```python
        for i, cp in enumerate(cps, 1):
            out.append(f"**Checkpoint {i}** [{cp.intent}] {cp.question}")
```
with:
```python
        out.extend(_render_checkpoints_and_blocks(parsed))
```

- [ ] **Step 6: Run the synth tests.**

Run: `.venv/Scripts/python.exe -m pytest tests/services/test_practice_arc_synth.py -q`
Expected: PASS (the new interleave test + the existing CBP-mode synth tests).

- [ ] **Step 7: Run the full suite.**

Run: `.venv/Scripts/python.exe -m pytest tests/ -q`
Expected: PASS.

- [ ] **Step 8: Commit.**

```bash
git add app/services/pipeline.py tests/services/test_practice_arc_synth.py
git commit -m "feat(cbp): render learning blocks interleaved between checkpoints"
```

---

### Task 4: Update the 7 CBP subject prompts

**Files (modify each):**
`prompts/biology/case-based-preview.md`, `prompts/english/case-based-preview.md`, `prompts/geometriya-g7-11/case-based-preview.md`, `prompts/history/case-based-preview.md`, `prompts/kimyo-g7-11/case-based-preview.md`, `prompts/math-algebra/case-based-preview.md`, `prompts/physics/case-based-preview.md`

Each file has a `## CBP canonical structure` block listing the slots and a checkpoint/DPE section. The edit is identical in shape per file (subject wording stays as-is).

- [ ] **Step 1: Insert the two blocks into the structure list.** In each file's structure code-block, add a Learning Block line after Checkpoint 1 and after Checkpoint 2, and renumber. Result should read:

```
1. Case setup          — ...
2. Checkpoint 1        — Identify: ...
3. Learning Block 1    — short explanation of the concept the student just identified (textbook-grounded)
4. Checkpoint 2        — Decide: ...
5. Learning Block 2    — show the method/relationship the student will now apply
6. Checkpoint 3        — Justify or Avoid Mistake: ...
7. Decision Process Explanation (DPE) — after Checkpoint 3, before the final simulation (canonical CBP slot 7); OPEN-ENDED, options = null
8. Final simulation    — correct path + wrong path + why wrong fails
9. Feedback summary
10. Completion rules
```

- [ ] **Step 2: Add a Learning Blocks output-rules section.** After the `## Checkpoint rules` section in each file, insert:

```markdown
## Learning Blocks (slots 3 & 5)

Two short teaching moments, emitted as `learning_block_1` and `learning_block_2`.
- **learning_block_1** (after Checkpoint 1): a 1–3 sentence explanation of the concept the student just identified, grounded in the textbook. Set `source_concept_id` to the SourceMap concept it teaches.
- **learning_block_2** (after Checkpoint 2): a 1–3 sentence explanation that shows the method/relationship to apply. Set `source_concept_id`.
- Keep them **text-first and short**. Use `visual_svg` ONLY if a tiny diagram is essential AND not already shown in the case — otherwise omit it (a `[Diagram: ...]` note in the text is preferred). This protects the output-token budget.
- Do NOT name the method in `learning_block_1` if the case still expects the student to commit at Checkpoint 2 first.
```

- [ ] **Step 3: Ensure `why_wrong_fails` is instructed.** In each file's final-simulation guidance, confirm the wrong path includes a `why_wrong_fails` explanation ("why the wrong path fails"). If a file's simulation section doesn't mention it, add: `- why_wrong_fails: one sentence on why the wrong path cannot be correct (REQUIRED).`

- [ ] **Step 4: Sanity-check prompts load.**

Run: `.venv/Scripts/python.exe -m pytest tests/ -k "prompt_coverage or flow" -q`
Expected: PASS (prompt files present + parse for all phases/subjects).

- [ ] **Step 5: Commit.**

```bash
git add prompts/biology/case-based-preview.md prompts/english/case-based-preview.md prompts/geometriya-g7-11/case-based-preview.md prompts/history/case-based-preview.md prompts/kimyo-g7-11/case-based-preview.md prompts/math-algebra/case-based-preview.md prompts/physics/case-based-preview.md
git commit -m "feat(cbp): instruct Learning Blocks + why_wrong_fails in 7 CBP prompts"
```

---

### Task 5: Update the 11 CBP-mode game prompts

**Files (modify each):**
`prompts/biology/practice-memory-match.md`, `prompts/english/practice-memory-match.md`, `prompts/english/practice-sentence.md`, `prompts/geometriya-g7-11/practice-jigsaw.md`, `prompts/geometriya-g7-11/practice-tictactoe.md`, `prompts/history/practice-jigsaw.md`, `prompts/history/practice-memory-match.md`, `prompts/kimyo-g7-11/practice-tictactoe.md`, `prompts/math-algebra/practice-jigsaw.md`, `prompts/math-algebra/practice-tictactoe.md`, `prompts/physics/practice-tictactoe.md`

These generate `CbpModeGame`, which now inherits the two required learning blocks + required `why_wrong_fails`. Their specs already describe LB1/LB2 (e.g. Jigsaw: LB1 "the pair is valid only because the source gives a relationship", LB2 "direction and role").

- [ ] **Step 1: Add the Learning Blocks output section.** In each file, after its checkpoint/structure guidance, insert (adapting the per-game wording to the mechanic — pairs/pieces/board/sentence):

```markdown
## Learning Blocks (required — slots 3 & 5)

Emit `learning_block_1` (after Checkpoint 1) and `learning_block_2` (after Checkpoint 2): each a 1–3 sentence, textbook-grounded teaching moment for this game's mechanic. Set `source_concept_id`. Keep them short and text-first; use `visual_svg` only if a tiny diagram is essential and not already shown.
```

- [ ] **Step 2: Ensure `why_wrong_fails` is instructed.** In each file's final-simulation / wrong-path guidance, confirm a `why_wrong_fails` sentence is required; add it if absent (mirroring Task 4 Step 3).

- [ ] **Step 3: Sanity-check prompts load.**

Run: `.venv/Scripts/python.exe -m pytest tests/ -k "prompt_coverage or practice_arc_flow" -q`
Expected: PASS.

- [ ] **Step 4: Commit.**

```bash
git add prompts/biology/practice-memory-match.md prompts/english/practice-memory-match.md prompts/english/practice-sentence.md prompts/geometriya-g7-11/practice-jigsaw.md prompts/geometriya-g7-11/practice-tictactoe.md prompts/history/practice-jigsaw.md prompts/history/practice-memory-match.md prompts/kimyo-g7-11/practice-tictactoe.md prompts/math-algebra/practice-jigsaw.md prompts/math-algebra/practice-tictactoe.md prompts/physics/practice-tictactoe.md
git commit -m "feat(cbp): instruct Learning Blocks + why_wrong_fails in 11 CBP-mode game prompts"
```

---

### Task 6: Live verification + worklog

**Files:**
- Temporary (throwaway, untracked — `rm` after): `verify_cbp.py`
- Modify: `docs/memory/MASTER_MEMORY.md`, `docs/memory/INDEX.md`

- [ ] **Step 1: Full suite green.**

Run: `.venv/Scripts/python.exe -m pytest tests/ -q`
Expected: PASS (all tasks integrated).

- [ ] **Step 2: Live CBP generation smoke.** Confirm a real claude CBP generation now emits both learning blocks and does NOT trip the 32k output ceiling. Write a throwaway `verify_cbp.py` that runs the `case-based-preview` phase for the geometry book section (extract pinned to gemini, content=claude) and prints whether `learning_block_1`/`learning_block_2` are populated and the call succeeded. Run with:

```bash
PYTHONUTF8=1 DATABASE_URL='postgresql+asyncpg://edu:edu@localhost:5433/edu_homework' AUTH_TOKEN='' .venv/Scripts/python.exe verify_cbp.py
```
Expected: prints both blocks populated; no `rc=1 :: API Error ... 32000 output token maximum`. If it trips the ceiling, tighten the LB prompt guidance (shorter, no SVG) and/or move the SVG out of the blocks, then re-run.

- [ ] **Step 3: Remove the throwaway.**

```bash
rm verify_cbp.py
```

- [ ] **Step 4: Worklog entry.** Append a `## [0015]` entry to `docs/memory/MASTER_MEMORY.md` summarizing the CBP-completeness change (learning blocks added, why_wrong_fails required, fidelity collector extended, 18 prompts updated, live smoke result) and add the one-line pointer to `docs/memory/INDEX.md`.

- [ ] **Step 5: Commit.**

```bash
git add docs/memory/MASTER_MEMORY.md docs/memory/INDEX.md
git commit -m "docs: worklog 0015 — CBP completeness (learning blocks + why_wrong_fails)"
git push origin Nggaev-v2
```

---

## Self-Review

**Spec coverage:** §3 schema → Task 1. §4 fidelity collector → Task 2. §5 synth render → Task 3. §6 prompts (7 CBP / 11 game) → Tasks 4 & 5. §7 testing → embedded in every task (TDD) + Task 1 Step 8 sweep. §8 back-compat → no task needed (no runtime re-validation). §9 risks (32k ceiling) → Task 6 Step 2 live smoke + LB-short prompt guidance in Tasks 4/5. §10 acceptance → Task 6. All spec sections covered.

**Placeholder scan:** No TBD/TODO. Prompt tasks (4/5) give the exact section text to insert; per-subject wording adaptation is inherent to prompt content, not a placeholder. Task 6's `verify_cbp.py` is described by behavior + run command (a throwaway script, mirrors prior `rerun_job.py` pattern).

**Type consistency:** `LearningBlock` fields (`explanation`, `title`, `visual_svg`, `source_concept_id`) are used identically in the schema (Task 1), the collector (Task 2: `source_concept_id`), and the renderer (Task 3: `title`, `explanation`, `visual_svg`). Field names `learning_block_1`/`learning_block_2` and `why_wrong_fails` are consistent across all tasks.
