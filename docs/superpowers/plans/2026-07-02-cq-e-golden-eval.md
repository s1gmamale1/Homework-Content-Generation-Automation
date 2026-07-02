# CQ-E — R20 Golden-eval harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **⚠️ PROVISIONAL DECISIONS — confirm at the single approval gate.** The user was asked the three open decisions but was away-from-keyboard. This plan is drafted against the **recommended** answer to each; each is isolated so a change is cheap.
> 1. **Rubric mechanism = HYBRID** — deterministic dimensions (language artifacts, reflection fabrication, error-detection format, signal-reads of `judge_status`/`solver_status`/`validation_warnings`) run FREE in pytest; only the 3 dimensions the judge is provably blind to (boundary trace, answer-key correctness, broken-question) + extract-fidelity use bounded LLM-rubric calls. *If changed to pure-LLM → drop Task 2's free tier, all dims move into Task 3; to pure-deterministic → drop Task 3, accept that answer-key + boundary are unscored.*
> 2. **Golden set v1 = the 5 audited lessons only** (G8 algebra+geometry, UZ). *If expansion is chosen → Task 1 adds N more manifest entries + source fixtures + human-scored expected verdicts; everything else is unchanged (the harness is entry-count-agnostic).*
> 3. **Gate = two-tier** — free pytest layer (deterministic dims, every PR) + paid `scripts/golden_eval.py` exit-code (LLM dims, manual on prompt/model PRs). *If single-script → fold Task 4's pytest gate into the script.*

**Goal:** Ship R20 — a frozen golden-set quality-regression harness: score generated packets against the audit's rubric, diff against committed baselines, gate prompt/model-change PRs on no-regression. Also tune + baseline the deterministic fidelity check that [0079] shipped warn-only.

**Architecture:** A **standalone, offline** harness (the collision map forbids touching the pipeline/worker/schema — and inline scoring is the wrong shape anyway). A new `app/services/golden_eval.py` scores one packet (its 11 phase markdowns + source text + TOC next-lesson) across 6 audit dimensions, returning a structured `PacketScore`. Deterministic dimensions reuse `content_lint.lint_phase` and read `phase_outputs` signals; LLM dimensions make ONE `agent.run_phase(schema=RubricVerdict)` structured call each (the judge/solver pattern), `transport=api`. `scripts/golden_eval.py` loads a packet (by `job_id` from a DB, read-only) or a committed scored-fixture, scores it, diffs against the committed baseline JSON, and exits non-zero on regression. The 5 audited (defective, pre-fix) packets in `edu_copy` are the **rubric-validation** set (the harness must reproduce their audit FLAGs); the **frozen baseline** is the same 5 lessons regenerated on the FIXED system — done LAST, blocked on CQ-C.

**Tech Stack:** FastAPI + SQLAlchemy async (Postgres), pytest / pytest-asyncio, gemini/claude over the SDK (`transport=api`), `pdftotext` for source fixtures.

## Approach & key decisions

- **Offline harness, zero pipeline coupling.** The collision map (CQ-C in flight) forbids `pipeline.py`, `worker.py`, `jobs.py`, `model_tiers.py`, `agent_models.py`, `phase_outputs.py`, launch-stamping, `alembic/`. The harness needs none of them: it **reads** packets via `phase_repo.list_for_job` (read-only) and scores offline. New surface only: `app/services/golden_eval.py`, `scripts/golden_eval.py`, `tests/golden/**`.
- **Reuse the signals CQ-B/CQ-C/0079 already produce.** `content_lint.lint_phase(phase_name, output_md, subject=, output_language=)` (merged CQ-B [0110]) gives the deterministic language/format/misconception dimension for free — the harness imports it, does not reimplement it. `phase_outputs.judge_status`, `.validation_warnings`, and CQ-C's `.solver_status` are read as cheap signals; **`solver_status` is read via `getattr(po, "solver_status", None)`** so the harness compiles and runs on the pre-CQ-C base and lights up automatically once CQ-C merges (I design to read its column, never touch its files).
- **The judge is blind to exactly the two worst classes**, so those get LLM-rubric scorers: **boundary** (does the packet teach/test the NEXT lesson's concepts — the exact thing CQ-A's boundary note targets; scored against the real source + the TOC successor title) and **answer-key** (re-solve the key-bearing items, diff — the class CQ-C fixes live; the harness both reads `solver_status` AND does an independent re-solve for baselining). A pure-deterministic rubric would structurally miss both (3/5 + 3/5 of the audit).
- **Rubric-validation vs baseline are two distinct sets.** The 5 audited packets in `edu_copy` are **defective** (pre-fix) — they are the acceptance target: the harness must reproduce their known verdicts (deterministic dims flag the mechanical defects; LLM dims flag the boundary/key/broken cases). The **frozen baseline** is the same 5 lessons **regenerated on the fixed system** (post CQ-A/B/C/D + restart) — future PRs diff against it. Baselining the defective packets would freeze the bug.
- **Baseline-freeze is LAST and BLOCKED (hard gate condition).** Scaffolding (Tasks 1–5) develops entirely against the defective `edu_copy` packets (read-only) + committed source fixtures — no baseline needed. Task 6 (freeze) regenerates the golden set on the fixed system and is **explicitly blocked on CQ-C's merge + the head-server restart**, run only on the user's explicit go; it states its exact run count + cost.
- **Verified load-bearing facts (against real code @ base `3716fd8`):** all 5 audit jobs exist in `edu_copy` with 12 `done` phase rows each (`3ca0da6f`/`8f734563`/`263d99c5` math-algebra, `9504ad94`/`1122356a` geometriya-g7-11; G8, UZ; 3 gemini + 2 claude). `content_lint.lint_phase(...) -> list[LintFinding]` + `findings_to_warnings` are merged (`app/services/content_lint.py`). `phase_outputs` exposes `output_md`, `status`, `judge_status`, `validation_warnings` (JSONB); CQ-C adds `solver_status` (read via getattr). `agent.run_phase(*, schema=…) -> PhaseResult` (`.parsed` validated Pydantic, one retry on parse-fail, writes an `agent_usages` row tagged by `operation`) is the structured-call surface — the same one `phase_judge.judge`/CQ-C's solver use. `toc_entries.get_next_in_book` (merged CQ-A [0109]) gives the successor title for the boundary scorer. `pricing.cost_usd(provider, model, usage)` prices a run. `pdftotext` is at `/opt/homebrew/bin/pdftotext`.
- **Money.** Rubric development reads the defective packets (no generation). LLM-rubric **scoring** of those 5 packets for acceptance is bounded (≤ ~4 api calls × 5 packets, single-phase scale — the judge/solver-smoke class, pre-authorized) and its cost is reported. Task 6's **baseline-freeze generation** is the sole mass-ish run (5 lessons × 11 phases) — gated on explicit user go, cost stated up front.

## Global Constraints

- **Collision map — do NOT touch** (CQ-C in flight off this same base): `app/services/pipeline.py`, `app/services/worker.py`, `app/repositories/jobs.py`, `app/services/model_tiers.py`, `app/services/agent_models.py`, `app/repositories/phase_outputs.py` (read its model, never modify), the launch-stamping path, `alembic/versions/**`. **No migration** in this PR.
- **Read CQ-C's `solver_status` via `getattr(po, "solver_status", None)`** — never import from or edit CQ-C's files.
- **Transport for all real model calls: `transport=api` (SDK) only** (CLAUDE.md standing decision). No cli smoke.
- **Money rule:** never mass-generate homework. Bounded single-lesson/single-phase api calls are pre-authorized for dev + acceptance; **log each smoke's token cost**. Task 6's baseline-freeze full-golden-set generation is the paid exception — exact run count + token/cost estimate stated in the task, run only on the user's explicit go.
- **Worklog ID 0113** (0112 is CQ-C's; re-verify next-free against `docs/memory/INDEX.md` at finish — INDEX currently ends at 0111). Expect append-only conflicts in `MASTER_MEMORY.md`/`INDEX.md` and the ROADMAP R20/R21 lines on rebase — hand-merge, keep both sides, **never clobber CQ-C's closes**.
- **Worktree `../HCGA-cqe`, branch `cq-e-golden-eval` off `origin/Nggaev-v2`, commit prefix `cqe:`.** Commit this plan as the FIRST commit on the branch. **Stage only the files each task lists — never `git add -A`.**
- One commit per task; each task ends TDD-green with `uv run python -m pytest <its files> -q`.

## File Structure

- **Create** `app/services/golden_eval.py` — the scorer: `PacketScore`/`DimensionScore` dataclasses, the 6 dimension scorers (deterministic + LLM-rubric), `score_packet(...)`, `RubricVerdict` schema, `diff_scores(baseline, current)`.
- **Create** `scripts/golden_eval.py` — CLI runner/gate: load packet (by `job_id` or fixture), score, diff vs baseline, print report, exit-code.
- **Create** `tests/golden/manifest.json` — the frozen golden set (5 entries: job_id, book_id, subject, grade, language, source page range, per-dimension `audit_verdict`).
- **Create** `tests/golden/sources/<job8>.txt` (×5) — committed real textbook source text (pdftotext of the lesson pages).
- **Create** `tests/golden/baselines/<job8>.json` (×5) — frozen fixed-system scores (**Task 6, LAST**).
- **Create** `tests/golden/test_manifest.py`, `tests/golden/test_deterministic_scorers.py`, `tests/golden/test_llm_scorers.py`, `tests/golden/test_golden_gate.py`, `tests/golden/test_reproduces_audit.py`.
- **Read-only imports:** `content_lint`, `phase_outputs` (repo `list_for_job` + model columns), `agent`, `pricing`, `toc_entries`.

---

### Task 1: Golden-set manifest + source fixtures + loader

**Files:**
- Create: `tests/golden/manifest.json`, `tests/golden/sources/<job8>.txt` (×5), part of `app/services/golden_eval.py` (loader + dataclasses)
- Test: `tests/golden/test_manifest.py`

**Interfaces:**
- Produces: `GoldenEntry` dataclass `{job_id: str, book_id: str, subject: str, grade: str, language: str, source_pages: str, audit_verdict: dict[str,str]}` and `load_golden_set() -> list[GoldenEntry]` (reads `manifest.json` relative to the repo). `audit_verdict` maps each of the 6 dimension keys (`boundary`, `answer_key`, `broken_question`, `language`, `reflection`, `extract_fidelity`) to `"flag"`/`"pass"`. Consumed by Tasks 2–6.

**Manifest content** (the 5 audit entries, verdicts derived from `docs/research/2026-07-01-content-quality-audit-g8-math.md` — the packet table + defect taxonomy; the implementer copies page ranges + findings from there):

| job (8) | pages | boundary | answer_key | broken_question | language | reflection | extract_fidelity |
|---|---|---|---|---|---|---|---|
| 3ca0da6f | 12–17 | pass | flag | flag | flag | flag | flag |
| 8f734563 | 22–26 | pass | flag | pass | flag | flag | flag |
| 263d99c5 | 34–38 | flag | flag | pass | flag | flag | pass |
| 9504ad94 | 8–10 | flag | pass | pass | flag | flag | pass |
| 1122356a | 41–43 | flag | pass | flag | flag | flag | pass |

- [ ] **Step 1: Write the failing test** (`tests/golden/test_manifest.py`):

```python
import json, pathlib
from app.services import golden_eval

_MANIFEST = pathlib.Path(__file__).resolve().parents[2] / "tests" / "golden" / "manifest.json"
_DIMS = {"boundary", "answer_key", "broken_question", "language", "reflection", "extract_fidelity"}


def test_manifest_has_five_audit_entries():
    entries = golden_eval.load_golden_set()
    assert len(entries) == 5
    assert {e.job_id[:8] for e in entries} == {
        "3ca0da6f", "8f734563", "263d99c5", "9504ad94", "1122356a"}


def test_every_entry_scores_all_six_dimensions():
    for e in golden_eval.load_golden_set():
        assert set(e.audit_verdict) == _DIMS
        assert all(v in ("flag", "pass") for v in e.audit_verdict.values())


def test_source_fixture_exists_and_names_the_lesson():
    # each committed source fixture must be non-trivial real text
    root = pathlib.Path(__file__).resolve().parents[2] / "tests" / "golden" / "sources"
    for e in golden_eval.load_golden_set():
        f = root / f"{e.job_id[:8]}.txt"
        assert f.is_file() and len(f.read_text(encoding="utf-8")) > 500
```

- [ ] **Step 2: Run test to verify it fails** — `uv run python -m pytest tests/golden/test_manifest.py -q` → FAIL (`golden_eval` / manifest missing).

- [ ] **Step 3: Capture source fixtures + write manifest + loader.**
  - For each entry, capture the lesson's source text: find the book PDF (`var/books/<book_id>/source.pdf`; if absent locally, `book_fetch.ensure_book_pdf_sync` off `edu_copy`, or fetch from the head) and run `pdftotext -f <first> -l <last> source.pdf - > tests/golden/sources/<job8>.txt` for the lesson's PDF page range. **Verify** the captured text contains the section title (the printed page range in the audit may be offset from the PDF page index — adjust the `-f/-l` window until the fixture names the lesson; record the mapping in a comment field in the manifest entry). Book IDs: resolve each via `select book_id from homework_jobs where id::text like '<job8>%'` against `edu_copy`.
  - Write `manifest.json` (the 5 entries above) and the `GoldenEntry`/`load_golden_set` loader in `app/services/golden_eval.py`.

- [ ] **Step 4: Run test to verify it passes** — `uv run python -m pytest tests/golden/test_manifest.py -q` → PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add tests/golden/manifest.json tests/golden/sources app/services/golden_eval.py tests/golden/test_manifest.py
git commit -m "cqe: golden-set manifest (5 audit entries) + source fixtures + loader"
```

---

### Task 2: Deterministic dimension scorers (free tier)

**Files:**
- Modify: `app/services/golden_eval.py` (add deterministic scorers + `DimensionScore`)
- Test: `tests/golden/test_deterministic_scorers.py`

**Interfaces:**
- Consumes: `content_lint.lint_phase`, `phase_outputs` rows.
- Produces: `DimensionScore{dimension: str, verdict: Literal["flag","pass"], detail: str, mechanism: Literal["deterministic","llm"]}`; `score_language(phases, subject, language) -> DimensionScore`, `score_reflection(phases) -> DimensionScore`, `score_error_detection_format(phases, subject, language) -> DimensionScore`, and `read_signals(phases) -> dict` (folds `judge_status`, `getattr(po,"solver_status",None)`, `validation_warnings` counts). `phases` = `list[PhaseView]` where `PhaseView{phase_name, output_md, judge_status, validation_warnings, solver_status}` (a plain read-model the scorer builds from `phase_outputs` rows or from fixtures — decouples scoring from the ORM).

- [ ] **Step 1: Write the failing test** — deterministic scorers over synthetic `PhaseView`s (no DB):

```python
from app.services.golden_eval import (
    PhaseView, score_language, score_reflection, score_error_detection_format)


def _pv(name, md):
    return PhaseView(phase_name=name, output_md=md, judge_status="ok",
                     validation_warnings=None, solver_status=None)


def test_language_scorer_flags_mixed_script_and_english_template():
    # Cyrillic 'а' spliced into a Latin word + an English scaffolding token
    phases = [_pv("flashcards", "atamа bo'yicha. Mode: Hard")]
    s = score_language(phases, subject="matematika", language="uz")
    assert s.verdict == "flag" and s.mechanism == "deterministic"


def test_language_scorer_passes_clean_uzbek():
    phases = [_pv("flashcards", "Toza o'zbekcha matn, hech qanday aralashuv yo'q.")]
    assert score_language(phases, subject="matematika", language="uz").verdict == "pass"


def test_reflection_scorer_flags_pre_asserted_outcome():
    phases = [_pv("reflection", "## Redo Route\nNeeds Retry. Ikkilanishlar kuzatildi.")]
    assert score_reflection(phases).verdict == "flag"


def test_reflection_scorer_passes_neutral_structure():
    phases = [_pv("reflection", "## Redo Route\nAgar ilova qayta ishlashni belgilasa...")]
    assert score_reflection(phases).verdict == "pass"
```

- [ ] **Step 2: Run to verify it fails** — `uv run python -m pytest tests/golden/test_deterministic_scorers.py -q` → FAIL (import errors).

- [ ] **Step 3: Implement** the deterministic scorers in `golden_eval.py`:
  - `score_language` → runs `content_lint.lint_phase` on every phase; any `mixed_script`/`calque`/`english_template` finding ⇒ `flag`.
  - `score_reflection` → checks the `reflection` phase for pre-asserted-outcome signals (`"needs retry"`, `"not passed"`, `"handled well"`, `"ikkilanish"` — the CQ-A regression watch); any hit ⇒ `flag`.
  - `score_error_detection_format` → `content_lint.lint_phase("practice-error-detection", …)`; an `error_detection` finding (>1 broken block) ⇒ `flag`.
  - `read_signals` → count `validation_warnings`, collect `judge_status` and `getattr(po,"solver_status",None)` across phases.

- [ ] **Step 4: Run to verify it passes** — `uv run python -m pytest tests/golden/test_deterministic_scorers.py -q` → PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add app/services/golden_eval.py tests/golden/test_deterministic_scorers.py
git commit -m "cqe: deterministic dimension scorers (language, reflection, error-detection, signals)"
```

---

### Task 3: LLM-rubric dimension scorers

**Files:**
- Modify: `app/services/golden_eval.py` (add `RubricVerdict` schema + LLM scorers)
- Test: `tests/golden/test_llm_scorers.py` (mock the `agent.run_phase` IO boundary; run the REAL scorer body)

**Interfaces:**
- Consumes: `agent.run_phase(schema=RubricVerdict)`, `toc_entries.get_next_in_book` (successor title), the source fixture text.
- Produces: `RubricVerdict{verdict: Literal["flag","pass"], severity: Literal["none","minor","major"], evidence: str}`; `async score_boundary(...)`, `async score_answer_key(...)`, `async score_broken_question(...)`, `async score_extract_fidelity(...)`, each `-> DimensionScore` (mechanism `"llm"`), plus `_build_rubric_prompt(dimension, ...)`. Each makes exactly ONE `agent.run_phase(schema=RubricVerdict, phase_name="__golden__", operation="golden:<dim>", transport="api", ...)` call and maps `.parsed.verdict` to the `DimensionScore`. An `agent.run_phase` exception ⇒ `DimensionScore(verdict="pass", detail="scorer-unavailable: <e>", mechanism="llm")` (never crash a run; a scorer error must not read as a regression — logged, surfaced in the report).

- [ ] **Step 1: Write the failing test** — mock `agent.run_phase` to return a canned `PhaseResult`, assert the scorer builds a prompt that includes the source + next-lesson title and maps the verdict:

```python
import types, pytest
from app.services import golden_eval as ge


class _FakeParsed:
    def __init__(self, verdict): self.verdict, self.severity, self.evidence = verdict, "major", "e"


@pytest.mark.asyncio
async def test_boundary_scorer_passes_source_and_next_lesson_and_maps_verdict(monkeypatch):
    captured = {}
    async def fake_run_phase(**kw):
        captured.update(kw)
        return types.SimpleNamespace(parsed=_FakeParsed("flag"), text="", usage={})
    monkeypatch.setattr(ge.agent, "run_phase", fake_run_phase)
    s = await ge.score_boundary(
        boss_arena_md="... uses the converse ...", preview_md="...",
        source_text="Pifagor teoremasi ... (no converse here)",
        next_lesson_title="Pifagor teoremasiga teskari teorema",
        provider="gemini", model="gemini-2.5-pro", transport="api")
    assert s.verdict == "flag" and s.mechanism == "llm"
    assert "teskari" in captured["phase_prompt"]          # next-lesson title threaded in
    assert "Pifagor teoremasi" in captured["phase_prompt"] # source threaded in


@pytest.mark.asyncio
async def test_scorer_error_degrades_to_pass_not_crash(monkeypatch):
    async def boom(**kw): raise RuntimeError("api down")
    monkeypatch.setattr(ge.agent, "run_phase", boom)
    s = await ge.score_boundary(boss_arena_md="x", preview_md="x", source_text="x",
                                next_lesson_title="y", provider="gemini",
                                model="gemini-2.5-pro", transport="api")
    assert s.verdict == "pass" and "unavailable" in s.detail
```

- [ ] **Step 2: Run to verify it fails** — `uv run python -m pytest tests/golden/test_llm_scorers.py -q` → FAIL.

- [ ] **Step 3: Implement** the `RubricVerdict` schema + the 4 LLM scorers. Prompts encode the audit method per dimension:
  - **boundary** — "Using ONLY the source lesson below, and knowing the NEXT lesson is «{title}», does the packet teach/test any next-lesson concept (converse/inverse, recognition criteria, generalization, a term defined only next lesson)? flag if so."
  - **answer_key** — re-solve each key-bearing item from the source; flag ONLY a demonstrable wrong key (mirror CQ-C's conservative, high-confidence-only framing to avoid false positives). Also fold the `solver_status` signal: if `solver_status` indicates a correction, that corroborates a flag.
  - **broken_question** — flag a question that is unanswerable, self-contradictory, or whose "wrong method" coincidentally equals the right answer, or needs machinery taught nowhere in packet/source.
  - **extract_fidelity** — compare worked examples/quotes in the packet against the source; flag transcription drift (this is the tuned successor to [0079]'s warn-only fidelity check — the R20 remit).

- [ ] **Step 4: Run to verify it passes** — `uv run python -m pytest tests/golden/test_llm_scorers.py -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/golden_eval.py tests/golden/test_llm_scorers.py
git commit -m "cqe: LLM-rubric scorers (boundary, answer-key, broken-question, extract-fidelity)"
```

---

### Task 4: `score_packet` + diff + runner/gate

**Files:**
- Modify: `app/services/golden_eval.py` (`score_packet`, `diff_scores`)
- Create: `scripts/golden_eval.py`
- Test: `tests/golden/test_golden_gate.py`

**Interfaces:**
- Produces: `async score_packet(entry, phases, source_text, next_lesson_title, *, provider, model, transport, llm=True) -> PacketScore` (`PacketScore{job_id, scores: dict[str,DimensionScore]}`; `llm=False` runs deterministic dims only — the free tier); `diff_scores(baseline: PacketScore, current: PacketScore) -> list[str]` (regressions = a dimension that was `pass` in baseline and is `flag` now); a `_load_phases_from_db(job_id) -> list[PhaseView]` helper (read-only `phase_repo.list_for_job`). The runner `scripts/golden_eval.py` exits non-zero if any regression is found.

- [ ] **Step 1: Write the failing test** (`tests/golden/test_golden_gate.py`) — deterministic-tier `score_packet(llm=False)` over fixtures + `diff_scores`:

```python
import pytest
from app.services import golden_eval as ge


@pytest.mark.asyncio
async def test_deterministic_score_packet_and_diff_detects_regression():
    entry = ge.load_golden_set()[0]
    clean = [ge.PhaseView("flashcards", "Toza matn.", "ok", None, None),
             ge.PhaseView("reflection", "Agar ilova belgilasa...", "ok", None, None)]
    dirty = [ge.PhaseView("flashcards", "Mode: Hard atamа", "ok", None, None),
             ge.PhaseView("reflection", "Needs Retry", "ok", None, None)]
    base = await ge.score_packet(entry, clean, "src", "next", provider="gemini",
                                 model="gemini-2.5-pro", transport="api", llm=False)
    cur = await ge.score_packet(entry, dirty, "src", "next", provider="gemini",
                                model="gemini-2.5-pro", transport="api", llm=False)
    assert base.scores["language"].verdict == "pass"
    assert cur.scores["language"].verdict == "flag"
    regressions = ge.diff_scores(base, cur)
    assert any("language" in r for r in regressions)
    assert ge.diff_scores(base, base) == []   # identical → no regression
```

- [ ] **Step 2: Run to verify it fails** — `uv run python -m pytest tests/golden/test_golden_gate.py -q` → FAIL.

- [ ] **Step 3: Implement** `score_packet` (deterministic dims always; LLM dims when `llm=True`), `diff_scores`, `_load_phases_from_db`, and `scripts/golden_eval.py`:
  - `scripts/golden_eval.py --job <id> [--no-llm] [--baseline tests/golden/baselines/<job8>.json]`: load phases (DB), source fixture, next-lesson title (`toc_entries.get_next_in_book`), `score_packet`, print a per-dimension report, and if a baseline is given, `diff_scores` → **exit 1 on any regression, 0 otherwise**. Prints total token cost (`pricing.cost_usd`) of the LLM dims.

- [ ] **Step 4: Run to verify it passes** — `uv run python -m pytest tests/golden/test_golden_gate.py -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/golden_eval.py scripts/golden_eval.py tests/golden/test_golden_gate.py
git commit -m "cqe: score_packet + diff + scripts/golden_eval runner/gate (exit-code on regression)"
```

---

### Task 5: Acceptance — the rubric reproduces the audit verdicts

**Files:**
- Test: `tests/golden/test_reproduces_audit.py` (deterministic tier — FREE, committed)

**Interfaces:** none new (asserts Tasks 2–4 against the manifest's `audit_verdict`).

- [ ] **Step 1: Write the failing test** — the DETERMINISTIC dimensions, scored over the real defective packets read from `edu_copy`, must match the manifest's `audit_verdict` for those dims. DB-gated (reads `edu_copy` read-only):

```python
import os, pytest
from app.services import golden_eval as ge

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_GOLDEN_AUDIT") != "1",
    reason="reads edu_copy; set RUN_GOLDEN_AUDIT=1 + DATABASE_URL=edu_copy")

_DET = ("language", "reflection")   # deterministic dims validated for free


@pytest.mark.asyncio
async def test_deterministic_dims_reproduce_audit_flags():
    for entry in ge.load_golden_set():
        phases = await ge._load_phases_from_db(entry.job_id)
        score = await ge.score_packet(entry, phases, "", None, provider="gemini",
                                      model="gemini-2.5-pro", transport="api", llm=False)
        for dim in _DET:
            assert score.scores[dim].verdict == entry.audit_verdict[dim], (
                f"{entry.job_id[:8]} {dim}: got {score.scores[dim].verdict}, "
                f"audit says {entry.audit_verdict[dim]}")
```

- [ ] **Step 2: Run to verify it fails** — `RUN_GOLDEN_AUDIT=1 DATABASE_URL=<edu_copy> uv run python -m pytest tests/golden/test_reproduces_audit.py -q` → FAIL until scorers are tuned to the real outputs. **Iterate** the deterministic scorers (Task 2 code) against the real packets until the `language`/`reflection` verdicts match the audit for all 5 (the audit says all 5 flag both). *If a real packet legitimately does not flag a dim the audit flagged, correct the manifest verdict + note why — the audit is the ground truth but a scorer that can't see a defect deterministically is honest signal, not a bug to force.*

- [ ] **Step 3: LLM-tier acceptance (controller-run, paid, at the gate).** Add to `scripts/golden_eval.py` a `--audit-check` mode that scores the LLM dims over the 5 `edu_copy` packets and prints, per packet per LLM dim, `got vs audit`. The controller runs it over `transport=api` and pastes the result + token cost into the PR body. **Pass = the LLM dims reproduce the audit's boundary/answer-key/broken flags** (e.g. `1122356a` boundary=flag, `263d99c5` answer_key=flag). Bounded: ≤ 4 dims × 5 packets api calls.

- [ ] **Step 4: Commit**

```bash
git add tests/golden/test_reproduces_audit.py app/services/golden_eval.py scripts/golden_eval.py
git commit -m "cqe: acceptance — rubric reproduces audit verdicts (deterministic free + LLM audit-check mode)"
```

---

### Task 6: Baseline-freeze (LAST — BLOCKED on CQ-C merge + head restart)

> **⛔ HARD GATE: do NOT run this task until `[CQ-C]` (0112) is merged AND the head server has been restarted on the fixed code, AND the user gives explicit go.** Baselines must freeze the FIXED system; freezing before CQ-C lands would bake the answer-key bug into the baseline. Tasks 1–5 do not depend on this — the harness is fully built and validated without it.

**Files:**
- Create: `tests/golden/baselines/<job8>.json` (×5)

**Cost statement (state before running):** regenerate the 5 golden lessons on the fixed system = **5 jobs × 11 content phases = 55 phase generations** + judge + solver per phase + the 4 LLM-rubric dims × 5 = 20 scoring calls. Estimate at the campaign content tier (gemini-2.5-pro/flash + 3.1-pro judge/solver): **~$X/packet → ~$5X total** (the implementer computes X from `pricing.cost_usd` on a single-packet dry run first, states it, and waits for go).

- [ ] **Step 1** — on the user's explicit go: launch the 5 golden lessons through the normal generation path on the fixed head (their `(book, section)` are known from the manifest), let them complete `done`.
- [ ] **Step 2** — `uv run python -m scripts.golden_eval --job <id>` (llm on) for each, writing `--emit-baseline tests/golden/baselines/<job8>.json`.
- [ ] **Step 3** — sanity: the fixed-system baselines should show the audit's flagged dims now `pass` (boundary/reflection/answer-key improved) — this is the regression-harness's own proof that the CQ-A–D fixes hold. Note any dim still flagged (honest residual).
- [ ] **Step 4: Commit**

```bash
git add tests/golden/baselines
git commit -m "cqe: freeze golden baselines on the fixed system (post CQ-A/B/C/D)"
```

---

## Finish (after Tasks 1–5 green; Task 6 as gated above)

1. Full suite green: `uv run python -m pytest tests/ -q` (the free golden tier runs here; `RUN_GOLDEN_AUDIT`/DB-gated tests run separately).
2. `git fetch origin` → `git log HEAD..origin/Nggaev-v2`; rebase onto `origin/Nggaev-v2` if it moved (expect append-only `MASTER_MEMORY`/`INDEX`/ROADMAP conflicts — hand-merge, **never clobber CQ-C's closes**), re-run suite.
3. PR **`[CQ-E] Golden-eval harness (R20)`** — gatekeeper merges, no self-merge. PR body carries the deterministic-tier results + the paid LLM audit-check output + token cost.
4. Worklog **0113** (re-verify free) in `MASTER_MEMORY.md` + INDEX row.
5. Close **R20** in `docs/memory/ROADMAP.md` (move to Shipped/Closed) + **CQ-E** in `docs/memory/REMEDIATION_CLUSTERS.md` (Cluster 10). Leave R21 items 2/6 to CQ-C/CQ-D.
6. `git mv` the plan to `docs/superpowers/plans/shipped/`.
7. De-stale `docs/HOW_IT_WORKS.md` / `docs/CODE_MAP.md` (add the golden-eval harness + gate).

## Self-Review (author)

- **Spec coverage:** golden set (T1), rubric scoring (T2 deterministic + T3 LLM), baselines+diff (T4/T6), PR gate (T4 exit-code + T5 free pytest), tune+baseline the 0079 fidelity check (T3 extract-fidelity + T6). All R20 deliverables covered.
- **Collision map honored:** no listed file touched; `solver_status` read via `getattr`; no migration.
- **Placeholder scan:** none — real code/commands throughout; the one intentional deferral (Task 6) is a hard gated step with a stated cost, not a placeholder.
- **Type consistency:** `GoldenEntry`/`PhaseView`/`DimensionScore`/`PacketScore`/`RubricVerdict` defined in T1–T3, consumed consistently in T4–T6; `score_packet(..., llm=)` and `diff_scores` signatures match across tasks.
- **Money:** dev reads defective packets (free); LLM acceptance is bounded + cost-reported; the one paid generation run (T6) is hard-gated on CQ-C + user go with a cost statement.
