# Extract-completeness check (warn-only) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make extract under-summarization *visible* — one bounded, warn-only model call per fresh extract that compares the produced summary against the lesson's own source pages and records the core items the extract dropped.

**Architecture:** Mirror CQ-D's fidelity guard, inverted. Fidelity asks *"did the extract invent something?"* (`extract_fidelity_candidates` → `verify_extract_fidelity` → regen-once); this asks *"did the extract drop something?"* (`_lesson_source_or_none` → `agent.check_extract_coverage` → warn-only). It runs inline in `pipeline._execute_phase`'s extract branch after the accepted output is chosen, appends to the existing `warnings` list, and rides the existing fenced done-write. No new write path, no migration, no change to `lesson_context`'s shape.

**Tech Stack:** Python 3.12 · FastAPI · SQLAlchemy async · pydantic v2 structured output via `agent.run_phase(schema=…)` · `transport=api` over the plain Gemini API key · pytest / pytest-asyncio.

---

## Approach & key decisions

- **Chosen shape: a cheap LLM completeness check against the lesson's own source pages, warn-only.** One `run_phase(schema=…)` call per *fresh* extract, input = the lesson page window (±1) + the produced summary, on the extract role's pinned cheap model. It is the first check in the stack that reads the **source** instead of trusting the extract, which is precisely the invisibility the coverage audit named (`docs/research/2026-07-06-coverage-audit.md`).
- **Rejected — deterministic marker scan only** (`N-misol` / `masala` / `Пример` / `Задача` vs the contract's `## Worked-example types`): free, but blind to concept/fact omissions. Verified against the labeled data: of the 8 ground-truth extract-losses, only 3 are worked-example types — a marker scan structurally cannot see the other 5 (`Noqavariq ko'pburchak`, `Ion zaryadi miqdori qoidasi`, the two Crusades facts, …).
- **Rejected — producer-side self-critique** (second pass inside the extract, no check): cannot be staged warn-only, cannot be calibrated against the labeled dataset, and yields no measurement for the deferred regen decision.
- **Rejected — making the judge source-aware**: the judge runs per phase (×11), so shipping the source window into it multiplies tokens by an order of magnitude, and it re-opens `_FIDELITY_RULE`'s hard-won contradiction-vs-absence calibration (worklog 0159 / R25).
- **Warn-only in v1, regen deliberately deferred.** Standing rule: never gate on a new check's first version (the validate_toc / solver lesson). The regen-once path is already shaped by CQ-D — `agent.summarize_lesson(correction_hint=…)` — and drops in at the same call site once live precision is measured. Every check call writes an `agent_usages` row (`operation="lesson.extract.coverage"`) whose `raw_envelope` carries the verdict, so that precision is queryable fleet-wide with no new plumbing.
- **Load-bearing facts, each verified against real code at `2ebab53`:**
  - `pipeline.py:1648` initialises `warnings: list[str] = []` and `pipeline.py:1651` gates the judge/lint block behind `if phase_name != "extract"` — the done-write at `pipeline.py:1865-1882` (fenced with `claim_token`) is **common to both branches**, so the extract branch can contribute warnings with no signature change and no new write.
  - The cross-job extract cache **returns early** at `pipeline.py:1441-1472`, before the check's call site — a reused extract costs nothing, because the producing job already paid.
  - `agent.read_page_range_text(pdf, ps, pe, margin=1)` + `agent.validate_extract_text` already exist and are exactly what `_verify_source_for_section` (`pipeline.py:1290`) uses. The new helper deliberately **drops that function's whole-book fallback**: a whole-book "source" would make the checker enumerate *other* lessons' items and report them as omissions.
  - `lesson_context`'s shape does **not** change (the v3 enumerated contract shipped in worklog 0119) — so the coverage audit's heaviest composition constraint ("a shape change touches every content phase + judge + solver") is satisfied by construction. The regression surface is the extract phase only.
  - The 9 audited lessons are still live in `edu_copy` with their exact labeled extracts (`builtin:extract:v2`, char counts 440/693/1455/666/1452/704/4613/3110/2546 — byte-for-byte the artifacts the labels describe) and their books are on disk under `var/books/`. The acceptance gate is realizable today.
- **Known limit, stated up front:** the checker is itself a cheap-tier model reading a page window. It narrows the blind spot; it does not close it. And the labels it is calibrated against came from `gemini-3.1-pro` plus one hand-verified case — calibration measures agreement with a strong model, not with truth.

---

## Global Constraints

- **Warn-only. Non-negotiable.** This check must never fail a job, never park a job, never mutate the extract, and never gate a regen. Every failure path is fail-open.
- **Transport:** all real calls run `transport=api` (the cli path is retired from operational use — CLAUDE.md standing decision 2026-07-01). Never benchmark or verify against cli.
- **Money rule:** no mass generation. Calibration is 9 lessons × 2 models = 18 bounded calls; the live gate is ONE single-lesson generation. Every task that spends money reports tokens and `$`.
- **Gemini-only policy:** the check reuses the extract role's *provider*; only the *model* is overridable (`extract_coverage_model`), and only within that provider.
- **Composition:** the CQ-D fidelity tests (`tests/services/test_extract_fidelity.py`) and the extract-dispatch tests (`tests/services/test_pipeline_extract_dispatch.py`) must stay green **unmodified**. If a task needs to edit either file, stop — the design is wrong.
- **Staging discipline:** stage only the files each task lists. Never `git add -A` — other sessions commit to this branch's base.
- **Branch:** all work happens in the worktree `/Users/macmini5/Documents/HCGA-extract-coverage` on `feat/extract-coverage-check` (cut from `origin/Nggaev-v2` @ `2ebab53`). Verify with a hard guard before every commit:
  ```bash
  [ "$(git rev-parse --abbrev-ref HEAD)" = "feat/extract-coverage-check" ] || exit 1
  ```
- **Worktree .env trap:** app code run from this worktree walks up and finds `/Users/macmini5/Documents/.env`, **not** the repo's. Any script that makes real calls must assert its module path and take credentials from explicitly exported env vars (Task 5 does this).

---

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `app/config.py` (modify, extract block ~line 227) | Three knobs: `extract_coverage_check_enabled`, `extract_coverage_model`, `extract_coverage_max_items`. | 1 |
| `app/services/agent.py` (modify, beside `verify_extract_fidelity` ~line 1400) | `ExtractCoverageMiss` / `ExtractCoverageVerdict` models, `_CHECK_COVERAGE_PROMPT`, `check_extract_coverage(...)` — the model boundary, fail-open. | 1 |
| `app/services/pipeline.py` (modify) | `_lesson_source_or_none` (strict lesson-scoped source, no whole-book fallback) · `_extract_coverage_warnings` (pure formatter) · `_check_extract_coverage` (orchestration + skips) · the call site in the extract branch. | 2, 3, 4 |
| `tests/services/test_extract_coverage.py` (create) | Agent-boundary + config + pure-formatter tests. | 1, 3 |
| `tests/services/test_pipeline_extract_coverage.py` (create) | Source-helper tests + wiring tests on the real `_execute_phase` via the DB-free harness. | 2, 4 |
| `scripts/extract_coverage_calibrate.py` (create) | Calibration harness against the 9-lesson labeled dataset. Read-only DB, real bounded calls. | 5 |
| `docs/research/2026-08-07-extract-coverage-calibration.md` (create) | Calibration result: recall, noise, per-lesson table, the default-on/off decision. | 5 |
| Docs: `docs/memory/MASTER_MEMORY.md`, `docs/memory/INDEX.md`, `docs/memory/WISHLIST.md`, `docs/memory/ROADMAP.md`, `docs/HOW_IT_WORKS.md`, `docs/CODE_MAP.md` | Worklog, index row, deferred-regen backlog item, live-doc de-staling. | 7 |

---

### Task 1: Config knobs + the `check_extract_coverage` agent boundary

**Files:**
- Modify: `app/config.py:227` (immediately after `extract_min_summary_chars`)
- Modify: `app/services/agent.py:1402` (immediately after `verify_extract_fidelity`)
- Test: `tests/services/test_extract_coverage.py` (create)

**Interfaces:**
- Consumes: `agent.run_phase(provider=, model=, phase_prompt=, phase_name=, schema=, homework_job_id=, phase_output_id=, operation=, transport=) -> PhaseResult` (has `.parsed`); `agent.logger`.
- Produces (later tasks depend on these exact names):
  - `agent.ExtractCoverageMiss` — pydantic model, fields `label: str`, `central: bool = False`
  - `agent.ExtractCoverageVerdict` — field `missing: list[ExtractCoverageMiss]`
  - `async agent.check_extract_coverage(*, summary: str, source_text: str, section_title: str, section_number: str, provider: str, model: Optional[str], transport: str, homework_job_id: Optional[UUID], phase_output_id: Optional[UUID]) -> list[ExtractCoverageMiss]`
  - `settings.extract_coverage_check_enabled: bool`, `settings.extract_coverage_model: Optional[str]`, `settings.extract_coverage_max_items: int`

- [ ] **Step 1: Write the failing tests**

Create `tests/services/test_extract_coverage.py`:

```python
# tests/services/test_extract_coverage.py
"""Extract-completeness check (warn-only) — agent boundary + config defaults.

The check is the inverse of CQ-D's fidelity guard: fidelity asks whether the
extract INVENTED something, this asks whether it DROPPED something. It must
never raise — a broken check degrades to 'no findings', never to a failed job.
"""
import pytest
from unittest.mock import AsyncMock, patch

from app.config import settings
from app.services import agent as agent_mod
from app.services.agent import (
    ExtractCoverageMiss,
    ExtractCoverageVerdict,
    check_extract_coverage,
)


def test_config_defaults_are_warn_only_and_inherit_the_extract_model():
    # Kill switch present; model override defaults to "inherit the extract role".
    assert isinstance(settings.extract_coverage_check_enabled, bool)
    assert settings.extract_coverage_model is None
    assert settings.extract_coverage_max_items >= 1


@pytest.mark.asyncio
async def test_check_returns_missing_items_from_model():
    fake = agent_mod.PhaseResult(
        text="{}",
        parsed=ExtractCoverageVerdict(missing=[
            ExtractCoverageMiss(label="Izotoplar massa ulushi orqali o'rtacha atom massasi", central=True),
            ExtractCoverageMiss(label="Ion zaryadi miqdori qoidasi", central=False),
        ]),
    )
    with patch.object(agent_mod, "run_phase", AsyncMock(return_value=fake)) as rp:
        out = await check_extract_coverage(
            summary="periodic trends narrative only",
            source_text="… 3-misol … 4-misol …",
            section_title="Kimyoviy elementlar", section_number="13",
            provider="gemini", model="gemini-3.5-flash-lite", transport="api",
            homework_job_id=None, phase_output_id=None,
        )
    assert [m.label for m in out] == [
        "Izotoplar massa ulushi orqali o'rtacha atom massasi",
        "Ion zaryadi miqdori qoidasi",
    ]
    assert [m.central for m in out] == [True, False]
    assert rp.call_args.kwargs["schema"] is ExtractCoverageVerdict
    assert rp.call_args.kwargs["operation"] == "lesson.extract.coverage"
    # The lesson identity MUST reach the prompt — the ±1 page window carries
    # neighbouring lessons, and without the title the checker reports their
    # items as omissions.
    prompt = rp.call_args.kwargs["phase_prompt"]
    assert "Kimyoviy elementlar" in prompt and "13" in prompt
    assert "periodic trends narrative only" in prompt
    assert "3-misol" in prompt


@pytest.mark.asyncio
async def test_check_clean_extract_returns_empty():
    fake = agent_mod.PhaseResult(text="{}", parsed=ExtractCoverageVerdict(missing=[]))
    with patch.object(agent_mod, "run_phase", AsyncMock(return_value=fake)):
        out = await check_extract_coverage(
            summary="complete", source_text="source", section_title="T", section_number="1",
            provider="gemini", model="gemini-3.5-flash-lite", transport="api",
            homework_job_id=None, phase_output_id=None,
        )
    assert out == []


@pytest.mark.asyncio
async def test_check_is_fail_open_on_model_error():
    with patch.object(agent_mod, "run_phase", AsyncMock(side_effect=RuntimeError("429 boom"))):
        out = await check_extract_coverage(
            summary="s", source_text="src", section_title="T", section_number="1",
            provider="gemini", model="gemini-3.5-flash-lite", transport="api",
            homework_job_id=None, phase_output_id=None,
        )
    assert out == []          # advisory: an error degrades to 'no findings'


@pytest.mark.asyncio
async def test_check_drops_blank_labels_and_unparsed_verdicts():
    fake = agent_mod.PhaseResult(
        text="not json",
        parsed=ExtractCoverageVerdict(missing=[ExtractCoverageMiss(label="   ", central=True)]),
    )
    with patch.object(agent_mod, "run_phase", AsyncMock(return_value=fake)):
        assert await check_extract_coverage(
            summary="s", source_text="src", section_title="T", section_number="1",
            provider="gemini", model="gemini-3.5-flash-lite", transport="api",
            homework_job_id=None, phase_output_id=None,
        ) == []

    unparsed = agent_mod.PhaseResult(text="plain text, no schema", parsed=None)
    with patch.object(agent_mod, "run_phase", AsyncMock(return_value=unparsed)):
        assert await check_extract_coverage(
            summary="s", source_text="src", section_title="T", section_number="1",
            provider="gemini", model="gemini-3.5-flash-lite", transport="api",
            homework_job_id=None, phase_output_id=None,
        ) == []


@pytest.mark.asyncio
async def test_empty_summary_or_source_makes_no_paid_call():
    with patch.object(agent_mod, "run_phase", AsyncMock()) as rp:
        assert await check_extract_coverage(
            summary="   ", source_text="src", section_title="T", section_number="1",
            provider="gemini", model="gemini-3.5-flash-lite", transport="api",
            homework_job_id=None, phase_output_id=None,
        ) == []
        assert await check_extract_coverage(
            summary="s", source_text="", section_title="T", section_number="1",
            provider="gemini", model="gemini-3.5-flash-lite", transport="api",
            homework_job_id=None, phase_output_id=None,
        ) == []
    rp.assert_not_awaited()
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd /Users/macmini5/Documents/HCGA-extract-coverage
uv run python -m pytest tests/services/test_extract_coverage.py -q
```
Expected: FAIL — `ImportError: cannot import name 'ExtractCoverageMiss' from 'app.services.agent'`.

- [ ] **Step 3: Add the config knobs**

In `app/config.py`, immediately after the `extract_min_summary_chars` line (~227):

```python
    # Extract-completeness check (warn-only, worklog TBD-plan 2026-08-07): one
    # bounded call per FRESH extract comparing the summary against the lesson's
    # own source pages. Advisory only — it never fails a job and never regens.
    extract_coverage_check_enabled: bool = True
    # None = inherit the extract role's model (the cheap pinned extractor).
    # Set to a stronger model only if calibration shows the pinned tier can't
    # see the omissions (see docs/research/2026-08-07-extract-coverage-calibration.md).
    extract_coverage_model: str | None = None
    extract_coverage_max_items: int = 8   # cap on items named in one warning
```

(`config.py` does **not** import `Optional` — it uses the `str | None` style, as at `gemini_api_key`. Match that.)

- [ ] **Step 4: Add the agent boundary**

In `app/services/agent.py`, immediately after `verify_extract_fidelity` ends (~line 1402):

```python
class ExtractCoverageMiss(BaseModel):
    """One core teachable item the SOURCE lesson has and the extract dropped."""
    label: str
    central: bool = False


class ExtractCoverageVerdict(BaseModel):
    """Model verdict for the extract-completeness check. `missing` is empty when
    the extract captures everything the lesson teaches."""
    missing: list[ExtractCoverageMiss] = Field(default_factory=list)


_CHECK_COVERAGE_PROMPT = (
    "You are checking whether a LESSON SUMMARY is COMPLETE with respect to the "
    "SOURCE textbook text it was written from. Downstream homework generators "
    "read ONLY the summary — they never see the source — so anything the "
    "summary omits can never be taught.\n\n"
    "The SOURCE below is a printed page window containing the lesson titled "
    '"{title}" (section {number}). The window may also contain fragments of the '
    "NEIGHBOURING lessons — ignore anything that is not part of that lesson.\n\n"
    "List the CORE teachable items of THAT lesson that the SUMMARY does NOT "
    "capture — what a student is expected to learn, recall or apply:\n"
    "- concepts / terms the lesson defines\n"
    "- rules / theorems / formulas the lesson states\n"
    "- WORKED-EXAMPLE and problem TYPES the lesson demonstrates (what the "
    "student must be able to solve) — these are dropped most often, so check "
    "them explicitly\n"
    "- key facts (dates, names, classifications) the lesson teaches\n\n"
    "Rules:\n"
    "- Report an item ONLY if it is genuinely absent from the SUMMARY. Different "
    "wording, a shorter phrasing, or a more general statement that still covers "
    "the item is NOT a miss.\n"
    "- Do NOT report items belonging to a neighbouring lesson, nor background "
    "the lesson only mentions in passing.\n"
    "- Set `central` true ONLY for primary teaching points; secondary or "
    "supporting details are false.\n"
    "- `label` is a short name for the item, in the lesson's language.\n"
    "- If the summary captures everything, return an empty list.\n\n"
    "===== LESSON SUMMARY =====\n{summary}\n===== END SUMMARY =====\n\n"
    "===== SOURCE TEXTBOOK TEXT =====\n{source}\n===== END SOURCE ====="
)


async def check_extract_coverage(
    *, summary: str, source_text: str, section_title: str, section_number: str,
    provider: str, model: Optional[str], transport: str,
    homework_job_id: Optional[UUID], phase_output_id: Optional[UUID],
) -> list[ExtractCoverageMiss]:
    """One structured call: which core items of the SOURCE lesson are absent
    from the extract SUMMARY. WARN-ONLY — the caller records the result and
    never acts on it. Never raises: on any failure returns [] (fail-open, the
    same contract as verify_extract_fidelity)."""
    if not (summary or "").strip() or not (source_text or "").strip():
        return []
    prompt = _CHECK_COVERAGE_PROMPT.format(
        title=section_title, number=section_number,
        summary=summary, source=source_text,
    )
    try:
        result = await run_phase(
            provider=provider, model=model, phase_prompt=prompt,
            phase_name="lesson.extract.coverage", schema=ExtractCoverageVerdict,
            homework_job_id=homework_job_id, phase_output_id=phase_output_id,
            operation="lesson.extract.coverage", transport=transport,
        )
    except Exception as exc:  # noqa: BLE001 — advisory: must never fail a job
        logger.warning(f"agent.check_extract_coverage failed (fail-open): {exc!r}")
        return []
    parsed = result.parsed
    if isinstance(parsed, ExtractCoverageVerdict):
        return [m for m in parsed.missing if (m.label or "").strip()]
    return []
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
uv run python -m pytest tests/services/test_extract_coverage.py -q
```
Expected: PASS (6 tests).

- [ ] **Step 6: Prove the CQ-D neighbour is untouched**

```bash
uv run python -m pytest tests/services/test_extract_fidelity.py tests/services/test_config_extract_robustness.py -q
```
Expected: PASS, with no edits to either file.

- [ ] **Step 7: Commit**

```bash
[ "$(git rev-parse --abbrev-ref HEAD)" = "feat/extract-coverage-check" ] || exit 1
git add app/config.py app/services/agent.py tests/services/test_extract_coverage.py
git commit -m "feat(extract): add warn-only extract-completeness check boundary

One bounded structured call comparing an extract summary against the lesson's
own source text. Inverse of the CQ-D fidelity guard (invention) — this detects
OMISSION, the loss class the coverage audit found is invisible to the judge.
Fail-open by contract; the caller lands in a later task."
```

---

### Task 2: `_lesson_source_or_none` — the strict lesson-scoped source

**Files:**
- Modify: `app/services/pipeline.py:1304` (immediately after `_verify_source_for_section`)
- Test: `tests/services/test_pipeline_extract_coverage.py` (create)

**Interfaces:**
- Consumes: `agent.read_page_range_text(pdf_path, ps, pe, *, margin=0) -> str`; `agent.validate_extract_text(text) -> Optional[str]`.
- Produces: `async pipeline._lesson_source_or_none(pdf_path: Path, section: dict) -> Optional[str]`.

**Why this is not `_verify_source_for_section`:** that helper falls back to the whole `book_text` when the page range is missing. For a *fidelity* check that fallback is conservative (more source = fewer false "invented" verdicts). For a *completeness* check it inverts: the checker would enumerate every other lesson's items and report them all as omissions. Absent a real page window this check must not run at all.

- [ ] **Step 1: Write the failing tests**

Create `tests/services/test_pipeline_extract_coverage.py`:

```python
# tests/services/test_pipeline_extract_coverage.py
"""Extract-completeness check — pipeline side (source scoping + wiring).

Wiring tests drive the REAL ``pipeline._execute_phase`` with DB-free mocks
(same harness idiom as test_pipeline_extract_dispatch.py), so the actual branch
is exercised rather than a copy of it.
"""
import asyncio
from pathlib import Path

import pytest

from app.services import pipeline


def test_lesson_source_returns_page_scoped_text(monkeypatch):
    seen = {}

    def _page_range(path, ps, pe, *, margin=0):
        seen["args"] = (ps, pe, margin)
        return "L" * 4000

    monkeypatch.setattr(pipeline.agent, "read_page_range_text", _page_range)
    out = asyncio.run(pipeline._lesson_source_or_none(
        Path("/tmp/x.pdf"), {"title": "T", "number": "1", "page_start": 51, "page_end": 62}))
    assert out == "L" * 4000
    # ±1 page — the same window the CQ-D verify call uses.
    assert seen["args"] == (51, 62, 1)


def test_lesson_source_is_none_without_a_page_range(monkeypatch):
    called = {"n": 0}

    def _page_range(path, ps, pe, *, margin=0):
        called["n"] += 1
        return "text"

    monkeypatch.setattr(pipeline.agent, "read_page_range_text", _page_range)
    for section in ({"page_start": None, "page_end": 62}, {"page_start": 51, "page_end": None}, {}):
        assert asyncio.run(pipeline._lesson_source_or_none(Path("/tmp/x.pdf"), section)) is None
    # NO whole-book fallback: a whole-book source would make the checker report
    # every OTHER lesson's items as omissions.
    assert called["n"] == 0


def test_lesson_source_is_none_when_text_layer_is_unusable(monkeypatch):
    # Scanned / garbled window → Gate A rejects it → skip the check entirely.
    monkeypatch.setattr(pipeline.agent, "read_page_range_text",
                        lambda path, ps, pe, *, margin=0: "x" * 40)
    assert asyncio.run(pipeline._lesson_source_or_none(
        Path("/tmp/x.pdf"), {"page_start": 1, "page_end": 3})) is None


def test_lesson_source_is_none_on_read_error(monkeypatch):
    def _boom(path, ps, pe, *, margin=0):
        raise OSError("pdf read failed")

    monkeypatch.setattr(pipeline.agent, "read_page_range_text", _boom)
    assert asyncio.run(pipeline._lesson_source_or_none(
        Path("/tmp/x.pdf"), {"page_start": 1, "page_end": 3})) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run python -m pytest tests/services/test_pipeline_extract_coverage.py -q
```
Expected: FAIL — `AttributeError: module 'app.services.pipeline' has no attribute '_lesson_source_or_none'`.

- [ ] **Step 3: Implement the helper**

In `app/services/pipeline.py`, immediately after `_verify_source_for_section` (which ends at line 1304):

```python
async def _lesson_source_or_none(pdf_path, section: dict) -> "str | None":
    """STRICT lesson-scoped source text for the completeness check: the lesson's
    own printed pages (±1), or None.

    Deliberately has NO whole-book fallback — unlike _verify_source_for_section.
    A completeness check handed the whole book would enumerate every OTHER
    lesson's items and report them as omissions, so 'no usable window' must mean
    'do not run the check', never 'check against everything'. A window that
    fails Gate A (scanned / garbled text layer) is likewise unusable — that is
    also what keeps the check off the vision-extract path."""
    ps, pe = section.get("page_start"), section.get("page_end")
    if not ps or not pe:
        return None
    try:
        text = await asyncio.to_thread(
            agent.read_page_range_text, pdf_path, ps, pe, margin=1
        )
    except Exception as exc:  # noqa: BLE001 — advisory path, never fail a job
        logger.warning(f"extract coverage: source read failed (fail-open): {exc!r}")
        return None
    if not (text or "").strip() or agent.validate_extract_text(text) is not None:
        return None
    return text
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run python -m pytest tests/services/test_pipeline_extract_coverage.py -q
```
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
[ "$(git rev-parse --abbrev-ref HEAD)" = "feat/extract-coverage-check" ] || exit 1
git add app/services/pipeline.py tests/services/test_pipeline_extract_coverage.py
git commit -m "feat(extract): strict lesson-scoped source for the completeness check

No whole-book fallback by design: a completeness check given the whole book
reports every other lesson's items as omissions. No window (or an unusable
text layer) means the check does not run."
```

---

### Task 3: `_extract_coverage_warnings` — the pure warning formatter

**Files:**
- Modify: `app/services/pipeline.py` (immediately after `_lesson_source_or_none` from Task 2)
- Test: `tests/services/test_extract_coverage.py:end` (append)

**Interfaces:**
- Consumes: `agent.ExtractCoverageMiss` (Task 1); `settings.extract_coverage_max_items` (Task 1).
- Produces: `pipeline._extract_coverage_warnings(misses: list[agent.ExtractCoverageMiss]) -> list[str]` — zero or exactly one string, central items first, prefix `extract_coverage:`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/services/test_extract_coverage.py`:

```python
# --- warning formatter (pure) ------------------------------------------------

from app.services import pipeline as pipeline_mod


def _miss(label, central=False):
    return ExtractCoverageMiss(label=label, central=central)


def test_no_misses_formats_to_no_warning():
    assert pipeline_mod._extract_coverage_warnings([]) == []


def test_one_aggregated_warning_lists_central_items_first():
    out = pipeline_mod._extract_coverage_warnings([
        _miss("secondary detail"),
        _miss("isotope mass-fraction problem", central=True),
        _miss("valence → unknown element problem", central=True),
    ])
    assert len(out) == 1
    msg = out[0]
    assert msg.startswith("extract_coverage:")
    assert "3 item(s)" in msg and "2 central" in msg
    # central first, so a truncated read still shows what matters most
    assert msg.index("isotope mass-fraction problem") < msg.index("secondary detail")


def test_warning_caps_the_item_list(monkeypatch):
    monkeypatch.setattr(settings, "extract_coverage_max_items", 2)
    out = pipeline_mod._extract_coverage_warnings([_miss(f"item {i}") for i in range(5)])
    assert len(out) == 1
    assert "(+3 more)" in out[0]
    assert "item 4" not in out[0]


def test_blank_labels_are_dropped_and_long_labels_truncated():
    assert pipeline_mod._extract_coverage_warnings([_miss("  "), _miss("")]) == []
    out = pipeline_mod._extract_coverage_warnings([_miss("L" * 400)])
    assert len(out[0]) < 300
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run python -m pytest tests/services/test_extract_coverage.py -q
```
Expected: FAIL — `AttributeError: module 'app.services.pipeline' has no attribute '_extract_coverage_warnings'` (the four new formatter tests fail; the six from Task 1 still pass).

- [ ] **Step 3: Implement the formatter**

In `app/services/pipeline.py`, immediately after `_lesson_source_or_none`:

```python
def _extract_coverage_warnings(misses: list) -> list[str]:
    """Format completeness findings as ONE advisory warning string (or none).

    Central items come first so a truncated read still shows what matters. The
    `extract_coverage:` prefix is deliberately distinct from `lint:` (which
    marks deterministic checks) — this one costs a model call."""
    labels = [(m.label or "").strip()[:80] for m in misses if (m.label or "").strip()]
    if not labels:
        return []
    ordered = (
        [(m.label or "").strip()[:80] for m in misses if m.central and (m.label or "").strip()]
        + [(m.label or "").strip()[:80] for m in misses if not m.central and (m.label or "").strip()]
    )
    n_central = sum(1 for m in misses if m.central and (m.label or "").strip())
    cap = max(1, settings.extract_coverage_max_items)
    shown = "; ".join(ordered[:cap])
    more = f" (+{len(ordered) - cap} more)" if len(ordered) > cap else ""
    return [
        f"extract_coverage: {len(ordered)} item(s) the lesson teaches are absent "
        f"from the extract ({n_central} central): {shown}{more}"
    ]
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run python -m pytest tests/services/test_extract_coverage.py -q
```
Expected: PASS (10 tests).

- [ ] **Step 5: Commit**

```bash
[ "$(git rev-parse --abbrev-ref HEAD)" = "feat/extract-coverage-check" ] || exit 1
git add app/services/pipeline.py tests/services/test_extract_coverage.py
git commit -m "feat(extract): format completeness findings as one advisory warning

Central items first, capped, distinct 'extract_coverage:' prefix (a model call,
not a deterministic lint)."
```

---

### Task 4: Wire the check into the extract branch

**Files:**
- Modify: `app/services/pipeline.py` — new `_check_extract_coverage` orchestrator (after `_extract_coverage_warnings`), the call site in the extract branch (~line 1568, after `_run_with_failover` returns), the `extract_warnings` init before `try:` (~line 1423), and the `warnings` init (line 1648).
- Test: `tests/services/test_pipeline_extract_coverage.py` (append)

**Interfaces:**
- Consumes: `pipeline._lesson_source_or_none` (Task 2), `pipeline._extract_coverage_warnings` (Task 3), `agent.check_extract_coverage` (Task 1), `settings.extract_coverage_check_enabled` / `settings.extract_coverage_model` (Task 1).
- Produces: `async pipeline._check_extract_coverage(*, output_md, pdf_path, section, provider, model, transport, job_id, po_id) -> list[str]`; the extract phase row's `validation_warnings` now carries `extract_coverage:` strings.

**Placement rationale:** after `_run_with_failover` returns the accepted output, so the check runs **once per job, not once per failover attempt** — and so the deferred regen-once follow-up drops in at the same point, before the extract is promoted to `lesson_context`. The cross-job cache returns at `pipeline.py:1472`, *before* this point, so a reused extract never re-pays.

- [ ] **Step 1: Write the failing tests**

Append to `tests/services/test_pipeline_extract_coverage.py`:

```python
# --- wiring on the real _execute_phase ---------------------------------------

from contextlib import asynccontextmanager
from typing import Optional
from uuid import uuid4

from app.config import settings
from app.services import agent as agent_mod


class _FakePhaseRow:
    def __init__(self):
        self.id = uuid4()


class _FakeSession:
    async def commit(self):
        return None


@asynccontextmanager
async def _fake_session():
    yield _FakeSession()


_CLEAN_TEXT = (
    "Hujayra tirik organizmlarning eng kichik tuzilish va funksional birligidir. "
    "Har bir hujayra membrana, sitoplazma va yadrodan tashkil topgan. "
    "Yadro irsiy axborotni saqlaydi va hujayra faoliyatini boshqaradi. "
) * 12


def _install_harness(monkeypatch, *, cached_extract=None):
    """DB-free harness; captures every set_status write so the test can assert
    what landed in validation_warnings on the done-write."""
    writes = []
    monkeypatch.setattr(pipeline, "SessionLocal", _fake_session)

    async def _create_or_reset(session, **kwargs):
        return _FakePhaseRow()

    async def _set_status(session, po_id, status, **kwargs):
        writes.append((status, kwargs))
        return None

    async def _noop(*args, **kwargs):
        return None

    async def _find_latest_extract(session, **kwargs):
        return cached_extract

    monkeypatch.setattr(pipeline.phase_repo, "create_or_reset", _create_or_reset)
    monkeypatch.setattr(pipeline.phase_repo, "set_status", _set_status)
    monkeypatch.setattr(pipeline.phase_repo, "find_latest_extract", _find_latest_extract)
    monkeypatch.setattr(pipeline.jobs_repo, "set_status", _noop)
    monkeypatch.setattr(pipeline.agent, "read_whole_book_text", lambda path: _CLEAN_TEXT)
    monkeypatch.setattr(pipeline.agent, "pdf_page_count", lambda path: 2)
    monkeypatch.setattr(pipeline.agent, "read_page_range_text",
                        lambda path, ps, pe, *, margin=0: _CLEAN_TEXT)
    monkeypatch.setattr(pipeline.agent, "validate_extract_summary", lambda out: None)

    async def _normal(**kwargs):
        return ("A normal whole-text lesson summary passing Gate B validation.", 5, 7)

    monkeypatch.setattr(pipeline.agent, "summarize_lesson", _normal)
    return writes


def _install_coverage_spy(monkeypatch, *, misses=(), boom=None):
    calls = {"n": 0, "kwargs": None}

    async def _check(**kwargs):
        calls["n"] += 1
        calls["kwargs"] = kwargs
        if boom is not None:
            raise boom
        return list(misses)

    monkeypatch.setattr(pipeline.agent, "check_extract_coverage", _check)
    return calls


def _run_extract_phase(**overrides):
    kwargs = dict(
        job_id=uuid4(), phase_name="extract", phase_order=1, subject="biology",
        provider="claude", model="claude-sonnet-4-6",
        pdf_path=Path("/tmp/does-not-matter.pdf"), attach_file=False,
        section={"id": None, "title": "Zamburug'lar", "number": "6",
                 "page_start": 19, "page_end": 24},
        lesson_context=None, prior_outputs={}, difficulty=None,
        transport="api", extract_transport="api", judge_transport="api",
        extract_provider="gemini", extract_model="gemini-3.5-flash-lite",
    )
    kwargs.update(overrides)
    return asyncio.run(pipeline._execute_phase(**kwargs))


def _done_warnings(writes):
    return next(kw.get("validation_warnings") for status, kw in writes if status == "done")


def test_coverage_warning_lands_on_the_extract_done_write(monkeypatch):
    writes = _install_harness(monkeypatch)
    calls = _install_coverage_spy(monkeypatch, misses=[
        agent_mod.ExtractCoverageMiss(label="Ektotrof mikoriza", central=True),
    ])
    _run_extract_phase()
    assert calls["n"] == 1
    warnings = _done_warnings(writes)
    assert warnings and warnings[0].startswith("extract_coverage:")
    assert "Ektotrof mikoriza" in warnings[0]
    # the pinned extract role serves the check, over the extract's transport
    assert calls["kwargs"]["provider"] == "gemini"
    assert calls["kwargs"]["model"] == "gemini-3.5-flash-lite"
    assert calls["kwargs"]["transport"] == "api"
    assert calls["kwargs"]["section_title"] == "Zamburug'lar"


def test_clean_extract_writes_no_warnings(monkeypatch):
    writes = _install_harness(monkeypatch)
    _install_coverage_spy(monkeypatch, misses=[])
    _run_extract_phase()
    assert _done_warnings(writes) is None


def test_kill_switch_makes_no_call(monkeypatch):
    monkeypatch.setattr(settings, "extract_coverage_check_enabled", False)
    writes = _install_harness(monkeypatch)
    calls = _install_coverage_spy(monkeypatch, misses=[
        agent_mod.ExtractCoverageMiss(label="x", central=True)])
    _run_extract_phase()
    assert calls["n"] == 0
    assert _done_warnings(writes) is None


def test_model_override_wins_over_the_extract_role_model(monkeypatch):
    monkeypatch.setattr(settings, "extract_coverage_model", "gemini-3.5-flash")
    _install_harness(monkeypatch)
    calls = _install_coverage_spy(monkeypatch, misses=[])
    _run_extract_phase()
    assert calls["kwargs"]["model"] == "gemini-3.5-flash"


def test_reused_extract_never_pays_for_the_check(monkeypatch):
    class _Cached:
        id = uuid4()
        job_id = uuid4()
        output_md = "A previously produced extract summary, reused verbatim."

    writes = _install_harness(monkeypatch, cached_extract=_Cached())
    calls = _install_coverage_spy(monkeypatch, misses=[
        agent_mod.ExtractCoverageMiss(label="x", central=True)])

    async def _record(**kwargs):
        return None

    monkeypatch.setattr(pipeline.agent, "record_cached_lesson_extract", _record)
    monkeypatch.setattr(pipeline.agent, "summarize_lesson",
                        lambda **kw: pytest.fail("cached path must not re-extract"))
    out_md, tin, tout, _ph, _ps = _run_extract_phase(
        section={"id": uuid4(), "title": "T", "number": "1",
                 "page_start": 19, "page_end": 24})
    assert out_md == _Cached.output_md
    # the producing job already ran the check — re-running it would re-bill the
    # same lesson on every repeat job for that section.
    assert calls["n"] == 0


def test_no_page_range_skips_the_check_without_failing_the_phase(monkeypatch):
    writes = _install_harness(monkeypatch)
    calls = _install_coverage_spy(monkeypatch, misses=[])
    out_md, *_ = _run_extract_phase(
        section={"id": None, "title": "T", "number": "1",
                 "page_start": None, "page_end": None})
    assert calls["n"] == 0
    assert out_md            # the extract itself still completed
    assert _done_warnings(writes) is None


def test_check_failure_is_fail_open_and_the_phase_still_completes(monkeypatch):
    writes = _install_harness(monkeypatch)
    _install_coverage_spy(monkeypatch, boom=RuntimeError("verdict blew up"))
    out_md, *_ = _run_extract_phase()
    assert out_md
    assert _done_warnings(writes) is None


def test_lease_and_cancel_signals_are_never_swallowed(monkeypatch):
    """A control signal means this worker no longer owns the job. Swallowing it
    inside an advisory check would let an obsolete worker keep writing."""
    for signal in (pipeline.LeaseLostSignal, pipeline.CancelWonSignal):
        _install_harness(monkeypatch)
        _install_coverage_spy(monkeypatch, boom=signal())
        with pytest.raises(signal):
            _run_extract_phase()
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run python -m pytest tests/services/test_pipeline_extract_coverage.py -q
```
Expected: FAIL — `AttributeError: <module 'app.services.agent'> does not have the attribute 'check_extract_coverage'` is already satisfied by Task 1, so the real failures are `test_coverage_warning_lands_on_the_extract_done_write` (no call made, `calls["n"] == 0`) and its siblings.

- [ ] **Step 3: Add the orchestrator**

In `app/services/pipeline.py`, immediately after `_extract_coverage_warnings`:

```python
async def _check_extract_coverage(
    *, output_md: str, pdf_path, section: dict, provider: str, model,
    transport: str, job_id, po_id,
) -> list[str]:
    """WARN-ONLY completeness check: does the produced extract capture what the
    SOURCE lesson teaches? Returns advisory warning strings (possibly empty).

    This is the ONLY check in the stack that reads the source rather than
    trusting the extract — the judge grades every packet against the extract as
    ground truth (`phase_judge._FIDELITY_RULE`), so an under-summarizing extract
    is otherwise invisible to every downstream check.

    Fail-open on everything EXCEPT the lease/cancel control signals: those mean
    this worker no longer owns the job, and swallowing one would let an obsolete
    worker carry on writing. Slot saturation and session-limit pauses ARE
    swallowed here — parking a job whose extract already succeeded, over an
    advisory check, would cost more than the check is worth."""
    if not settings.extract_coverage_check_enabled:
        return []
    try:
        source = await _lesson_source_or_none(pdf_path, section)
        if source is None:
            logger.info(
                f"[job {job_id}] extract coverage: skipped (no usable lesson source text)"
            )
            return []
        misses = await agent.check_extract_coverage(
            summary=output_md, source_text=source,
            section_title=section.get("title") or "",
            section_number=section.get("number") or "",
            provider=provider,
            model=settings.extract_coverage_model or model,
            transport=transport,
            homework_job_id=job_id, phase_output_id=po_id,
        )
    except (LeaseLostSignal, CancelWonSignal):
        raise
    except Exception as exc:  # noqa: BLE001 — advisory: never fail/park a job
        logger.warning(
            f"[job {job_id}] extract coverage check skipped (fail-open): {exc!r}"
        )
        return []
    out = _extract_coverage_warnings(misses)
    if out:
        logger.warning(f"[job {job_id}] {out[0]}")
    return out
```

- [ ] **Step 4: Wire the call site**

Three edits in `_execute_phase`:

1. Immediately **before** `try:` (line 1423), initialise the carrier:

```python
    extract_warnings: list[str] = []
    try:
```

2. In the extract branch, immediately **after** the `if scanned_reason … else …` block sets `output_md` and **before** `artifact = artifact_from_markdown(output_md, mode="markdown_builtin")` (line 1570):

```python
            # Extract-completeness (warn-only): the only check that reads the
            # SOURCE instead of trusting the extract. Runs on the ACCEPTED
            # output — once per job, not once per failover attempt — and never
            # mutates it. The cross-job cache path returns above, so a reused
            # extract never re-pays.
            extract_warnings = await _check_extract_coverage(
                output_md=output_md, pdf_path=pdf_path, section=section,
                provider=extract_provider, model=extract_model,
                transport=extract_transport, job_id=job_id, po_id=po_id,
            )
```

3. Line 1648 — seed the shared warnings list from the carrier:

```python
    warnings: list[str] = list(extract_warnings)
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
uv run python -m pytest tests/services/test_pipeline_extract_coverage.py -q
```
Expected: PASS (12 tests).

- [ ] **Step 6: Prove the neighbours are untouched, then the whole suite**

```bash
uv run python -m pytest tests/services/test_pipeline_extract_dispatch.py \
  tests/services/test_extract_fidelity.py tests/services/test_extract_gates.py \
  tests/services/test_extract_subset.py tests/services/test_execute_phase_judge.py \
  tests/services/test_execute_phase_api_auth.py -q
uv run python -m pytest tests/ -q
```
Expected: PASS in both, with no edits to any pre-existing test file.

- [ ] **Step 7: Commit**

```bash
[ "$(git rev-parse --abbrev-ref HEAD)" = "feat/extract-coverage-check" ] || exit 1
git add app/services/pipeline.py tests/services/test_pipeline_extract_coverage.py
git commit -m "feat(extract): run the completeness check on the accepted extract

Warn-only findings ride the extract row's existing validation_warnings and the
existing fenced done-write — so preview.tsx renders them and job.tsx counts
them with no new API surface. Skips: kill switch, cache reuse, no page window,
unusable text layer. Fail-open except lease/cancel control signals."
```

---

### Task 5: Calibrate against the labeled dataset (acceptance gate — real calls)

**Files:**
- Create: `scripts/extract_coverage_calibrate.py`
- Create: `docs/research/2026-08-07-extract-coverage-calibration.md`
- Modify (only if the measurement says so): `app/config.py` — the default of `extract_coverage_check_enabled` and/or `extract_coverage_model`

**Interfaces:**
- Consumes: `agent.check_extract_coverage` (Task 1); `agent.read_page_range_text`; `content_lint._salient_tokens`; the labeled dataset `docs/research/2026-07-06-coverage-audit-data.json`.
- Produces: a measurement, and the *value* of two config defaults.

**Ground truth (verified present in `edu_copy` at plan time — extracts intact, `builtin:extract:v2`, books on disk):**

| job_id | lesson | pages | extract chars | labeled misses |
|---|---|---|---|---|
| `1e8fc0c2-86f0-4496-ab40-0aa86df1f832` | math-algebra §5 (compact FP case) | 27–29 | 440 | **0** |
| `3179e47d-5549-4036-b3b0-714ff5c1d109` | math-algebra §2 | 12–17 | 693 | **0** |
| `19f32884-504b-4d39-92e7-45f0a9c61ce5` | geometriya 1-mavzu | 5–7 | 1455 | 1 |
| `5df1dd08-37dd-4e77-ab46-c0f6ee1a6f33` | geometriya 5–6-mavzu | 16–18 | 666 | 1 |
| `08b02e07-ba94-4299-8bba-f708ad14bfa7` | kimyo §13 (hand-verified) | 51–62 | 1452 | **2** |
| `04aa4527-c860-4de3-9e75-095e3c85cbbb` | kimyo §16 | 71–73 | 704 | 2 |
| `6d3bf652-944c-4ab4-b787-3e4becb56472` | history §18 Crusades | 106–115 | 4613 | 2 |
| `7bd23497-2d5b-4b83-adc3-4d01273ecc39` | history §10 Saljuqiylar | 62–65 | 3110 | **0** |
| `1a4f4fa2-6a50-47fd-9d76-14f0f4d27803` | biology §6 Fungi | 19–24 | 2546 | **0** |

**Acceptance bars (decided before the run — do not move them afterwards):**
1. **Hard bar A:** kimyo §13 reports **both** dropped worked-example types (isotope mass-fraction → average atomic mass; composition+valence → unknown element). This is the hand-verified case; a checker that misses it does not address the audit's dominant finding.
2. **Hard bar B:** math-algebra §5 (440-char complete extract — the Gate B false-positive case) and §2 report **zero** misses. A checker that flags a complete compact extract re-creates the false-positive class Gate B was reworked to kill.
3. **Reported, not gated:** recall over all 8 labeled misses; and the item count reported on the 4 clean lessons. Ground truth is *not exhaustive*, so an item reported on a clean lesson is a **candidate** false positive — hand-check each against the source before calling it noise.
4. **Decision rule (mechanical):** run `gemini-3.5-flash-lite` (the current pinned extract model) first. If it passes both hard bars with ≤2 hand-confirmed false positives across the 4 clean lessons → ship `extract_coverage_check_enabled=True` with `extract_coverage_model=None`. Else run `gemini-3.5-flash`; if that passes → keep enabled and set `extract_coverage_model="gemini-3.5-flash"`. If neither passes → ship `extract_coverage_check_enabled=False` (code stays, default off) and record the negative result. **Warn-only either way** — the default is the only thing this decides.

- [ ] **Step 1: Write the calibration harness**

Create `scripts/extract_coverage_calibrate.py`:

```python
"""Calibrate the warn-only extract-completeness check against the labeled
coverage-audit dataset (9 lessons, 8 known extract-omissions).

Read-only against edu_copy; the only writes are the agent_usages rows the check
itself records. Bounded: 9 lessons x 1 call per model. Prints token + $ totals
for the money-rule log.

Run (from the worktree, credentials exported explicitly — the worktree's
find_dotenv walks up to /Users/macmini5/Documents/.env, NOT the repo's):

  cd /Users/macmini5/Documents/HCGA-extract-coverage
  export GEMINI_API_KEY=...            # plain key, not Vertex SA
  export CALIBRATE_DSN=postgresql://edu:edu@127.0.0.1:5432/edu_copy
  uv run python scripts/extract_coverage_calibrate.py gemini-3.5-flash-lite
"""
import asyncio
import json
import os
import sys
from pathlib import Path
from uuid import UUID

import asyncpg

import app.config  # noqa: F401 — triggers load_dotenv
from app.services import agent, content_lint

MODEL = sys.argv[1] if len(sys.argv) > 1 else "gemini-3.5-flash-lite"
DSN = os.environ.get("CALIBRATE_DSN", "postgresql://edu:edu@127.0.0.1:5432/edu_copy")
DATA = Path("docs/research/2026-07-06-coverage-audit-data.json")
BOOKS = Path("var/books")

# Guard the worktree trap: this MUST be the worktree's code, not the main
# checkout's (a -c script can silently import the other one → false all-clear).
assert "HCGA-extract-coverage" in agent.__file__, f"wrong agent module: {agent.__file__}"


def _matches(labeled: str, reported: str) -> bool:
    """Cross-language fuzzy match: share a salient (>=4-char) token."""
    a = set(content_lint._salient_tokens(labeled))
    b = set(content_lint._salient_tokens(reported))
    return bool(a & b)


async def main() -> None:
    rows = json.loads(DATA.read_text())
    conn = await asyncpg.connect(DSN)
    total_hit = total_labeled = total_reported_clean = 0
    report: list[str] = []
    try:
        for r in rows:
            job_id = r["job"]
            db = await conn.fetchrow(
                "select p.output_md, j.book_id from homework_jobs j "
                "join phase_outputs p on p.job_id = j.id and p.phase_name = 'extract' "
                "where j.id = $1", UUID(job_id))
            if db is None or not db["output_md"]:
                report.append(f"SKIP {job_id} {r['subject']} {r['sec']}: extract row missing")
                continue
            book_dir = BOOKS / str(db["book_id"])
            pdf = book_dir / "source.pdf"
            if not pdf.exists():
                report.append(f"SKIP {job_id} {r['subject']} {r['sec']}: pdf missing at {pdf}")
                continue
            ps, pe = (int(x) for x in r["pages"].split("-"))
            source = await asyncio.to_thread(
                agent.read_page_range_text, pdf, ps, pe, margin=1)

            misses = await agent.check_extract_coverage(
                summary=db["output_md"], source_text=source,
                section_title=r.get("title") or r["sec"], section_number=r["sec"],
                provider="gemini", model=MODEL, transport="api",
                homework_job_id=None, phase_output_id=None,
            )
            reported = [m.label for m in misses]
            labeled = [i["label"] for i in r["items"] if not i.get("in_extract")]
            hit = [lab for lab in labeled if any(_matches(lab, rep) for rep in reported)]
            extra = [rep for rep in reported
                     if not any(_matches(lab, rep) for lab in labeled)]

            total_labeled += len(labeled)
            total_hit += len(hit)
            if not labeled:
                total_reported_clean += len(reported)

            report.append(
                f"\n{r['subject']} {r['sec']} ({r['pages']}pp, extract {r['extract_chars']} chars)"
                f"\n  labeled misses ({len(labeled)}): " + (" | ".join(labeled) or "(none — CLEAN lesson)") +
                f"\n  reported ({len(reported)}): " + (" | ".join(reported) or "(none)") +
                f"\n  caught {len(hit)}/{len(labeled)}; unlabeled-reported {len(extra)}"
            )
    finally:
        await conn.close()

    print("\n".join(report))
    print(f"\n=== MODEL {MODEL} ===")
    print(f"recall over labeled extract-losses: {total_hit}/{total_labeled}")
    print(f"items reported on the 4 CLEAN lessons (candidate FPs, hand-check each): "
          f"{total_reported_clean}")
    print("\nTokens/$ — query agent_usages for operation='lesson.extract.coverage' "
          "in the last hour and paste the total into the calibration doc.")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Run it on the pinned extract model**

```bash
cd /Users/macmini5/Documents/HCGA-extract-coverage
export GEMINI_API_KEY=<plain key>
uv run python scripts/extract_coverage_calibrate.py gemini-3.5-flash-lite 2>&1 | tee /tmp/calib-lite.txt
```
Expected: a per-lesson table plus recall and clean-lesson counts. **Do not proceed on a crash** — fix the harness first (a harness bug that silently skips lessons would fake the gate).

- [ ] **Step 3: Evaluate the hard bars; run the stronger model only if needed**

Check hard bar A (kimyo §13 → both worked-example types) and hard bar B (math §5 and §2 → zero). If either fails:

```bash
uv run python scripts/extract_coverage_calibrate.py gemini-3.5-flash 2>&1 | tee /tmp/calib-flash.txt
```

- [ ] **Step 4: Pull the real cost**

```bash
psql -U macmini5 -d edu_copy -Atc "
select model_name, count(*), sum(prompt_tokens), sum(output_tokens)
from agent_usages
where operation='lesson.extract.coverage' and started_at > now() - interval '2 hours'
group by model_name;"
```
Record the numbers; convert with `app/services/pricing.py`'s map for the `$` line.

- [ ] **Step 5: Write the calibration doc**

Create `docs/research/2026-08-07-extract-coverage-calibration.md` containing: the method (one paragraph), the per-lesson table from the run, recall, the hand-check verdict on every item reported for a clean lesson (real miss vs false positive — say which, with the source evidence), the money-rule line (calls / tokens / `$`), and the resulting default decision under the Step-0 rule. State explicitly that the labels came from `gemini-3.1-pro` plus one hand-verified case, so this measures agreement, not truth.

- [ ] **Step 6: Apply the decided defaults**

Edit `app/config.py` per the decision rule (only if the measurement calls for it), then:

```bash
uv run python -m pytest tests/services/test_extract_coverage.py -q
```
Expected: PASS — `test_config_defaults_are_warn_only_and_inherit_the_extract_model` asserts only *types* and the `None` model default, so a flipped enable-default keeps it green. If the decision sets `extract_coverage_model`, update that one assertion to the decided value in the same commit.

- [ ] **Step 7: Commit**

```bash
[ "$(git rev-parse --abbrev-ref HEAD)" = "feat/extract-coverage-check" ] || exit 1
git add scripts/extract_coverage_calibrate.py docs/research/2026-08-07-extract-coverage-calibration.md app/config.py tests/services/test_extract_coverage.py
git commit -m "test(extract): calibrate the completeness check on the labeled dataset

9 audited lessons, 8 known extract-omissions, both hard bars evaluated
(kimyo §13 must fire, compact math §5/§2 must not). Sets the shipped defaults
from the measurement rather than from taste."
```

---

### Task 6: Live acceptance gate — one real generation over `transport=api`

**Files:** none (verification only; findings go into Task 7's worklog)

**Why:** CLAUDE.md's acceptance gate — anything that affects generation is proven by a real generation smoke on the transport production uses. Task 5 proves the *checker*; this proves the *wiring* in a real job: the extract still completes, the warning lands on the row, and nothing regressed.

- [ ] **Step 1: Pick a lesson and launch one job in-process**

Choose one **long, fact-dense** lesson (the class the audit shows leaks most) from a book already on disk, and one **short** lesson as the negative control. Launch a single job each over `transport=api` with `extract_coverage_check_enabled=True`. Use the existing single-lesson launch path (`scripts/smoke_per_role.py` is the closest working template for an in-process bounded call — read it before adapting; do not mass-generate).

- [ ] **Step 2: Verify the extract row**

```bash
psql -U macmini5 -d edu_copy -Atc "
select p.job_id, p.status, p.validation_warnings
from phase_outputs p where p.phase_name='extract'
order by p.completed_at desc limit 5;"
```
Expected: both jobs `done`; the coverage warning (if any) present as an `extract_coverage:` string; **no job failed and no job parked**.

- [ ] **Step 3: Verify the check billed correctly and once**

```bash
psql -U macmini5 -d edu_copy -Atc "
select operation, auth_mode, model_name, count(*), sum(prompt_tokens), sum(output_tokens)
from agent_usages where started_at > now() - interval '1 hour'
group by 1,2,3 order by 1;"
```
Expected: exactly **one** `lesson.extract.coverage` row per fresh extract, `auth_mode='api'`, on the extract role's model (or the calibrated override) — **not** one per phase and **not** one per failover attempt.

- [ ] **Step 4: Verify the FE surfaces it**

Open the job in the SPA (`/preview`), confirm the extract phase shows the warning text, and that `job.tsx`'s warning count includes it. Screenshot or quote the rendered string in the worklog.

- [ ] **Step 5: Re-run the same lesson to prove the cache path is free**

Re-launch the same section; confirm no new `lesson.extract.coverage` row appears (the reused extract must not re-pay).

- [ ] **Step 6: Record the money-rule line**

Total calls / tokens / `$` for the smoke, from the query in Step 3. This goes in the worklog.

---

### Task 7: Finish — docs, backlog, live-reference de-staling

**Files:**
- Modify: `docs/memory/MASTER_MEMORY.md` (new worklog entry), `docs/memory/INDEX.md` (row), `docs/memory/WISHLIST.md` (deferred regen), `docs/memory/ROADMAP.md` (coverage-audit residue status), `docs/HOW_IT_WORKS.md`, `docs/CODE_MAP.md`
- Move: `docs/superpowers/plans/2026-08-07-extract-coverage-check.md` → `docs/superpowers/plans/shipped/`

- [ ] **Step 1: Check the base hasn't moved, rebase if it has**

```bash
cd /Users/macmini5/Documents/HCGA-extract-coverage
git fetch origin
git log HEAD..origin/Nggaev-v2 --oneline
```
If non-empty: `git rebase origin/Nggaev-v2`, resolve conflicts, then **re-run the full suite** before continuing. Re-check the worklog number and INDEX tail after rebasing — both go stale mid-lane (the #92/#93 lesson).

- [ ] **Step 2: Write the worklog entry**

Append to `docs/memory/MASTER_MEMORY.md` (next free number — read the INDEX tail, don't guess): what shipped, the calibration numbers, the live-gate result and `$`, the three skip conditions, and — explicitly — that **regen-on-omission is deferred** with the measurement that would justify it. Add the matching row to `docs/memory/INDEX.md`.

- [ ] **Step 3: File the deferred follow-up**

Add one line to `docs/memory/WISHLIST.md`:

```markdown
- `extract-coverage-regen-1`: the warn-only completeness check (worklog TBD) records dropped items but never acts — once live precision is measured from `agent_usages` (`operation='lesson.extract.coverage'`, verdict in `raw_envelope`), decide whether a confirmed CENTRAL omission should drive one `summarize_lesson(correction_hint=…)` regen, exactly as CQ-D's fidelity guard does.
```

Update the `docs/memory/ROADMAP.md` note on the coverage-audit residue: Finding 1 (extract-loss) now has a detector; Finding 3 (phase-loss) was already covered by `lint:coverage_thin`.

- [ ] **Step 4: De-stale the live-reference docs**

- `docs/HOW_IT_WORKS.md` — in the extract section, add the check next to the CQ-D fidelity guard, naming it as the only source-reading check and stating it is advisory.
- `docs/CODE_MAP.md` — `agent.check_extract_coverage`, `pipeline._lesson_source_or_none` / `_extract_coverage_warnings` / `_check_extract_coverage`, `scripts/extract_coverage_calibrate.py`, and the three settings.

- [ ] **Step 5: Move the plan to shipped**

```bash
git mv docs/superpowers/plans/2026-08-07-extract-coverage-check.md docs/superpowers/plans/shipped/
```

- [ ] **Step 6: Full suite + commit**

```bash
uv run python -m pytest tests/ -q
[ "$(git rev-parse --abbrev-ref HEAD)" = "feat/extract-coverage-check" ] || exit 1
git add docs/memory/MASTER_MEMORY.md docs/memory/INDEX.md docs/memory/WISHLIST.md docs/memory/ROADMAP.md docs/HOW_IT_WORKS.md docs/CODE_MAP.md docs/superpowers/plans/
git commit -m "docs: worklog + de-stale reference docs for the extract-completeness check"
git show --stat HEAD          # verify the commit CONTENTS match the message
```

- [ ] **Step 7: Hand off**

Invoke `superpowers:finishing-a-development-branch`. Default is push the branch and open a PR against `Nggaev-v2` — **the user decides**; never self-merge (gatekeeping is GK2's).

---

## Self-review

**Spec coverage** — every design decision maps to a task: cheap-LLM detector → Task 1; strict lesson-scoped source → Task 2; aggregated central-first warning → Task 3; inline placement, skip conditions, `validation_warnings` + phase-console surfacing → Task 4; calibration against the labeled dataset and the default-on/off decision → Task 5; real-generation acceptance and per-fresh-extract billing → Task 6; worklog, deferred-regen backlog item, live-doc de-staling → Task 7.

**Placeholder scan** — the only deliberately unresolved values are the worklog number (must be read from the INDEX tail at finish time, not guessed — a known staleness trap) and the calibration outcome, which Task 5 resolves by a mechanical decision rule stated before the run.

**Type consistency** — `ExtractCoverageMiss(label, central)` / `ExtractCoverageVerdict(missing)` are defined in Task 1 and used with those exact field names in Tasks 3, 4, 5. `check_extract_coverage` is called with the same keyword set (`summary`, `source_text`, `section_title`, `section_number`, `provider`, `model`, `transport`, `homework_job_id`, `phase_output_id`) in Tasks 4 and 5 as defined in Task 1. `_lesson_source_or_none` / `_extract_coverage_warnings` / `_check_extract_coverage` keep one spelling throughout.

**Deliberate non-goals** — no regen, no gating, no `lesson_context` shape change, no judge change, no migration, no new API endpoint, no batch/book-level rollup (that overlaps the unbuilt `judge-failure-rollup-1`).
