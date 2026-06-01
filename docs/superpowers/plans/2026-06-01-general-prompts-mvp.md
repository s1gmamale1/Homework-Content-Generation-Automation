# General Prompts MVP (Path A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every subject generate homework from one set of general, subject-parameterized prompts via a single flow, with `classify`/easy-hard removed.

**Architecture:** New `prompts/_general/<phase>.md` (7 files) served to all subjects by a resolver in `prompts.py` (general-only now, `USE_SUBJECT_PROMPTS` switch for later, `{{SUBJECT}}` substitution). `flows.py` collapses to one `GENERAL_FLOW`. `classify` is removed from the schema map and the pipeline head; `difficulty` is pinned to `None` (not ripped out). Subject prompt files are read-only references, never modified.

**Tech Stack:** Python 3.13, FastAPI, Pydantic, pytest, `uv`. Spec: `docs/nets_general_prompts_mvp_design.md`.

---

## Conventions

- Run tests with: `"C:/Users/Recruiter/AppData/Roaming/Python/Python314/Scripts/uv.exe" run python -m pytest <args>` (or `.venv/Scripts/python.exe -m pytest`).
- Work on an **isolated branch off `Nggaev-v2`** (another session shares this repo). Do NOT edit any file under `prompts/<subject>/` — only read them.
- Baseline before starting: `uv run python -m pytest tests/ -q` should be green; note the count.

---

## File Structure

**Create (7 general prompts):**
- `prompts/_general/case-based-preview.md`
- `prompts/_general/flashcards.md`
- `prompts/_general/memory-check.md`
- `prompts/_general/practice-rlc.md`
- `prompts/_general/practice-error-detection.md`
- `prompts/_general/boss-arena.md`
- `prompts/_general/reflection.md`

**Modify:**
- `app/services/prompts.py` — resolver (general-only + switch + `{{SUBJECT}}` + keep `provider_suffix`).
- `app/services/flows.py` — single `GENERAL_FLOW`, `flow_for()`, `SUBJECTS`; drop `SUBJECT_FLOWS`/easy/hard/has_classify; trim `PHASE_DEPS`.
- `app/services/agent.py` — drop `"classify"` from `STRUCTURED_PHASE_SCHEMAS`.
- `app/services/pipeline.py` — classify-free `run()`; `difficulty` pinned `None`.

**Create/replace tests:**
- `tests/services/test_prompts_resolver.py` (new)
- `tests/services/test_general_flow.py` (new)
- `tests/services/test_prompt_coverage.py` (rewrite)
- `tests/services/test_practice_arc_flow.py` (retire — superseded by `test_general_flow.py`)

---

## Task 1: Author the 7 general prompts

**Files:** Create the 7 files under `prompts/_general/`.

This is content authoring, not code. For each prompt: **read** the named clean subject prompt, **copy** it, **strip** subject-specific framing, **insert** `{{SUBJECT}}`, then **cross-check** against the spec §4 "must enforce" list. Never modify the source file.

Generalization rules (apply to every file):
- Replace the subject name in the title/intro with `{{SUBJECT}}` (e.g. `# Prompt: Case-Based Preview — Biology` → `# Prompt: Case-Based Preview — {{SUBJECT}}`; "You are building … for a Biology homework session" → "… for a {{SUBJECT}} homework session").
- Replace subject-specific case types / examples / pair types with a one-line instruction to derive them from the lesson's `lesson_context` + source map (keep the *structural* rules verbatim).
- Keep every schema/structural rule and self-check verbatim.
- Keep the language block as **formal Uzbek ("Siz")** (do not add an English branch).

- [ ] **Step 1: `boss-arena.md`** — source: `prompts/biology/boss-arena.md` (all 7 are byte-identical; PASS). Copy verbatim, swap the subject word for `{{SUBJECT}}`. Cross-check: Why→How→What all required, no MCQ options, `concept_ids`, ≥4 questions.

- [ ] **Step 2: `memory-check.md`** — source: `prompts/biology/memory-check.md` (7 are near-identical; PASS). Generalize. Cross-check: 3 kinds; option objects `{text,is_correct,reason}` (4 for option-kinds, one correct); `fill_blank` → `blanks {answer,accepted_variations}`, no options; `why_prompt` + `expected_reasoning_keywords`; per-item `flashcard_id`; `pass_threshold` 0.60.

- [ ] **Step 3: `flashcards.md`** — source: `prompts/biology/flashcards.md` (PASS; do NOT use `physics/flashcards.md`, it's a mislabeled math clone). Generalize. Cross-check: stable unique `id`, required `type` + `difficulty`, optional `hint/explanation/example/misconception/cluster`.

- [ ] **Step 4: `practice-error-detection.md`** — source: `prompts/biology/practice-error-detection.md` (PASS). Generalize (keep the `pattern` Literal + the "why mandatory for math/science" rule). Cross-check: exactly one `is_error` block, type-the-correction, `concept_ids`.

- [ ] **Step 5: `practice-rlc.md`** — source: `prompts/physics/practice-rlc.md`. Generalize AND **remove the fabricated "Reverse-test variant (required, §6)" section** (the spec has no such variant). Cross-check: first-person expert, predict→decide→justify+confidence, real-misconception distractors, Strip Test, `concept_ids`.

- [ ] **Step 6: `reflection.md`** — source: `prompts/physics/reflection.md` (clean; do NOT use biology/history/english — they carry stale v1 phase refs). Generalize. Cross-check: short debrief, tied to performance, no deleted-phase references.

- [ ] **Step 7: `case-based-preview.md`** — source: `prompts/biology/case-based-preview.md`. Generalize. **Apply the known fix:** the source's JSON example (the ```json block) jumps from `checkpoints` to `decision_process_explanation` — INSERT both `learning_block_1` and `learning_block_2` keys in the JSON example, shape `{ "explanation": "...", "title": "...", "visual_svg": null, "source_concept_id": "..." }`, between `checkpoints` and `decision_process_explanation`. Cross-check: 3 checkpoints, both learning blocks in prose AND JSON, DPE `options:null` before simulation, method unnamed, `source_concept_ids`.

- [ ] **Step 8: Verify all 7 exist and are parameterized**

Run:
```bash
ls prompts/_general/ && grep -L "{{SUBJECT}}" prompts/_general/*.md
```
Expected: 7 `.md` files listed; `grep -L` prints **nothing** (every file contains `{{SUBJECT}}`).

- [ ] **Step 9: Commit**

```bash
git add prompts/_general/
git commit -m "feat(flow-v2): general prompts (_general) for all subjects — Path A"
```

---

## Task 2: Prompt resolver in `prompts.py`

**Files:**
- Modify: `app/services/prompts.py`
- Test: `tests/services/test_prompts_resolver.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/services/test_prompts_resolver.py
import importlib
import pytest
from app.services import prompts as P


@pytest.fixture
def tmp_prompts(tmp_path, monkeypatch):
    (tmp_path / "_general").mkdir()
    (tmp_path / "_general" / "boss-arena.md").write_text(
        "Boss for {{SUBJECT}} only.", encoding="utf-8")
    (tmp_path / "physics").mkdir()
    (tmp_path / "physics" / "boss-arena.md").write_text(
        "SUBJECT-SPECIFIC physics boss.", encoding="utf-8")
    monkeypatch.setattr(P, "PROMPTS_DIR", tmp_path)
    P._cache.clear(); P._hash_cache.clear()
    return tmp_path


def test_general_only_and_subject_substitution(tmp_prompts):
    # USE_SUBJECT_PROMPTS is False → always _general, {{SUBJECT}} substituted.
    out = P.get_prompt("physics", "boss-arena")
    assert "{{SUBJECT}}" not in out
    assert "Boss for" in out and "Physics" in out
    assert "SUBJECT-SPECIFIC" not in out  # subject file ignored in MVP


def test_provider_suffix_preserved(tmp_prompts):
    out = P.get_prompt("physics", "boss-arena", provider_suffix="USE $imagegen")
    assert out.endswith("USE $imagegen")


def test_switch_prefers_subject_when_enabled(tmp_prompts, monkeypatch):
    monkeypatch.setattr(P, "USE_SUBJECT_PROMPTS", True)
    P._cache.clear(); P._hash_cache.clear()
    out = P.get_prompt("physics", "boss-arena")
    assert "SUBJECT-SPECIFIC" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/services/test_prompts_resolver.py -v`
Expected: FAIL (current `get_prompt` reads `prompts/<subject>/`, has no `{{SUBJECT}}` substitution, no `USE_SUBJECT_PROMPTS`).

- [ ] **Step 3: Rewrite `app/services/prompts.py`**

```python
import hashlib
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"
GENERAL_DIR = "_general"
# MVP: general prompts serve every subject. Set True later to prefer a
# subject-specific prompt when prompts/<subject>/<phase>.md exists.
USE_SUBJECT_PROMPTS = False

SUBJECT_LABELS = {
    "biology": "Biology (Biologiya)",
    "english": "English",
    "geometriya-g7-11": "Geometry (Geometriya)",
    "history": "History (Tarix)",
    "kimyo-g7-11": "Chemistry (Kimyo)",
    "math-algebra": "Mathematics / Algebra (Matematika / Algebra)",
    "physics": "Physics (Fizika)",
}

_cache: dict[str, dict[str, str]] = {}
_hash_cache: dict[str, dict[str, str]] = {}


def _resolve_dir(subject: str, phase_name: str) -> str:
    if USE_SUBJECT_PROMPTS and (PROMPTS_DIR / subject / f"{phase_name}.md").is_file():
        return subject
    return GENERAL_DIR


def _load_dir(dirname: str) -> tuple[dict[str, str], dict[str, str]]:
    d = PROMPTS_DIR / dirname
    if not d.is_dir():
        raise FileNotFoundError(f"Prompt directory not found: {d}")
    bodies: dict[str, str] = {}
    hashes: dict[str, str] = {}
    for md in d.glob("*.md"):
        body = md.read_text(encoding="utf-8")
        bodies[md.stem] = body
        hashes[md.stem] = hashlib.sha256(body.encode("utf-8")).hexdigest()
    return bodies, hashes


def load_all() -> None:
    dirs = {GENERAL_DIR}
    if USE_SUBJECT_PROMPTS:
        from app.services.flows import SUPPORTED_SUBJECTS
        dirs.update(SUPPORTED_SUBJECTS)
    for dirname in dirs:
        bodies, hashes = _load_dir(dirname)
        _cache[dirname] = bodies
        _hash_cache[dirname] = hashes


def _raw(dirname: str, phase_name: str) -> tuple[str, str]:
    if dirname not in _cache:
        bodies, hashes = _load_dir(dirname)
        _cache[dirname] = bodies
        _hash_cache[dirname] = hashes
    if phase_name not in _cache[dirname]:
        raise KeyError(f"Prompt {dirname}/{phase_name}.md not found")
    return _cache[dirname][phase_name], _hash_cache[dirname][phase_name]


def get_prompt(subject: str, phase_name: str, provider_suffix: str = "") -> str:
    dirname = _resolve_dir(subject, phase_name)
    body, _h = _raw(dirname, phase_name)
    body = body.replace("{{SUBJECT}}", SUBJECT_LABELS.get(subject, subject))
    if provider_suffix:
        body = body + "\n\n" + provider_suffix
    return body


def get_prompt_hash(subject: str, phase_name: str) -> str:
    # Provenance only (recorded on agent_usages rows); does NOT drive cross-job
    # reuse — extract uses its own "builtin:extract:v1" hash in pipeline.py.
    dirname = _resolve_dir(subject, phase_name)
    _b, h = _raw(dirname, phase_name)
    return h
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/services/test_prompts_resolver.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add app/services/prompts.py tests/services/test_prompts_resolver.py
git commit -m "feat(flow-v2): _general prompt resolver + {{SUBJECT}} substitution"
```

---

## Task 3: Single flow in `flows.py` + update flow tests

**Files:**
- Modify: `app/services/flows.py`
- Test: `tests/services/test_general_flow.py` (new), `tests/services/test_prompt_coverage.py` (rewrite), `tests/services/test_practice_arc_flow.py` (delete)

- [ ] **Step 1: Write the failing tests**

```python
# tests/services/test_general_flow.py
import pytest
from app.services import flows


def test_flow_is_identical_for_every_subject():
    expected = [
        "case-based-preview", "flashcards", "memory-check",
        "practice-rlc", "practice-error-detection",
        "boss-arena", "reflection",
    ]
    for subject in flows.SUPPORTED_SUBJECTS:
        assert flows.flow_for(subject) == expected


def test_no_easy_hard_or_classify():
    assert not hasattr(flows, "SUBJECT_FLOWS")
    src = (flows.__file__)
    text = open(src, encoding="utf-8").read()
    assert "has_classify" not in text
    for phase in flows.GENERAL_FLOW:
        assert phase != "classify"


def test_phase_deps_have_no_reading_or_cbp_mode_games():
    assert "reading" not in flows.PHASE_DEPS
    for dead in ("practice-memory-match", "practice-tictactoe",
                 "practice-jigsaw", "practice-sentence"):
        assert dead not in flows.PHASE_DEPS


def test_unknown_subject_raises():
    with pytest.raises(KeyError):
        flows.flow_for("chemistry-unknown")
```

```python
# tests/services/test_prompt_coverage.py  (REPLACE the whole file)
"""Fail-fast: every phase in the general flow has a _general prompt."""
import pytest
from app.services import flows
from app.services.prompts import get_prompt

_PAIRS = [(s, p) for s in flows.SUPPORTED_SUBJECTS for p in flows.GENERAL_FLOW]


@pytest.mark.parametrize("subject,phase", _PAIRS)
def test_every_flow_phase_has_a_general_prompt(subject, phase):
    body = get_prompt(subject, phase)
    assert body.strip(), f"empty prompt for {phase}"
    assert "{{SUBJECT}}" not in body  # substituted
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/services/test_general_flow.py -v`
Expected: FAIL (`flows.flow_for` / `flows.GENERAL_FLOW` / `flows.SUPPORTED_SUBJECTS` don't exist in the new shape yet; `SUBJECT_FLOWS` still present).

- [ ] **Step 3: Rewrite the top of `app/services/flows.py`**

Replace the module docstring + the entire `SUBJECT_FLOWS`/`SUPPORTED_SUBJECTS` block (lines 1–100) with:

```python
"""Single Flow v2 phase sequence for every subject (MVP — no classify, no
easy/hard). Subject-specific prompts/flows are a future override layer.
New_Flow.md (docs/Infra_prompts/Flow) is the source of truth, NOT flow.md."""

import re

_SVG_BLOCK_RE = re.compile(r"<svg\b[^>]*>.*?</svg>", re.DOTALL | re.IGNORECASE)


def _strip_svgs(text: str) -> str:
    return _SVG_BLOCK_RE.sub("[diagram omitted]", text)


SUBJECTS: list[str] = [
    "biology", "english", "geometriya-g7-11", "history",
    "kimyo-g7-11", "math-algebra", "physics",
]

# One flow, all subjects. Learning sections → practice arc (2 light games) →
# Boss → Reflection. CBP-mode games are Path B (after CbpModeGame is lightened).
GENERAL_FLOW: list[str] = [
    "case-based-preview", "flashcards", "memory-check",
    "practice-rlc", "practice-error-detection",
    "boss-arena", "reflection",
]


def flow_for(subject: str) -> list[str]:
    if subject not in SUBJECTS:
        raise KeyError(f"Unsupported subject: {subject}")
    return list(GENERAL_FLOW)


SUPPORTED_SUBJECTS: list[str] = sorted(SUBJECTS)
```

Then replace `PHASE_DEPS` (the dict) with:

```python
PHASE_DEPS: dict[str, list[str]] = {
    "memory-check":             ["flashcards"],
    "practice-rlc":             ["case-based-preview", "flashcards"],
    "practice-error-detection": ["case-based-preview", "flashcards", "memory-check"],
    "boss-arena":               ["case-based-preview", "flashcards", "memory-check"],
    "reflection":               ["case-based-preview", "boss-arena"],
}
```

And replace `MAX_OUTPUT_TOKENS_BY_PHASE` with only the live key:

```python
MAX_OUTPUT_TOKENS_BY_PHASE: dict[str, int] = {
    "reflection": 700,
}
```

Leave `max_output_tokens_for`, `file_needed_phases`, `PHASE_FILE_NEEDED`, `filter_prior_outputs`, and `resolve_phase_deps` as-is (they already work off `PHASE_DEPS`). In `filter_prior_outputs`/`resolve_phase_deps` docstrings, the "preview-*" alias mentions are now moot but harmless — leave the code unchanged.

- [ ] **Step 4: Delete the obsolete flow test**

```bash
git rm tests/services/test_practice_arc_flow.py
```
(Its assertions were about `SUBJECT_FLOWS` easy/hard + all-six-games — superseded by `test_general_flow.py`.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run python -m pytest tests/services/test_general_flow.py tests/services/test_prompt_coverage.py -v`
Expected: PASS (all parametrized pairs green).

- [ ] **Step 6: Commit**

```bash
git add app/services/flows.py tests/services/test_general_flow.py tests/services/test_prompt_coverage.py
git commit -m "feat(flow-v2): single GENERAL_FLOW for all subjects; drop easy/hard"
```

---

## Task 4: Remove `classify` from the schema map

**Files:**
- Modify: `app/services/agent.py`
- Test: add to `tests/services/test_general_flow.py`

- [ ] **Step 1: Add the failing test**

Append to `tests/services/test_general_flow.py`:

```python
def test_classify_not_registered():
    from app.services.agent import STRUCTURED_PHASE_SCHEMAS
    assert "classify" not in STRUCTURED_PHASE_SCHEMAS
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run python -m pytest tests/services/test_general_flow.py::test_classify_not_registered -v`
Expected: FAIL (`"classify"` is currently in the map).

- [ ] **Step 3: Edit `app/services/agent.py`**

In `STRUCTURED_PHASE_SCHEMAS`, delete the line `"classify": ClassifyDecision,`. Then remove the now-unused `ClassifyDecision` import (find it near the top of `agent.py`). Run `grep -n ClassifyDecision app/services/agent.py` and confirm zero remaining references before deleting the import.

- [ ] **Step 4: Run it to verify it passes**

Run: `uv run python -m pytest tests/services/test_general_flow.py::test_classify_not_registered -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/agent.py tests/services/test_general_flow.py
git commit -m "feat(flow-v2): drop classify from STRUCTURED_PHASE_SCHEMAS"
```

---

## Task 5: Classify-free pipeline (`difficulty` pinned None)

**Files:**
- Modify: `app/services/pipeline.py`

No new unit test (the pipeline `run()` needs a DB to exercise); verification is import-smoke + the full suite staying green. We make `classify` never run and replace the `SUBJECT_FLOWS` references; `difficulty` stays a local `None` threaded through the existing `Optional[str] = None` params (it only ever became the string `"Difficulty: unspecified"` in a prompt, so None is safe and identical to the old "easy/hard not yet set" state).

- [ ] **Step 1: Fix the flows import**

In `app/services/pipeline.py`, find the import of `SUBJECT_FLOWS` from `app.services.flows` and replace `SUBJECT_FLOWS` with `flow_for` (keep the other names imported on that line).

- [ ] **Step 2: Replace the sequence-planning block (currently ~lines 455–465)**

Old:
```python
        # ─── plan phase sequence ───────────────────────────────
        flow = SUBJECT_FLOWS[subject]
        sequence: list[str] = ["extract"]
        if flow["has_classify"]:
            sequence.append("classify")
        else:
            sequence.extend(flow["hard"])
        log.info(
            f"[job {job_id}] sequence planned | has_classify={flow['has_classify']} "
            f"initial_phases={sequence}"
        )
```
New:
```python
        # ─── plan phase sequence (single flow — no classify/easy-hard) ──
        sequence: list[str] = ["extract", *flow_for(subject)]
        log.info(f"[job {job_id}] sequence planned | phases={sequence}")
```

- [ ] **Step 3: Replace the head-phases block (currently ~lines 491–493)**

Old:
```python
        head_phases: list[str] = ["extract"]
        if flow["has_classify"]:
            head_phases.append("classify")
```
New:
```python
        head_phases: list[str] = ["extract"]
```

- [ ] **Step 4: Delete the classify branch (currently ~lines 566–587)**

Delete the entire `elif phase_name == "classify":` block (from `elif phase_name == "classify":` through the `log.info(... appended_phases ...)` call). The `if phase_name == "extract":` block directly above it stays. Leave the `difficulty: Optional[str] = None` local (line ~471) as-is — it is now never reassigned, which is intended.

- [ ] **Step 5: Verify import + full suite**

Run:
```bash
uv run python -c "import main; from app.services import pipeline, flows, agent; print('IMPORT_OK')"
uv run python -m pytest tests/ -q
```
Expected: `IMPORT_OK`, and the suite is **green**. If anything references `SUBJECT_FLOWS`/`has_classify`/`ClassifyDecision`, fix per the message. (Run `grep -rn "SUBJECT_FLOWS\|has_classify\|ClassifyDecision\|flow\[" app/ tests/` and resolve any live hits; `_parse_classify` in pipeline.py is now dead — leaving it is fine, or remove it.)

- [ ] **Step 6: Commit**

```bash
git add app/services/pipeline.py
git commit -m "feat(flow-v2): classify-free pipeline run() (difficulty pinned None)"
```

---

## Task 6: Full verification + real smoke + worklog

**Files:** none (verification); then `docs/memory/` worklog.

- [ ] **Step 1: Full suite green**

Run: `uv run python -m pytest tests/ -q`
Expected: all pass (baseline count minus the deleted practice-arc-flow tests, plus the new resolver/general-flow tests).

- [ ] **Step 2: Real `claude`-CLI smoke (2 subjects)**

With the stack able to reach the `claude` CLI, generate a job for **physics** and one for **english**, and confirm each runs `extract → case-based-preview → flashcards → memory-check → practice-rlc → practice-error-detection → boss-arena → reflection` (no `classify`), and that `cbp_json` (with both `learning_block_1/2`), `memory_check_json`, `practice_rlc_json`, `practice_error_detection_json`, and `boss_arena_json` are schema-valid. Output is formal Uzbek. No phase exceeds a few minutes.

- [ ] **Step 3: Worklog entry**

Add an entry to `docs/memory/MASTER_MEMORY.md` (+ `INDEX.md` row): what shipped (general prompts + single flow + classify removal), that subject prompts + `flow.md`/`instruction.md`/`classify.md` are now dead-but-untouched, and that Path B (lighten `CbpModeGame` + re-add the subject-matched game) is the next workstream.

- [ ] **Step 4: Commit**

```bash
git add docs/memory/
git commit -m "docs(memory): worklog — general prompts MVP (Path A) shipped"
```

---

## Self-Review (done while writing)

- **Spec coverage:** resolver+switch+`{{SUBJECT}}`+`provider_suffix` (Task 2 / spec §3.1) ✓; single flow + `flow_for` (Task 3 / §3.2) ✓; 7 general prompts seeded from clean subject prompts + CBP JSON fix + RLC reverse-test removal (Task 1 / §4) ✓; classify removal across schema map + pipeline + difficulty (Tasks 4–5 / §5.3) ✓; Uzbek default, no reading, no CBP-mode games (Tasks 1,3 / §2) ✓; tests (Tasks 2,3,4 / §7) ✓; worklog dead-docs note (Task 6 / §8) ✓.
- **Placeholders:** none — every code step shows code; prompt-authoring steps name the exact source file + transformations + cross-check.
- **Type/name consistency:** `flow_for`, `GENERAL_FLOW`, `SUBJECTS`, `SUPPORTED_SUBJECTS`, `_resolve_dir`, `USE_SUBJECT_PROMPTS`, `SUBJECT_LABELS` used identically across tasks.
- **No subject-prompt edits:** Task 1 only reads them; Task 3 deletes one obsolete test; nothing writes under `prompts/<subject>/`.
