# Memory Check Completeness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Upgrade Memory Check items from flat `options: list[str]` + `correct_index` to per-type structure: option objects (`is_correct` + `reason`) for MCQ/CCE, `blanks` for fill-blank, plus a WHY apparatus + feedback — enforced by a kind-keyed validator.

**Architecture:** New `MemoryCheckOption`/`MemoryCheckBlank` models + an enriched `MemoryCheckItem` with a `model_validator`; the synth renderer rewritten for the new shape (keeping the §8 teacher-note split); 7 `memory-check.md` prompts rewritten. Schema + synth land together (the teacher-note test couples them).

**Tech Stack:** Python 3.13, Pydantic v2, pytest (`.venv/Scripts/python.exe -m pytest`).

**Spec:** `docs/superpowers/specs/2026-05-30-memory-check-completeness-design.md`

---

### Task 1: Schema + synth + fixtures (one cohesive, green commit)

**Files:**
- Modify: `app/schemas/memory_check.py`
- Modify: `app/services/pipeline.py` (`_synth_md_for_structured`, memory-check branch)
- Modify (fixtures + tests): `tests/schemas/test_learning_schemas.py`, `tests/services/test_teacher_note_labeling.py`

- [ ] **Step 1: Add models + validator.** In `app/schemas/memory_check.py`, change the import line to `from pydantic import BaseModel, Field, field_validator, model_validator`. Replace the `MemoryCheckItem` class (keep `MemoryCheckKind` above it and `MemoryCheckPack` below it) with:

```python
class MemoryCheckOption(BaseModel):
    text: str = Field(min_length=1)
    is_correct: bool = False
    reason: Optional[str] = None  # why this distractor is wrong (MCQ) / flawed reasoning (CCE)


class MemoryCheckBlank(BaseModel):
    answer: str = Field(min_length=1)
    accepted_variations: list[str] = Field(default_factory=list)


_OPTION_KINDS = {"multiple_choice", "choose_correct_explanation"}


class MemoryCheckItem(BaseModel):
    # Required: every item traces back to a specific flashcard.
    flashcard_id: str = Field(min_length=1)
    kind: MemoryCheckKind
    prompt: str = Field(min_length=1)
    options: list[MemoryCheckOption] = Field(default_factory=list)  # MCQ + CCE
    blanks: list[MemoryCheckBlank] = Field(default_factory=list)    # fill_blank
    why_prompt: Optional[str] = None
    expected_reasoning_keywords: list[str] = Field(default_factory=list)
    correct_feedback: Optional[str] = None
    wrong_feedback: Optional[str] = None
    explanation: Optional[str] = None

    @model_validator(mode="after")
    def _shape_matches_kind(self) -> "MemoryCheckItem":
        if self.kind in _OPTION_KINDS:
            if len(self.options) != 4:
                raise ValueError(f"{self.kind} requires exactly 4 options, got {len(self.options)}")
            if sum(1 for o in self.options if o.is_correct) != 1:
                raise ValueError(f"{self.kind} requires exactly one correct option")
            if self.blanks:
                raise ValueError(f"{self.kind} must not carry blanks")
        elif self.kind == "fill_blank":
            if not self.blanks:
                raise ValueError("fill_blank requires at least one blank")
            if self.options:
                raise ValueError("fill_blank must not carry options")
        return self
```
Leave `MemoryCheckPack` (items + locked `pass_threshold`) unchanged.

- [ ] **Step 2: Rewrite the synth branch.** In `app/services/pipeline.py`, replace the `memory-check` branch body:
```python
    if phase_name == "memory-check":
        items = getattr(parsed, "items", None) or []
        threshold = getattr(parsed, "pass_threshold", 0.60)
        out = [
            f"_{len(items)} memory-check items (pass ≥{int(threshold * 100)}%) "
            f"— interactive check rendered in preview._\n"
        ]
        for i, it in enumerate(items, 1):
            fid = getattr(it, "flashcard_id", "") or ""
            fid_tag = f" [←{fid}]" if fid else ""
            out.append(f"{i}. **[{it.kind.upper()}]{fid_tag}** {it.prompt}")
            opts = it.options or []
            for j, opt in enumerate(opts):
                out.append(f"   - {chr(97 + j)}) {opt}")
            if opts and it.correct_index is not None and 0 <= it.correct_index < len(opts):
                ci = it.correct_index
                out.append(f"   - {_teacher(f'Correct answer: {chr(97 + ci)}) {opts[ci]}')}")
            if getattr(it, "explanation", None):
                out.append(f"   - {_teacher(it.explanation)}")
        return "\n".join(out)
```
with:
```python
    if phase_name == "memory-check":
        items = getattr(parsed, "items", None) or []
        threshold = getattr(parsed, "pass_threshold", 0.60)
        out = [
            f"_{len(items)} memory-check items (pass ≥{int(threshold * 100)}%) "
            f"— interactive check rendered in preview._\n"
        ]
        for i, it in enumerate(items, 1):
            fid = getattr(it, "flashcard_id", "") or ""
            fid_tag = f" [←{fid}]" if fid else ""
            out.append(f"{i}. **[{it.kind.upper()}]{fid_tag}** {it.prompt}")
            opts = getattr(it, "options", None) or []
            for j, opt in enumerate(opts):
                out.append(f"   - {chr(97 + j)}) {opt.text}")  # student view — correctness hidden
            correct = next((o for o in opts if o.is_correct), None)
            if correct is not None:
                ci = opts.index(correct)
                out.append(f"   - {_teacher(f'Correct: {chr(97 + ci)}) {correct.text}')}")
            for j, opt in enumerate(opts):
                if not opt.is_correct and getattr(opt, "reason", None):
                    out.append(f"   - {_teacher(f'{chr(97 + j)}) wrong — {opt.reason}')}")
            for b in getattr(it, "blanks", None) or []:
                acc = f" (also accept: {', '.join(b.accepted_variations)})" if b.accepted_variations else ""
                out.append(f"   - {_teacher(f'Answer: {b.answer}{acc}')}")
            if getattr(it, "why_prompt", None):
                out.append(f"   - **Why:** {it.why_prompt}")  # student-facing reasoning prompt
            if getattr(it, "explanation", None):
                out.append(f"   - {_teacher(it.explanation)}")
        return "\n".join(out)
```

- [ ] **Step 3: Update the `_item()` fixture.** In `tests/schemas/test_learning_schemas.py`, replace the `_item()` helper body so it builds the new valid MCQ shape (4 options, one correct):
```python
def _item(**over) -> dict:
    base = dict(
        flashcard_id="card_1",
        prompt="Which rule divides a fraction by a whole number?",
        kind="multiple_choice",
        options=[
            dict(text="multiply the denominator by the whole number", is_correct=True),
            dict(text="multiply the numerator", is_correct=False, reason="changes the wrong part of the fraction"),
            dict(text="add the whole number to the denominator", is_correct=False, reason="division is not addition"),
            dict(text="flip the fraction over", is_correct=False, reason="that is the rule for dividing by a fraction"),
        ],
    )
    base.update(over)
    return base
```
The 4 existing tests using `_item()` stay valid (valid item passes the new validator; `flashcard_id=""`, `kind="essay"`, and `pass_threshold=0.5` still raise).

- [ ] **Step 4: Add validator tests.** Append to `tests/schemas/test_learning_schemas.py` (imports already cover `MemoryCheckItem`, `pytest`, `ValidationError`):
```python
def test_mcq_requires_exactly_four_options() -> None:
    three = _item()["options"][:3]
    with pytest.raises(ValidationError):
        MemoryCheckItem(**_item(options=three))


def test_mcq_requires_exactly_one_correct() -> None:
    opts = _item()["options"]
    opts2 = [dict(o) for o in opts]
    opts2[1]["is_correct"] = True  # now two correct
    with pytest.raises(ValidationError):
        MemoryCheckItem(**_item(options=opts2))


def test_fill_blank_requires_blanks_and_no_options() -> None:
    with pytest.raises(ValidationError):  # no blanks
        MemoryCheckItem(flashcard_id="card_1", kind="fill_blank",
                        prompt="A proper fraction's numerator is _____ its denominator.")
    with pytest.raises(ValidationError):  # blanks AND options
        MemoryCheckItem(flashcard_id="card_1", kind="fill_blank", prompt="_____",
                        blanks=[dict(answer="smaller than")],
                        options=[dict(text="x", is_correct=True)])


def test_fill_blank_valid() -> None:
    it = MemoryCheckItem(flashcard_id="card_1", kind="fill_blank",
                         prompt="A proper fraction's numerator is _____ its denominator.",
                         blanks=[dict(answer="smaller than", accepted_variations=["less than"])])
    assert it.blanks[0].answer == "smaller than"
    assert it.options == []
```

- [ ] **Step 5: Update the teacher-note fixture.** In `tests/services/test_teacher_note_labeling.py`, the `test_memory_check_answer_only_in_teacher_notes` fixture builds the OLD shape. Rewrite its `MemoryCheckItem(...)` to the new option-objects shape (4 options, one correct), keeping the test's intent:
```python
    pack = MemoryCheckPack(
        items=[
            MemoryCheckItem(
                flashcard_id="card_1",
                prompt="Which organelle makes ATP?",
                kind="multiple_choice",
                options=[
                    {"text": "Nucleus", "is_correct": False, "reason": "stores DNA, not energy"},
                    {"text": "Mitochondrion", "is_correct": True},
                    {"text": "Ribosome", "is_correct": False, "reason": "builds proteins"},
                    {"text": "Vacuole", "is_correct": False, "reason": "storage, not energy"},
                ],
                explanation="Mitochondrion is the powerhouse because reasons.",
            )
        ]
    )
```
Keep the existing assertions; they still hold (the synth shows the correct option only inside a `🔑 TEACHER NOTE`, and `explanation` is teacher-only). If an assertion references `correct_index` or `✓`, leave the `✓` checks (the new synth emits no `✓`).

- [ ] **Step 6: Run the full suite.**

Run: `.venv/Scripts/python.exe -m pytest tests/ -q`
Expected: PASS. If any other test builds a `MemoryCheckItem` in the old shape, fix it the same way and re-run.

- [ ] **Step 7: Commit.**

```bash
git add app/schemas/memory_check.py app/services/pipeline.py tests/schemas/test_learning_schemas.py tests/services/test_teacher_note_labeling.py
git commit -m "feat(memory-check): per-type item model (option objects + blanks + WHY) with kind-keyed validator"
```

---

### Task 2: Rewrite the 7 memory-check prompts

**Files (modify each):**
`prompts/{biology,english,geometriya-g7-11,history,kimyo-g7-11,math-algebra,physics}/memory-check.md`

- [ ] **Step 1: Update each file's item-format section** to the enriched shape (preserve subject voice, the `flashcard_id` rule, the 3-kind balance, the 60% gate). Insert/replace the format guidance with:
```markdown
## Item format

Every item references a `flashcard_id` (the card it tests) and a `kind`.

- **multiple_choice / choose_correct_explanation:** `options` = EXACTLY 4 objects, each `{text, is_correct, reason}`. Exactly ONE `is_correct: true`. Each wrong option's `reason` names the real misconception it represents (for choose_correct_explanation, the `reason` is the flawed reasoning). Options must be similar in length/style so the answer isn't given away by formatting. No `blanks`.
- **fill_blank:** put `_____` in the `prompt`; give `blanks` = one or more `{answer, accepted_variations}` (accepted_variations = other spellings/phrasings that should count as correct). No `options`.
- `why_prompt` + `expected_reasoning_keywords` — REQUIRED for science subjects (biology/physics/chemistry), optional otherwise.
- `correct_feedback` / `wrong_feedback` — short, encouraged.
- Keep items balanced: no more than ~60% of one kind. Each item must trace to a card the student studied.
```

- [ ] **Step 2: Sanity-check prompts load.**

Run: `.venv/Scripts/python.exe -m pytest tests/ -k "prompt_coverage or learning_flow or flow" -q`
Expected: PASS.

- [ ] **Step 3: Commit.**

```bash
git add prompts/biology/memory-check.md prompts/english/memory-check.md prompts/geometriya-g7-11/memory-check.md prompts/history/memory-check.md prompts/kimyo-g7-11/memory-check.md prompts/math-algebra/memory-check.md prompts/physics/memory-check.md
git commit -m "feat(memory-check): rewrite 7 prompts to option-objects + blanks + WHY apparatus"
```

---

### Task 3: Live smoke + worklog + push

**Files:**
- Temporary (throwaway, `rm` after): `verify_memcheck.py`
- Modify: `docs/memory/MASTER_MEMORY.md`, `docs/memory/INDEX.md`

- [ ] **Step 1: Full suite green.** `.venv/Scripts/python.exe -m pytest tests/ -q` → PASS.

- [ ] **Step 2: Live memory-check smoke.** Throwaway `verify_memcheck.py` mirroring the prior smokes: lesson_context from job `c6457a80` extract `phase_output` id `57e55ade-f0b3-4384-a9a8-a78f056900ad`; flashcards prior_output optional; `agent.run_phase_prompt_structured(provider="claude", model="claude-sonnet-4-6", phase_prompt=get_prompt("geometriya-g7-11","memory-check"), response_schema=MemoryCheckPack, lesson_context=..., prior_outputs={}, difficulty="hard", phase_name="memory-check", homework_job_id=JOB)`. Print: item count; for MCQ/CCE items assert exactly 4 options + exactly 1 correct; for fill_blank assert ≥1 blank; confirm it validated (the schema validator ran). Run:
```bash
PYTHONUTF8=1 DATABASE_URL='postgresql+asyncpg://edu:edu@localhost:5433/edu_homework' AUTH_TOKEN='' .venv/Scripts/python.exe verify_memcheck.py
```
Expected: items validate (4-option/one-correct or blanks), no exception.

- [ ] **Step 3: Remove the throwaway.** `rm verify_memcheck.py`

- [ ] **Step 4: Worklog 0017.** Append `## [0017]` to `docs/memory/MASTER_MEMORY.md` + one-line pointer in `docs/memory/INDEX.md`.

- [ ] **Step 5: Commit + push.**
```bash
git add docs/memory/MASTER_MEMORY.md docs/memory/INDEX.md
git commit -m "docs: worklog 0017 — memory-check per-type item model"
git push origin Nggaev-v2
```

---

## Self-Review

**Spec coverage:** §3 schema+validator → Task 1 Steps 1,3,4. §4 synth → Task 1 Step 2 + Step 5 fixture. §5 prompts → Task 2. §6 tests → Task 1 Steps 4-6. §7 back-compat → no task. §8 risks (coupling, exactly-4) → Task 1 bundles schema+synth to stay green. §9 acceptance → Task 3. Covered.

**Placeholder scan:** No TBD/TODO. Task 2 gives the exact item-format block; per-subject voice preservation is editing, not a placeholder. Task 3 smoke described by behavior + exact ids + command.

**Type consistency:** `MemoryCheckOption` (`text`/`is_correct`/`reason`) + `MemoryCheckBlank` (`answer`/`accepted_variations`) names match across schema (Task 1.1), synth (Task 1.2: `opt.text`, `opt.is_correct`, `opt.reason`, `b.answer`, `b.accepted_variations`), fixtures (Task 1.3/1.5), and the smoke (Task 3). `_OPTION_KINDS` matches the synth's option-kind handling.
