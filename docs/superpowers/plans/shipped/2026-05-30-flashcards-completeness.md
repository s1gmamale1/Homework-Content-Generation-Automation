# Flashcards Completeness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the flashcard from a 3-field reference card to the spec's 8-field active-recall card (`front, back, hint, explanation, example, misconception, type, difficulty`), required `type`/`difficulty`, the rest optional.

**Architecture:** Add two closed enums + five fields to `app/schemas/flashcards.py`; surface them in the synth renderer; rewrite the 7 subject `flashcards.md` prompts to the 8-field shape with word limits. No runtime study games (deck only).

**Tech Stack:** Python 3.13, Pydantic v2, pytest (`.venv/Scripts/python.exe -m pytest`).

**Spec:** `docs/superpowers/specs/2026-05-30-flashcards-completeness-design.md`

---

### Task 1: Schema — 8-field Flashcard

**Files:**
- Modify: `app/schemas/flashcards.py`
- Modify (fixtures + new tests): `tests/schemas/test_learning_schemas.py`

- [ ] **Step 1: Add the enums + fields.** In `app/schemas/flashcards.py`, change the import line `from typing import Optional` to `from typing import Literal, Optional`. Then, above `class Flashcard`, add:

```python
FlashcardType = Literal[
    "definition", "term_to_meaning", "formula", "process_step",
    "question_answer", "misconception", "image_label", "vocabulary",
    "grammar", "example",
]
FlashcardDifficulty = Literal["easy", "medium", "hard"]
```
Replace the `Flashcard` class body with:
```python
class Flashcard(BaseModel):
    # Stable ID so Memory Check items can reference specific cards across sessions.
    # Format convention: "card_<N>" (e.g. "card_1", "card_12").
    id: str = Field(min_length=1)
    front: str = Field(min_length=1)
    back: str = Field(min_length=1)
    type: FlashcardType
    difficulty: FlashcardDifficulty
    hint: Optional[str] = None
    explanation: Optional[str] = None
    example: Optional[str] = None
    misconception: Optional[str] = None
    cluster: Optional[str] = None  # optional grouping label (e.g., 'Names', 'Frameworks')
```
Leave `FlashcardsPack` and its `_ids_must_be_unique` validator unchanged.

- [ ] **Step 2: Fix the existing fixtures.** In `tests/schemas/test_learning_schemas.py`, every `Flashcard(...)` call now needs `type` + `difficulty`. Add `type="definition", difficulty="easy"` to each of the four calls (the `id=""` one keeps `id=""` so it still isolates the empty-id check; just add the two new args). Example: `Flashcard(id="card_1", front="a", back="b", type="definition", difficulty="easy")`.

- [ ] **Step 3: Run the existing learning-schema tests.**

Run: `.venv/Scripts/python.exe -m pytest tests/schemas/test_learning_schemas.py -q`
Expected: PASS (fixtures now valid; the id-uniqueness and empty-id tests still raise as before).

- [ ] **Step 4: Add new behavior tests.** Append to `tests/schemas/test_learning_schemas.py` (it already imports `Flashcard`, `pytest`, `ValidationError` — if not, add them):

```python
def _valid_card(**overrides) -> dict:
    base = dict(id="card_1", front="Mitoxondriya", back="Hujayra energiya markazi",
                type="definition", difficulty="easy")
    base.update(overrides)
    return base


def test_flashcard_requires_type_and_difficulty() -> None:
    for missing in ("type", "difficulty"):
        kwargs = _valid_card()
        del kwargs[missing]
        with pytest.raises(ValidationError):
            Flashcard(**kwargs)


def test_flashcard_rejects_unknown_type_and_difficulty() -> None:
    with pytest.raises(ValidationError):
        Flashcard(**_valid_card(type="vibes"))
    with pytest.raises(ValidationError):
        Flashcard(**_valid_card(difficulty="impossible"))


def test_flashcard_optionals_default_none() -> None:
    c = Flashcard(**_valid_card())
    assert c.explanation is None and c.example is None and c.misconception is None and c.hint is None


def test_flashcard_requires_nonempty_front_and_back() -> None:
    with pytest.raises(ValidationError):
        Flashcard(**_valid_card(front=""))
    with pytest.raises(ValidationError):
        Flashcard(**_valid_card(back=""))
```

- [ ] **Step 5: Run schema tests + full suite.**

Run: `.venv/Scripts/python.exe -m pytest tests/ -q`
Expected: PASS. If any other test builds a `Flashcard(...)` without `type`/`difficulty`, add them there too and re-run.

- [ ] **Step 6: Commit.**

```bash
git add app/schemas/flashcards.py tests/schemas/test_learning_schemas.py
git commit -m "feat(flashcards): 8-field active-recall card (type, difficulty + explanation/example/misconception)"
```

---

### Task 2: Synth render — surface the new fields

**Files:**
- Modify: `app/services/pipeline.py` (`_synth_md_for_structured`, `flashcards` branch)
- Modify: `tests/services/` — add a small synth test (create `tests/services/test_flashcards_synth.py`)

- [ ] **Step 1: Write the failing test.** Create `tests/services/test_flashcards_synth.py`:

```python
from app.schemas.flashcards import Flashcard, FlashcardsPack
from app.services.pipeline import _synth_md_for_structured


def test_flashcards_synth_shows_type_difficulty_and_optionals() -> None:
    pack = FlashcardsPack(cards=[
        Flashcard(
            id="card_1", front="Mitoxondriya", back="Hujayra energiya markazi",
            type="definition", difficulty="easy",
            explanation="ATP shu yerda ishlab chiqariladi.",
            example="Mushak hujayralarida ko'p bo'ladi.",
            misconception="Yadro bilan adashtirmang.",
        ),
    ])
    md = _synth_md_for_structured("flashcards", pack)
    assert "definition" in md and "easy" in md
    assert "ATP shu yerda" in md           # explanation surfaced
    assert "Mushak hujayralarida" in md    # example surfaced
    assert "adashtirmang" in md            # misconception surfaced
```

- [ ] **Step 2: Run it to verify it fails.**

Run: `.venv/Scripts/python.exe -m pytest tests/services/test_flashcards_synth.py -v`
Expected: FAIL (current render shows only front/back/hint).

- [ ] **Step 3: Update the flashcards branch.** In `app/services/pipeline.py`, replace the `flashcards` branch body:
```python
    if phase_name == "flashcards":
        cards = getattr(parsed, "cards", None) or []
        out = [f"_{len(cards)} flashcards — interactive deck rendered in preview._\n"]
        for i, c in enumerate(cards, 1):
            card_id = getattr(c, "id", "") or ""
            id_tag = f" `{card_id}`" if card_id else ""
            out.append(f"{i}.{id_tag} **{c.front}** — {c.back}")
            if getattr(c, "hint", None):
                out.append(f"   - hint: {c.hint}")
        return "\n".join(out)
```
with:
```python
    if phase_name == "flashcards":
        cards = getattr(parsed, "cards", None) or []
        out = [f"_{len(cards)} flashcards — interactive deck rendered in preview._\n"]
        for i, c in enumerate(cards, 1):
            card_id = getattr(c, "id", "") or ""
            id_tag = f" `{card_id}`" if card_id else ""
            tag = ""
            if getattr(c, "type", None) or getattr(c, "difficulty", None):
                tag = f" _({c.type} · {c.difficulty})_"
            out.append(f"{i}.{id_tag} **{c.front}** — {c.back}{tag}")
            if getattr(c, "hint", None):
                out.append(f"   - hint: {c.hint}")
            if getattr(c, "explanation", None):
                out.append(f"   - explanation: {c.explanation}")
            if getattr(c, "example", None):
                out.append(f"   - example: {c.example}")
            if getattr(c, "misconception", None):
                out.append(f"   - misconception: {c.misconception}")
        return "\n".join(out)
```

- [ ] **Step 4: Run synth test + full suite.**

Run: `.venv/Scripts/python.exe -m pytest tests/ -q`
Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add app/services/pipeline.py tests/services/test_flashcards_synth.py
git commit -m "feat(flashcards): synth renders type/difficulty + explanation/example/misconception"
```

---

### Task 3: Rewrite the 7 subject flashcards prompts

**Files (modify each):**
`prompts/{biology,english,geometriya-g7-11,history,kimyo-g7-11,math-algebra,physics}/flashcards.md`

Each currently describes a 3-field card. Rewrite the card-format section of each to the 8-field card. Preserve each subject's existing examples/voice and the existing `[Diagram: ...]` no-raw-SVG rule and the stable `card_1, card_2…` id rule.

- [ ] **Step 1: For each file, replace the card-format / output section with this 8-field spec** (adapt the `type` list + examples to the subject):

```markdown
## Card format — 8 fields

Each card emits these fields:
- `id` — stable sequential `card_1, card_2, …` (never skip/reuse).
- `front` — the cue (term / question / prompt). **3–14 words.**
- `back` — the answer (definition / value). **5–22 words, never over 25** (formula/process exception).
- `type` — one of: <SUBJECT'S ALLOWED TYPES, e.g. for sciences: definition, term_to_meaning, formula, process_step, question_answer, misconception, image_label>.
- `difficulty` — `easy | medium | hard`.
- `hint` (optional) — a nudge, ≤12 words, never the answer.
- `explanation` (optional, encouraged) — 1 short sentence on why/how.
- `example` (optional, encouraged) — 1 short concrete example.
- `misconception` (optional) — 1 sentence naming a common wrong idea. **Required for trap / false-friend cards.**

Rules:
- One retrievable idea per card. Do NOT fold explanation/example/misconception into `back`.
- Every card MUST set `type` and `difficulty`.
- Diagrams: use a bracket `[Diagram: ...]` description — do NOT emit raw inline `<svg>`.
- Deck size: <existing per-difficulty count, e.g. easy 5–8 / hard 8–12>.
```
Replace `<SUBJECT'S ALLOWED TYPES>` and `<existing per-difficulty count>` per subject (sciences→biology/physics/kimyo; math_family→math-algebra/geometriya add `example`, drop `image_label`; languages→english add `vocabulary, grammar`; humanities→history add `process_step`). Keep each file's existing example cards but ensure they show `type`/`difficulty`.

- [ ] **Step 2: Sanity-check prompts load.**

Run: `.venv/Scripts/python.exe -m pytest tests/ -k "prompt_coverage or flow" -q`
Expected: PASS.

- [ ] **Step 3: Commit.**

```bash
git add prompts/biology/flashcards.md prompts/english/flashcards.md prompts/geometriya-g7-11/flashcards.md prompts/history/flashcards.md prompts/kimyo-g7-11/flashcards.md prompts/math-algebra/flashcards.md prompts/physics/flashcards.md
git commit -m "feat(flashcards): rewrite 7 subject prompts to the 8-field active-recall card"
```

---

### Task 4: Live smoke + worklog + push

**Files:**
- Temporary (throwaway, untracked — `rm` after): `verify_flashcards.py`
- Modify: `docs/memory/MASTER_MEMORY.md`, `docs/memory/INDEX.md`

- [ ] **Step 1: Full suite green.**

Run: `.venv/Scripts/python.exe -m pytest tests/ -q`
Expected: PASS.

- [ ] **Step 2: Live flashcards smoke.** Write a throwaway `verify_flashcards.py` that runs the `flashcards` phase for the geometry book section (lesson_context from job `c6457a80` extract `phase_output` id `57e55ade-f0b3-4384-a9a8-a78f056900ad`; reuse the flashcards `phase_output` id from that job for usage, or pass `phase_output_id=None`), provider `claude`, `response_schema=FlashcardsPack`, prompt `get_prompt("geometriya-g7-11","flashcards")`. Print: number of cards, whether every card has non-empty `type` and `difficulty`, and assert no `<svg` substring in the dumped JSON. Run:

```bash
PYTHONUTF8=1 DATABASE_URL='postgresql+asyncpg://edu:edu@localhost:5433/edu_homework' AUTH_TOKEN='' .venv/Scripts/python.exe verify_flashcards.py
```
Expected: 6–12 cards, every card has `type`+`difficulty`, no raw `<svg>`. If a card is missing `type`/`difficulty`, tighten the prompt and re-run.

- [ ] **Step 3: Remove the throwaway.**

```bash
rm verify_flashcards.py
```

- [ ] **Step 4: Worklog 0016.** Append a `## [0016]` entry to `docs/memory/MASTER_MEMORY.md` (8-field card shipped, schema + synth + 7 prompts, live smoke result) and add the one-line pointer to `docs/memory/INDEX.md`.

- [ ] **Step 5: Commit + push.**

```bash
git add docs/memory/MASTER_MEMORY.md docs/memory/INDEX.md
git commit -m "docs: worklog 0016 — flashcards 8-field active-recall card"
git push origin Nggaev-v2
```

---

## Self-Review

**Spec coverage:** §3 schema → Task 1. §5 synth → Task 2. §4 prompts (7 files) → Task 3. §6 tests → embedded per task + Task 1 Step 5 sweep. §7 back-compat → no task (write-only). §8 risks (2 required enums) → covered by closed-enum tests + live smoke. §9 acceptance → Task 4. All covered.

**Placeholder scan:** No TBD/TODO. Task 3 gives the exact 8-field section to insert; `<SUBJECT'S ALLOWED TYPES>`/`<count>` are explicit per-subject substitutions (the superset + subset mapping is named), not vague placeholders. Task 4's `verify_flashcards.py` is described by behavior + exact ids + run command (throwaway, mirrors prior smokes).

**Type consistency:** `FlashcardType`/`FlashcardDifficulty` names + the field names (`type`, `difficulty`, `explanation`, `example`, `misconception`) are identical across Task 1 (schema), Task 2 (synth render), and Task 4 (smoke). Test helper `_valid_card` uses the same field names.
