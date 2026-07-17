# Teaching-Equivalence + Learnability Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An offline audit tool that measures whether a generated homework packet actually *teaches what the textbook lesson teaches* — via a closed-book simulated-student exam derived from the textbook, sat once before and once after "studying" only the packet.

**Architecture:** New standalone module `app/services/teaching_audit.py` (schemas, protocol validation, pure prompt builders, classification, orchestrator) + CLI `scripts/teaching_audit.py`, mirroring the `golden_eval` house pattern (offline, `agent.run_phase` with `schema=`, `homework_job_id=None`, per-step `operation=` labels). Normal audit = **5 logical LLM calls** (exam → pre-test → post-test → grade → coverage). Sensitivity audit = **7 logical calls**, paired against a true empty-packet control: one exam + one pre-test + **one combined grading call** shared, plus a post-test and coverage for each of the two study documents (normal packet vs empty control). The single grading call is **blinded** — sittings are relabeled `s0/s1/…` and remapped only after grading, so the examiner can't tell the control from the real packet.

**Tech Stack:** Python 3.12, Pydantic schemas via `agent.run_phase(schema=…)`, `transport=api` (production transport), pytest with `monkeypatch.setattr(ta.agent, "run_phase", fake)`.

## Approach & key decisions

- **The contract being tested:** *a student with only prior-grade knowledge, given nothing but the packet, can pass an exam derived from the textbook lesson.* Two distinct failure columns fall out per objective: **`not_taught`** (packet never actually teaches the objective — teaching-equivalence failure) vs **`not_learnable`** (packet teaches it but the closed-book student still failed — learnability failure). Engagement/motivation is explicitly out of scope (user decision, 2026-07-17).
- **The load-bearing anti-circularity rule:** the exam is derived from the **textbook pages only** — the packet never influences the exam. Today's judge grades the packet against itself/its extract, so silent under-coverage passes; this closes that hole (verified this session: no existing layer measures packet-vs-textbook).
- **The student sees the STUDENT-FACING deliverable, nothing more** (gate-1 blocker 1): the loader keeps only `status == "done"` AND `phase_name != "extract"` phase rows — exactly mirroring what real exports give students (`app/api/v1/jobs.py:87` `_phase_zip`, `app/services/notion_archive.py:289`). The `extract` row is an internal textbook-derived summary; including it would let the post-test pass on textbook knowledge the packet never delivers. Regression-tested via the pure `filter_deliverable`.
- **Knowledge-leak mitigation** (the known failure mode of simulated students — the LLM answers from its own knowledge): (a) closed-book persona with an explicit "if the packet didn't teach it, you don't know it" rule; (b) the examiner is instructed to prefer *lesson-specific* facts/terms/methods over generally-derivable reasoning; (c) the **pre-test sitting is the control** — uniform latent leak inflates the pre-test, pushing objectives into `already_known` (not `learned`), so only genuine packet-driven gains score as `learned`.
- **Standalone tool, not a golden_eval dimension** (user decision): the multi-call protocol + textbook-page access is too big for one rubric dimension of the frozen CQ-E harness. The JSON report is shaped so CQ-E can later consume it as an advisory dimension.
- **Short-answer, LLM-graded exam** (user decision): exactly 2 questions per objective, examiner grades free-text answers against a textbook-derived key. MCQ was rejected — closed-book guessing (~25%) adds noise at per-objective granularity.
- **Protocol integrity is validated fail-loud** (gate-1 blocker 3): schemas guarantee field types, not consistency, and `all([])` on an empty matrix would fabricate a clean verdict. Validation enforces: unique non-empty objective/question ids, **non-blank objective statements, question text, and answer keys** (gate-2 smaller correction — a structurally complete but empty exam is unusable), exactly `_QUESTIONS_PER_OBJECTIVE` questions per objective, no unknown objective references, per-sitting answer id sets exactly equal to the question id set, exactly one grade per (question, sitting-label) pair, exactly one coverage row per objective, ≥1 objective. Any violation raises `TeachingAuditError` — no silent defaults, no warnings-and-continue.
- **`mentioned` counts as NOT taught** (gate-1 blocker 5): coverage `mentioned` = "the term appears but is never actually explained" — a failed objective that was merely mentioned is a **`not_taught`** teaching-equivalence failure. Only a failed **`taught`** objective is `not_learnable`.
- **The report retains the full evidence chain** (gate-1 blocker 4): the JSON report embeds all parsed artifacts (exam with questions+keys, sittings' answers, per-question grades with evidence, per-objective coverage with evidence) so a human can audit the audit — none of that is recoverable from `agent_usages`.
- **Sensitivity is a PAIRED experiment with an IMMUTABLE baseline** (gate-1 blocker 2 + gate-2 blocker 1): `paired_audit` derives the exam ONCE, sits the pre-test ONCE, and **grades every sitting in ONE combined call** (`pre`, `post_normal`, `post_control` together). Because grading happens exactly once, the pre-test baseline is byte-identical across both legs — the examiner cannot grade the same pre answers two different ways, so an objective can't drift between `already_known` and `learned` between legs. The two legs then aggregate off that one shared grade set, differing only in which post-sitting they read. (An earlier design graded each leg independently → non-paired baseline → rejected at gate 2.)
- **The negative control is a TRUE empty packet, not phase-ablation** (gate-2 blocker 2): the control leg's study document is a fixed "no study material" sentinel — not the packet-with-two-phases-removed. Verified against `prompts/_general/`: the residual phases carry explicit answer keys (`memory-check.md:24` "state the expected answer", "Mark which single option is correct"; `practice-error-detection.md:33` "The correct version — the right content"), so a gutted-but-nonempty packet can still teach the objectives — equal learned-counts there would NOT prove the instrument broken. An empty control is the clean check: if a student given nothing still scores `learned` (with a low pre-test), the post-test path is leaking latent knowledge and the instrument is broken.
- **The grader is BLINDED** (gate-3 blocker 1): the combined grading call would otherwise carry semantic sitting labels (`pre`/`post_normal`/`post_control`) that tell the examiner which sitting is the control it "should" mark down — biasing the very comparison the audit rests on. The orchestrator relabels sittings to opaque `s0/s1/…` in the grading prompt and remaps back to semantics only after the verdict returns. A call-boundary test asserts the grading prompt contains none of `post_normal`/`post_control`/`control`/`normal`.
- **Sensitivity is a DUAL gate with distinct failure reasons** (gate-3 blocker 2): coverage is part of the instrument, so a coverage scorer that hallucinates `taught` on an empty document is a broken instrument even if learned-counts drop. `sensitivity_pass` requires BOTH (a) `control.learned_count < normal.learned_count` (student path) AND (b) every control coverage row is `absent` (coverage path). `sensitivity_failures()` reports the two independently so a run tells you *which* path failed — student-path leakage vs coverage-path hallucination.
- **Fail loud, not degrade-to-pass:** unlike `golden_eval._score_via_rubric` (which degrades to protect baseline freezing), this is an audit instrument — a dead scorer makes the run worthless, so any call failure raises `TeachingAuditError`. Advisory by default: exit 0 with the report; `--strict` exits 1 on failure columns / failed sensitivity.
- **Models:** examiner `gemini/gemini-2.5-pro` (needs to out-think the packet generator; same default as the CQ-E LLM scorer), student `gemini/gemini-2.5-flash` (persona fidelity, cheap). Model names CLI-overridable; **transport is pinned `api`** — the CLI exposes no transport flag (cli transport is operationally retired; the module keeps a `transport` kwarg defaulting to `"api"` for tests only).
- **Load-bearing facts verified against code:** `agent.run_phase(provider, model, phase_prompt, phase_name, homework_job_id=None, phase_output_id=None, schema=…, operation=…, transport=…) -> PhaseResult(.parsed, .usage)` (`agent.py:926`); **structured-output validation retries once and records EACH attempt as its own `agent_usages` row, the failed one `success=False`** (`agent.py:1121`) — so "N logical calls" ≤ "N usage rows", and the CLI's cost (summed over `_call`'s retained successful-attempt usage) may undercount retry attempts (gate-2 smaller correction); every `run_phase` call writes a ledger row (`agent.py:807` `_record_usage`), so the tool is "no domain/pipeline mutations; usage-ledger writes only", not literally read-only; textbook text via `agent.read_page_range_text(pdf_path, page_start, page_end, margin=0)` (`agent.py:1491`) + `storage.book_pdf_path(book_id)`; `toc_entries.page_start/page_end` nullable (`app/models/toc_entry.py:25-26`); packet via `phase_outputs.list_for_job(session, job_id)` (`app/repositories/phase_outputs.py:91`) with `PhaseOutput.status` (`app/models/phase_output.py:29`); student-deliverable filter precedent `app/api/v1/jobs.py:87` + `app/services/notion_archive.py:289`; `Book.subject/.grade` (`app/models/book.py:14-15`), `HomeworkJob.output_language` (`app/models/homework_job.py:30`); cost via `pricing.cost_usd(provider, model, usage)` (`pricing.py:83`); house test pattern `monkeypatch.setattr(ge.agent, "run_phase", fake)` (`tests/golden/test_llm_scorers.py:31`); `pyproject.toml` sets `asyncio_mode = "auto"` — async tests need no marker.

## Global Constraints

- **Money rule:** never mass-generate. The only real-model calls are the Task 6 acceptance smoke — ONE lesson, one paired sensitivity run (7 logical calls), cost reported in $.
- All real calls run `transport=api` (cli is operationally retired); the CLI exposes no transport flag.
- **No domain/pipeline mutations; usage-ledger writes only** — every `run_phase` call records an `agent_usages` row (desired attribution, not a side effect to suppress). No schema changes, no migrations, no pipeline/worker edits.
- Stage only the files each task lists — never `git add -A`.
- Commit trailer: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- Suite bar: `uv run python -m pytest tests/ -q` green (real-DB tests need `RUN_DB_INTEGRATION=1`; canonical bar is WITHOUT the flag).

## File structure

- Create `app/services/teaching_audit.py` — one module, five layers top-to-bottom: Pydantic call schemas → classification → protocol validation → pure prompt builders → loaders + orchestrator + report renderer.
- Create `scripts/teaching_audit.py` — thin CLI (argparse, asyncio.run, cost print), mirrors `scripts/golden_eval.py` bootstrap.
- Create `tests/services/test_teaching_audit.py` — all unit tests (pure functions + orchestrator with fake `run_phase`).

---

### Task 1: Schemas, classification, protocol validation

**Files:**
- Create: `app/services/teaching_audit.py`
- Test: `tests/services/test_teaching_audit.py`

**Interfaces:**
- Produces (later tasks rely on these exact names): `Objective`, `ExamQuestion`, `ExamSpec`, `StudentAnswer`, `StudentAnswers`, `QuestionGrade` (with `sitting: str` label), `GradedExam`, `ObjectiveCoverage`, `CoverageReport`, `TeachingAuditError`, `ObjectiveResult`, `AuditResult` (with `variant: str`), `_QUESTIONS_PER_OBJECTIVE = 2`, `classify_objective(pre_score, post_score, max_score, coverage) -> str`, `_validate_exam(exam) -> None`, `_validate_answers_and_grades(exam, answers_by_sitting: dict[str, StudentAnswers], graded) -> None`, `_validate_coverage(exam, coverage_report) -> None`, `validate_protocol(exam, answers_by_sitting, graded, coverage_report) -> None`, `aggregate(exam, graded, coverage_report, *, pre_label='pre', post_label='post') -> list[ObjectiveResult]`.
- Test helpers later tasks reuse from this file: `_exam_two_objectives()`, `_grade(qid, sitting, verdict)`, `_answers(qids)`, `_grades_for(qids, sittings)`, `_coverage(pairs)`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/services/test_teaching_audit.py
"""Unit tests for the teaching-equivalence audit (closed-book simulated-student exam)."""
import pytest

from app.services import teaching_audit as ta


# ---------- shared fixtures ----------

def _exam_two_objectives() -> ta.ExamSpec:
    return ta.ExamSpec(
        objectives=[
            ta.Objective(id="O1", statement="Parallelogramm ta'rifi"),
            ta.Objective(id="O2", statement="Diagonallar xossasi"),
        ],
        questions=[
            ta.ExamQuestion(id="Q1", objective_id="O1", question="q1", answer_key="a1"),
            ta.ExamQuestion(id="Q2", objective_id="O1", question="q2", answer_key="a2"),
            ta.ExamQuestion(id="Q3", objective_id="O2", question="q3", answer_key="a3"),
            ta.ExamQuestion(id="Q4", objective_id="O2", question="q4", answer_key="a4"),
        ],
    )


def _grade(qid, sitting, verdict):
    return ta.QuestionGrade(question_id=qid, sitting=sitting, verdict=verdict, evidence="e")


def _answers(qids):
    return ta.StudentAnswers(answers=[ta.StudentAnswer(question_id=q, answer="j") for q in qids])


def _grades_for(qids, sittings):
    return ta.GradedExam(grades=[_grade(q, s, "wrong") for q in qids for s in sittings])


def _coverage(pairs):
    return ta.CoverageReport(coverages=[
        ta.ObjectiveCoverage(objective_id=o, coverage=c, evidence="e") for o, c in pairs
    ])


# ---------- classify_objective ----------

def test_classify_already_known_when_pre_high():
    # pre 2/2 → the packet had nothing to add; already_known even if post dips
    assert ta.classify_objective(2.0, 1.0, 2.0, "taught") == "already_known"


def test_classify_learned_when_pre_low_post_high():
    assert ta.classify_objective(0.0, 2.0, 2.0, "taught") == "learned"
    assert ta.classify_objective(0.5, 1.5, 2.0, "mentioned") == "learned"


def test_classify_failed_absent_or_mentioned_is_not_taught():
    # 'mentioned' = named but never explained → the packet did NOT teach it
    assert ta.classify_objective(0.0, 0.5, 2.0, "absent") == "not_taught"
    assert ta.classify_objective(0.5, 0.5, 2.0, "mentioned") == "not_taught"


def test_classify_failed_taught_is_not_learnable():
    assert ta.classify_objective(0.0, 1.0, 2.0, "taught") == "not_learnable"


def test_classify_rejects_nonpositive_max():
    with pytest.raises(ValueError):
        ta.classify_objective(0.0, 0.0, 0.0, "taught")


# ---------- validation ----------

_QIDS = ["Q1", "Q2", "Q3", "Q4"]


def _ok_kwargs():
    exam = _exam_two_objectives()
    return dict(
        exam=exam,
        answers_by_sitting={"pre": _answers(_QIDS), "post": _answers(_QIDS)},
        graded=_grades_for(_QIDS, ("pre", "post")),
        coverage_report=_coverage([("O1", "taught"), ("O2", "absent")]),
    )


def test_validate_protocol_accepts_consistent_set():
    ta.validate_protocol(**_ok_kwargs())  # must not raise


def test_validate_protocol_rejects_each_violation():
    def check(**overrides):
        kw = {**_ok_kwargs(), **overrides}
        with pytest.raises(ta.TeachingAuditError):
            ta.validate_protocol(**kw)

    base = _exam_two_objectives()
    # empty exam
    check(exam=ta.ExamSpec(objectives=[], questions=[]))
    # duplicate objective id
    dup_o = base.model_copy(deep=True); dup_o.objectives[1].id = "O1"
    check(exam=dup_o)
    # duplicate question id
    dup_q = base.model_copy(deep=True); dup_q.questions[1].id = "Q1"
    check(exam=dup_q)
    # wrong question count per objective (3 on O1, 1 on O2)
    three = base.model_copy(deep=True); three.questions[2].objective_id = "O1"
    check(exam=three)
    # question references unknown objective
    unk = base.model_copy(deep=True); unk.questions[0].objective_id = "OX"
    check(exam=unk)
    # a sitting's answer id set mismatch (missing Q4)
    check(answers_by_sitting={"pre": _answers(["Q1", "Q2", "Q3"]), "post": _answers(_QIDS)})
    # duplicate answer id within a sitting
    check(answers_by_sitting={"pre": _answers(["Q1", "Q1", "Q3", "Q4"]), "post": _answers(_QIDS)})
    # missing grade pair
    missing = _grades_for(_QIDS, ("pre", "post")); missing.grades.pop()
    check(graded=missing)
    # duplicate grade pair
    dup_g = _grades_for(_QIDS, ("pre", "post")); dup_g.grades.append(_grade("Q1", "pre", "correct"))
    check(graded=dup_g)
    # coverage missing an objective
    check(coverage_report=_coverage([("O1", "taught")]))
    # coverage row for unknown objective
    check(coverage_report=_coverage([("O1", "taught"), ("O2", "absent"), ("OX", "taught")]))


def test_validate_exam_rejects_blank_content():
    # gate-2 smaller correction: blank statement / question / answer_key are unusable
    blank_stmt = _exam_two_objectives().model_copy(deep=True); blank_stmt.objectives[0].statement = "  "
    blank_q = _exam_two_objectives().model_copy(deep=True); blank_q.questions[0].question = ""
    blank_key = _exam_two_objectives().model_copy(deep=True); blank_key.questions[0].answer_key = "   "
    for bad in (blank_stmt, blank_q, blank_key):
        with pytest.raises(ta.TeachingAuditError):
            ta._validate_exam(bad)


# ---------- aggregate ----------

def test_aggregate_builds_per_objective_matrix():
    exam = _exam_two_objectives()
    graded = ta.GradedExam(grades=[
        _grade("Q1", "pre", "wrong"), _grade("Q2", "pre", "wrong"),
        _grade("Q1", "post", "correct"), _grade("Q2", "post", "correct"),
        _grade("Q3", "pre", "wrong"), _grade("Q4", "pre", "wrong"),
        _grade("Q3", "post", "wrong"), _grade("Q4", "post", "partial"),
    ])
    cov = _coverage([("O1", "taught"), ("O2", "absent")])
    results = ta.aggregate(exam, graded, cov)
    by_id = {r.objective_id: r for r in results}
    assert by_id["O1"].outcome == "learned"
    assert by_id["O1"].pre_score == 0.0 and by_id["O1"].post_score == 2.0
    assert by_id["O2"].outcome == "not_taught"
    assert by_id["O2"].post_score == 0.5 and by_id["O2"].coverage == "absent"


def test_aggregate_reads_the_requested_post_label():
    # the paired audit reads post_normal / post_control off ONE shared grade set
    exam = _exam_two_objectives()
    graded = ta.GradedExam(grades=(
        [_grade(q, "pre", "wrong") for q in _QIDS]
        + [_grade(q, "post_normal", "correct") for q in _QIDS]
        + [_grade(q, "post_control", "wrong") for q in _QIDS]
    ))
    cov = _coverage([("O1", "taught"), ("O2", "taught")])
    normal = ta.aggregate(exam, graded, cov, pre_label="pre", post_label="post_normal")
    control = ta.aggregate(exam, graded, cov, pre_label="pre", post_label="post_control")
    assert all(r.outcome == "learned" for r in normal)
    assert all(r.outcome == "not_learnable" for r in control)  # taught but failed
    # the pre baseline is identical because it is the SAME grade rows
    assert [r.pre_score for r in normal] == [r.pre_score for r in control]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/services/test_teaching_audit.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.teaching_audit'` (collection error).

- [ ] **Step 3: Write the implementation**

```python
# app/services/teaching_audit.py
"""Teaching-equivalence + learnability audit (closed-book simulated-student exam).

Measures whether a generated homework packet teaches what the TEXTBOOK lesson
teaches. Normal protocol per lesson (5 logical `agent.run_phase` calls, api):

  1. examiner derives objectives + a short-answer exam FROM THE TEXTBOOK PAGES
     ONLY (the packet never influences the exam — anti-circularity),
  2. a simulated student sits the exam closed-book with prior-grade knowledge
     only (pre-test = knowledge-leak control),
  3. the same student sits it again after "studying" ONLY the packet's
     student-facing deliverable (done, non-extract phases — the same filter as
     the real download / Notion exports),
  4. the examiner grades pre + post against the textbook-derived key,
  5. the examiner checks, per objective, whether the packet taught / mentioned /
     omitted it.

Sensitivity protocol (`paired_audit`, 7 logical calls): ONE exam + ONE pre-test,
then a post-test for the normal packet AND for a TRUE empty control packet, then
ONE combined grading call over all three sittings (so the pre-test baseline is
byte-identical across legs — a paired experiment), then a coverage call per leg.
A working instrument must report fewer 'learned' objectives on the empty-control
leg; the empty control (not phase-ablation) is the only valid negative control,
because the residual non-preview/flashcard phases carry explicit answer keys.

Per-objective outcomes: already_known · learned · not_taught (the packet never
actually teaches it — 'mentioned' counts as not taught) · not_learnable (taught
but not absorbable). Engagement is explicitly out of scope.

Standalone / offline like `golden_eval`: DB imports local, no pipeline/worker
coupling; no domain mutations — the only DB writes are the `agent_usages`
ledger rows every run_phase call records. Unlike golden_eval this module FAILS
LOUD (`TeachingAuditError`) — a dead or protocol-inconsistent scorer makes an
audit run worthless, so there are no degrade-to-pass paths and no silent
defaults.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel

from app.services import agent


class TeachingAuditError(RuntimeError):
    """Any unrecoverable audit failure (missing data, dead scorer, unparsed or
    protocol-inconsistent call output)."""


# --------------------------------------------------------------------------
# LLM call schemas (passed as `schema=` to agent.run_phase)
# --------------------------------------------------------------------------


class Objective(BaseModel):
    id: str  # "O1", "O2", …
    statement: str  # what the lesson teaches, in the lesson's language


class ExamQuestion(BaseModel):
    id: str  # "Q1", "Q2", …
    objective_id: str
    question: str
    answer_key: str
    grading_notes: str = ""


class ExamSpec(BaseModel):
    objectives: list[Objective]
    questions: list[ExamQuestion]


class StudentAnswer(BaseModel):
    question_id: str
    answer: str


class StudentAnswers(BaseModel):
    answers: list[StudentAnswer]


class QuestionGrade(BaseModel):
    question_id: str
    sitting: str  # free label — "pre" / "post" / "post_normal" / "post_control"
    verdict: Literal["correct", "partial", "wrong"]
    evidence: str


class GradedExam(BaseModel):
    grades: list[QuestionGrade]


class ObjectiveCoverage(BaseModel):
    objective_id: str
    coverage: Literal["taught", "mentioned", "absent"]
    evidence: str


class CoverageReport(BaseModel):
    coverages: list[ObjectiveCoverage]


# --------------------------------------------------------------------------
# Classification (pure)
# --------------------------------------------------------------------------

_VERDICT_SCORE = {"correct": 1.0, "partial": 0.5, "wrong": 0.0}
_KNOWN_FRACTION = 0.75  # ≥75% of max on a sitting counts as "passing" that objective
_QUESTIONS_PER_OBJECTIVE = 2

Outcome = Literal["already_known", "learned", "not_taught", "not_learnable"]


def classify_objective(
    pre_score: float, post_score: float, max_score: float, coverage: str
) -> Outcome:
    """Map one objective's (pre, post, coverage) to its outcome column.

    Thresholds are fractions of the objective's max score so the rule is
    independent of questions-per-objective. `already_known` wins over a post
    dip — the packet had nothing left to teach there. A failed objective that
    was 'absent' OR merely 'mentioned' (named but never explained) is
    `not_taught`; only a failed 'taught' objective is `not_learnable`.
    """
    if max_score <= 0:
        raise ValueError(f"max_score must be positive, got {max_score!r}")
    if pre_score / max_score >= _KNOWN_FRACTION:
        return "already_known"
    if post_score / max_score >= _KNOWN_FRACTION:
        return "learned"
    return "not_taught" if coverage in ("absent", "mentioned") else "not_learnable"


# --------------------------------------------------------------------------
# Protocol validation (pure, fail-loud)
# --------------------------------------------------------------------------


def _validate_exam(exam: ExamSpec) -> None:
    """Structural integrity of the examiner's output. Called right after the
    exam call (so a broken exam doesn't burn student/grader calls) and again
    inside validate_protocol."""
    obj_ids = [o.id for o in exam.objectives]
    if not obj_ids:
        raise TeachingAuditError("exam has no objectives")
    if any(not i.strip() for i in obj_ids) or len(set(obj_ids)) != len(obj_ids):
        raise TeachingAuditError(f"exam objective ids not unique/non-empty: {obj_ids}")
    if any(not o.statement.strip() for o in exam.objectives):
        raise TeachingAuditError("exam has an objective with a blank statement")
    q_ids = [q.id for q in exam.questions]
    if not q_ids:
        raise TeachingAuditError("exam has no questions")
    if any(not i.strip() for i in q_ids) or len(set(q_ids)) != len(q_ids):
        raise TeachingAuditError(f"exam question ids not unique/non-empty: {q_ids}")
    if any(not q.question.strip() or not q.answer_key.strip() for q in exam.questions):
        raise TeachingAuditError("exam has a question with blank text or a blank answer key")
    per_obj = Counter(q.objective_id for q in exam.questions)
    unknown = set(per_obj) - set(obj_ids)
    if unknown:
        raise TeachingAuditError(f"questions reference unknown objectives: {sorted(unknown)}")
    bad = {o: per_obj.get(o, 0) for o in obj_ids if per_obj.get(o, 0) != _QUESTIONS_PER_OBJECTIVE}
    if bad:
        raise TeachingAuditError(
            f"expected exactly {_QUESTIONS_PER_OBJECTIVE} questions per objective, got {bad}"
        )


def _validate_answers_and_grades(
    exam: ExamSpec, answers_by_sitting: dict[str, StudentAnswers], graded: GradedExam
) -> None:
    """Every sitting answers exactly the question set; grades are exactly one
    per (question, sitting-label) over the sittings present."""
    q_set = {q.id for q in exam.questions}
    for label, sitting in answers_by_sitting.items():
        ids = [a.question_id for a in sitting.answers]
        if len(set(ids)) != len(ids) or set(ids) != q_set:
            raise TeachingAuditError(
                f"'{label}' answer ids != question ids (got {sorted(ids)}, want {sorted(q_set)})"
            )
    pairs = [(g.question_id, g.sitting) for g in graded.grades]
    expected = {(q, s) for q in q_set for s in answers_by_sitting}
    if len(set(pairs)) != len(pairs) or set(pairs) != expected:
        raise TeachingAuditError(
            f"grades are not exactly one per (question, sitting) pair "
            f"(got {len(pairs)} rows over {len(set(pairs))} distinct pairs, want {len(expected)})"
        )


def _validate_coverage(exam: ExamSpec, coverage_report: CoverageReport) -> None:
    cov_ids = [c.objective_id for c in coverage_report.coverages]
    obj_set = {o.id for o in exam.objectives}
    if len(set(cov_ids)) != len(cov_ids) or set(cov_ids) != obj_set:
        raise TeachingAuditError(
            f"coverage rows != exactly one per objective (got {sorted(cov_ids)}, "
            f"want {sorted(obj_set)})"
        )


def validate_protocol(
    exam: ExamSpec,
    answers_by_sitting: dict[str, StudentAnswers],
    graded: GradedExam,
    coverage_report: CoverageReport,
) -> None:
    """Single-leg convenience: schemas enforce field types, THIS enforces the
    protocol. Any inconsistency raises — a malformed scorer output must never
    flow into a clean verdict (`all([])` would otherwise fabricate one)."""
    _validate_exam(exam)
    _validate_answers_and_grades(exam, answers_by_sitting, graded)
    _validate_coverage(exam, coverage_report)


@dataclass(frozen=True)
class ObjectiveResult:
    objective_id: str
    statement: str
    pre_score: float
    post_score: float
    max_score: float
    coverage: str
    outcome: Outcome


def aggregate(
    exam: ExamSpec,
    graded: GradedExam,
    coverage_report: CoverageReport,
    *,
    pre_label: str = "pre",
    post_label: str = "post",
) -> list[ObjectiveResult]:
    """Join grades + coverage back onto the exam's objectives, reading the pre
    baseline from `pre_label` and the post score from `post_label`.

    PRECONDITION: the relevant validation has passed — lookups here are total
    by construction (a KeyError would mean validation was skipped, a bug).
    """
    grade_by_key = {(g.question_id, g.sitting): g for g in graded.grades}
    coverage_by_id = {c.objective_id: c.coverage for c in coverage_report.coverages}

    results: list[ObjectiveResult] = []
    for obj in exam.objectives:
        questions = [q for q in exam.questions if q.objective_id == obj.id]
        pre_score = sum(_VERDICT_SCORE[grade_by_key[(q.id, pre_label)].verdict] for q in questions)
        post_score = sum(_VERDICT_SCORE[grade_by_key[(q.id, post_label)].verdict] for q in questions)
        max_score = float(len(questions))
        coverage = coverage_by_id[obj.id]
        results.append(
            ObjectiveResult(
                objective_id=obj.id,
                statement=obj.statement,
                pre_score=pre_score,
                post_score=post_score,
                max_score=max_score,
                coverage=coverage,
                outcome=classify_objective(pre_score, post_score, max_score, coverage),
            )
        )
    return results


@dataclass(frozen=True)
class AuditResult:
    """One audit leg: per-objective matrix + verdicts + the full evidence chain."""

    job_id: str
    lesson_title: str
    subject: str
    grade: Optional[str]
    language: str
    variant: str  # "full" (real packet) or "control" (empty negative-control packet)
    objectives: list[ObjectiveResult]
    # Full parsed artifacts (exam incl. keys, sittings' answers, grades w/
    # evidence, coverage w/ evidence) as model_dump() dicts — retained so a
    # human can audit the audit; NOT recoverable from agent_usages.
    artifacts: dict = field(default_factory=dict)
    # One entry per LLM call this result paid for: {"step","provider","model","usage"}.
    # Empty on paired-audit legs (PairedResult.calls carries the shared ledger).
    calls: list[dict] = field(default_factory=list)

    @property
    def learned_count(self) -> int:
        return sum(1 for r in self.objectives if r.outcome == "learned")

    @property
    def teaching_equivalent(self) -> bool:
        return all(r.outcome != "not_taught" for r in self.objectives)

    @property
    def learnable(self) -> bool:
        return all(r.outcome != "not_learnable" for r in self.objectives)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/services/test_teaching_audit.py -q`
Expected: 10 passed.

- [ ] **Step 5: Commit**

```bash
git add app/services/teaching_audit.py tests/services/test_teaching_audit.py
git commit -m "feat(teaching-audit): schemas + fail-loud protocol validation + label-aware aggregate"
```

---

### Task 2: Prompt builders (pure, isolation-enforcing)

**Files:**
- Modify: `app/services/teaching_audit.py` (append a "Prompt builders" section)
- Test: `tests/services/test_teaching_audit.py` (append)

**Interfaces:**
- Consumes: `ExamSpec`, `StudentAnswers`, `Objective`, `_QUESTIONS_PER_OBJECTIVE` from Task 1.
- Produces: `build_exam_prompt(*, textbook_text, lesson_title, subject, grade, language) -> str`, `build_pretest_prompt(*, exam, subject, grade, language) -> str`, `build_posttest_prompt(*, exam, packet_md, subject, grade, language) -> str`, `build_grading_prompt(*, exam, sittings: list[tuple[str, StudentAnswers]], language) -> str`, `build_coverage_prompt(*, objectives, packet_md, language) -> str`, plus `_format_questions(exam) -> str`, `_format_sitting(label, answers) -> str`.

The signatures ARE the isolation guarantee: the pre-test builder cannot leak the textbook or packet because it never receives them. Tests pin this with sentinel strings.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/services/test_teaching_audit.py

TEXTBOOK_SENTINEL = "XTEXTBOOKX parallelogramm sahifa matni"
PACKET_SENTINEL = "XPACKETX flashcards matni"


def _exam_min() -> ta.ExamSpec:
    return ta.ExamSpec(
        objectives=[ta.Objective(id="O1", statement="ta'rif")],
        questions=[
            ta.ExamQuestion(id="Q1", objective_id="O1", question="Savol bir?", answer_key="Javob bir"),
            ta.ExamQuestion(id="Q2", objective_id="O1", question="Savol ikki?", answer_key="Javob ikki"),
        ],
    )


def test_exam_prompt_contains_textbook_and_never_packet():
    p = ta.build_exam_prompt(
        textbook_text=TEXTBOOK_SENTINEL, lesson_title="Parallelogramm",
        subject="matematika", grade="8", language="uz",
    )
    assert TEXTBOOK_SENTINEL in p
    assert "Parallelogramm" in p
    assert "2" in p  # per-objective question count pinned in the instructions


def test_pretest_prompt_has_questions_but_no_textbook_no_packet_no_keys():
    p = ta.build_pretest_prompt(exam=_exam_min(), subject="matematika", grade="8", language="uz")
    assert "Savol bir?" in p and "Savol ikki?" in p
    assert TEXTBOOK_SENTINEL not in p and PACKET_SENTINEL not in p
    assert "Javob bir" not in p  # answer keys must never reach the student


def test_posttest_prompt_has_packet_and_questions_but_no_keys():
    p = ta.build_posttest_prompt(
        exam=_exam_min(), packet_md=PACKET_SENTINEL, subject="matematika", grade="8", language="uz",
    )
    assert PACKET_SENTINEL in p and "Savol bir?" in p
    assert "Javob bir" not in p


def test_grading_prompt_has_keys_and_all_sittings_but_no_packet():
    pre = ta.StudentAnswers(answers=[ta.StudentAnswer(question_id="Q1", answer="PRE-JAVOB")])
    post = ta.StudentAnswers(answers=[ta.StudentAnswer(question_id="Q1", answer="POST-JAVOB")])
    p = ta.build_grading_prompt(
        exam=_exam_min(), sittings=[("pre", pre), ("post_normal", post)], language="uz",
    )
    assert "Javob bir" in p and "PRE-JAVOB" in p and "POST-JAVOB" in p
    assert "pre" in p and "post_normal" in p  # sitting labels named for the grader
    assert PACKET_SENTINEL not in p


def test_coverage_prompt_has_objectives_and_packet():
    p = ta.build_coverage_prompt(
        objectives=_exam_min().objectives, packet_md=PACKET_SENTINEL, language="uz",
    )
    assert "ta'rif" in p and PACKET_SENTINEL in p
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/services/test_teaching_audit.py -q`
Expected: 5 new FAIL with `AttributeError: … has no attribute 'build_exam_prompt'` (Task 1 tests still pass).

- [ ] **Step 3: Write the implementation**

```python
# append to app/services/teaching_audit.py

# --------------------------------------------------------------------------
# Prompt builders (pure). The parameter lists ARE the isolation contract:
#   exam       ← textbook only (never the packet — anti-circularity)
#   pre-test   ← questions only (no textbook, no packet, no answer keys)
#   post-test  ← questions + one study document (no textbook, no answer keys)
#   grading    ← questions + keys + labeled sittings (no packet, no textbook)
#   coverage   ← objectives + one study document (no textbook)
# --------------------------------------------------------------------------


def _format_questions(exam: ExamSpec) -> str:
    return "\n".join(f"- [{q.id}] {q.question}" for q in exam.questions)


def _format_sitting(label: str, answers: StudentAnswers) -> str:
    lines = "\n".join(f"- [{a.question_id}] {a.answer}" for a in answers.answers)
    return f"--- SITTING '{label}' ANSWERS ---\n{lines}"


def _student_persona(subject: str, grade: Optional[str], language: str) -> str:
    g = grade or "?"
    return (
        f"You are simulating an AVERAGE grade-{g} student in Uzbekistan sitting a "
        f"{subject} exam, answering in language '{language}'.\n"
        f"HARD RULES:\n"
        f"- You know ONLY what an average student knows BEFORE this lesson is taught "
        f"(prior grades + earlier lessons), plus whatever study material this prompt "
        f"explicitly gives you.\n"
        f"- Closed book: no outside sources, no adult/model knowledge. If the material "
        f"you were given did not teach something, you DO NOT know it — answer exactly "
        f"'I don't know'.\n"
        f"- Answer briefly, like a real student: 1-3 sentences or the computation."
    )


def build_exam_prompt(
    *, textbook_text: str, lesson_title: str, subject: str, grade: Optional[str], language: str
) -> str:
    return (
        f"You are a strict {subject} examiner. Below are the OFFICIAL TEXTBOOK PAGES for "
        f"the grade-{grade or '?'} lesson '{lesson_title}' (language '{language}').\n\n"
        f"1. Derive the lesson's LEARNING OBJECTIVES — the 3 to 6 distinct things these "
        f"pages actually teach (concepts, definitions, methods, facts). Ignore exercises "
        f"and decoration. Ids O1, O2, … Every objective needs a non-empty statement.\n"
        f"2. Write EXACTLY {_QUESTIONS_PER_OBJECTIVE} short-answer exam questions per "
        f"objective, ids Q1, Q2, … (every id unique, every question non-empty). Prefer "
        f"LESSON-SPECIFIC facts, terms, methods and the textbook's own examples over "
        f"anything answerable by general reasoning — the exam must discriminate 'studied "
        f"this lesson' from 'is generally clever'.\n"
        f"3. For each question give a concise non-empty answer_key (and grading_notes when "
        f"partial credit is possible). Questions, keys and objectives in language "
        f"'{language}'.\n\n"
        f"--- TEXTBOOK PAGES ---\n{textbook_text}\n\n"
        f"Respond with the ExamSpec JSON schema only."
    )


def build_pretest_prompt(
    *, exam: ExamSpec, subject: str, grade: Optional[str], language: str
) -> str:
    return (
        f"{_student_persona(subject, grade, language)}\n\n"
        f"It is the day BEFORE this lesson is taught. You were given NO study material. "
        f"Answer every question; use 'I don't know' freely — guessing well is NOT the "
        f"goal, simulating a real pre-lesson student is.\n\n"
        f"--- EXAM QUESTIONS ---\n{_format_questions(exam)}\n\n"
        f"Respond with the StudentAnswers JSON schema only — EXACTLY one entry per "
        f"question id above, no extras."
    )


def build_posttest_prompt(
    *, exam: ExamSpec, packet_md: str, subject: str, grade: Optional[str], language: str
) -> str:
    return (
        f"{_student_persona(subject, grade, language)}\n\n"
        f"You just studied the homework packet below — it is your ONLY study material for "
        f"this lesson. You may use ONLY what this packet's text actually taught you, plus "
        f"prior-grade knowledge. If the packet did not teach it, you do not know it — "
        f"answer 'I don't know'.\n\n"
        f"--- STUDY PACKET ---\n{packet_md}\n\n"
        f"--- EXAM QUESTIONS ---\n{_format_questions(exam)}\n\n"
        f"Respond with the StudentAnswers JSON schema only — EXACTLY one entry per "
        f"question id above, no extras."
    )


def build_grading_prompt(
    *, exam: ExamSpec, sittings: list[tuple[str, StudentAnswers]], language: str
) -> str:
    keyed = "\n".join(
        f"- [{q.id}] {q.question}\n  KEY: {q.answer_key}"
        + (f"\n  NOTES: {q.grading_notes}" if q.grading_notes else "")
        for q in exam.questions
    )
    labels = ", ".join(label for label, _ in sittings)
    blocks = "\n\n".join(_format_sitting(label, ans) for label, ans in sittings)
    return (
        f"You are the examiner grading {len(sittings)} sittings of the same short-answer "
        f"exam (language '{language}'). Grade each answer STRICTLY against its KEY: "
        f"'correct' (matches the key's substance), 'partial' (half-right per the "
        f"key/notes), 'wrong' (anything else — 'I don't know' is wrong). Judge ONLY "
        f"against the key, never your own knowledge. A missing answer is wrong.\n"
        f"Return EXACTLY one grade per (question, sitting) pair. The sitting labels are: "
        f"{labels}. Use these exact label strings verbatim — no duplicates, no omissions, "
        f"one-line evidence each.\n\n"
        f"--- QUESTIONS + KEYS ---\n{keyed}\n\n{blocks}\n\n"
        f"Respond with the GradedExam JSON schema only."
    )


def build_coverage_prompt(
    *, objectives: list[Objective], packet_md: str, language: str
) -> str:
    objs = "\n".join(f"- [{o.id}] {o.statement}" for o in objectives)
    return (
        f"You are auditing a homework packet (language '{language}') against a lesson's "
        f"learning objectives. For EACH objective (exactly one row per objective id) "
        f"decide:\n"
        f"- 'taught': the packet EXPLAINS it with enough substance (definition + example "
        f"or worked usage) that a student could apply it,\n"
        f"- 'mentioned': the term/fact appears but is never actually explained,\n"
        f"- 'absent': it does not appear at all.\n"
        f"Quote the packet location as evidence ('flashcard 4', 'preview §2', or "
        f"'nowhere').\n\n"
        f"--- OBJECTIVES ---\n{objs}\n\n"
        f"--- PACKET ---\n{packet_md}\n\n"
        f"Respond with the CoverageReport JSON schema only."
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/services/test_teaching_audit.py -q`
Expected: 15 passed.

- [ ] **Step 5: Commit**

```bash
git add app/services/teaching_audit.py tests/services/test_teaching_audit.py
git commit -m "feat(teaching-audit): isolation-enforcing prompt builders (multi-sitting grader)"
```

---

### Task 3: Input loaders (job → textbook text + student-facing packet)

**Files:**
- Modify: `app/services/teaching_audit.py` (append a "Loaders" section)
- Test: `tests/services/test_teaching_audit.py` (append)

**Interfaces:**
- Produces: `AuditInputs` dataclass (`job_id, book_id, subject, grade, language, lesson_title, textbook_text, phases: list[tuple[str, str]]`), `filter_deliverable(rows: list[tuple[str, str, str]]) -> list[tuple[str, str]]` (rows are `(phase_name, status, output_md)`), `packet_md(phases) -> str`, `CONTROL_STUDY_MD: str` (the empty negative-control study document), `async load_audit_inputs(job_id) -> AuditInputs`.
- Consumes (verified upstream): `agent.read_page_range_text(pdf_path, page_start, page_end)` (`agent.py:1491`), `storage.book_pdf_path(book_id)`, `phase_outputs.list_for_job(session, job_id)` (`phase_outputs.py:91`), `PhaseOutput.status`, `TOCEntry.page_start/page_end/section_title`, `Book.subject/.grade`, `HomeworkJob.output_language`. Deliverable-filter precedent: `app/api/v1/jobs.py:87` (`_phase_zip`), `app/services/notion_archive.py:289`.

- [ ] **Step 1: Write the failing tests** (pure parts — `filter_deliverable` + `packet_md`; the DB loader mirrors `golden_eval._load_phases_from_db`, which has no unit test either, and is exercised by the Task 6 smoke)

```python
# append to tests/services/test_teaching_audit.py

def test_filter_deliverable_excludes_extract_and_non_done():
    # Gate-1 blocker 1: the student must see the STUDENT-FACING deliverable only —
    # same filter as jobs._phase_zip and the Notion export (done, non-extract, non-empty).
    rows = [
        ("extract", "done", "internal textbook summary"),
        ("case-based-preview", "done", "cbp matni"),
        ("flashcards", "failed", "half-written"),
        ("boss-arena", "done", "boss matni"),
        ("reflection", "done", "   "),
    ]
    assert ta.filter_deliverable(rows) == [
        ("case-based-preview", "cbp matni"),
        ("boss-arena", "boss matni"),
    ]


def test_packet_md_renders_sections_and_skips_empty():
    phases = [("case-based-preview", "cbp matni"), ("flashcards", ""), ("boss-arena", "boss matni")]
    md = ta.packet_md(phases)
    assert "## case-based-preview" in md and "cbp matni" in md
    assert "boss matni" in md
    assert "## flashcards" not in md  # empty output → omitted


def test_control_study_md_is_a_nonempty_no_material_sentinel():
    # gate-2 blocker 2: the negative control is a TRUE empty packet, not phase-ablation
    assert ta.CONTROL_STUDY_MD.strip()          # non-empty so the prompt block is well-formed
    assert "no study material" in ta.CONTROL_STUDY_MD.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/services/test_teaching_audit.py -q`
Expected: 3 new FAIL (`AttributeError: … 'filter_deliverable'`).

- [ ] **Step 3: Write the implementation**

```python
# append to app/services/teaching_audit.py

# --------------------------------------------------------------------------
# Loaders (DB imports local so the module stays import-light, mirroring
# golden_eval._load_phases_from_db; no domain writes)
# --------------------------------------------------------------------------

# The negative-control study document (gate-2 blocker 2). A TRUE empty packet:
# phase-ablation is invalid because the residual phases (memory-check, error
# detection, boss-arena, …) carry explicit answer keys and could still teach.
CONTROL_STUDY_MD = "(no study material was provided for this lesson.)"


def filter_deliverable(rows: list[tuple[str, str, str]]) -> list[tuple[str, str]]:
    """(phase_name, status, output_md) rows → the STUDENT-FACING deliverable.

    Mirrors the real exports' filter (`jobs._phase_zip`, notion_archive):
    done, non-`extract`, non-empty. The `extract` row is an internal
    textbook-derived summary — feeding it to the simulated student would let
    the post-test pass on textbook knowledge the packet never delivers.
    """
    return [
        (name, md)
        for name, status, md in rows
        if name != "extract" and status == "done" and (md or "").strip()
    ]


def packet_md(phases: list[tuple[str, str]]) -> str:
    """Render (phase_name, output_md) pairs as one study-packet document.
    Phases with empty output are omitted."""
    parts = [
        f"## {name}\n\n{md.strip()}"
        for name, md in phases
        if (md or "").strip()
    ]
    return "\n\n".join(parts)


@dataclass(frozen=True)
class AuditInputs:
    job_id: str
    book_id: str
    subject: str
    grade: Optional[str]
    language: str
    lesson_title: str
    textbook_text: str
    phases: list[tuple[str, str]]  # student-facing (phase_name, output_md), in phase order


async def load_audit_inputs(job_id: UUID | str) -> AuditInputs:
    """Load everything the audit needs for one job. FAILS LOUD on any gap —
    missing job/book/TOC row, NULL page range, unreadable PDF or empty page
    text, or a packet with no completed deliverable phase at all."""
    from app.db import SessionLocal
    from app.models import Book, HomeworkJob, TOCEntry
    from app.repositories import phase_outputs as phase_repo
    from app.services import storage

    async with SessionLocal() as session:
        job = await session.get(HomeworkJob, job_id)
        if job is None:
            raise TeachingAuditError(f"homework_jobs row {job_id!r} not found")
        book = await session.get(Book, job.book_id)
        if book is None:
            raise TeachingAuditError(f"books row {job.book_id!r} not found")
        toc = await session.get(TOCEntry, job.toc_entry_id)
        if toc is None:
            raise TeachingAuditError(f"toc_entries row {job.toc_entry_id!r} not found")
        if toc.page_start is None or toc.page_end is None:
            raise TeachingAuditError(
                f"TOC entry {toc.id} has no page range (page_start={toc.page_start!r}, "
                f"page_end={toc.page_end!r}) — cannot derive a textbook exam"
            )
        rows = await phase_repo.list_for_job(session, job.id)
        phases = filter_deliverable(
            [(r.phase_name, r.status, r.output_md or "") for r in rows]
        )
        subject, grade, language = job.subject, book.grade, job.output_language
        lesson_title, book_id = toc.section_title, str(job.book_id)
        page_start, page_end = toc.page_start, toc.page_end

    if not phases:
        raise TeachingAuditError(
            f"job {job_id} has no completed deliverable phases to audit"
        )

    pdf_path = storage.book_pdf_path(book_id)
    if not pdf_path.exists():
        raise TeachingAuditError(f"source PDF missing: {pdf_path}")
    textbook_text = agent.read_page_range_text(pdf_path, page_start, page_end)
    if not textbook_text:
        raise TeachingAuditError(
            f"pages {page_start}-{page_end} of {pdf_path.name} yielded no text "
            f"(image-only scan?) — cannot derive a textbook exam"
        )

    return AuditInputs(
        job_id=str(job_id),
        book_id=book_id,
        subject=subject,
        grade=grade,
        language=language,
        lesson_title=lesson_title,
        textbook_text=textbook_text,
        phases=phases,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/services/test_teaching_audit.py -q`
Expected: 18 passed.

- [ ] **Step 5: Commit**

```bash
git add app/services/teaching_audit.py tests/services/test_teaching_audit.py
git commit -m "feat(teaching-audit): loaders with student-deliverable filter + empty control sentinel"
```

---

### Task 4: Orchestrator — `audit_job` (5 calls) + `paired_audit` (7 calls, immutable baseline)

**Files:**
- Modify: `app/services/teaching_audit.py` (append)
- Test: `tests/services/test_teaching_audit.py` (append)

**Interfaces:**
- Consumes: everything above, plus `agent.run_phase` (`agent.py:926`) — call shape copied from `golden_eval._score_via_rubric` (`golden_eval.py:401`): `phase_name="__teach__"`, `homework_job_id=None`, `phase_output_id=None`, `schema=<BaseModel>`, `operation="teach:<step>"`, `transport=…`; result is `PhaseResult` with `.parsed` / `.usage`.
- Produces: `async audit_job(job_id, *, provider="gemini", examiner_model="gemini-2.5-pro", student_model="gemini-2.5-flash", transport="api", inputs=None) -> AuditResult`; `async paired_audit(job_id, *, same kwargs) -> PairedResult` where `PairedResult` is a frozen dataclass `{normal: AuditResult, control: AuditResult, calls: list[dict]}` with a dual `sensitivity_pass: bool` property (student-path AND coverage-path) and a `sensitivity_failures() -> list[str]` method; plus the internal `_grade_blinded(...)` helper. The `inputs` kwarg lets tests inject `AuditInputs` without a DB.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/services/test_teaching_audit.py


def _inputs() -> ta.AuditInputs:
    return ta.AuditInputs(
        job_id="job-1", book_id="book-1", subject="matematika", grade="8", language="uz",
        lesson_title="Parallelogramm", textbook_text=TEXTBOOK_SENTINEL,
        phases=[("case-based-preview", PACKET_SENTINEL), ("boss-arena", "boss matni")],
    )


class _R:
    def __init__(self, parsed):
        self.parsed = parsed
        self.usage = {"prompt_tokens": 10, "output_tokens": 5, "cached_tokens": 0,
                      "total_tokens": 15, "raw": {}}


def _fake_factory(captured, *, grade_meanings, control_learns=True, drop_grade=False,
                  broken_coverage=False):
    """Schema-dispatching fake: records every call, answers with a minimal
    PROTOCOL-CONSISTENT object for the requested schema.

    The grader is BLINDED, so it receives opaque labels s0, s1, … The fake
    emits grades on those opaque labels in the order of `grade_meanings` (the
    semantic meaning of each position: 'pre' → wrong; 'post'/'post_normal' →
    correct (student learns); 'post_control' → correct iff control_learns).
    The orchestrator remaps s{i} back to the semantic label afterwards.

    Coverage is prompt-aware: an empty-control packet correctly scores 'absent'
    (a working scorer), unless `broken_coverage` forces 'taught' to simulate a
    coverage-path failure."""
    exam = _exam_min()

    async def fake_run_phase(**kw):
        captured.append(kw)
        schema = kw["schema"]
        if schema is ta.ExamSpec:
            parsed = exam
        elif schema is ta.StudentAnswers:
            parsed = ta.StudentAnswers(answers=[
                ta.StudentAnswer(question_id="Q1", answer="j1"),
                ta.StudentAnswer(question_id="Q2", answer="j2"),
            ])
        elif schema is ta.GradedExam:
            grades = []
            for i, meaning in enumerate(grade_meanings):
                label = f"s{i}"  # opaque — the grader never sees the semantics
                for q in ("Q1", "Q2"):
                    if meaning == "pre":
                        v = "wrong"
                    elif meaning == "post_control":
                        v = "correct" if control_learns else "wrong"
                    else:  # post / post_normal
                        v = "correct"
                    grades.append(_grade(q, label, v))
            if drop_grade:
                grades.pop()
            parsed = ta.GradedExam(grades=grades)
        elif schema is ta.CoverageReport:
            is_control = ta.CONTROL_STUDY_MD in kw["phase_prompt"]
            cov = "taught" if broken_coverage else ("absent" if is_control else "taught")
            parsed = ta.CoverageReport(coverages=[
                ta.ObjectiveCoverage(objective_id="O1", coverage=cov, evidence="cbp"),
            ])
        else:  # pragma: no cover
            raise AssertionError(f"unexpected schema {schema}")
        return _R(parsed)

    return fake_run_phase


async def test_audit_job_five_calls_isolation_and_evidence(monkeypatch):
    captured = []
    monkeypatch.setattr(ta.agent, "run_phase",
                        _fake_factory(captured, grade_meanings=("pre", "post")))
    result = await ta.audit_job("job-1", inputs=_inputs())

    assert [kw["operation"] for kw in captured] == [
        "teach:exam", "teach:pretest", "teach:posttest", "teach:grade", "teach:coverage",
    ]
    by_op = {kw["operation"]: kw for kw in captured}
    assert TEXTBOOK_SENTINEL in by_op["teach:exam"]["phase_prompt"]
    assert PACKET_SENTINEL not in by_op["teach:exam"]["phase_prompt"]
    assert TEXTBOOK_SENTINEL not in by_op["teach:pretest"]["phase_prompt"]
    assert PACKET_SENTINEL not in by_op["teach:pretest"]["phase_prompt"]
    assert PACKET_SENTINEL in by_op["teach:posttest"]["phase_prompt"]
    assert PACKET_SENTINEL not in by_op["teach:grade"]["phase_prompt"]
    assert by_op["teach:exam"]["model"] == "gemini-2.5-pro"
    assert by_op["teach:pretest"]["model"] == "gemini-2.5-flash"
    assert by_op["teach:grade"]["model"] == "gemini-2.5-pro"
    assert all(kw["homework_job_id"] is None for kw in captured)
    assert all(kw["transport"] == "api" for kw in captured)

    assert result.variant == "full"
    assert [r.outcome for r in result.objectives] == ["learned"]
    assert len(result.calls) == 5 and result.calls[0]["step"] == "exam"
    assert result.artifacts["exam"]["questions"][0]["answer_key"] == "Javob bir"
    assert result.artifacts["graded"]["grades"][0]["evidence"] == "e"
    assert result.artifacts["coverage"]["coverages"][0]["evidence"] == "cbp"


async def test_paired_audit_seven_calls_empty_control_and_shared_baseline(monkeypatch):
    captured = []
    monkeypatch.setattr(
        ta.agent, "run_phase",
        _fake_factory(captured, grade_meanings=("pre", "post_normal", "post_control")),
    )
    paired = await ta.paired_audit("job-1", inputs=_inputs())

    assert [kw["operation"] for kw in captured] == [
        "teach:exam", "teach:pretest",
        "teach:posttest", "teach:posttest",   # normal packet, then empty control
        "teach:grade",                          # ONE combined grading call
        "teach:coverage", "teach:coverage",    # normal, then control
    ]
    posts = [kw["phase_prompt"] for kw in captured if kw["operation"] == "teach:posttest"]
    assert PACKET_SENTINEL in posts[0]                         # normal leg sees the packet
    assert PACKET_SENTINEL not in posts[1]                     # control leg: empty packet
    assert ta.CONTROL_STUDY_MD in posts[1]
    covs = [kw["phase_prompt"] for kw in captured if kw["operation"] == "teach:coverage"]
    assert PACKET_SENTINEL in covs[0] and PACKET_SENTINEL not in covs[1]

    assert paired.normal.variant == "full" and paired.control.variant == "control"
    assert paired.normal.artifacts["exam"] == paired.control.artifacts["exam"]
    # the SAME immutable grade set underlies both legs (remapped to semantics)
    assert paired.normal.artifacts["graded"] == paired.control.artifacts["graded"]
    assert len(paired.calls) == 7
    assert paired.normal.calls == [] and paired.control.calls == []
    # control also 'learns' in this fake → no drop → sensitivity FAILS on the student path
    assert paired.control.learned_count == paired.normal.learned_count
    assert paired.sensitivity_pass is False
    assert any("student-path" in r for r in paired.sensitivity_failures())


async def test_grading_call_is_blinded(monkeypatch):
    # gate-3 blocker 1: the grader must not see which sitting is the control
    captured = []
    monkeypatch.setattr(
        ta.agent, "run_phase",
        _fake_factory(captured, grade_meanings=("pre", "post_normal", "post_control")),
    )
    await ta.paired_audit("job-1", inputs=_inputs())
    grade_prompt = next(kw["phase_prompt"] for kw in captured if kw["operation"] == "teach:grade")
    assert "post_normal" not in grade_prompt and "post_control" not in grade_prompt
    assert "control" not in grade_prompt.lower() and "normal" not in grade_prompt.lower()
    assert "s0" in grade_prompt and "s1" in grade_prompt and "s2" in grade_prompt


async def test_paired_audit_grades_once_so_pre_baseline_is_immutable(monkeypatch):
    # gate-2 blocker 1: the grader is invoked exactly once; a divergent second
    # grading of the pre answers is structurally impossible.
    grader_calls = {"n": 0}
    exam = _exam_min()

    async def fake(**kw):
        schema = kw["schema"]
        if schema is ta.ExamSpec:
            parsed = exam
        elif schema is ta.StudentAnswers:
            parsed = ta.StudentAnswers(answers=[
                ta.StudentAnswer(question_id="Q1", answer="j"),
                ta.StudentAnswer(question_id="Q2", answer="j"),
            ])
        elif schema is ta.GradedExam:
            grader_calls["n"] += 1
            # would give a DIFFERENT pre verdict if ever called a second time
            pre_v = "wrong" if grader_calls["n"] == 1 else "correct"
            grades = []
            for i, meaning in enumerate(("pre", "post_normal", "post_control")):
                label = f"s{i}"  # blinded opaque labels
                for q in ("Q1", "Q2"):
                    grades.append(_grade(q, label, pre_v if meaning == "pre" else "correct"))
            parsed = ta.GradedExam(grades=grades)
        elif schema is ta.CoverageReport:
            cov = "absent" if ta.CONTROL_STUDY_MD in kw["phase_prompt"] else "taught"
            parsed = ta.CoverageReport(coverages=[
                ta.ObjectiveCoverage(objective_id="O1", coverage=cov, evidence="e")])
        return _R(parsed)

    monkeypatch.setattr(ta.agent, "run_phase", fake)
    paired = await ta.paired_audit("job-1", inputs=_inputs())
    assert grader_calls["n"] == 1  # graded ONCE — no divergent second grading possible
    assert ([r.pre_score for r in paired.normal.objectives]
            == [r.pre_score for r in paired.control.objectives])


async def test_paired_sensitivity_passes_when_control_fails(monkeypatch):
    captured = []
    monkeypatch.setattr(
        ta.agent, "run_phase",
        _fake_factory(captured, grade_meanings=("pre", "post_normal", "post_control"),
                      control_learns=False),
    )
    paired = await ta.paired_audit("job-1", inputs=_inputs())
    assert paired.normal.learned_count == 1 and paired.control.learned_count == 0
    # empty control correctly scores 'absent' coverage → both gates pass
    assert all(r.coverage == "absent" for r in paired.control.objectives)
    assert paired.sensitivity_pass is True
    assert paired.sensitivity_failures() == []


async def test_paired_sensitivity_fails_when_control_coverage_not_absent(monkeypatch):
    # gate-3 blocker 2: coverage is part of the instrument — an empty document
    # scored 'taught' is a broken scorer, must fail sensitivity even if learned drops.
    captured = []
    monkeypatch.setattr(
        ta.agent, "run_phase",
        _fake_factory(captured, grade_meanings=("pre", "post_normal", "post_control"),
                      control_learns=False, broken_coverage=True),
    )
    paired = await ta.paired_audit("job-1", inputs=_inputs())
    assert paired.normal.learned_count == 1 and paired.control.learned_count == 0  # student path OK
    assert paired.sensitivity_pass is False                                        # coverage path broke
    reasons = paired.sensitivity_failures()
    assert any("coverage-path" in r for r in reasons)
    assert not any("student-path" in r for r in reasons)


async def test_audit_job_fails_loud_on_unparsed_call(monkeypatch):
    async def dead(**kw):
        return _R(None)

    monkeypatch.setattr(ta.agent, "run_phase", dead)
    with pytest.raises(ta.TeachingAuditError, match="teach:exam"):
        await ta.audit_job("job-1", inputs=_inputs())


async def test_audit_job_fails_loud_on_protocol_violation(monkeypatch):
    captured = []
    monkeypatch.setattr(ta.agent, "run_phase",
                        _fake_factory(captured, grade_meanings=("pre", "post"), drop_grade=True))
    with pytest.raises(ta.TeachingAuditError):
        await ta.audit_job("job-1", inputs=_inputs())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/services/test_teaching_audit.py -q`
Expected: 8 new FAIL (`AttributeError: … 'audit_job'`).

- [ ] **Step 3: Write the implementation**

```python
# append to app/services/teaching_audit.py

# --------------------------------------------------------------------------
# Orchestrator
# --------------------------------------------------------------------------


async def _call(
    step: str,
    prompt: str,
    schema: type[BaseModel],
    *,
    provider: str,
    model: Optional[str],
    transport: str,
    calls: list[dict],
):
    """One structured audit call. Mirrors golden_eval._score_via_rubric's
    run_phase shape but FAILS LOUD instead of degrading — a dead scorer makes
    the whole audit worthless.

    NOTE: run_phase may record more than one `agent_usages` row for this call
    (a structured-output validation retry logs the failed attempt too,
    agent.py:1121). `usage` here is the successful final attempt only, so the
    summed $ cost can undercount retried attempts."""
    try:
        result = await agent.run_phase(
            provider=provider,
            model=model,
            phase_prompt=prompt,
            phase_name="__teach__",
            homework_job_id=None,
            phase_output_id=None,
            schema=schema,
            operation=f"teach:{step}",
            transport=transport,
        )
    except Exception as exc:
        raise TeachingAuditError(f"teach:{step} call failed: {exc}") from exc
    if result.parsed is None:
        raise TeachingAuditError(f"teach:{step} returned no parsed {schema.__name__}")
    calls.append(
        {"step": step, "provider": provider, "model": model, "usage": dict(result.usage or {})}
    )
    return result.parsed


async def _exam_and_pretest(data, *, provider, examiner_model, student_model, transport, calls):
    exam: ExamSpec = await _call(
        "exam",
        build_exam_prompt(
            textbook_text=data.textbook_text, lesson_title=data.lesson_title,
            subject=data.subject, grade=data.grade, language=data.language,
        ),
        ExamSpec,
        provider=provider, model=examiner_model, transport=transport, calls=calls,
    )
    _validate_exam(exam)  # fail before burning student/grader calls
    pre: StudentAnswers = await _call(
        "pretest",
        build_pretest_prompt(
            exam=exam, subject=data.subject, grade=data.grade, language=data.language,
        ),
        StudentAnswers,
        provider=provider, model=student_model, transport=transport, calls=calls,
    )
    return exam, pre


async def _posttest(data, exam, study_md, *, provider, student_model, transport, calls):
    return await _call(
        "posttest",
        build_posttest_prompt(
            exam=exam, packet_md=study_md,
            subject=data.subject, grade=data.grade, language=data.language,
        ),
        StudentAnswers,
        provider=provider, model=student_model, transport=transport, calls=calls,
    )


async def _coverage(data, exam, study_md, *, provider, examiner_model, transport, calls):
    return await _call(
        "coverage",
        build_coverage_prompt(objectives=exam.objectives, packet_md=study_md, language=data.language),
        CoverageReport,
        provider=provider, model=examiner_model, transport=transport, calls=calls,
    )


async def _grade_blinded(
    data, exam, semantic_sittings: list[tuple[str, StudentAnswers]],
    *, provider, examiner_model, transport, calls,
) -> GradedExam:
    """The ONE grading call, BLINDED (gate-3 blocker 1): the grader sees opaque
    labels s0, s1, … (never 'pre'/'post_normal'/'post_control'), so it cannot
    favor the real packet or mark down the control. We remap the returned
    sitting labels back to their semantics before validation/aggregation."""
    to_semantic = {f"s{i}": label for i, (label, _) in enumerate(semantic_sittings)}
    blinded = [(f"s{i}", ans) for i, (_, ans) in enumerate(semantic_sittings)]
    graded = await _call(
        "grade",
        build_grading_prompt(exam=exam, sittings=blinded, language=data.language),
        GradedExam,
        provider=provider, model=examiner_model, transport=transport, calls=calls,
    )
    try:
        return GradedExam(grades=[
            g.model_copy(update={"sitting": to_semantic[g.sitting]}) for g in graded.grades
        ])
    except KeyError as exc:
        raise TeachingAuditError(f"grader returned an unknown sitting label {exc}") from exc


def _result(data, *, variant, objectives, artifacts, calls) -> AuditResult:
    return AuditResult(
        job_id=data.job_id,
        lesson_title=data.lesson_title,
        subject=data.subject,
        grade=data.grade,
        language=data.language,
        variant=variant,
        objectives=objectives,
        artifacts=artifacts,
        calls=calls,
    )


async def audit_job(
    job_id: UUID | str,
    *,
    provider: str = "gemini",
    examiner_model: str = "gemini-2.5-pro",
    student_model: str = "gemini-2.5-flash",
    transport: str = "api",
    inputs: Optional[AuditInputs] = None,
) -> AuditResult:
    """Run the normal 5-call protocol for one job's packet."""
    data = inputs if inputs is not None else await load_audit_inputs(job_id)
    calls: list[dict] = []
    study = packet_md(data.phases)
    exam, pre = await _exam_and_pretest(
        data, provider=provider, examiner_model=examiner_model,
        student_model=student_model, transport=transport, calls=calls,
    )
    post = await _posttest(data, exam, study, provider=provider,
                           student_model=student_model, transport=transport, calls=calls)
    graded = await _grade_blinded(
        data, exam, [("pre", pre), ("post", post)],
        provider=provider, examiner_model=examiner_model, transport=transport, calls=calls,
    )
    coverage = await _coverage(data, exam, study, provider=provider,
                               examiner_model=examiner_model, transport=transport, calls=calls)
    validate_protocol(exam, {"pre": pre, "post": post}, graded, coverage)
    objectives = aggregate(exam, graded, coverage, pre_label="pre", post_label="post")
    artifacts = {
        "exam": exam.model_dump(), "pre": pre.model_dump(), "post": post.model_dump(),
        "graded": graded.model_dump(), "coverage": coverage.model_dump(),
    }
    return _result(data, variant="full", objectives=objectives, artifacts=artifacts, calls=calls)


@dataclass(frozen=True)
class PairedResult:
    """Sensitivity experiment: same exam, same pre-test, ONE shared grade set,
    two study documents (real packet vs empty control)."""

    normal: AuditResult
    control: AuditResult
    calls: list[dict]  # all 7 calls (legs carry calls=[]; this is the one ledger)

    @property
    def _student_path_ok(self) -> bool:
        """Fewer learned objectives on the empty control than on the real packet."""
        return self.control.learned_count < self.normal.learned_count

    @property
    def _coverage_path_ok(self) -> bool:
        """Every objective's coverage on the EMPTY control is 'absent' — a
        coverage scorer that finds teaching in a blank document is broken."""
        return all(r.coverage == "absent" for r in self.control.objectives)

    @property
    def sensitivity_pass(self) -> bool:
        """DUAL gate (gate-3 blocker 2): both the student path and the coverage
        path must hold, since coverage is part of the instrument."""
        return self._student_path_ok and self._coverage_path_ok

    def sensitivity_failures(self) -> list[str]:
        """Distinct, human-readable reasons — so a failure tells you WHICH path
        broke (student-path leakage vs coverage-path hallucination)."""
        reasons: list[str] = []
        if not self._student_path_ok:
            reasons.append(
                f"student-path leak: control learned={self.control.learned_count} is not < "
                f"normal learned={self.normal.learned_count} — a student given no material "
                f"still 'learned'"
            )
        if not self._coverage_path_ok:
            bad = [r.objective_id for r in self.control.objectives if r.coverage != "absent"]
            reasons.append(
                f"coverage-path failure: the empty control scored non-absent coverage on "
                f"{bad} — the coverage scorer hallucinates teaching"
            )
        return reasons


async def paired_audit(
    job_id: UUID | str,
    *,
    provider: str = "gemini",
    examiner_model: str = "gemini-2.5-pro",
    student_model: str = "gemini-2.5-flash",
    transport: str = "api",
    inputs: Optional[AuditInputs] = None,
) -> PairedResult:
    """7-call paired sensitivity audit. exam + pre-test ONCE; a post-test for
    the real packet and for the empty control; ONE combined grading call over
    {pre, post_normal, post_control} (so the pre baseline is immutable across
    legs); a coverage call per leg. Both legs aggregate off the one shared
    grade set, reading their own post label."""
    data = inputs if inputs is not None else await load_audit_inputs(job_id)
    calls: list[dict] = []
    study = packet_md(data.phases)
    exam, pre = await _exam_and_pretest(
        data, provider=provider, examiner_model=examiner_model,
        student_model=student_model, transport=transport, calls=calls,
    )
    post_normal = await _posttest(data, exam, study, provider=provider,
                                  student_model=student_model, transport=transport, calls=calls)
    post_control = await _posttest(data, exam, CONTROL_STUDY_MD, provider=provider,
                                   student_model=student_model, transport=transport, calls=calls)
    graded = await _grade_blinded(
        data, exam,
        [("pre", pre), ("post_normal", post_normal), ("post_control", post_control)],
        provider=provider, examiner_model=examiner_model, transport=transport, calls=calls,
    )
    cov_normal = await _coverage(data, exam, study, provider=provider,
                                 examiner_model=examiner_model, transport=transport, calls=calls)
    cov_control = await _coverage(data, exam, CONTROL_STUDY_MD, provider=provider,
                                  examiner_model=examiner_model, transport=transport, calls=calls)

    answers = {"pre": pre, "post_normal": post_normal, "post_control": post_control}
    _validate_answers_and_grades(exam, answers, graded)
    _validate_coverage(exam, cov_normal)
    _validate_coverage(exam, cov_control)

    graded_dump = graded.model_dump()
    normal = _result(
        data, variant="full",
        objectives=aggregate(exam, graded, cov_normal, pre_label="pre", post_label="post_normal"),
        artifacts={"exam": exam.model_dump(), "pre": pre.model_dump(),
                   "post": post_normal.model_dump(), "graded": graded_dump,
                   "coverage": cov_normal.model_dump()},
        calls=[],
    )
    control = _result(
        data, variant="control",
        objectives=aggregate(exam, graded, cov_control, pre_label="pre", post_label="post_control"),
        artifacts={"exam": exam.model_dump(), "pre": pre.model_dump(),
                   "post": post_control.model_dump(), "graded": graded_dump,
                   "coverage": cov_control.model_dump()},
        calls=[],
    )
    return PairedResult(normal=normal, control=control, calls=calls)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/services/test_teaching_audit.py -q`
Expected: 26 passed.

- [ ] **Step 5: Commit**

```bash
git add app/services/teaching_audit.py tests/services/test_teaching_audit.py
git commit -m "feat(teaching-audit): orchestrator — blinded grader + dual-gate paired sensitivity"
```

---

### Task 5: Report renderer + CLI script

**Files:**
- Modify: `app/services/teaching_audit.py` (append `render_markdown`, `result_to_dict`)
- Create: `scripts/teaching_audit.py`
- Test: `tests/services/test_teaching_audit.py` (append renderer tests)

**Interfaces:**
- Consumes: `AuditResult`, `PairedResult`, `ObjectiveResult`, `pricing.cost_usd(provider, model, usage)` (`pricing.py:83`).
- Produces: `render_markdown(result) -> str`, `result_to_dict(result) -> dict` (JSON-safe, INCLUDES `artifacts`), CLI `uv run python scripts/teaching_audit.py --job <id> [--sensitivity] [--out PATH] [--provider gemini] [--examiner-model gemini-2.5-pro] [--student-model gemini-2.5-flash] [--strict]`. **No transport flag — api only.**

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/services/test_teaching_audit.py

def _result_fixture() -> ta.AuditResult:
    return ta.AuditResult(
        job_id="job-1", lesson_title="Parallelogramm", subject="matematika", grade="8",
        language="uz", variant="full",
        objectives=[
            ta.ObjectiveResult("O1", "ta'rif", 0.0, 2.0, 2.0, "taught", "learned"),
            ta.ObjectiveResult("O2", "xossa", 0.0, 0.5, 2.0, "absent", "not_taught"),
        ],
        artifacts={"exam": {}, "pre": {}, "post": {}, "graded": {}, "coverage": {}},
        calls=[{"step": "exam", "provider": "gemini", "model": "gemini-2.5-pro",
                "usage": {"prompt_tokens": 10, "output_tokens": 5}}],
    )


def test_render_markdown_has_matrix_and_verdicts():
    md = ta.render_markdown(_result_fixture())
    assert "O1" in md and "learned" in md
    assert "not_taught" in md
    assert "teaching-equivalent: NO" in md and "learnable: YES" in md


def test_result_to_dict_retains_artifacts_and_roundtrips():
    d = ta.result_to_dict(_result_fixture())
    assert d["job_id"] == "job-1" and d["teaching_equivalent"] is False
    assert d["variant"] == "full"
    assert d["objectives"][1]["outcome"] == "not_taught"
    assert set(d["artifacts"]) == {"exam", "pre", "post", "graded", "coverage"}
    import json
    json.dumps(d)  # must be JSON-serializable
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/services/test_teaching_audit.py -q`
Expected: 2 new FAIL (`AttributeError: … 'render_markdown'`).

- [ ] **Step 3: Write the implementation**

```python
# append to app/services/teaching_audit.py

# --------------------------------------------------------------------------
# Report rendering
# --------------------------------------------------------------------------


def result_to_dict(result: AuditResult) -> dict:
    return {
        "job_id": result.job_id,
        "lesson_title": result.lesson_title,
        "subject": result.subject,
        "grade": result.grade,
        "language": result.language,
        "variant": result.variant,
        "teaching_equivalent": result.teaching_equivalent,
        "learnable": result.learnable,
        "learned_count": result.learned_count,
        "objectives": [
            {
                "objective_id": r.objective_id,
                "statement": r.statement,
                "pre_score": r.pre_score,
                "post_score": r.post_score,
                "max_score": r.max_score,
                "coverage": r.coverage,
                "outcome": r.outcome,
            }
            for r in result.objectives
        ],
        # full evidence chain — questions, keys, answers, grades, coverage —
        # so a human can audit the audit (not recoverable from agent_usages)
        "artifacts": dict(result.artifacts),
        "calls": [dict(c) for c in result.calls],
    }


def render_markdown(result: AuditResult) -> str:
    lines = [
        f"# Teaching audit — {result.lesson_title} "
        f"({result.subject}, grade {result.grade or '?'}, {result.language})"
        + (" [control]" if result.variant == "control" else ""),
        "",
        f"- teaching-equivalent: {'YES' if result.teaching_equivalent else 'NO'}",
        f"- learnable: {'YES' if result.learnable else 'NO'}",
        "",
        "| objective | statement | pre | post | coverage | outcome |",
        "|---|---|---|---|---|---|",
    ]
    for r in result.objectives:
        lines.append(
            f"| {r.objective_id} | {r.statement} | {r.pre_score:g}/{r.max_score:g} "
            f"| {r.post_score:g}/{r.max_score:g} | {r.coverage} | {r.outcome} |"
        )
    return "\n".join(lines)
```

```python
# scripts/teaching_audit.py
"""Closed-book simulated-student audit for one generated homework packet.

Usage:
  uv run python scripts/teaching_audit.py --job <job-id>
  uv run python scripts/teaching_audit.py --job <job-id> --sensitivity

Derives an exam from the TEXTBOOK lesson pages, sits a simulated closed-book
student before and after "studying" the packet, and reports the per-objective
matrix (already_known / learned / not_taught / not_learnable) + $ cost.
--sensitivity runs the PAIRED experiment (one exam + one pre-test + one grading
call shared; real packet vs empty control) and reports whether the instrument
detects the difference. All calls run transport=api (cli transport is retired).
Exit 0 always unless --strict (then 1 on a failed verdict / failed sensitivity).

Cost note: the printed $ sums the successful attempt of each logical call;
structured-output retries log extra agent_usages rows the CLI total does not
include, so real spend may be marginally higher.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

from app.services import pricing, teaching_audit as ta  # noqa: E402


def _cost(calls: list[dict]) -> float:
    return sum(
        pricing.cost_usd(c["provider"], c["model"], c["usage"])
        for c in calls
        if c.get("usage")
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--job", required=True, help="homework_jobs id of the packet to audit")
    p.add_argument("--sensitivity", action="store_true",
                   help="paired 7-call run: real packet vs empty control, shared exam+pretest+grade")
    p.add_argument("--out", default=None,
                   help="JSON report path (default var/teaching_audit/<job8>[-sensitivity].json)")
    p.add_argument("--provider", default="gemini")
    p.add_argument("--examiner-model", default="gemini-2.5-pro")
    p.add_argument("--student-model", default="gemini-2.5-flash")
    p.add_argument("--strict", action="store_true",
                   help="exit 1 when not teaching-equivalent/learnable (or sensitivity fails)")
    return p.parse_args(argv)


def _write_report(args: argparse.Namespace, payload: dict, suffix: str) -> None:
    out = pathlib.Path(args.out) if args.out else (
        _REPO_ROOT / "var" / "teaching_audit" / f"{args.job[:8]}{suffix}.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"report: {out}")


async def _run(args: argparse.Namespace) -> int:
    kw = dict(provider=args.provider, examiner_model=args.examiner_model,
              student_model=args.student_model)

    if args.sensitivity:
        paired = await ta.paired_audit(args.job, **kw)
        print(ta.render_markdown(paired.normal))
        print()
        print(ta.render_markdown(paired.control))
        verdict = "PASS" if paired.sensitivity_pass else "FAIL"
        print(f"\nsensitivity: {verdict} (learned real={paired.normal.learned_count} "
              f"control={paired.control.learned_count})")
        failures = paired.sensitivity_failures()
        for reason in failures:
            print(f"  - {reason}")
        cost = _cost(paired.calls)
        print(f"cost: ${cost:.4f} across {len(paired.calls)} logical calls "
              f"(examiner {args.examiner_model}, student {args.student_model}, api)")
        _write_report(args, {
            "normal": ta.result_to_dict(paired.normal),
            "control": ta.result_to_dict(paired.control),
            "sensitivity_pass": paired.sensitivity_pass,
            "sensitivity_failures": failures,
            "calls": [dict(c) for c in paired.calls],
            "cost_usd": cost,
        }, "-sensitivity")
        ok = paired.sensitivity_pass and paired.normal.teaching_equivalent and paired.normal.learnable
        return 1 if (args.strict and not ok) else 0

    result = await ta.audit_job(args.job, **kw)
    print(ta.render_markdown(result))
    cost = _cost(result.calls)
    print(f"\ncost: ${cost:.4f} across {len(result.calls)} logical calls "
          f"(examiner {args.examiner_model}, student {args.student_model}, api)")
    payload = ta.result_to_dict(result)
    payload["cost_usd"] = cost
    _write_report(args, payload, "")
    ok = result.teaching_equivalent and result.learnable
    return 1 if (args.strict and not ok) else 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        return asyncio.run(_run(args))
    except ta.TeachingAuditError as exc:
        raise SystemExit(f"teaching-audit failed: {exc}")


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests + the full suite**

Run: `uv run python -m pytest tests/services/test_teaching_audit.py -q` → 28 passed.
Run: `uv run python -m pytest tests/ -q` → green (same skip count as base).
Run: `uv run python scripts/teaching_audit.py --help` → usage text (no transport flag), exit 0.

- [ ] **Step 5: Commit**

```bash
git add app/services/teaching_audit.py scripts/teaching_audit.py tests/services/test_teaching_audit.py
git commit -m "feat(teaching-audit): evidence-retaining report + api-only CLI (--sensitivity, --strict)"
```

---

### Task 6: Acceptance gate — real bounded smoke = one paired sensitivity run

**Controller-run, not a subagent task.** This is the CLAUDE.md acceptance gate: real model calls over `transport=api`, bounded, cost-reported. Money rule: ONE lesson, ONE paired run = 7 logical calls (a few more `agent_usages` rows if any structured-output validation retries), expected ≈ $0.10–0.30.

- [ ] **Step 1:** Pick one `done` job on the production DB (`edu_copy`) whose TOC entry has a page range — e.g. from the audited G8-math golden set (`tests/golden/manifest.json`). Verify with:

```bash
docker exec edu-postgres psql -U edu -d edu_copy -c "SELECT j.id, t.section_title, t.page_start, t.page_end FROM homework_jobs j JOIN toc_entries t ON t.id = j.toc_entry_id WHERE j.status='done' AND t.page_start IS NOT NULL LIMIT 5;"
```

(Adjust container/DB names to the live environment; `DATABASE_URL` must point at the same DB when running the script.)

- [ ] **Step 2:** Capture the run's UTC start so the attribution query in Step 4 counts only THIS run's rows (the operation labels are shared across every historical run): run `date -u +%FT%TZ` and record the value as `<START>`. Then the paired run — `uv run python scripts/teaching_audit.py --job <id> --sensitivity`. Record: both matrices, verdicts + any failure reasons, the sensitivity line, $ cost. Sanity-read the JSON report's retained artifacts: objectives must be real lesson content (not generic); exam questions must be lesson-specific; the pre-test should be mostly wrong/"I don't know" — **if the pre-test aces the exam, the exam is not lesson-specific enough: iterate on `build_exam_prompt` wording and re-run once before judging the instrument.**

- [ ] **Step 3:** **The gate (DUAL):** `sensitivity: PASS` requires BOTH control `learned` < real `learned` (student path) AND every control-leg coverage row `absent` (coverage path) — causal by construction: same exam, same pre-test, one blinded shared grading call, only the study document differs. On FAIL the CLI prints which path broke: a *student-path* leak means the post-test is leaking latent knowledge (or the packet genuinely teaches nothing — check the normal matrix); a *coverage-path* failure means the coverage scorer found teaching in a blank document. Either way STOP, report to the user with the retained artifacts + failure reasons, do not ship as a working instrument.

- [ ] **Step 4:** Verify usage attribution for THIS run only (filter on the `<START>` timestamp from Step 2 — the labels alone would sum every historical `teach:*` call and the counts would be false after the first run). Expected operations are present, api, out-of-pipeline (there may be MORE than 7 rows if a structured call retried; the retry rows carry `success='f'`):

```bash
docker exec edu-postgres psql -U edu -d edu_copy -c "SELECT operation, count(*) FILTER (WHERE success) AS ok_rows, count(*) AS rows, bool_and(auth_mode='api') AS all_api, bool_and(homework_job_id IS NULL) AS all_null_job FROM agent_usages WHERE operation LIKE 'teach:%' AND created_at >= '<START>' GROUP BY operation ORDER BY operation;"
```

Expected `ok_rows`: `teach:exam` 1, `teach:pretest` 1, `teach:posttest` 2, `teach:grade` 1, `teach:coverage` 2; `all_api` and `all_null_job` both true.

- [ ] **Step 5:** Commit any prompt-wording fixes that came out of Step 2/3 (with test updates if builder contracts changed), message `fix(teaching-audit): calibrate exam/persona prompts from live smoke`.

---

### Task 7: Finish (docs + worklog + plan archive)

- [ ] **Step 1:** De-stale live-system docs: add the tool to `docs/CODE_MAP.md` (module + script, one-paragraph) and a short "Teaching-equivalence audit" subsection in `docs/HOW_IT_WORKS.md` under quality/eval (alongside the CQ-E harness description) — including the honest limitation: it measures teaching under simulation; classroom data remains the only ground truth. `README.md` only if it lists the scripts inventory.
- [ ] **Step 2:** Worklog entry in `docs/memory/MASTER_MEMORY.md` + row in `docs/memory/INDEX.md` (**check the INDEX tail for the next free number at write time** — they go stale mid-lane), including the measured smoke cost and the sensitivity-gate outcome.
- [ ] **Step 3:** `git mv docs/superpowers/plans/2026-07-17-teaching-equivalence-audit.md docs/superpowers/plans/shipped/`.
- [ ] **Step 4:** Rebase check per CLAUDE.md (`git fetch origin && git log HEAD..origin/Nggaev-v2` → rebase + re-run suite if moved), then push and open the PR for the external gate (GK2). Implementer does not self-merge.
- [ ] **Step 5: Commit** the docs batch:

```bash
git add docs/CODE_MAP.md docs/HOW_IT_WORKS.md docs/memory/MASTER_MEMORY.md docs/memory/INDEX.md docs/superpowers/plans/shipped/2026-07-17-teaching-equivalence-audit.md
git commit -m "docs(teaching-audit): worklog + code-map + how-it-works; archive plan"
```

---

## Out of scope (explicitly)

- **CQ-E integration** — the JSON report is CQ-E-consumable by shape, but wiring it in as an advisory dimension is a follow-up (wishlist line, not this plan).
- **Batch sweeps** — one job per invocation. Sweeping a whole batch is a `for` loop for a future operator run; automating it invites money-rule violations.
- **Real-student validation** — the tool measures teaching under simulation; classroom data remains the only ground truth (noted in HOW_IT_WORKS wording).
- **Engagement/motivation** — out of contract by user decision.
