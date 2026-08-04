# Structured `content_json` — HCGA Producer Implementation Plan (Pass 1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate the platform's canonical `content_json` directly for `practice-rlc` and
`practice-sentence`, render markdown *from* that JSON, and ship a committed exporter that posts the
complete ingest envelope.

**Architecture:** The model authors JSON against a strict Pydantic schema. Every generation path
(initial, judge regen, solver regen, markdown fallback) returns one atomic `PhaseArtifact` carrying
both `content_json` and the markdown rendered from it, so the two can never desynchronize. Markdown
remains the substrate for judge/solver/lint/audit/Notion/console — it just stops being authored.

**Tech Stack:** Python 3.14, Pydantic v2 (strict), SQLAlchemy 2 + asyncpg, Alembic, pytest.

**Spec:** `docs/superpowers/specs/2026-08-03-content-json-output-design.md` (rev 5).

**Worktree:** `/Users/macmini5/Documents/HCGA-content-json` on `Nggaev-v2`. Do **not** use the main
checkout at `/Users/macmini5/Documents/Homework-Content-Generation-Automation` — another session
owns it.

## Scope

This plan covers **HCGA only**. Two sibling plans are required and are NOT in scope here:

- **Platform** (`Class-A-Education-Platform-Backend`, base `origin/Akademiya-AI` @ `2cf98fb`) —
  native path in `homework_transform.py`, `TRANSFORMER_VERSION_CHB` bump in `emission.py`, strict
  projection, scrub-then-revalidate, `min_chars` bound.
- **Mobile** (`Class-A-Education-Mobile`, base `origin/main` @ `f761c5a`) — `configToSteps()` must
  pass `minChars`.

### Ship order and what may NOT be claimed

- **HCGA may ship first**, but until the platform plan lands this is **producer-only**: it does
  **not** reduce the live 22.8% failure rate, and **no phase becomes `native`**. Do not claim
  platform adoption in the worklog, PR description, or acceptance notes.
- **The platform plan must preserve markdown fallback** for absent or invalid structured data — its
  native path is additive, never a replacement.
- **Mobile must land before RLC is declared complete.** Without `minChars` passed through
  `configToSteps()`, RLC ships a real grading-parity defect (mobile permits 10 chars, server grades
  at the configured/default 80). RLC is "done" only once the mobile fix is deployed.
- **Final acceptance runs across all three deployed heads** — not against local checkouts. See
  "Cross-repo final acceptance" at the end of this plan.

## Pre-flight: branch-collision gate (run before Task 1, and again on resume)

This machine runs multiple sessions against this repo. **Before the first edit:**

```bash
cd /Users/macmini5/Documents/HCGA-content-json
git fetch --all --prune
git worktree list                      # who holds what
git branch -a                          # look for overlapping lanes
gh pr list --state open --json number,title,headRefName
git log HEAD..origin/Nggaev-v2         # has the base moved?
```

Inspect for work overlapping `app/services/pipeline.py`, `app/services/prompts.py`,
`app/schemas/`, `prompts/_general/`, or `alembic/versions/`. **Known live lane:**
`feat/model-config-3x-flash` touches `pipeline.py` and plans migration `0049` — serialize behind it
(see Task 4 Step 1).

Keep the scan **read-only**: never switch, reset, clean or commit on another session's branch or
worktree. **Verify the branch before every commit** — `git rev-parse --abbrev-ref HEAD` must be
`Nggaev-v2`, and abort if it is not. Repeat this gate when resuming stale work or when the base moves.

### Gate result — 2026-08-03, recorded per the standing rule

Refs inspected: `origin/Nggaev-v2` @ `36725fa` · `feat/model-config-3x-flash` @ `94dad05`
(worktree = main checkout) · `feat/model-config-3x-flash-exec` @ `8e6b558`
(worktree `../HCGA-model-config`, **unpushed, no PR**) · `feat/gemini-global-default` @ `f82bdc2` ·
`fix/dashboard-mobile-wrap` @ `3ff8403` (PR #108 open).

**Conclusion: no blocking overlap.** The model-config lane touches `agent_models.py`,
`model_tiers.py`, `pricing.py`, `config.py`, `jobs.py`, `batch.py`, `job_reactivation.py`,
`teaching_audit.py` and `scripts/*` — but **not** `pipeline.py`, **not** `prompts/`, **not**
`app/schemas/`. The single interaction is migration numbering: **`0049` is claimed**, so this plan
takes **`0050`** (see Task 4 Step 1).

No serialization needed; Tasks 1–3 and 5–9 may proceed immediately. Re-run this gate on resume.

## Global Constraints

- **Never `git add -A`.** Stage only the files each task lists — other sessions share this repo.
- **Commit per task.** Conventional-commit messages, trailer
  `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`.
- **`uv run python -m pytest`** — never bare `pytest`.
- **Contract authority is `origin/Akademiya-AI`**, never the stale local `Nggaev` checkout of the
  platform repo (668 commits behind).
- **Normalization** is exactly mobile's: `s.strip().lower()` then collapse internal whitespace runs
  to a single space.
- **`min_chars` bound: 20 ≤ n ≤ 1000.**
- **Sentence-fill Pass 1 is `mode="word_bank"` only.**
- **No live model calls** except Task 9's acceptance smoke.
- Full suite must be green before the final commit: `uv run python -m pytest tests/ -q`.

---

### Task 1: Subject mapping + pure payload builder

**Files:**
- Create: `app/services/platform_payload.py`
- Test: `tests/services/test_platform_payload.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces:
  - `class SubjectMapError(RuntimeError)`
  - `load_subject_map(raw: str) -> dict[str, int]`
  - `build_ingest_payload(*, job: dict, phases: list[dict], subject_map: dict[str, int]) -> dict`

`job` keys used: `id` (UUID or str), `book_id` (UUID or str), `subject` (str), `grade` (str|int),
`output_language` (str). `phases` items use: `phase_name`, `output_md`, `content_json`,
`content_schema_version`, `authoring_mode`, `judge_status`, `status`.

- [ ] **Step 1: Write the failing test**

```python
import pytest
from app.services import platform_payload as pp


def test_load_subject_map_rejects_non_positive_and_malformed():
    assert pp.load_subject_map('{"history": 7}') == {"history": 7}
    with pytest.raises(pp.SubjectMapError):
        pp.load_subject_map('{"history": 0}')
    with pytest.raises(pp.SubjectMapError):
        pp.load_subject_map('{"history": -3}')
    with pytest.raises(pp.SubjectMapError):
        pp.load_subject_map('{"history": "7"}')
    with pytest.raises(pp.SubjectMapError):
        pp.load_subject_map('{"history": true}')   # bool is not a valid id
    with pytest.raises(pp.SubjectMapError):
        pp.load_subject_map("not json")


def _job():
    return {
        "id": "11111111-1111-1111-1111-111111111111",
        "book_id": "22222222-2222-2222-2222-222222222222",
        "subject": "history",
        "grade": "8",
        "output_language": "ru",
    }


def _phase(**kw):
    base = {
        "phase_name": "practice-rlc",
        "output_md": "# x",
        "content_json": {"a": 1},
        "content_schema_version": "rlc_config@1",
        "authoring_mode": "structured",
        "judge_status": "ok",
        "status": "done",
    }
    base.update(kw)
    return base


def test_build_payload_shape_and_string_uuids():
    out = pp.build_ingest_payload(
        job=_job(), phases=[_phase()], subject_map={"history": 7}
    )
    assert out["source"] == "hcg"
    assert out["source_ref"] == "22222222-2222-2222-2222-222222222222"
    assert out["external_key"] == "11111111-1111-1111-1111-111111111111"
    assert isinstance(out["source_ref"], str) and isinstance(out["external_key"], str)
    assert out["language"] == "ru"
    assert out["subject_id"] == 7
    assert out["grade"] == "8"
    assert isinstance(out["phases"], list)          # LIST, not dict
    assert out["phases"][0]["phase_name"] == "practice-rlc"


def test_build_payload_missing_subject_mapping_is_hard_error():
    with pytest.raises(pp.SubjectMapError):
        pp.build_ingest_payload(job=_job(), phases=[_phase()], subject_map={"biology": 3})


def test_build_payload_excludes_extract_and_non_done_and_empty():
    phases = [
        _phase(phase_name="extract"),
        _phase(phase_name="practice-sentence", status="failed"),
        _phase(phase_name="flashcards", output_md=""),
        _phase(),
    ]
    out = pp.build_ingest_payload(job=_job(), phases=phases, subject_map={"history": 7})
    assert [p["phase_name"] for p in out["phases"]] == ["practice-rlc"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/services/test_platform_payload.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.platform_payload'`

- [ ] **Step 3: Write minimal implementation**

```python
"""Pure builder for the platform's homework-import envelope.

No I/O: the subject map is injected as a dict so the builder stays unit-testable.
The platform iterates ``payload["phases"]`` as a LIST of objects keyed
``phase_name`` — never a dict.
"""
from __future__ import annotations

import json
from typing import Any


class SubjectMapError(RuntimeError):
    """Malformed subject map, or a subject with no platform id."""


def load_subject_map(raw: str) -> dict[str, int]:
    """Parse the PLATFORM_SUBJECT_MAP JSON: canonical HCGA subject -> platform id."""
    try:
        data = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise SubjectMapError(f"subject map is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise SubjectMapError("subject map must be a JSON object")
    out: dict[str, int] = {}
    for key, value in data.items():
        # bool is a subclass of int — reject it explicitly.
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise SubjectMapError(f"subject '{key}': platform id must be a positive int")
        out[str(key)] = value
    return out


_ENVELOPE_KEYS = (
    "phase_name", "output_md", "content_json",
    "content_schema_version", "authoring_mode", "judge_status",
)


def build_ingest_payload(
    *, job: dict, phases: list[dict], subject_map: dict[str, int]
) -> dict[str, Any]:
    """Build the complete ingest envelope for one done job."""
    subject = job["subject"]
    if subject not in subject_map:
        raise SubjectMapError(
            f"no platform subject_id mapped for HCGA subject '{subject}'"
        )
    rows = [
        {k: p.get(k) for k in _ENVELOPE_KEYS}
        for p in phases
        if p.get("phase_name") != "extract"
        and p.get("status") == "done"
        and (p.get("output_md") or "").strip()
    ]
    return {
        "source": "hcg",
        "source_ref": str(job["book_id"]),
        "external_key": str(job["id"]),
        "language": job["output_language"],
        "subject_id": subject_map[subject],
        "grade": str(job["grade"]),
        "phases": rows,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/services/test_platform_payload.py -q`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add app/services/platform_payload.py tests/services/test_platform_payload.py
git commit -m "feat(platform): pure ingest-payload builder + subject map

The platform iterates phases as a LIST keyed phase_name; the untracked
export_homeworks.py emitted a dict. Subject ids come from an explicit map
(never inferred) and a miss is a hard error before any HTTP call. UUIDs
serialize as strings.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `content_json` schemas (strict Pydantic)

**Files:**
- Create: `app/schemas/content_json/__init__.py`
- Create: `app/schemas/content_json/common.py`
- Create: `app/schemas/content_json/rlc.py`
- Create: `app/schemas/content_json/sentence_fill.py`
- Test: `tests/schemas/test_content_json.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces:
  - `norm(s: str) -> str` (mobile-exact normalization)
  - `RlcConfig` with `SCHEMA_VERSION = "rlc_config@1"`
  - `SentenceFillConfig` with `SCHEMA_VERSION = "sentence_fill_config@1"`
  - `SCHEMAS: dict[str, type[BaseModel]]` mapping phase name → model

- [ ] **Step 1: Write the failing test**

```python
import pytest
from pydantic import ValidationError

from app.schemas.content_json import SCHEMAS, RlcConfig, SentenceFillConfig, norm


def test_norm_matches_mobile():
    assert norm("  Hello   World ") == "hello world"


def _rlc(**over):
    def opts(n=2):
        return [{"id": f"o{i}", "label": f"L{i}", "is_correct": i == 0} for i in range(n)]
    cfg = {
        "id": "c1", "title": "T", "intro": "I", "expert_role": "historian",
        "steps": [
            {"id": "s1", "kind": "decision", "title": "a", "prompt": "p", "options": opts()},
            {"id": "s2", "kind": "info_request", "title": "b", "prompt": "p", "options": opts()},
            {"id": "s3", "kind": "final_decision", "title": "c", "prompt": "p", "options": opts()},
            {"id": "s4", "kind": "concept_select", "title": "d", "prompt": "p",
             "concept_chips": [{"id": "c1", "label": "A", "is_correct": True},
                               {"id": "c2", "label": "B", "is_correct": False}]},
            {"id": "s5", "kind": "reasoning", "title": "e", "prompt": "p", "min_chars": 80},
        ],
    }
    cfg.update(over)
    return cfg


def test_rlc_happy_path():
    m = RlcConfig.model_validate(_rlc())
    assert m.SCHEMA_VERSION == "rlc_config@1"
    assert len(m.steps) == 5


def test_rlc_rejects_bad_expert_role():
    with pytest.raises(ValidationError):
        RlcConfig.model_validate(_rlc(expert_role="wizard"))


def test_rlc_rejects_extra_keys():
    with pytest.raises(ValidationError):
        RlcConfig.model_validate(_rlc(answer_key="Paris"))


@pytest.mark.parametrize("bad", [-1, 0, 19, 1001, True])
def test_rlc_min_chars_bounded(bad):
    cfg = _rlc()
    cfg["steps"][4]["min_chars"] = bad
    with pytest.raises(ValidationError):
        RlcConfig.model_validate(cfg)


def test_rlc_requires_exactly_one_correct_option():
    cfg = _rlc()
    for o in cfg["steps"][0]["options"]:
        o["is_correct"] = True
    with pytest.raises(ValidationError):
        RlcConfig.model_validate(cfg)


def test_rlc_option_labels_normalized_unique_and_non_empty():
    cfg = _rlc()
    cfg["steps"][0]["options"][1]["label"] = "  l0  "   # normalizes to "l0"
    with pytest.raises(ValidationError):
        RlcConfig.model_validate(cfg)


def _sf(**over):
    cfg = {"items": [{
        "id": "i1", "mode": "word_bank",
        "passage": "A ___ and a ___.",
        "answers": ["cat", "dog"],
        "word_bank": ["cat", "dog", "fox"],
    }]}
    cfg.update(over)
    return cfg


def test_sentence_fill_happy_path():
    m = SentenceFillConfig.model_validate(_sf())
    assert m.SCHEMA_VERSION == "sentence_fill_config@1"


def test_sentence_fill_rejects_free_recall():
    cfg = _sf()
    cfg["items"][0]["mode"] = "free_recall"
    with pytest.raises(ValidationError):
        SentenceFillConfig.model_validate(cfg)


def test_sentence_fill_answers_must_match_blank_count():
    cfg = _sf()
    cfg["items"][0]["answers"] = ["cat"]
    with pytest.raises(ValidationError):
        SentenceFillConfig.model_validate(cfg)


def test_sentence_fill_duplicate_answers_rejected():
    cfg = _sf()
    cfg["items"][0]["answers"] = ["cat", " CAT "]
    cfg["items"][0]["word_bank"] = ["cat", "fox"]
    with pytest.raises(ValidationError):
        SentenceFillConfig.model_validate(cfg)


def test_sentence_fill_bank_must_contain_every_answer():
    cfg = _sf()
    cfg["items"][0]["word_bank"] = ["cat", "fox"]
    with pytest.raises(ValidationError):
        SentenceFillConfig.model_validate(cfg)


def test_schemas_registry():
    assert SCHEMAS["practice-rlc"] is RlcConfig
    assert SCHEMAS["practice-sentence"] is SentenceFillConfig
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/schemas/test_content_json.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.schemas.content_json'`

- [ ] **Step 3: Write minimal implementation**

`app/schemas/content_json/common.py`:

```python
"""Shared helpers for content_json schemas.

`norm` MUST match mobile's SentenceFill `norm()` exactly:
    s.trim().toLowerCase().replace(/\\s+/g, " ")
Any divergence makes our uniqueness check disagree with the runtime's
chip-consumption behaviour.
"""
from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict

_WS = re.compile(r"\s+")


def norm(s: str) -> str:
    return _WS.sub(" ", s.strip().lower())


def all_unique_normalized(values: list[str]) -> bool:
    seen = [norm(v) for v in values]
    return len(set(seen)) == len(seen)


class StrictModel(BaseModel):
    """Reject unknown keys and loose types everywhere."""

    model_config = ConfigDict(extra="forbid", strict=True)
```

`app/schemas/content_json/rlc.py`:

```python
from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator, model_validator

from .common import StrictModel, all_unique_normalized

EXPERT_ROLES = (
    "fire_inspector", "structural_engineer", "business_consultant",
    "medical_diagnostician", "agronomist", "teacher", "lawyer",
    "city_planner", "epidemiologist", "ethicist", "historian", "general",
)
STEP_ORDER = ("decision", "info_request", "final_decision", "concept_select", "reasoning")

MIN_CHARS_FLOOR = 20
MIN_CHARS_CEIL = 1000


class Choice(StrictModel):
    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    is_correct: bool = False


class Step(StrictModel):
    id: str = Field(min_length=1)
    kind: Literal[STEP_ORDER]  # type: ignore[valid-type]
    title: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    options: list[Choice] | None = None
    concept_chips: list[Choice] | None = None
    min_chars: int | None = None

    @model_validator(mode="after")
    def _per_kind(self):
        if self.kind in ("decision", "info_request", "final_decision"):
            opts = self.options or []
            if len(opts) < 2:
                raise ValueError(f"{self.kind}: options needs >=2 entries")
            if sum(1 for o in opts if o.is_correct) != 1:
                raise ValueError(f"{self.kind}: exactly 1 is_correct option required")
            if not all_unique_normalized([o.label for o in opts]):
                raise ValueError(f"{self.kind}: option labels must be normalized-unique")
        elif self.kind == "concept_select":
            chips = self.concept_chips or []
            if len(chips) < 2:
                raise ValueError("concept_select: concept_chips needs >=2 entries")
            if sum(1 for c in chips if c.is_correct) != 1:
                raise ValueError("concept_select: exactly 1 is_correct chip required")
            if not all_unique_normalized([c.label for c in chips]):
                raise ValueError("concept_select: chip labels must be normalized-unique")
        elif self.kind == "reasoning":
            n = self.min_chars
            if n is None or not (MIN_CHARS_FLOOR <= n <= MIN_CHARS_CEIL):
                raise ValueError(
                    f"reasoning.min_chars must be {MIN_CHARS_FLOOR}..{MIN_CHARS_CEIL}"
                )
        return self


class RlcConfig(StrictModel):
    SCHEMA_VERSION: str = "rlc_config@1"

    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    intro: str = Field(min_length=1)
    expert_role: Literal[EXPERT_ROLES]  # type: ignore[valid-type]
    steps: list[Step]

    @field_validator("SCHEMA_VERSION")
    @classmethod
    def _pin_version(cls, v: str) -> str:
        if v != "rlc_config@1":
            raise ValueError("SCHEMA_VERSION is fixed")
        return v

    @model_validator(mode="after")
    def _shape(self):
        if len(self.steps) != 5:
            raise ValueError("steps must contain exactly 5 entries")
        for i, (step, expected) in enumerate(zip(self.steps, STEP_ORDER)):
            if step.kind != expected:
                raise ValueError(f"steps[{i}].kind must be '{expected}'")
        if not all_unique_normalized([s.id for s in self.steps]):
            raise ValueError("step ids must be unique")
        return self
```

`app/schemas/content_json/sentence_fill.py`:

```python
from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .common import StrictModel, all_unique_normalized, norm


class SentenceItem(StrictModel):
    id: str = Field(min_length=1)
    # Pass 1 is word_bank ONLY: mobile's normalizeConfigItems() drops `mode` and
    # the component has no TextInput, so free_recall is uncompletable.
    mode: Literal["word_bank"]
    passage: str = Field(min_length=1)
    answers: list[str]
    word_bank: list[str]

    @model_validator(mode="after")
    def _shape(self):
        blanks = self.passage.count("___")
        if not (1 <= blanks <= 6):
            raise ValueError("passage needs 1-6 '___' blanks")
        if len(self.answers) != blanks:
            raise ValueError(f"answers length must equal blank count ({blanks})")
        if any(not a.strip() for a in self.answers):
            raise ValueError("answers must be non-empty")
        if any(not w.strip() for w in self.word_bank):
            raise ValueError("word_bank entries must be non-empty")
        if not all_unique_normalized(self.answers):
            raise ValueError("answers must be normalized-unique (mobile consumes each chip once)")
        if not all_unique_normalized(self.word_bank):
            raise ValueError("word_bank entries must be normalized-unique")
        bank = {norm(w) for w in self.word_bank}
        if not all(norm(a) in bank for a in self.answers):
            raise ValueError("word_bank must contain every answer")
        return self


class SentenceFillConfig(StrictModel):
    SCHEMA_VERSION: str = "sentence_fill_config@1"

    items: list[SentenceItem] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_ids(self):
        if not all_unique_normalized([i.id for i in self.items]):
            raise ValueError("item ids must be unique")
        return self
```

`app/schemas/content_json/__init__.py`:

```python
from .common import all_unique_normalized, norm
from .rlc import RlcConfig
from .sentence_fill import SentenceFillConfig

SCHEMAS: dict[str, type] = {
    "practice-rlc": RlcConfig,
    "practice-sentence": SentenceFillConfig,
}

__all__ = [
    "SCHEMAS", "RlcConfig", "SentenceFillConfig", "norm", "all_unique_normalized",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/schemas/test_content_json.py -q`
Expected: PASS (13 tests)

- [ ] **Step 5: Commit**

```bash
git add app/schemas/content_json tests/schemas/test_content_json.py
git commit -m "feat(schemas): strict rlc_config + sentence_fill_config models

Derived from validators.py on origin/Akademiya-AI plus the mobile consumers,
NOT the legacy _homework_phase_parsers. extra=forbid + strict types; min_chars
bounded 20..1000 (the platform validator has no range and grade_rlc would let
min_chars=-1 pass an empty answer); normalization matches mobile norm() exactly
so uniqueness agrees with chip consumption; sentence-fill is word_bank-only
because mobile has no free-recall input.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Markdown renderers

**Files:**
- Create: `app/services/phase_render.py`
- Test: `tests/services/test_phase_render.py`

**Interfaces:**
- Consumes: `app.schemas.content_json.SCHEMAS`, `RlcConfig`, `SentenceFillConfig` (Task 2).
- Produces:
  - `RENDERER_VERSION = "1"`
  - `render_md(phase_name: str, cfg: BaseModel) -> str`
  - `class RenderError(RuntimeError)`

- [ ] **Step 1: Write the failing test**

```python
import pytest

from app.schemas.content_json import RlcConfig, SentenceFillConfig
from app.services import content_lint, phase_render


def _rlc_cfg():
    def opts():
        return [{"id": "o0", "label": "Yes", "is_correct": True},
                {"id": "o1", "label": "No", "is_correct": False}]
    return RlcConfig.model_validate({
        "id": "c1", "title": "Fire audit", "intro": "You inspect a hall.",
        "expert_role": "fire_inspector",
        "steps": [
            {"id": "s1", "kind": "decision", "title": "Choose", "prompt": "Evacuate?", "options": opts()},
            {"id": "s2", "kind": "info_request", "title": "Ask", "prompt": "What data?", "options": opts()},
            {"id": "s3", "kind": "final_decision", "title": "Decide", "prompt": "Final?", "options": opts()},
            {"id": "s4", "kind": "concept_select", "title": "Concept", "prompt": "Which?",
             "concept_chips": [{"id": "k1", "label": "Load", "is_correct": True},
                               {"id": "k2", "label": "Colour", "is_correct": False}]},
            {"id": "s5", "kind": "reasoning", "title": "Explain", "prompt": "Why?", "min_chars": 80},
        ],
    })


def test_render_rlc_has_title_and_every_step_and_option():
    md = phase_render.render_md("practice-rlc", _rlc_cfg())
    assert md.startswith("# ")
    assert "Fire audit" in md
    for text in ("Evacuate?", "What data?", "Final?", "Which?", "Why?", "Yes", "No", "Load"):
        assert text in md


def test_render_rlc_passes_content_lint():
    md = phase_render.render_md("practice-rlc", _rlc_cfg())
    findings = content_lint.lint_phase(
        "practice-rlc", md, subject="history", output_language="ru"
    )
    assert [f.code for f in findings if f.code == "empty_body"] == []


def test_render_sentence_lists_passage_and_bank():
    cfg = SentenceFillConfig.model_validate({"items": [{
        "id": "i1", "mode": "word_bank", "passage": "A ___ ran.",
        "answers": ["cat"], "word_bank": ["cat", "dog"]}]})
    md = phase_render.render_md("practice-sentence", cfg)
    assert "A ___ ran." in md and "cat" in md and "dog" in md


def test_render_unknown_phase_raises():
    with pytest.raises(phase_render.RenderError):
        phase_render.render_md("flashcards", _rlc_cfg())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/services/test_phase_render.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.phase_render'`

- [ ] **Step 3: Write minimal implementation**

```python
"""Deterministic markdown renderers for structured phases.

Markdown is DERIVED from content_json, never authored. Its shape must stay close
enough to the previous hand-authored markdown that the judge, solver,
content_lint, teaching audit, Notion renderer and the operator console keep
working — that is the renderer's real contract, verified in tests.
"""
from __future__ import annotations

from pydantic import BaseModel

from app.schemas.content_json import RlcConfig, SentenceFillConfig

RENDERER_VERSION = "1"


class RenderError(RuntimeError):
    """No renderer registered for this phase, or the config is the wrong type."""


def _render_rlc(cfg: RlcConfig) -> str:
    out: list[str] = [f"# {cfg.title}", "", cfg.intro, "",
                      f"**Role:** {cfg.expert_role}", ""]
    for n, step in enumerate(cfg.steps, start=1):
        out += [f"## {n}. {step.title}", "", step.prompt, ""]
        for choice in (step.options or []):
            out.append(f"- {choice.label}")
        for chip in (step.concept_chips or []):
            out.append(f"- {chip.label}")
        if step.kind == "reasoning":
            out.append(f"_Minimum {step.min_chars} characters._")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def _render_sentence(cfg: SentenceFillConfig) -> str:
    out: list[str] = ["# Sentence fill", ""]
    for n, item in enumerate(cfg.items, start=1):
        out += [f"## {n}.", "", item.passage, "",
                "**Word bank:** " + ", ".join(item.word_bank), ""]
    return "\n".join(out).rstrip() + "\n"


_RENDERERS = {
    "practice-rlc": (RlcConfig, _render_rlc),
    "practice-sentence": (SentenceFillConfig, _render_sentence),
}


def render_md(phase_name: str, cfg: BaseModel) -> str:
    entry = _RENDERERS.get(phase_name)
    if entry is None:
        raise RenderError(f"no renderer for phase '{phase_name}'")
    expected_type, fn = entry
    if not isinstance(cfg, expected_type):
        raise RenderError(
            f"phase '{phase_name}' expects {expected_type.__name__}, got {type(cfg).__name__}"
        )
    return fn(cfg)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/services/test_phase_render.py -q`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add app/services/phase_render.py tests/services/test_phase_render.py
git commit -m "feat(render): deterministic markdown renderers for rlc + sentence-fill

Markdown becomes derived, never authored. The renderer's contract is that its
output stays acceptable to every existing markdown consumer.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Migration + structured columns

**Files:**
- Create: `alembic/versions/00NN_phase_output_structured.py` (see Step 1 for the number)
- Modify: `app/models/phase_output.py`
- Modify: `app/repositories/phase_outputs.py` (`create_or_reset` clears the new fields)
- Test: `tests/repositories/test_phase_output_structured.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `PhaseOutput.content_json`, `.authoring_mode`, `.content_schema_version`,
  `.renderer_version`; `AUTHORING_MODES` tuple.

- [ ] **Step 1: Determine the migration number**

**Gate finding (2026-08-03): `0049` is TAKEN.** The model-config lane's execution branch
`feat/model-config-3x-flash-exec` (worktree `../HCGA-model-config`, **unpushed, no PR yet**) already
contains `alembic/versions/0049_launch_defaults_3x.py`. **Use `0050`.**

Because their `0049` is unmerged, set `down_revision` to the **current merged head** (`0048`), not to
their revision id — pointing at an unmerged revision would dangle if their lane is abandoned or
renumbered.

```bash
ls alembic/versions/ | sed 's/_.*//' | sort -n | tail -3   # confirm before writing
git fetch origin && git log --oneline origin/Nggaev-v2 -1  # confirm the merged head
```

**At finish/rebase time, re-check**: if their `0049` has merged ahead of you, re-point your
`down_revision` to it so alembic keeps a single head. If alembic reports multiple heads after a
rebase, that is the signal — resolve it before merging, never with a merge-migration added silently.

- [ ] **Step 2: Write the failing test**

```python
from app.models.phase_output import AUTHORING_MODES, PhaseOutput


def test_structured_columns_exist_on_model():
    for col in ("content_json", "authoring_mode", "content_schema_version", "renderer_version"):
        assert col in PhaseOutput.__table__.columns


def test_authoring_modes_enumerated():
    assert AUTHORING_MODES == (
        "structured", "markdown_fallback", "markdown_builtin",
        "markdown_custom", "markdown_legacy",
    )
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run python -m pytest tests/repositories/test_phase_output_structured.py -q`
Expected: FAIL — `ImportError: cannot import name 'AUTHORING_MODES'`

- [ ] **Step 4: Add the model columns**

In `app/models/phase_output.py`, add near the top:

```python
AUTHORING_MODES = (
    "structured",
    "markdown_fallback",
    "markdown_builtin",
    "markdown_custom",
    "markdown_legacy",
)
```

and inside the class, after `output_md`:

```python
    # Structured generation (content_json lane). All nullable — pre-migration
    # rows read as markdown_legacy.
    content_json: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    authoring_mode: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    content_schema_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    renderer_version: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
```

- [ ] **Step 5: Write the migration**

```python
"""phase_outputs: structured content_json columns

Revision ID: 00NN_phase_output_structured
Revises: <current head revision id>
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "00NN_phase_output_structured"
down_revision = "<current head revision id>"
branch_labels = None
depends_on = None

_MODES = (
    "structured", "markdown_fallback", "markdown_builtin",
    "markdown_custom", "markdown_legacy",
)


def upgrade() -> None:
    op.add_column("phase_outputs", sa.Column("content_json", JSONB(), nullable=True))
    op.add_column("phase_outputs", sa.Column("authoring_mode", sa.String(32), nullable=True))
    op.add_column("phase_outputs", sa.Column("content_schema_version", sa.String(64), nullable=True))
    op.add_column("phase_outputs", sa.Column("renderer_version", sa.String(16), nullable=True))
    modes = ", ".join(f"'{m}'" for m in _MODES)
    op.create_check_constraint(
        "ck_phase_outputs_authoring_mode",
        "phase_outputs",
        f"authoring_mode IS NULL OR authoring_mode IN ({modes})",
    )


def downgrade() -> None:
    op.drop_constraint("ck_phase_outputs_authoring_mode", "phase_outputs", type_="check")
    for col in ("renderer_version", "content_schema_version", "authoring_mode", "content_json"):
        op.drop_column("phase_outputs", col)
```

- [ ] **Step 6: Clear the fields in `create_or_reset`**

In `app/repositories/phase_outputs.py`, inside the reset branch that clears per-attempt fields, add:

```python
        row.content_json = None
        row.authoring_mode = None
        row.content_schema_version = None
        row.renderer_version = None
```

- [ ] **Step 7: Run tests + migration round-trip**

```bash
uv run python -m pytest tests/repositories/test_phase_output_structured.py -q
uv run alembic upgrade head && uv run alembic downgrade -1 && uv run alembic upgrade head
```
Expected: tests PASS; migration up/down/up clean.

- [ ] **Step 8: Commit**

```bash
git add alembic/versions app/models/phase_output.py app/repositories/phase_outputs.py tests/repositories/test_phase_output_structured.py
git commit -m "feat(db): phase_outputs structured columns + authoring_mode constraint

content_json IS NULL is ambiguous (legacy rows, unsupported phases, custom
prompts all null), so provenance is explicit and DB-constrained.
create_or_reset clears every structured field so a retry cannot resurrect a
stale artifact.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Typed structured failure + atomic `PhaseArtifact`

**Files:**
- Create: `app/services/phase_artifact.py`
- Test: `tests/services/test_phase_artifact.py`

**Interfaces:**
- Consumes: `phase_render.render_md`, `RENDERER_VERSION` (Task 3); `SCHEMAS` (Task 2).
- Produces:
  - `@dataclass(frozen=True) PhaseArtifact` with fields `output_md, content_json, authoring_mode,
    content_schema_version, renderer_version`
  - `class StructuredPhaseError(RuntimeError)`
  - `artifact_from_config(phase_name, cfg) -> PhaseArtifact`
  - `artifact_from_markdown(output_md, *, mode) -> PhaseArtifact`

- [ ] **Step 1: Write the failing test**

```python
import pytest

from app.schemas.content_json import SentenceFillConfig
from app.services.phase_artifact import (
    PhaseArtifact, StructuredPhaseError, artifact_from_config, artifact_from_markdown,
)


def _cfg():
    return SentenceFillConfig.model_validate({"items": [{
        "id": "i1", "mode": "word_bank", "passage": "A ___ ran.",
        "answers": ["cat"], "word_bank": ["cat", "dog"]}]})


def test_artifact_from_config_is_complete_and_consistent():
    art = artifact_from_config("practice-sentence", _cfg())
    assert art.authoring_mode == "structured"
    assert art.content_schema_version == "sentence_fill_config@1"
    assert art.renderer_version == "1"
    assert "A ___ ran." in art.output_md
    # content_json is a plain dict (model_dump), never the model itself
    assert isinstance(art.content_json, dict)
    assert art.content_json["items"][0]["id"] == "i1"


def test_artifact_from_markdown_has_no_structured_fields():
    art = artifact_from_markdown("# hi", mode="markdown_fallback")
    assert art.content_json is None
    assert art.content_schema_version is None
    assert art.renderer_version is None
    assert art.authoring_mode == "markdown_fallback"


def test_artifact_from_markdown_rejects_unknown_mode():
    with pytest.raises(ValueError):
        artifact_from_markdown("# hi", mode="structured")


def test_render_failure_becomes_structured_phase_error():
    with pytest.raises(StructuredPhaseError):
        artifact_from_config("flashcards", _cfg())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/services/test_phase_artifact.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.phase_artifact'`

- [ ] **Step 3: Write minimal implementation**

```python
"""The single artifact every generation path returns.

Judge regen and solver regen both replace output_md wholesale. If content_json
were persisted independently, a regenerated markdown would survive beside a
stale JSON and the "source of truth" would be a lie. So every path — initial,
judge regen, solver regen, markdown fallback — returns one of these, and it is
persisted only after the final accepted generation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from pydantic import BaseModel

from app.models.phase_output import AUTHORING_MODES
from app.services.phase_render import RENDERER_VERSION, RenderError, render_md

_MARKDOWN_MODES = tuple(m for m in AUTHORING_MODES if m != "structured")


class StructuredPhaseError(RuntimeError):
    """A schema-validation or render-conformance failure.

    Deliberately distinct from transport errors: the pipeline falls back to
    markdown ONLY on this type. Auth, 429, slot-saturation, timeout and network
    errors must keep their existing retry/failover semantics.
    """


@dataclass(frozen=True)
class PhaseArtifact:
    output_md: str
    content_json: Optional[dict] = None
    authoring_mode: str = "markdown_legacy"
    content_schema_version: Optional[str] = None
    renderer_version: Optional[str] = None


def artifact_from_config(phase_name: str, cfg: BaseModel) -> PhaseArtifact:
    """Render markdown from a validated config and pair them atomically."""
    try:
        md = render_md(phase_name, cfg)
    except RenderError as exc:
        raise StructuredPhaseError(str(exc)) from exc
    if not md.strip():
        raise StructuredPhaseError(f"renderer produced empty markdown for '{phase_name}'")
    return PhaseArtifact(
        output_md=md,
        content_json=cfg.model_dump(mode="json"),
        authoring_mode="structured",
        content_schema_version=getattr(cfg, "SCHEMA_VERSION", None),
        renderer_version=RENDERER_VERSION,
    )


def artifact_from_markdown(output_md: str, *, mode: str) -> PhaseArtifact:
    if mode not in _MARKDOWN_MODES:
        raise ValueError(f"'{mode}' is not a markdown authoring mode")
    return PhaseArtifact(output_md=output_md, authoring_mode=mode)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/services/test_phase_artifact.py -q`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add app/services/phase_artifact.py tests/services/test_phase_artifact.py
git commit -m "feat(pipeline): atomic PhaseArtifact + typed StructuredPhaseError

One artifact per generation path so content_json and output_md can never
desynchronize across judge/solver regeneration. StructuredPhaseError is the
ONLY trigger for markdown fallback — transport errors keep failover semantics.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Structured authoring prompts

**Files:**
- Create: `prompts/_general/structured/practice-rlc.md`
- Create: `prompts/_general/structured/practice-sentence.md`
- Modify: `app/services/prompts.py` (add `get_structured_prompt`)
- Test: `tests/services/test_structured_prompts.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `prompts.get_structured_prompt(subject: str, phase: str, *, output_language: str) -> str | None`
  — returns `None` when the phase has no structured prompt.

- [ ] **Step 1: Write the failing test**

```python
from app.services import prompts


def test_structured_prompt_exists_for_pass1_phases():
    for phase in ("practice-rlc", "practice-sentence"):
        body = prompts.get_structured_prompt("history", phase, output_language="ru")
        assert body and "JSON" in body


def test_structured_prompt_absent_for_other_phases():
    assert prompts.get_structured_prompt("history", "flashcards", output_language="ru") is None


def test_structured_prompt_does_not_demand_markdown_only():
    body = prompts.get_structured_prompt("history", "practice-rlc", output_language="ru")
    assert "Markdown only" not in body
    assert "Respond in **Markdown only**" not in body


def test_structured_rlc_prompt_names_the_five_step_order():
    body = prompts.get_structured_prompt("history", "practice-rlc", output_language="ru")
    for kind in ("decision", "info_request", "final_decision", "concept_select", "reasoning"):
        assert kind in body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/services/test_structured_prompts.py -q`
Expected: FAIL — `AttributeError: module 'app.services.prompts' has no attribute 'get_structured_prompt'`

- [ ] **Step 3: Write the RLC structured prompt**

`prompts/_general/structured/practice-rlc.md`:

```markdown
# Real-Life Challenge (structured) — {{SUBJECT}}

Build ONE real-life challenge for this {{SUBJECT}} lesson. Return **JSON only**, conforming exactly
to the schema below. No prose, no code fences.

## Required shape

- `id` — short slug, non-empty.
- `title` — the challenge name.
- `intro` — 1-3 sentences setting the scene.
- `expert_role` — EXACTLY one of: `fire_inspector`, `structural_engineer`, `business_consultant`,
  `medical_diagnostician`, `agronomist`, `teacher`, `lawyer`, `city_planner`, `epidemiologist`,
  `ethicist`, `historian`, `general`. Choose the closest fit for the lesson.
- `steps` — EXACTLY 5, in this order and no other:
  1. `kind: "decision"` — `options`, 2-4 entries, exactly one `is_correct: true`
  2. `kind: "info_request"` — `options`, 2-4 entries, exactly one `is_correct: true`
  3. `kind: "final_decision"` — `options`, 2-4 entries, exactly one `is_correct: true`
  4. `kind: "concept_select"` — `concept_chips`, 2-4 entries, exactly one `is_correct: true`
  5. `kind: "reasoning"` — `min_chars`, an integer between 20 and 1000 (use 80 unless the lesson
     warrants otherwise)

Every step needs `id`, `title`, `prompt`. Every option and chip needs `id` and `label`.

## Rules

- Option and chip labels must be **distinct** from each other within a step (compared
  case-insensitively, ignoring extra spaces) and non-empty.
- **Never reveal which option is correct in the visible text.** Do not write "(correct)",
  "правильный ответ", "to'g'ri javob" or any equivalent inside a `label`, `title` or `prompt`. The
  `is_correct` flag is the only place correctness is expressed, and it is stripped before the
  student sees the exercise.
- Ground every step in THIS lesson's content. Do not invent facts.
- All student-visible text is in the output language.

{{LANGUAGE_RULES}}
```

- [ ] **Step 4: Write the sentence-fill structured prompt**

`prompts/_general/structured/practice-sentence.md`:

```markdown
# Sentence Fill (structured) — {{SUBJECT}}

Build cloze items for this {{SUBJECT}} lesson. Return **JSON only**, conforming exactly to the
schema below. No prose, no code fences.

## Required shape

- `items` — 3-6 entries. Each item:
  - `id` — short slug, unique across items.
  - `mode` — always the string `"word_bank"`. No other value is supported.
  - `passage` — the sentence, with **1-6** blanks written as exactly three underscores `___`.
  - `answers` — one entry per blank, in blank order.
  - `word_bank` — every answer, plus 1-3 plausible distractors.

## Rules

- `answers` must contain **no duplicates** (compared case-insensitively, ignoring extra spaces).
  The student interface consumes each word-bank chip once, so a repeated answer makes the exercise
  impossible to finish.
- `word_bank` entries must likewise be distinct, and must include every answer verbatim.
- Blanks must remove a concept-bearing word, never a function word.
- Ground every sentence in THIS lesson's content.

{{LANGUAGE_RULES}}
```

- [ ] **Step 5: Add the loader**

In `app/services/prompts.py`, add:

```python
def get_structured_prompt(
    subject: str, phase: str, *, output_language: str = "uz"
) -> "str | None":
    """The JSON-authoring prompt for a structured phase, or None if it has none.

    Separate from `get_prompt`: that one is the MARKDOWN evaluation contract the
    judge, solver and lint read, and it says "Markdown only". A single prompt
    cannot both demand JSON and serve as the markdown contract.
    """
    path = _PROMPTS_DIR / "_general" / "structured" / f"{phase}.md"
    if not path.exists():
        return None
    body = path.read_text(encoding="utf-8")
    return _apply_substitutions(body, subject=subject, output_language=output_language)
```

Reuse whatever the module already calls for `{{SUBJECT}}` / `{{LANGUAGE_RULES}}` substitution — read
`get_prompt` and call the same helper rather than duplicating it. If that logic is inline in
`get_prompt`, extract it into `_apply_substitutions` and have `get_prompt` call it too.

- [ ] **Step 6: Run tests**

Run: `uv run python -m pytest tests/services/test_structured_prompts.py -q`
Expected: PASS (4 tests)

- [ ] **Step 7: Commit**

```bash
git add prompts/_general/structured app/services/prompts.py tests/services/test_structured_prompts.py
git commit -m "feat(prompts): structured JSON-authoring prompts for rlc + sentence

Kept separate from the markdown evaluation contract that judge/solver/lint read
— one prompt cannot both demand JSON and say 'Markdown only'. Prompts forbid
correctness markers in visible text, since platform redaction is key-based and
would leave a revealing label intact.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Wire the pipeline — structured path with typed fallback

**Files:**
- Modify: `app/services/pipeline.py` (`_execute_phase` and the judge/solver regen blocks)
- Test: `tests/services/test_pipeline_structured.py`

**Interfaces:**
- Consumes: `PhaseArtifact`, `StructuredPhaseError`, `artifact_from_config`,
  `artifact_from_markdown` (Task 5); `SCHEMAS` (Task 2); `get_structured_prompt` (Task 6).
- Produces: `_generate_artifact(...) -> PhaseArtifact` inside `pipeline.py`.

**Design notes for the implementer:**

- The structured attempt calls `agent.run_phase(..., schema=SCHEMAS[phase_name])`. Wrap **only the
  schema/render conversion** in a `try` that raises `StructuredPhaseError`; let every other
  exception propagate untouched so `_run_with_failover` still classifies and retries transport
  failures.
- The fallback must live **inside** the function passed to `_run_with_failover` (the `run_fn`), not
  outside it. `_run_with_failover` classifies and retries whatever escapes; a `StructuredPhaseError`
  escaping would be retried as if it were a transport fault.
- Judge regen and solver regen currently assign
  `output_md, tin, tout, produced_by = r_md, r_tin, r_tout, r_prod`. Change these to carry a
  `PhaseArtifact` instead, so the regenerated markdown and its JSON move together. A regenerated
  phase whose config no longer validates falls back to `markdown_fallback` for that phase.
- Phases with no structured schema keep today's path and record `authoring_mode="markdown_builtin"`.
  Custom uploaded prompts record `markdown_custom`.

- [ ] **Step 1: Write the failing test**

```python
import pytest

from app.schemas.content_json import SentenceFillConfig
from app.services import pipeline
from app.services.phase_artifact import PhaseArtifact, StructuredPhaseError


class _Boom(RuntimeError):
    pass


@pytest.mark.asyncio
async def test_schema_failure_falls_back_to_markdown(monkeypatch):
    async def fake_structured(*a, **k):
        raise StructuredPhaseError("schema exhausted")

    async def fake_markdown(*a, **k):
        return "# fallback body", 1, 2, "gemini"

    monkeypatch.setattr(pipeline, "_run_structured_attempt", fake_structured)
    monkeypatch.setattr(pipeline, "_run_markdown_attempt", fake_markdown)

    art = await pipeline._generate_artifact(phase_name="practice-sentence")
    assert isinstance(art, PhaseArtifact)
    assert art.authoring_mode == "markdown_fallback"
    assert art.content_json is None


@pytest.mark.asyncio
async def test_transport_error_does_NOT_fall_back(monkeypatch):
    async def fake_structured(*a, **k):
        raise _Boom("429 rate limited")

    async def fake_markdown(*a, **k):  # must never be reached
        raise AssertionError("markdown fallback ran on a transport error")

    monkeypatch.setattr(pipeline, "_run_structured_attempt", fake_structured)
    monkeypatch.setattr(pipeline, "_run_markdown_attempt", fake_markdown)

    with pytest.raises(_Boom):
        await pipeline._generate_artifact(phase_name="practice-sentence")


@pytest.mark.asyncio
async def test_structured_success_yields_structured_artifact(monkeypatch):
    cfg = SentenceFillConfig.model_validate({"items": [{
        "id": "i1", "mode": "word_bank", "passage": "A ___ ran.",
        "answers": ["cat"], "word_bank": ["cat", "dog"]}]})

    async def fake_structured(*a, **k):
        from app.services.phase_artifact import artifact_from_config
        return artifact_from_config("practice-sentence", cfg)

    monkeypatch.setattr(pipeline, "_run_structured_attempt", fake_structured)
    art = await pipeline._generate_artifact(phase_name="practice-sentence")
    assert art.authoring_mode == "structured"
    assert art.content_schema_version == "sentence_fill_config@1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/services/test_pipeline_structured.py -q`
Expected: FAIL — `AttributeError: module 'app.services.pipeline' has no attribute '_generate_artifact'`

- [ ] **Step 3: Implement `_generate_artifact` and the two attempt helpers**

Add to `pipeline.py`:

```python
async def _generate_artifact(*, phase_name: str, **kw) -> PhaseArtifact:
    """Structured attempt, then markdown fallback ONLY on StructuredPhaseError.

    Any other exception (auth, 429, slot saturation, timeout, network) propagates
    untouched so the existing classify/retry/failover logic still applies.
    """
    if phase_name in SCHEMAS:
        try:
            return await _run_structured_attempt(phase_name=phase_name, **kw)
        except StructuredPhaseError as exc:
            logger.warning(
                f"[{phase_name}] structured generation failed ({exc}); "
                f"falling back to markdown"
            )
    md, *_rest = await _run_markdown_attempt(phase_name=phase_name, **kw)
    mode = "markdown_fallback" if phase_name in SCHEMAS else "markdown_builtin"
    return artifact_from_markdown(md, mode=mode)
```

And the two attempt helpers:

```python
async def _run_structured_attempt(*, phase_name: str, **kw) -> PhaseArtifact:
    """One structured generation. Converts schema/render failures — and ONLY those —
    into StructuredPhaseError."""
    schema = SCHEMAS[phase_name]
    structured_prompt = get_structured_prompt(
        kw["subject"], phase_name, output_language=kw.get("output_language", "uz")
    )
    if not structured_prompt:
        raise StructuredPhaseError(f"no structured prompt for '{phase_name}'")
    try:
        result = await agent.run_phase(
            provider=kw["provider"],
            model=kw["model"],
            phase_prompt=structured_prompt,
            phase_name=phase_name,
            homework_job_id=kw["job_id"],
            phase_output_id=kw["phase_output_id"],
            lesson_context=kw.get("lesson_context"),
            prior_outputs=kw.get("prior_outputs"),
            schema=schema,
            transport=kw.get("transport", "cli"),
        )
    except ValidationError as exc:            # schema exhausted after run_phase's retry
        raise StructuredPhaseError(f"{phase_name}: schema validation failed: {exc}") from exc
    if result.parsed is None:
        raise StructuredPhaseError(f"{phase_name}: model returned no valid {schema.__name__}")
    return artifact_from_config(phase_name, result.parsed)


async def _run_markdown_attempt(*, phase_name: str, **kw):
    """The existing markdown call path, extracted so it can be monkeypatched and
    reused as the fallback. Returns (output_md, tokens_in, tokens_out, produced_by)."""
    return await _run_with_failover(
        requested_provider=kw["provider"],
        model=kw["model"],
        run_fn=_make_run(kw["base_phase_prompt"]),
        transport=kw.get("transport", "cli"),
        session_limit_strategy=kw.get("session_limit_strategy", "pause"),
    )
```

Add the imports at the top of `pipeline.py`:

```python
from pydantic import ValidationError

from app.schemas.content_json import SCHEMAS
from app.services.phase_artifact import (
    PhaseArtifact, StructuredPhaseError, artifact_from_config, artifact_from_markdown,
)
from app.services.prompts import get_structured_prompt
```

**Note on `_run_with_failover` placement:** `_run_structured_attempt` deliberately calls
`agent.run_phase` directly rather than going through `_run_with_failover`, so a
`StructuredPhaseError` can never be seen by the classifier. Transport errors raised by
`agent.run_phase` still propagate out of `_generate_artifact` untouched, and the caller wraps
`_generate_artifact` in the existing failover/retry machinery.

- [ ] **Step 4: Run tests**

Run: `uv run python -m pytest tests/services/test_pipeline_structured.py -q`
Expected: PASS (3 tests)

- [ ] **Step 5: RED-prove the fallback is typed**

Temporarily change the `except StructuredPhaseError` to `except Exception`, re-run, and confirm
`test_transport_error_does_NOT_fall_back` FAILS. Restore the narrow except and confirm it passes
again. Record both outputs in your report.

- [ ] **Step 6: Carry the artifact through judge and solver regen**

Change both regen blocks so the regenerated result becomes a `PhaseArtifact` and replaces the whole
artifact — never `output_md` alone. Persist once, after the final accepted generation.

- [ ] **Step 7: Run the full suite**

Run: `uv run python -m pytest tests/ -q`
Expected: all green.

- [ ] **Step 8: Commit**

```bash
git add app/services/pipeline.py tests/services/test_pipeline_structured.py
git commit -m "feat(pipeline): structured generation with typed markdown fallback

Fallback lives inside run_fn and triggers ONLY on StructuredPhaseError, so
_run_with_failover still classifies and retries transport faults. Judge and
solver regeneration now replace the whole PhaseArtifact rather than output_md
alone, which previously would have stranded a stale content_json.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Operator ingest CLI

**Files:**
- Create: `scripts/ingest_to_platform.py`
- Test: `tests/scripts/test_ingest_to_platform.py`

**Interfaces:**
- Consumes: `build_ingest_payload`, `load_subject_map`, `SubjectMapError` (Task 1).
- Produces: `validate_token(raw: str) -> str`, `main(argv) -> int`.

- [ ] **Step 1: Write the failing test**

```python
import pytest

from scripts import ingest_to_platform as cli


@pytest.mark.parametrize("bad", ["", "   ", "old,new", "tok en", "tok\ten", "a\nb"])
def test_validate_token_rejects_multi_blank_and_whitespace(bad):
    with pytest.raises(cli.TokenError):
        cli.validate_token(bad)


def test_validate_token_accepts_single_token():
    assert cli.validate_token("  abc123  ") == "abc123"
```

Rationale to keep in the test file as a comment: the platform compares the **entire** Bearer value
against each configured token, and it also requires the header to split into exactly two parts — so
a comma-joined value fails, and so does one containing whitespace.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/scripts/test_ingest_to_platform.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.ingest_to_platform'`

- [ ] **Step 3: Write minimal implementation**

```python
"""Post done homework jobs to the platform's homework-import endpoint.

Server side, LIBRARY_INGEST_TOKEN is a comma-separated ACCEPTANCE LIST, but each
request must present exactly ONE token: the server compares the whole Bearer
value against each entry. So this client reads a singular PLATFORM_INGEST_TOKEN.

--dry-run is the DEFAULT; posting requires --post.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_REPO_ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

from app.services.platform_payload import (  # noqa: E402
    SubjectMapError, build_ingest_payload, load_subject_map,
)

INGEST_PATH = "/api/v1/library/homework-imports/ingest"


class TokenError(RuntimeError):
    """The client token is unusable before any HTTP request is attempted."""


def validate_token(raw: str) -> str:
    token = (raw or "").strip()
    if not token:
        raise TokenError("PLATFORM_INGEST_TOKEN is empty")
    if "," in token:
        raise TokenError(
            "PLATFORM_INGEST_TOKEN must be ONE token — the server compares the whole "
            "Bearer value, so a comma-joined list authenticates as neither entry"
        )
    if any(ch.isspace() for ch in token):
        raise TokenError(
            "PLATFORM_INGEST_TOKEN must not contain whitespace — the server splits the "
            "Authorization header and requires exactly two parts"
        )
    return token


def _load_map() -> dict[str, int]:
    path = os.environ.get("PLATFORM_SUBJECT_MAP", "")
    if not path:
        raise SubjectMapError("PLATFORM_SUBJECT_MAP is not set")
    return load_subject_map(__import__("pathlib").Path(path).read_text(encoding="utf-8"))


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--job", action="append", default=[], help="job id (repeatable)")
    ap.add_argument("--post", action="store_true", help="actually POST (default is dry-run)")
    ap.add_argument("--check-map", action="store_true", help="print+validate the subject map, exit")
    args = ap.parse_args(argv)

    subject_map = _load_map()
    if args.check_map:
        print(json.dumps(subject_map, indent=2, sort_keys=True))
        return 0

    base = os.environ.get("PLATFORM_BASE_URL", "").rstrip("/")
    if not base:
        raise TokenError("PLATFORM_BASE_URL is not set")
    token = validate_token(os.environ.get("PLATFORM_INGEST_TOKEN", ""))

    # Fetching jobs + phases and the actual POST are wired in Step 5.
    print(f"target: {base}{INGEST_PATH}  jobs={args.job}  post={args.post}")
    _ = token, subject_map
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests**

Run: `uv run python -m pytest tests/scripts/test_ingest_to_platform.py -q`
Expected: PASS (7 tests)

- [ ] **Step 5: Wire the DB read and the POST**

Add to `scripts/ingest_to_platform.py`:

```python
import asyncio

import httpx
from sqlalchemy import text

from app.db import SessionLocal

_JOB_SQL = """
SELECT j.id::text AS id, j.book_id::text AS book_id, j.subject,
       b.grade, j.output_language
FROM homework_jobs j JOIN books b ON b.id = j.book_id
WHERE j.id::text = :jid AND j.status = 'done'
"""

_PHASE_SQL = """
SELECT phase_name, output_md, content_json, content_schema_version,
       authoring_mode, judge_status, status
FROM phase_outputs WHERE job_id::text = :jid ORDER BY phase_order
"""


async def _load_job(jid: str):
    async with SessionLocal() as s:
        job = (await s.execute(text(_JOB_SQL), {"jid": jid})).mappings().first()
        if job is None:
            raise RuntimeError(f"job {jid} not found or not done")
        phases = (await s.execute(text(_PHASE_SQL), {"jid": jid})).mappings().all()
    return dict(job), [dict(p) for p in phases]


def _post(base: str, token: str, payload: dict, client=None) -> int:
    http = client or httpx.Client(timeout=60)
    resp = http.post(
        f"{base}{INGEST_PATH}",
        json=payload,
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"},
    )
    print(f"  -> HTTP {resp.status_code} {resp.text[:300]}")
    return 0 if resp.status_code < 300 else 1
```

and replace the placeholder print in `main` with:

```python
    rc = 0
    for jid in args.job:
        job, phases = asyncio.run(_load_job(jid))
        payload = build_ingest_payload(job=job, phases=phases, subject_map=subject_map)
        if not args.post:
            print(json.dumps(payload, ensure_ascii=False, indent=2)[:4000])
            print(f"[dry-run] {jid}: {len(payload['phases'])} phases — not posted")
            continue
        rc |= _post(base, token, payload)
    return rc
```

- [ ] **Step 6: Prove dry-run never posts**

```python
def test_dry_run_does_not_post(monkeypatch, capsys):
    def _explode(*a, **k):
        raise AssertionError("dry-run must not POST")
    monkeypatch.setattr(cli, "_post", _explode)
    monkeypatch.setattr(cli, "_load_job", lambda jid: ({}, []))
    monkeypatch.setattr(cli, "_load_map", lambda: {"history": 7})
    monkeypatch.setenv("PLATFORM_BASE_URL", "https://example.test")
    monkeypatch.setenv("PLATFORM_INGEST_TOKEN", "tok")
    monkeypatch.setattr(
        cli, "build_ingest_payload", lambda **k: {"phases": []}
    )
    assert cli.main(["--job", "abc"]) == 0
```

Run: `uv run python -m pytest tests/scripts/test_ingest_to_platform.py -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add scripts/ingest_to_platform.py tests/scripts/test_ingest_to_platform.py
git commit -m "feat(scripts): committed operator ingest CLI, dry-run by default

Client token is singular (PLATFORM_INGEST_TOKEN) because the server compares the
entire Bearer value against its comma-separated acceptance list; comma, blank and
whitespace tokens are rejected before any HTTP call. --check-map validates the
subject mapping without posting.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Conformance gates + acceptance smoke

**Files:**
- Create: `tests/conformance/test_platform_contract.py`
- Create: `docs/research/2026-08-03-content-json-acceptance.md`

**Interfaces:**
- Consumes: everything above.
- Produces: no new runtime code.

**Note:** these tests import the platform's live validator module by file path from
`origin/Akademiya-AI`. If that checkout is unavailable in CI, mark them
`@pytest.mark.skipif(not PLATFORM_PATH.exists(), reason="platform checkout absent")` — they are a
local conformance gate, not a CI blocker.

- [ ] **Step 1: Write the structured gate**

```python
import importlib.util
import pathlib

import pytest

from app.schemas.content_json import RlcConfig, SentenceFillConfig

PLATFORM = pathlib.Path(
    "/Users/macmini5/Documents/Class-A-Education-Platform-Backend"
    "/apps/library/models/validators.py"
)
pytestmark = pytest.mark.skipif(not PLATFORM.exists(), reason="platform checkout absent")


def _validators():
    spec = importlib.util.spec_from_file_location("plat_validators", PLATFORM)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_our_rlc_config_passes_platform_validator():
    from tests.services.test_phase_render import _rlc_cfg  # reuse the fixture
    errors = {}
    _validators().validate_rlc_config(_rlc_cfg().model_dump(mode="json"), errors)
    assert errors == {}


def test_our_sentence_config_passes_platform_validator():
    cfg = SentenceFillConfig.model_validate({"items": [{
        "id": "i1", "mode": "word_bank", "passage": "A ___ ran.",
        "answers": ["cat"], "word_bank": ["cat", "dog"]}]})
    errors = {}
    _validators().validate_sentence_fill_config(cfg.model_dump(mode="json"), errors)
    assert errors == {}
```

- [ ] **Step 2: Write the markdown gate**

```python
def test_rendered_markdown_still_passes_content_lint():
    from app.services import content_lint, phase_render
    from tests.services.test_phase_render import _rlc_cfg
    md = phase_render.render_md("practice-rlc", _rlc_cfg())
    codes = [f.code for f in content_lint.lint_phase(
        "practice-rlc", md, subject="history", output_language="ru")]
    assert "empty_body" not in codes
```

- [ ] **Step 3: Run the conformance suite**

Run: `uv run python -m pytest tests/conformance/ -q`
Expected: PASS (3 tests), or SKIPPED if the platform checkout is absent.

- [ ] **Step 4: Run the full suite**

Run: `uv run python -m pytest tests/ -q`
Expected: all green.

- [ ] **Step 5: Acceptance smoke (the only live model calls in this plan)**

Generate **one** lesson restricted to the two Pass-1 phases over `transport=api`, in a **fresh
process** (prompts are cached in-process). Before generating, assert the structured prompts load:

```python
from app.services import prompts
assert "JSON" in prompts.get_structured_prompt("history", "practice-rlc", output_language="ru")
```

Then verify on the produced rows:
- `authoring_mode == "structured"` for both phases
- `content_schema_version` is `rlc_config@1` / `sentence_fill_config@1`
- `content_json` validates against the platform validator
- `output_md` is non-empty and renders

Record the cost and paste the outcome into
`docs/research/2026-08-03-content-json-acceptance.md`. **Report the $ spent.**

- [ ] **Step 7: Commit**

```bash
git add tests/conformance docs/research/2026-08-03-content-json-acceptance.md
git commit -m "test(conformance): structured + markdown gates, acceptance recorded

Two independent gates rather than parse(render(cfg))==cfg, which is invalid
because the platform's markdown parsers are deliberately lossy.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 9 — STATUS: RUN, FAILED criterion 3 (2026-08-04)

Executed on scratch DB `edu_cj_task9`, **$0.2717**, 13 successful calls.

| criterion | result |
|---|---|
| both phases `authoring_mode="structured"` | PASS (zero fallback warnings) |
| non-null valid `content_json`, correct versions | PASS (CLEAN against the platform validator) |
| rendered markdown passes existing consumers | **FAIL — `judge=major_shipped` on both** (solver `ok`) |
| usage + exact cost reported | PASS |

Cause: the judge grades rendered markdown against the **markdown authoring prompt**, which demands
narrative sections (`Task`, `Context`, `Prediction`, `Final summary`, Why/confidence prompts,
feedback lines, "How to play") that do not exist in `content_json`. Fixed by Task 10, not by
expanding the schema. **Task 9 must be re-run after Task 10, under a newly approved budget.**

---

### Task 10: Artifact-aware judge routing

**Files:**
- Modify: `app/services/pipeline.py` (the three `_judge_with_timeout` call sites)
- Test: `tests/services/test_judge_routing.py`

**Interfaces:**
- Consumes: `PhaseArtifact.authoring_mode` (Task 5), `prompts.get_structured_prompt` (Task 6),
  `phase_judge.judge(..., contract_override=)` (existing).
- Produces: `_judge_inputs_for(artifact, *, subject, phase_name, output_language) -> tuple[str, str | None]`
  returning `(text_to_grade, contract_override)`.

**Routing rule:**

| `authoring_mode` | text to grade | contract override |
|---|---|---|
| `structured` | canonical JSON of `artifact.content_json` (`json.dumps(..., sort_keys=True, ensure_ascii=False, indent=2)`) | `get_structured_prompt(subject, phase_name, output_language=...)` |
| `markdown_builtin` / `markdown_custom` / `markdown_fallback` | `artifact.output_md` | `None` (or the existing custom override — unchanged) |

**Do NOT** change the judge's signature, the solver (it keeps grading rendered markdown and its
author-only `## Answer key`), or the schemas.

- [ ] **Step 1: Write the failing tests**

```python
import json

from app.services import pipeline
from app.services.phase_artifact import PhaseArtifact


def _structured():
    return PhaseArtifact(output_md="# md", content_json={"b": 2, "a": 1},
                         authoring_mode="structured",
                         content_schema_version="rlc_config@1", renderer_version="2")


def test_structured_artifact_is_judged_on_canonical_json_with_structured_contract():
    text, contract = pipeline._judge_inputs_for(
        _structured(), subject="history", phase_name="practice-rlc", output_language="uz")
    assert json.loads(text) == {"a": 1, "b": 2}
    assert text.index('"a"') < text.index('"b"')      # canonical: sorted keys
    assert contract and "JSON" in contract            # the structured authoring prompt


@pytest.mark.parametrize("mode", ["markdown_builtin", "markdown_custom", "markdown_fallback"])
def test_markdown_modes_keep_todays_judge_path(mode):
    art = PhaseArtifact(output_md="# original markdown", authoring_mode=mode)
    text, contract = pipeline._judge_inputs_for(
        art, subject="history", phase_name="practice-rlc", output_language="uz")
    assert text == "# original markdown"
    assert contract is None


def test_all_three_judge_sites_route_through_the_helper():
    src = inspect.getsource(pipeline._execute_phase)
    assert src.count("_judge_inputs_for(") >= 3, src.count("_judge_inputs_for(")
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run python -m pytest tests/services/test_judge_routing.py -q`
Expected: FAIL — `AttributeError: module 'app.services.pipeline' has no attribute '_judge_inputs_for'`

- [ ] **Step 3: Implement the helper and route all three sites**

```python
def _judge_inputs_for(artifact, *, subject: str, phase_name: str, output_language: str):
    """(text_to_grade, contract_override) for the judge.

    A structured artifact's markdown is DERIVED, so grading it against the
    hand-authored markdown prompt is a category error — it demands narrative
    sections (Task/Context/Prediction/Final summary, Why + confidence prompts,
    feedback lines) that content_json does not and should not carry. Grade the
    JSON against the structured authoring contract instead. Every markdown mode
    keeps today's path exactly.
    """
    if artifact.authoring_mode == "structured" and artifact.content_json is not None:
        text = json.dumps(artifact.content_json, sort_keys=True, ensure_ascii=False, indent=2)
        return text, get_structured_prompt(subject, phase_name, output_language=output_language)
    return artifact.output_md, None
```

Route the initial judge, the one-free-retry judge, and the post-regen judge through it. Where a
custom prompt override already exists, that path must be preserved for `markdown_custom`.

- [ ] **Step 4: Run tests** — `uv run python -m pytest tests/services/test_judge_routing.py -q` → PASS

- [ ] **Step 5: RED-prove the routing**

Force `_judge_inputs_for` to always return `(artifact.output_md, None)`; the structured test must
FAIL. Restore and re-run. Report both outputs.

- [ ] **Step 6: Regeneration-site coverage**

Add tests proving BOTH regeneration sites re-derive judge inputs from the **current** artifact —
a judge regen that falls back to markdown must be judged on markdown with the markdown contract,
not on the stale structured JSON.

- [ ] **Step 7: Full suite + commit**

```
uv run python -m pytest tests/ -q
```

## Finish

- [ ] Full suite green: `uv run python -m pytest tests/ -q`
- [ ] `git fetch origin && git log HEAD..origin/Nggaev-v2` — rebase if the base moved, re-run suite
- [ ] Worklog entry in `docs/memory/MASTER_MEMORY.md` + row in `docs/memory/INDEX.md`
      (re-check the INDEX tail for the next free number at finish time — numbers go stale)
- [ ] `git mv` this plan into `docs/superpowers/plans/shipped/`
- [ ] De-stale `docs/CODE_MAP.md` + `docs/HOW_IT_WORKS.md` (new structured lane) and
      `docs/DATABASE.md` (new `phase_outputs` columns)
- [ ] Open the two sibling plans: platform native path, mobile `minChars`

## Cross-repo final acceptance (runs only after all three lanes deploy)

This is **not** part of the HCGA lane's own definition-of-done — it is the gate for the umbrella
spec, and it runs against **deployed heads**, never local checkouts.

Preconditions: HCGA deployed (structured generation live) · platform deployed (native path +
scrub-then-revalidate + `min_chars` bound) · mobile deployed (`minChars` passed through).

- [ ] Generate one lesson × `practice-rlc` + `practice-sentence` on the deployed HCGA head.
- [ ] Post it with `scripts/ingest_to_platform.py --post` against the deployed platform.
- [ ] Walk the full path: **ingest → sanitization → transform → publish validation →
      student-facing redaction.**
- [ ] Assert `transform_report` records **`native`** for both phases (this is the first point at
      which "native" may be claimed).
- [ ] Assert the privacy oracle on the student payload: **no hidden answer arrays, no
      `is_correct`, no `consequence`, no `acceptable_keywords`, and no correctness-identifying
      markers in visible text.** Word-bank entries and unlabeled options ARE expected — a word bank
      must expose its vocabulary and RLC must render every option.
- [ ] Assert mobile/server `min_chars` parity: a reasoning answer shorter than the configured
      minimum is rejected client-side, matching `grade_rlc`.
- [ ] Re-run `docs/research/2026-07-20-teaching-audit-drill-density.py` ($0) and record the new
      per-phase parse-loss for `practice-rlc` / `practice-sentence`.
