# Extract-completeness check (warn-only) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make extract under-summarization *visible* — one bounded, warn-only model call per fresh extract that compares the produced summary against the lesson's own source pages and records the core items the extract dropped.

**Architecture:** Mirror CQ-D's fidelity guard, inverted. Fidelity asks *"did the extract invent something?"* (`extract_fidelity_candidates` → `verify_extract_fidelity` → regen-once); this asks *"did the extract drop something?"* (`_lesson_source_or_none` → `agent.check_extract_coverage` → warn-only). It runs inline in `pipeline._execute_phase`'s extract branch after the accepted output is chosen, appends to the existing `warnings` list, and rides the existing fenced done-write. No new write path, no migration, no change to `lesson_context`'s shape.

**Tech Stack:** Python ≥3.13 (`pyproject.toml:5`) · FastAPI · SQLAlchemy async · pydantic v2 structured output via `agent.run_phase(schema=…)` · `transport=api` over the plain Gemini API key · pytest / pytest-asyncio.

---

## Approach & key decisions

- **Chosen shape: a cheap LLM completeness check against the lesson's own source pages, warn-only.** One `run_phase(schema=…)` call per *fresh* extract, input = the lesson page window (±1) + the produced summary, on the extract role's pinned cheap model. It is the first check in the stack that reads the **source** instead of trusting the extract, which is precisely the invisibility the coverage audit named (`docs/research/2026-07-06-coverage-audit.md`).
- **Rejected — deterministic marker scan only** (`N-misol` / `masala` / `Пример` / `Задача` vs the contract's `## Worked-example types`): free, but blind to concept/fact omissions. Verified against the labeled data: of the 8 ground-truth extract-losses, only 3 are worked-example types — a marker scan structurally cannot see the other 5 (`Noqavariq ko'pburchak`, `Ion zaryadi miqdori qoidasi`, the two Crusades facts, …).
- **Rejected — producer-side self-critique** (second pass inside the extract, no check): cannot be staged warn-only, cannot be calibrated against the labeled dataset, and yields no measurement for the deferred regen decision.
- **Rejected — making the judge source-aware**: the judge runs per phase (×11), so shipping the source window into it multiplies tokens by an order of magnitude, and it re-opens `_FIDELITY_RULE`'s hard-won contradiction-vs-absence calibration (worklog 0159 / R25).
- **Warn-only in v1, regen deliberately deferred.** Standing rule: never gate on a new check's first version (the validate_toc / solver lesson). The regen-once path is already shaped by CQ-D — `agent.summarize_lesson(correction_hint=…)` — and drops in at the same call site once live precision is measured.
- **Where that measurement actually lives (corrected at plan review):** `run_phase` records `extra_envelope={"phase_name", "difficulty", "schema", "attempt"}` only (`agent.py:1175-1192`), and the gemini api envelope is token counts (`api_transport.py:165-171`) — **the verdict never reaches `agent_usages.raw_envelope`.** So the deferred-regen decision is measured from `phase_outputs.validation_warnings` (join `agent_usages.phase_output_id` for cost), accepting that it is capped at `extract_coverage_max_items` and truncated to 80 chars per label. `agent_usages` still answers *how often* and *how much*; `validation_warnings` answers *what was flagged*. Do not repeat the raw_envelope claim anywhere.
- **Load-bearing facts, each verified against real code at `2ebab53`:**
  - `pipeline.py:1648` initialises `warnings: list[str] = []` and `pipeline.py:1651` gates the judge/lint block behind `if phase_name != "extract"` — the done-write at `pipeline.py:1865-1882` (fenced with `claim_token`) is **common to both branches**, so the extract branch can contribute warnings with no signature change and no new write.
  - The cross-job extract cache **returns early** at `pipeline.py:1441-1472`, before the check's call site — a reused extract costs nothing, because the producing job already paid.
  - `agent.read_page_range_text(pdf, ps, pe, margin=1)` + `agent.validate_extract_text` already exist and are exactly what `_verify_source_for_section` (`pipeline.py:1290`) uses. The new helper deliberately **drops that function's whole-book fallback**: a whole-book "source" would make the checker enumerate *other* lessons' items and report them as omissions.
  - **The extract row is invisible in the SPA today** (found at plan review): `preview.tsx:140`, `job.tsx:292` and `job.tsx:549-551` all filter `phase_name !== "extract"` before rendering or counting `validation_warnings`. So the already-shipped `lint:coverage_thin` has been landing in a column nobody can see. Task 4b fixes that for both warnings — without it, the surfacing decision this plan was built on is not actually delivered.
  - `lesson_context`'s shape does **not** change (the v3 enumerated contract shipped in worklog 0119) — so the coverage audit's heaviest composition constraint ("a shape change touches every content phase + judge + solver") is satisfied by construction. The regression surface is the extract phase only.
  - The 9 audited lessons are still live in `edu_copy` with their exact labeled extracts (`builtin:extract:v2`, char counts 440/693/1455/666/1452/704/4613/3110/2546 — byte-for-byte the artifacts the labels describe) and their books are on disk under `var/books/`. The acceptance gate is realizable today.
- **Known limit, stated up front:** the checker is itself a cheap-tier model reading a page window. It narrows the blind spot; it does not close it. And the labels it is calibrated against came from `gemini-3.1-pro` plus one hand-verified case — calibration measures agreement with a strong model, not with truth.

---

## Global Constraints

- **Warn-only. Non-negotiable.** This check must never fail a job, never park a job, never mutate the extract, and never gate a regen. Every failure path is fail-open.
- **Transport:** all real calls run `transport=api` (the cli path is retired from operational use — CLAUDE.md standing decision 2026-07-01). Never benchmark or verify against cli.
- **Money rule:** no mass generation. Calibration is 9 lessons × up to 2 models = ≤18 *logical* calls (≤36 spawns worst case — `run_phase` schema mode retries once on a validation failure, `agent.py:996`); the live gate is a **long lesson + a short negative control, plus one re-launch to prove the cache path is free** (three jobs, named explicitly in Task 6). Every task that spends money reports tokens and `$`.
- **Gemini-only policy:** the check reuses the extract role's *provider*; only the *model* is overridable (`extract_coverage_model`), and only within that provider.
- **Composition:** the CQ-D fidelity tests (`tests/services/test_extract_fidelity.py`) and the extract-dispatch tests (`tests/services/test_pipeline_extract_dispatch.py`) must stay green **unmodified**. If a task needs to edit either file, stop — the design is wrong.
- **Staging discipline:** stage only the files each task lists. Never `git add -A` — other sessions commit to this branch's base.
- **Branch:** all work happens in the worktree `/Users/macmini5/Documents/HCGA-extract-coverage` on `feat/extract-coverage-check` (cut from `origin/Nggaev-v2` @ `2ebab53`). Verify with a hard guard before every commit:
  ```bash
  [ "$(git rev-parse --abbrev-ref HEAD)" = "feat/extract-coverage-check" ] || exit 1
  ```
- **Worktree .env trap (verified, and worse than it looks):** the worktree has **no `.env`**, so `config.py`'s `load_dotenv` walks up to `/Users/macmini5/Documents/.env`, whose `DATABASE_URL` is `postgresql+asyncpg://edu:edu@192.168.1.80:5432/edu_copy` — a **remote** host — while the repo's own `.env` says `localhost`. So an app-side write (`agent_usages`, `phase_outputs`) from this worktree lands on a *different server* than a bare `psql -d edu_copy` reads. Every task that makes real calls must (a) assert its module path, and (b) **export `DATABASE_URL` explicitly** and run its verification SQL against that same DSN. Otherwise the money-rule queries return empty and fake a "nothing billed" result.

---

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `app/config.py` (modify, extract block ~line 227) | Three knobs: `extract_coverage_check_enabled`, `extract_coverage_model`, `extract_coverage_max_items`. | 1 |
| `app/services/agent.py` (modify, beside `verify_extract_fidelity` ~line 1400) | `ExtractCoverageMiss` / `ExtractCoverageVerdict` models, `_CHECK_COVERAGE_PROMPT`, `check_extract_coverage(...)` — the model boundary, fail-open. | 1 |
| `app/services/pipeline.py` (modify) | `_lesson_source_or_none` (strict lesson-scoped source, no whole-book fallback) · `_extract_coverage_warnings` (pure formatter) · `_check_extract_coverage` (orchestration + skips) · the call site in the extract branch. | 2, 3, 4 |
| `tests/services/test_extract_coverage.py` (create) | Agent-boundary + config + pure-formatter tests. | 1, 3 |
| `tests/services/test_pipeline_extract_coverage.py` (create) | Source-helper tests + wiring tests on the real `_execute_phase` via the DB-free harness. | 2, 4 |
| `web/src/routes/preview.tsx`, `web/src/routes/job.tsx` (modify) | Surface extract-row `validation_warnings` — today both filter the extract phase out, so these warnings render nowhere. | 4b |
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
    # The check runs outside the failover timeout guard — it must carry a bound
    # of its own, and one far tighter than per_attempt_timeout_seconds (600s),
    # because extract is the sequential head of the whole job.
    assert 0 < settings.extract_coverage_timeout_seconds < settings.per_attempt_timeout_seconds


def test_shipped_default_is_independent_of_the_test_environment():
    """The suite forces the check OFF via env (tests/conftest.py) so no unit test
    can reach a real spawn — so assert the SHIPPED default on the class, not on
    the env-resolved instance, or this test would silently stop meaning anything."""
    from app.config import Settings

    assert Settings.model_fields["extract_coverage_check_enabled"].default is True


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
    # Extract-completeness check (warn-only, plan 2026-08-07): one bounded call
    # per FRESH extract comparing the summary against the lesson's own source
    # pages. Advisory only — it never fails a job and never regens.
    # NOTE: this default is provisional until Task 5's calibration lands; that
    # task sets its final value from the measurement.
    extract_coverage_check_enabled: bool = True
    # Advisory work must not stall the sequential head phase: the check runs
    # OUTSIDE _run_with_failover's per_attempt_timeout_seconds (600s) guard, so
    # it carries its own, much tighter bound.
    extract_coverage_timeout_seconds: int = 120
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
Expected: PASS (7 tests).

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
    fails Gate A (scanned / garbled text layer) is likewise unusable.

    Note the vision-extract path is *usually*, not always, excluded by this:
    the vision route triggers on WHOLE-BOOK Gate A / density (pipeline.py:1502),
    while this re-applies Gate A to the lesson WINDOW. A mixed book whose window
    does carry a real text layer will still be checked — which is correct, since
    the window is then genuinely readable."""
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
Expected: FAIL — `AttributeError: module 'app.services.pipeline' has no attribute '_extract_coverage_warnings'` (the four new formatter tests fail; the seven from Task 1 still pass).

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
Expected: PASS (11 tests — 7 from Task 1, 4 formatter).

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
    # The suite defaults the check OFF (tests/conftest.py) so no OTHER test can
    # reach a real spawn through the new call site. These tests are the ones
    # that exercise it, so they turn it back on explicitly.
    monkeypatch.setattr(settings, "extract_coverage_check_enabled", True)
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
    # Order matters: _install_harness ENABLES the check (the suite defaults it
    # off), so the disable must come AFTER it or the harness wins and this test
    # fails against a correct implementation.
    writes = _install_harness(monkeypatch)
    monkeypatch.setattr(settings, "extract_coverage_check_enabled", False)
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


def test_slow_check_is_bounded_and_fails_open(monkeypatch):
    """extract is the sequential head of the job — a hung advisory call must not
    stall it. The check sits outside _run_with_failover's wait_for, so it needs
    its own bound.

    The elapsed assertion is what gives this test teeth: without asyncio.wait_for
    the phase still completes and still writes no warnings, so asserting only
    those two things would pass against an implementation with NO timeout at all
    — it would merely take 30 seconds."""
    import time

    monkeypatch.setattr(settings, "extract_coverage_timeout_seconds", 0.01)
    writes = _install_harness(monkeypatch)
    calls = {"n": 0}

    async def _slow(**kwargs):
        calls["n"] += 1
        await asyncio.sleep(30)
        return []

    monkeypatch.setattr(pipeline.agent, "check_extract_coverage", _slow)
    t0 = time.monotonic()
    out_md, *_ = _run_extract_phase()
    elapsed = time.monotonic() - t0

    assert calls["n"] == 1               # RED before the wiring exists
    assert elapsed < 5                   # RED without the timeout (would be ~30s)
    assert out_md                        # the extract itself still completed
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
        # Bounded independently: this call sits OUTSIDE _run_with_failover's
        # asyncio.wait_for (pipeline.py:1013), so on a cli-transport extract
        # nothing else would stop a hung subprocess from stalling the job's
        # sequential head phase.
        misses = await asyncio.wait_for(
            agent.check_extract_coverage(
                summary=output_md, source_text=source,
                section_title=section.get("title") or "",
                section_number=section.get("number") or "",
                provider=provider,
                model=settings.extract_coverage_model or model,
                transport=transport,
                homework_job_id=job_id, phase_output_id=po_id,
            ),
            timeout=settings.extract_coverage_timeout_seconds,
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

4. **`tests/conftest.py` — force the check OFF for the whole suite.** Without this, pre-existing tests reach a REAL spawn through the new call site: `test_pipeline_extract_dispatch.py` patches `read_page_range_text` to return Gate-A-passing text (`:120-124`, `_CLEAN_TEXT` at `:62-67`) but cannot patch `check_extract_coverage` (it does not exist at `2ebab53`), and `tests/conftest.py` has **no spawn guard** — only env sentinels and an events-bus loopback. At least three of its tests — `test_normal_book_unchanged`, `test_oversize_book_subsets_text` and `test_sparse_scanned_routes_to_vision` (whose `"h" * 17000` window also clears Gate A) — would each fire a real `gemini` CLI subprocess (installed on this host), then fail open and stay green: a money-rule violation that Step 6's "PASS" would actively mask. The sentinel below fixes all of them identically, so treat the list as "at least these", not an exhaustive audit.

Add beside the existing sentinels (~line 33), *before* any app import:

```python
# The extract-completeness check makes a REAL model call. Default it OFF for the
# suite so no test can reach a spawn through pipeline's extract branch; the tests
# that exercise it re-enable it explicitly (test_pipeline_extract_coverage.py).
os.environ.setdefault("EXTRACT_COVERAGE_CHECK_ENABLED", "false")
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
uv run python -m pytest tests/services/test_pipeline_extract_coverage.py -q
```
Expected: PASS (13 tests — 4 source-helper from Task 2, 9 wiring).

- [ ] **Step 6: Prove the neighbours are untouched, then the whole suite**

```bash
uv run python -m pytest tests/services/test_pipeline_extract_dispatch.py \
  tests/services/test_extract_fidelity.py tests/services/test_extract_gates.py \
  tests/services/test_extract_subset.py tests/services/test_execute_phase_judge.py \
  tests/services/test_execute_phase_api_auth.py -q
uv run python -m pytest tests/ -q
```
Expected: PASS in both, with no edits to any pre-existing *behavior* test (the one-line `conftest.py` sentinel above is the only test-infra change).

**Green is not sufficient here — prove no spawn happens.** These tests would also pass while silently shelling out to the `gemini` CLI (fail-open swallows the failure). Verify directly:

```bash
uv run python -m pytest tests/services/test_pipeline_extract_dispatch.py -q -p no:randomly --durations=5
```
Expected: sub-second durations. A multi-second `test_normal_book_unchanged` means the sentinel is not taking effect — stop and fix it before continuing. For certainty, temporarily add `raise AssertionError("spawn leaked")` at the top of `agent._spawn`, re-run the file, confirm it still passes, then revert.

- [ ] **Step 7: Commit**

```bash
[ "$(git rev-parse --abbrev-ref HEAD)" = "feat/extract-coverage-check" ] || exit 1
git add app/services/pipeline.py tests/services/test_pipeline_extract_coverage.py tests/conftest.py
git commit -m "feat(extract): run the completeness check on the accepted extract

Warn-only findings ride the extract row's existing validation_warnings and the
existing fenced done-write — no new API surface. (The SPA filters the extract
phase out of both warning surfaces today; Task 4b makes them visible.) Skips:
kill switch, cache reuse, no page window, unusable text layer. Bounded by its
own timeout. Fail-open except lease/cancel control signals."
```

---

### Task 4b: Surface extract-row warnings in the SPA

**Files:**
- Create: `web/src/lib/phase-warnings.ts`
- Create: `web/src/lib/phase-warnings.test.ts`
- Modify: `web/src/routes/preview.tsx` (render a source-checks strip in `PreviewPage`, ~line 384)
- Modify: `web/src/routes/job.tsx:545-556` (warning count)

**Interfaces:**
- Produces: `sourceCheckWarnings(phases): string[]` and `totalWarningCount(phases): number` from `web/src/lib/phase-warnings.ts`.

**Why this task exists:** found at plan review. `preview.tsx:140`, `job.tsx:292` and `job.tsx:549-551` all filter `phase_name !== "extract"` before rendering or counting `validation_warnings`. Extract-row warnings therefore render **nowhere** — which also means the already-shipped `lint:coverage_thin` has been invisible since worklog 0119. Without this task the plan's surfacing decision is not delivered.

**Deliberately NOT done:** the extract phase stays out of the phase pager and the phase timeline. It is internal scaffolding, not student content — only its *warnings* surface, in one compact strip.

- [ ] **Step 1: Write the failing test**

Create `web/src/lib/phase-warnings.test.ts` (repo idiom: `node:assert`, run by `npm test`):

```ts
import assert from "node:assert";
import { sourceCheckWarnings, totalWarningCount } from "./phase-warnings";

const phases = [
  { phase_name: "extract", status: "done", validation_warnings: ["extract_coverage: 2 item(s) …", "lint:coverage_thin: …"] },
  { phase_name: "flashcards", status: "done", validation_warnings: ["lint:mixed_script: …"] },
  { phase_name: "reflection", status: "done", validation_warnings: null },
  { phase_name: "boss-arena", status: "running", validation_warnings: ["ignored — not done"] },
] as any;

// source-side checks live ONLY on the extract row; the pager hides that row.
assert.deepStrictEqual(sourceCheckWarnings(phases), [
  "extract_coverage: 2 item(s) …",
  "lint:coverage_thin: …",
]);

// the job header count must include them — that is the whole point.
assert.strictEqual(totalWarningCount(phases), 3);

// empty / missing cases
assert.deepStrictEqual(sourceCheckWarnings([] as any), []);
assert.strictEqual(totalWarningCount([] as any), 0);
assert.deepStrictEqual(
  sourceCheckWarnings([{ phase_name: "extract", status: "done", validation_warnings: null }] as any),
  [],
);
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd /Users/macmini5/Documents/HCGA-extract-coverage/web
npm test
```
Expected: FAIL — `Cannot find module './phase-warnings'`.

- [ ] **Step 3: Write the pure module**

Create `web/src/lib/phase-warnings.ts`:

```ts
import type { PhaseOut } from "./types";

/** Warnings from the `extract` row — the ONLY place source-side checks land
 *  (`extract_coverage:` from the completeness check, `lint:coverage_thin` from
 *  the packet-vs-contract lint). The phase pager hides the extract row itself,
 *  so without this they render nowhere. */
export function sourceCheckWarnings(phases: PhaseOut[]): string[] {
  return (phases ?? [])
    .filter((p) => p.phase_name === "extract" && p.status === "done")
    .flatMap((p) => p.validation_warnings ?? []);
}

/** Every done phase's warnings, extract included. */
export function totalWarningCount(phases: PhaseOut[]): number {
  return (phases ?? [])
    .filter((p) => p.status === "done")
    .reduce((n, p) => n + (p.validation_warnings?.length ?? 0), 0);
}
```

(`PhaseOut` is the real export — `types.ts:200`, with `validation_warnings: string[] | null` at `:210` and `Job.phases: PhaseOut[]` at `:223`. Note `npm test` would NOT catch a wrong type name, since `tsx` erases type-only imports — only Step 6's `tsc` would.)

- [ ] **Step 4: Run the test to verify it passes**

```bash
npm test
```
Expected: PASS.

- [ ] **Step 5: Wire both routes**

In `web/src/routes/preview.tsx`, add the import and render the strip inside `PreviewPage`, immediately **before** `<PhasesPreview job={job} />` (~line 384) — outside `PhasesPreview` so it also shows when no content phase has finished:

```tsx
import { sourceCheckWarnings } from "../lib/phase-warnings";

// … inside PreviewPage, just above <PhasesPreview job={job} />:
// …computed once, near the other derived values in PreviewPage:
const sourceWarnings = sourceCheckWarnings(job.phases);

// …then in the JSX, immediately above <PhasesPreview job={job} />:
{sourceWarnings.length > 0 && (
  <section className="mt-6 rounded-2xl border border-amber-400/20 bg-amber-400/[0.06] px-5 py-3 backdrop-blur-xl">
    <h2 className="text-xs font-medium uppercase tracking-wide text-amber-200/70">
      Source checks
    </h2>
    <ul className="mt-2 list-disc pl-4 text-xs text-amber-200/80">
      {sourceWarnings.map((w) => (
        <li key={w}>{w}</li>
      ))}
    </ul>
  </section>
)}
```

In `web/src/routes/job.tsx:545-556`, keep the phase count content-only but count warnings across every done phase:

```tsx
  const stats = useMemo(() => {
    const all = job?.phases ?? [];
    const done = all.filter((p) => p.phase_name !== "extract" && p.status === "done");
    // Warnings come from EVERY done phase, extract included — source-side
    // checks (extract_coverage:, lint:coverage_thin) live only on that row.
    const warnings = totalWarningCount(all);
    return [
      { label: "phases", value: done.length },
      { label: "warnings", value: warnings },
    ].filter((s) => s.value > 0 || s.label === "phases");
  }, [job]);
```

…with `import { totalWarningCount } from "../lib/phase-warnings";` added at the top.

- [ ] **Step 6: Typecheck and build**

```bash
cd /Users/macmini5/Documents/HCGA-extract-coverage/web
npx tsc -p tsconfig.app.json --noEmit
npm run build
```
Expected: both clean. (Do **not** run `biome check` as a gate — the repo is known repo-wide dirty on it, WISHLIST `fe-biome-clean-1`.)

- [ ] **Step 7: Commit**

```bash
[ "$(git rev-parse --abbrev-ref HEAD)" = "feat/extract-coverage-check" ] || exit 1
git add web/src/lib/phase-warnings.ts web/src/lib/phase-warnings.test.ts web/src/routes/preview.tsx web/src/routes/job.tsx
git commit -m "fix(web): surface extract-row validation_warnings

Both preview and the job header filtered the extract phase out before reading
validation_warnings, so every source-side check landed in a column nobody could
see — including lint:coverage_thin, invisible since worklog 0119. The extract
row stays out of the pager and timeline; only its warnings surface."
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

If this aborts on the success-count check, suspect the app-side DB write path as
well as the calls themselves: _record_usage is best-effort and SWALLOWS write
failures (agent.py:814-815), so a wrong DATABASE_URL makes healthy calls look
like missing ones. That is the fail-safe direction — it never turns a broken run
into a passing score — but it is the first thing to check.

MUST be run as a MODULE (-m), not by path: this repo has no [build-system], so
`app` is never installed into the venv and only resolves from the repo root —
running `python scripts/<name>.py` puts scripts/ on sys.path[0] and dies with
`ModuleNotFoundError: No module named 'app'` (verified empirically). Same idiom
as scripts/cqd_extract_guards_smoke.py.

Run (from the worktree, env exported EXPLICITLY — the worktree has no .env, so
config.py walks up to /Users/macmini5/Documents/.env, whose DATABASE_URL points
at a REMOTE host. Both DSNs below must name the same server, or the usage rows
this script writes land somewhere the cost query never looks):

  cd /Users/macmini5/Documents/HCGA-extract-coverage
  export GEMINI_API_KEY=...            # plain key, not Vertex SA
  export DATABASE_URL=postgresql+asyncpg://edu:edu@127.0.0.1:5432/edu_copy
  export CALIBRATE_DSN=postgresql://edu:edu@127.0.0.1:5432/edu_copy
  export VAR_DIR=/Users/macmini5/Documents/Homework-Content-Generation-Automation/var
  uv run python -m scripts.extract_coverage_calibrate gemini-3.5-flash-lite
"""
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import asyncpg

import app.config  # noqa: F401 — triggers load_dotenv
from app.services import agent, content_lint

MODEL = sys.argv[1] if len(sys.argv) > 1 else "gemini-3.5-flash-lite"
DSN = os.environ.get("CALIBRATE_DSN", "postgresql://edu:edu@127.0.0.1:5432/edu_copy")
DATA = Path("docs/research/2026-07-06-coverage-audit-data.json")

# The worktree has NO var/ — the host's book store lives in the main checkout.
# Same trap the CQ-D smoke already had to code around (scripts/
# cqd_extract_guards_smoke.py:33-41); resolve the first root that exists.
_BOOK_ROOTS = [
    Path(os.environ["VAR_DIR"]) / "books" if os.environ.get("VAR_DIR") else None,
    Path.cwd() / "var" / "books",
    Path("/Users/macmini5/Documents/Homework-Content-Generation-Automation/var/books"),
]
BOOKS = next((r for r in _BOOK_ROOTS if r and r.is_dir()), None)
if BOOKS is None:
    raise SystemExit("no book store found — set VAR_DIR to the checkout holding var/books")

# Guard the worktree trap: this MUST be the worktree's code, not the main
# checkout's (a -c script can silently import the other one → false all-clear).
assert "HCGA-extract-coverage" in agent.__file__, f"wrong agent module: {agent.__file__}"


def _matches(labeled: str, reported: str) -> bool:
    """Cross-language fuzzy match: a salient (>=4-char) token of one appears
    INSIDE the other. Substring, not exact-set intersection — Uzbek is
    agglutinative ('izotop' / 'izotoplar' / 'izotoplarning' are three distinct
    exact tokens), so exact matching would score a genuinely-caught omission as
    a miss and could fail hard bar A for matcher reasons, not checker reasons.
    Same containment idiom content_lint.lint_coverage itself uses."""
    a, b = content_lint._norm(labeled), content_lint._norm(reported)
    return (any(t in b for t in content_lint._salient_tokens(labeled))
            or any(t in a for t in content_lint._salient_tokens(reported)))


async def main() -> None:
    rows = json.loads(DATA.read_text())
    conn = await asyncpg.connect(DSN)
    total_hit = total_labeled = total_reported_clean = 0
    evaluated = 0
    report: list[str] = []
    # started_at is stamped by run_phase in THIS process (agent.py:998), so this
    # is the same clock — no skew, and no slack. Slack would only widen the
    # window to the PREVIOUS run's rows: Step 3 runs a second model right after
    # the first, so a backward window would count those and abort a healthy run.
    t0 = datetime.now(timezone.utc)
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

            evaluated += 1
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
        # check_extract_coverage is fail-open BY CONTRACT: an auth/429/limiter
        # failure returns [] and is indistinguishable from "clean extract". So a
        # broken environment would score zero misses everywhere and PASS hard
        # bar B. Count the successful calls the check actually recorded.
        # model_name too, so a back-to-back second-model run can never count the
        # first run's rows even if the clocks were to collide.
        n_success = await conn.fetchval(
            "select count(*) from agent_usages "
            "where operation = 'lesson.extract.coverage' "
            "and success and started_at >= $1 and model_name = $2", t0, MODEL)
        # Informational ONLY — never an abort condition. run_phase's schema mode
        # records a success=False row under the SAME operation for a first
        # attempt that fails Pydantic validation, then retries and records a
        # success row (agent.py:1137-1156). Aborting on this would fail a
        # perfectly healthy run on one JSON flake — and it buys nothing: a call
        # that ULTIMATELY failed writes no success row, so n_success already
        # catches it.
        n_failed = await conn.fetchval(
            "select count(*) from agent_usages "
            "where operation = 'lesson.extract.coverage' "
            "and not success and started_at >= $1 and model_name = $2", t0, MODEL)
    finally:
        await conn.close()

    print("\n".join(report))
    if n_failed:
        print(f"\nnote: {n_failed} failed check attempt(s) recorded — expected "
              "occasionally in schema mode (first attempt retried).")
    if n_success != evaluated:
        raise SystemExit(
            f"\nABORT: expected {evaluated} successful check call(s), found "
            f"{n_success}. Fewer means fail-open hid a broken call (which then "
            "scores as a clean extract); more means this count caught another "
            "run's rows. Either way the score is not trustworthy — fix and re-run."
        )
    skipped = [line for line in report if line.startswith("SKIP ")]
    if skipped:
        # A partial run fakes the gate: 0/0 recall reads like a pass. Abort loud.
        raise SystemExit(
            f"\nABORT: {len(skipped)} of {len(rows)} lessons could not be evaluated. "
            "The calibration gate is only meaningful over the full labeled set — "
            "fix the DSN / book store and re-run.\n" + "\n".join(skipped)
        )
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
export DATABASE_URL=postgresql+asyncpg://edu:edu@127.0.0.1:5432/edu_copy
export CALIBRATE_DSN=postgresql://edu:edu@127.0.0.1:5432/edu_copy
export VAR_DIR=/Users/macmini5/Documents/Homework-Content-Generation-Automation/var
uv run python -m scripts.extract_coverage_calibrate gemini-3.5-flash-lite 2>&1 | tee /tmp/calib-lite.txt
```
Expected: all 9 lessons evaluated, then a per-lesson table plus recall and clean-lesson counts. **Do not proceed on a crash or on an ABORT** — the script refuses to print a score when any lesson was skipped, precisely because a partial run (0/0 recall) reads like a pass.

- [ ] **Step 3: Evaluate the hard bars; run the stronger model only if needed**

Check hard bar A (kimyo §13 → both worked-example types) and hard bar B (math §5 and §2 → zero). If either fails:

```bash
uv run python -m scripts.extract_coverage_calibrate gemini-3.5-flash 2>&1 | tee /tmp/calib-flash.txt
```

- [ ] **Step 4: Pull the real cost**

Query the SAME server the script wrote to (the DSN exported in Step 2 — not a bare `psql -d edu_copy`, which may be a different host entirely):

```bash
psql "postgresql://edu:edu@127.0.0.1:5432/edu_copy" -Atc "
select model_name, count(*), sum(prompt_tokens), sum(output_tokens)
from agent_usages
where operation='lesson.extract.coverage' and started_at > now() - interval '2 hours'
group by model_name;"
```
A zero-row result means the DSNs disagree, **not** that nothing was billed — reconcile before believing it. Record the numbers; convert with `app/services/pricing.py`'s map for the `$` line.

- [ ] **Step 5: Write the calibration doc**

Create `docs/research/2026-08-07-extract-coverage-calibration.md` containing: the method (one paragraph), the per-lesson table from the run, recall, the hand-check verdict on every item reported for a clean lesson (real miss vs false positive — say which, with the source evidence), the money-rule line (calls / tokens / `$`), and the resulting default decision under the Step-0 rule.

**Hand-confirm hard bar A explicitly.** `_matches` scores a hit when a *single* ≥4-char token appears in the other string, and generic Uzbek tokens (`qoidasi`, `massasi`, `hisoblash`, `misol…`) can bridge unrelated items — so an auto-scored bar-A pass is not sufficient evidence. Quote kimyo §13's two labeled items beside the checker's actual reported labels in the doc and state whether each is genuinely the same item. If the match is spurious, bar A **fails**. State explicitly that the labels came from `gemini-3.1-pro` plus one hand-verified case, so this measures agreement, not truth.

- [ ] **Step 6: Apply the decided defaults**

Edit `app/config.py` per the decision rule (only if the measurement calls for it), then:

```bash
uv run python -m pytest tests/services/test_extract_coverage.py -q
```
Expected: PASS. Two assertions may need updating **in this same commit**, depending on what the measurement decided:
- If the decision sets `extract_coverage_model`, update that assertion in `test_config_defaults_are_warn_only_and_inherit_the_extract_model` to the decided value.
- If the decision flips the default to `False`, update `test_shipped_default_is_independent_of_the_test_environment` to assert `is False`. That test exists to pin the *shipped* default against the suite's env override — its purpose survives either value, but it will fail loudly until you change it, which is the point.

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

- [ ] **Step 0: Pin the environment before anything else**

```bash
cd /Users/macmini5/Documents/HCGA-extract-coverage
export GEMINI_API_KEY=<plain key>
export DATABASE_URL=postgresql+asyncpg://edu:edu@127.0.0.1:5432/edu_copy
export PSQL_DSN=postgresql://edu:edu@127.0.0.1:5432/edu_copy   # same server
# The worktree has no var/ — the book store lives in the main checkout, and
# storage.book_pdf_path resolves it from settings.var_dir (default "var",
# RELATIVE), so without this the job cannot find its PDF at all.
export VAR_DIR=/Users/macmini5/Documents/Homework-Content-Generation-Automation/var
uv run python -c "
import app.config as c, app.services.agent as a, pathlib
print(a.__file__); print(c.settings.database_url); print(c.settings.var_dir)
assert (pathlib.Path(c.settings.var_dir) / 'books').is_dir(), 'book store not found'"
```
Expected: the module path contains `HCGA-extract-coverage`, `database_url` matches the DSN you exported, and the book-store assertion passes. If any is wrong, stop — the worktree walked up to `/Users/macmini5/Documents/.env` and every verification below would read a different database than the one being written.

- [ ] **Step 1: Pick a lesson and launch one job in-process**

Choose one **long, fact-dense** lesson (the class the audit shows leaks most) from a book already on disk, and one **short** lesson as the negative control. Launch a single job each over `transport=api` with `extract_coverage_check_enabled=True`. Use the existing single-lesson launch path (`scripts/smoke_per_role.py` is the closest working template for an in-process bounded call — read it before adapting; do not mass-generate).

- [ ] **Step 2: Verify the extract row**

```bash
psql "$PSQL_DSN" -Atc "
select p.job_id, p.status, p.validation_warnings
from phase_outputs p where p.phase_name='extract'
order by p.completed_at desc limit 5;"
```
Expected: both jobs `done`; the coverage warning (if any) present as an `extract_coverage:` string; **no job failed and no job parked**.

- [ ] **Step 3: Verify the check billed correctly and once**

```bash
psql "$PSQL_DSN" -Atc "
select operation, auth_mode, model_name, count(*), sum(prompt_tokens), sum(output_tokens)
from agent_usages where started_at > now() - interval '1 hour'
group by 1,2,3 order by 1;"
```
Expected: exactly **one** `lesson.extract.coverage` row per fresh extract, `auth_mode='api'`, on the extract role's model (or the calibrated override) — **not** one per phase and **not** one per failover attempt.

- [ ] **Step 4: Verify the FE surfaces it (needs Task 4b — it does not render without it)**

Rebuild the SPA (`cd web && npm run build`), open the job's `/preview`, and confirm the **Source checks** strip shows the `extract_coverage:` string, and that the job header's `warnings` stat includes it. Quote the rendered string in the worklog. If the smoke lesson produced no omissions, verify against a job that did — or temporarily point the strip at a job whose extract row carries a `lint:coverage_thin` warning (any pre-existing one works, and this is exactly the warning class that was invisible before Task 4b).

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
- `extract-coverage-regen-1`: the warn-only completeness check (worklog TBD) records dropped items but never acts — once live precision is measured (fire rate + cost from `agent_usages` where `operation='lesson.extract.coverage'`; the flagged items themselves from `phase_outputs.validation_warnings` on the extract row, since `run_phase` does not persist the verdict into `raw_envelope`), decide whether a confirmed CENTRAL omission should drive one `summarize_lesson(correction_hint=…)` regen, exactly as CQ-D's fidelity guard does.
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
git add docs/memory/MASTER_MEMORY.md docs/memory/INDEX.md docs/memory/WISHLIST.md docs/memory/ROADMAP.md docs/HOW_IT_WORKS.md docs/CODE_MAP.md docs/superpowers/plans/shipped/2026-08-07-extract-coverage-check.md
git commit -m "docs: worklog + de-stale reference docs for the extract-completeness check"
git show --stat HEAD          # verify the commit CONTENTS match the message
```

- [ ] **Step 7: Hand off**

Invoke `superpowers:finishing-a-development-branch`. Default is push the branch and open a PR against `Nggaev-v2` — **the user decides**; never self-merge (gatekeeping is GK2's).

---

## Self-review

**Spec coverage** — every design decision maps to a task: cheap-LLM detector → Task 1; strict lesson-scoped source → Task 2; aggregated central-first warning → Task 3; inline placement, skip conditions, timeout bound and `validation_warnings` → Task 4; actually making those warnings visible in the SPA → Task 4b; calibration against the labeled dataset and the default-on/off decision → Task 5; real-generation acceptance and per-fresh-extract billing → Task 6; worklog, deferred-regen backlog item, live-doc de-staling → Task 7.

**Corrections applied after the plan review (all verified against real code before accepting):**
1. The SPA filters the extract phase out of both warning surfaces (`preview.tsx:140`, `job.tsx:292`, `job.tsx:549-551`) — the surfacing claim was false, so **Task 4b** was added to deliver it (and it un-hides the already-shipped `lint:coverage_thin` as a side effect).
2. `run_phase` records `extra_envelope={"phase_name","difficulty","schema","attempt"}` only (`agent.py:1175-1192`) — the verdict never reaches `agent_usages.raw_envelope`, so the deferred-regen measurement now points at `phase_outputs.validation_warnings`.
3. The worktree has no `.env`; the walked-up one points `DATABASE_URL` at a **remote** host, so Tasks 5 and 6 now export it explicitly and run their verification SQL against that same DSN — otherwise the money-rule queries silently return empty.
4. The check sits outside `_run_with_failover`'s `asyncio.wait_for` (`pipeline.py:1013`) — it now carries `extract_coverage_timeout_seconds` (120s) so a hung advisory call cannot stall the job's sequential head phase.
5. The calibration matcher moved from exact token-set intersection to substring containment (the idiom `lint_coverage` itself uses) — Uzbek agglutination would otherwise fail hard bar A for matcher reasons rather than checker reasons.
6. The vision-path exclusion claim was **softened, not coded around**: the vision route triggers on whole-book Gate A while this check re-applies Gate A to the lesson window, so a mixed book with a readable window is still checked — which is correct behavior, so the code stands and only the claim changed.

**Placeholder scan** — the only deliberately unresolved values are the worklog number (must be read from the INDEX tail at finish time, not guessed — a known staleness trap) and the calibration outcome, which Task 5 resolves by a mechanical decision rule stated before the run.

**Type consistency** — `ExtractCoverageMiss(label, central)` / `ExtractCoverageVerdict(missing)` are defined in Task 1 and used with those exact field names in Tasks 3, 4, 5. `check_extract_coverage` is called with the same keyword set (`summary`, `source_text`, `section_title`, `section_number`, `provider`, `model`, `transport`, `homework_job_id`, `phase_output_id`) in Tasks 4 and 5 as defined in Task 1. `_lesson_source_or_none` / `_extract_coverage_warnings` / `_check_extract_coverage` keep one spelling throughout.

**Corrections applied after the second review round (again, each verified against real code first):**
7. **Blocker:** the new call site would have leaked REAL `gemini` CLI spawns into two pre-existing dispatch tests — that harness patches `read_page_range_text` to Gate-A-passing text but cannot patch a function that does not exist yet, and `tests/conftest.py` has no spawn guard. Fixed by defaulting the check OFF for the suite in `conftest.py`, re-enabling it explicitly in the new wiring harness, asserting the *shipped* default on the Settings class, and adding a durations/`_spawn`-sabotage proof that green really means "no spawn".
8. The worktree has **no `var/`** — the book store lives in the main checkout (the CQ-D smoke already codes around this at `scripts/cqd_extract_guards_smoke.py:37-45`). Task 5 now resolves the book root with that idiom and **aborts loudly if any lesson is skipped** (a 0/0 run reads like a pass); Task 6 Step 0 exports and asserts `VAR_DIR`.
9. `types.ts` exports **`PhaseOut`**, not `Phase` (`types.ts:200`) — the hedge is gone, and the plan notes that `npm test` alone would not have caught it since `tsx` erases type-only imports.
10. Hard bar A must be **hand-confirmed** in the calibration doc: `_matches` can bridge unrelated items on one generic token, so an auto-scored pass is not sufficient evidence.
11. Task 7 staged a whole directory (`docs/superpowers/plans/`) — narrowed to the explicit shipped path.

**Corrections applied after the third review round (all in the plan's own test code — the design was clean):**
12. `test_kill_switch_makes_no_call` disabled the switch *before* `_install_harness` re-enabled it — the harness won and the test would have failed against a correct implementation. Reordered, with the ordering hazard called out in-test. (A defect introduced by correction 7 — the round-2 fix.)
13. The timeout test asserted nothing that distinguished "timeout present" from "timeout absent": with no `asyncio.wait_for` the phase still completes and still writes no warnings, so it would merely have taken 30s and passed. Now asserts the call happened (RED before wiring) **and** elapsed < 5s (RED without the bound).
14. Task 5 Step 6 claimed a flipped enable-default keeps the config tests green — it does not, since correction 7 added a test pinning the shipped default to `True`. Step 6 now names both assertions that may need updating in that commit.
15. Stale expected-test counts in Task 1 (6→7) and Task 3 (10→11) after correction 7 added a test.
16. The calibration script could not tell a *clean verdict* from a *failed call* — `check_extract_coverage` returns `[]` for both by contract, so a broken environment would have scored zero misses everywhere and passed hard bar B. It now counts the successful `lesson.extract.coverage` usage rows against the number of lessons evaluated and aborts on a shortfall.

**Corrections applied after the fourth review round:**
17. Correction 16 over-fired: it aborted on **any** `success=False` usage row, but `run_phase`'s schema mode records exactly such a row for a first attempt that fails validation and then retries successfully (`agent.py:1137-1156`) — so one JSON flake on the mandated flash-lite run would have killed a healthy calibration. The abort is now `n_success != evaluated` alone (which already catches every ultimately-failed call, since those write no success row); `n_failed` prints as a note.
18. The success-count cutoff took the **DB** clock while `agent_usages.started_at` is stamped **host**-side — mixing clocks is what creates skew sensitivity, the opposite of the comment's claim. Now host clock minus 60s slack.
19. Stale "six from Task 1" in Task 3 Step 2 (seven), and the spawn-leak list in Task 4 Step 4 now names the third affected dispatch test and is explicitly non-exhaustive.

**Corrections applied after the fifth review round:**
20. Correction 18's 60-second backward slack was pure downside: `started_at` is stamped by `run_phase` **in the script's own process** (`agent.py:998`), so no skew is possible — but the window would have counted the *previous* run's rows, and Step 3 mandates running a second model straight after the first. A healthy run would have aborted with `n_success > evaluated`. Now an unslacked host timestamp, both count queries also filtered by `model_name`, and the abort message reads correctly in both directions.
21. `preview.tsx` called `sourceCheckWarnings(job.phases)` twice per render — hoisted to a local.

**Corrections applied after the sixth review round:**
22. The calibration script would have died at import under its own run command: this repo has no `[build-system]`, so `app` is never installed and only resolves from the repo root — `uv run python scripts/x.py` puts `scripts/` on `sys.path[0]` and raises `ModuleNotFoundError: No module named 'app'`. Verified empirically (by-path crashes, `-m` succeeds). All three run commands now use `uv run python -m scripts.extract_coverage_calibrate`, matching `scripts/cqd_extract_guards_smoke.py`'s documented form.
23. The money-rule constraint said "ONE single-lesson generation" while Task 6 launches a long lesson, a short control, and a cache re-launch — constraint reworded to match the task (still bounded and cost-reported).
24. Dead `from typing import Optional` in the Task 4 test append; Tech Stack said Python 3.12 where `pyproject.toml:5` requires ≥3.13.

**Deliberate non-goals** — no regen, no gating, no `lesson_context` shape change, no judge change, no migration, no new API endpoint, no batch/book-level rollup (that overlaps the unbuilt `judge-failure-rollup-1`).
