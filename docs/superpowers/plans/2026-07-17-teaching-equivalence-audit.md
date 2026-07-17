# Teaching-Equivalence + Learnability Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An offline audit tool that measures whether a generated homework packet actually *teaches what the textbook lesson teaches* — via a closed-book simulated-student exam derived from the textbook, sat once before and once after "studying" only the packet.

**Architecture:** New standalone module `app/services/teaching_audit.py` (schemas, pure prompt builders, classification, orchestrator) + CLI `scripts/teaching_audit.py`, mirroring the `golden_eval` house pattern (offline, read-only DB via local imports, `agent.run_phase` with `schema=`, `homework_job_id=None`, per-step `operation=` labels). Five LLM calls per audited lesson: derive exam → pre-test sitting → post-test sitting → grade both → coverage check.

**Tech Stack:** Python 3.12, Pydantic schemas via `agent.run_phase(schema=…)`, `transport=api` (production transport), pytest with `monkeypatch.setattr(ta.agent, "run_phase", fake)`.

## Approach & key decisions

- **The contract being tested:** *a student with only prior-grade knowledge, given nothing but the packet, can pass an exam derived from the textbook lesson.* Two distinct failure columns fall out per objective: **`not_taught`** (objective absent from the packet — teaching-equivalence failure) vs **`not_learnable`** (objective present but the closed-book student still failed it — learnability failure). Engagement/motivation is explicitly out of scope (user decision, 2026-07-17).
- **The load-bearing anti-circularity rule:** the exam is derived from the **textbook pages only** — the packet never influences the exam. Today's judge grades the packet against itself/its extract, so silent under-coverage passes; this closes that hole (verified this session: no existing layer measures packet-vs-textbook).
- **Knowledge-leak mitigation** (the known failure mode of simulated students — the LLM answers from its own knowledge): (a) closed-book persona with an explicit "if the packet didn't teach it, you don't know it" rule; (b) the examiner is instructed to prefer *lesson-specific* facts/terms/methods over generally-derivable reasoning; (c) the **pre-test sitting is the control** — whatever leaks through the persona inflates pre and post equally, and only the delta is scored.
- **Standalone tool, not a golden_eval dimension** (user decision): the 5-call protocol + textbook-page access is too big for one rubric dimension of the frozen CQ-E harness. The JSON report is shaped so CQ-E can later consume it as an advisory dimension.
- **Short-answer, LLM-graded exam** (user decision): 2 questions per objective, examiner grades free-text answers against a textbook-derived key. MCQ was rejected — closed-book guessing (~25%) adds noise at per-objective granularity.
- **Fail loud, not degrade-to-pass:** unlike `golden_eval._score_via_rubric` (which degrades to protect baseline freezing), this is an audit instrument — a dead scorer makes the run worthless, so any call failure raises `TeachingAuditError`. Advisory by default: exit 0 with the report; `--strict` exits 1 on failure columns.
- **Instrument sensitivity is validated, not assumed:** `--gutted` mode runs the same 5-call protocol with `flashcards` + `case-based-preview` stripped from the study packet (post-test AND coverage see the gutted document). If the gutted run doesn't show fewer `learned` objectives than the normal run on the same lesson, the instrument is broken. It is an independent full run (fresh textbook-derived exam each time — acceptable for a coarse one-time gate; exam-sharing between runs is a YAGNI refinement). One-time operator validation in the acceptance gate.
- **Models:** examiner `gemini/gemini-2.5-pro` (needs to out-think the packet generator; same default as the CQ-E LLM scorer), student `gemini/gemini-2.5-flash` (persona fidelity, cheap), `transport=api`. All CLI-overridable. No self-grade concern: the examiner grades *student answers against the textbook key*, never the generator's output quality.
- **Load-bearing facts verified against code:** `agent.run_phase(provider, model, phase_prompt, phase_name, homework_job_id=None, phase_output_id=None, schema=…, operation=…, transport=…) -> PhaseResult(.parsed, .usage)` (`agent.py:926`); textbook text via `agent.read_page_range_text(pdf_path, page_start, page_end, margin=0)` (`agent.py:1491`) + `storage.book_pdf_path(book_id)`; `toc_entries.page_start/page_end` nullable (`app/models/toc_entry.py:25-26`); packet via `phase_outputs.list_for_job(session, job_id)` (`app/repositories/phase_outputs.py:91`); `Book.subject/.grade/.source_language` (`app/models/book.py:14-32`), `HomeworkJob.output_language` (`app/models/homework_job.py:30`); cost via `pricing.cost_usd(provider, model, usage)` (`pricing.py:83`); house test pattern `monkeypatch.setattr(ge.agent, "run_phase", fake)` (`tests/golden/test_llm_scorers.py:31`).

## Global Constraints

- **Money rule:** never mass-generate. The only real-model calls are the Task 6 acceptance smoke — ONE lesson, normal + gutted (10 calls total), cost reported in $.
- All real calls run `transport=api` (cli is operationally retired).
- Read-only DB access; no schema changes, no migrations, no pipeline/worker edits.
- Stage only the files each task lists — never `git add -A`.
- Commit trailer: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- Suite bar: `uv run python -m pytest tests/ -q` green (real-DB tests need `RUN_DB_INTEGRATION=1`; canonical bar is WITHOUT the flag).

## File structure

- Create `app/services/teaching_audit.py` — one module, four layers top-to-bottom: Pydantic call schemas → pure classification → pure prompt builders → loaders + orchestrator + report renderer.
- Create `scripts/teaching_audit.py` — thin CLI (argparse, asyncio.run, cost print), mirrors `scripts/golden_eval.py` bootstrap.
- Create `tests/services/test_teaching_audit.py` — all unit tests (pure functions + orchestrator with fake `run_phase`).

---

### Task 1: Schemas + classification logic

**Files:**
- Create: `app/services/teaching_audit.py`
- Test: `tests/services/test_teaching_audit.py`

**Interfaces:**
- Produces (later tasks rely on these exact names): `Objective`, `ExamQuestion`, `ExamSpec`, `StudentAnswer`, `StudentAnswers`, `QuestionGrade`, `GradedExam`, `ObjectiveCoverage`, `CoverageReport`, `TeachingAuditError`, `ObjectiveResult`, `AuditResult`, `classify_objective(pre_score, post_score, max_score, coverage) -> str`, `aggregate(exam, graded, coverage_report) -> tuple[list[ObjectiveResult], list[str]]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/services/test_teaching_audit.py
"""Unit tests for the teaching-equivalence audit (closed-book simulated-student exam)."""
import pytest

from app.services import teaching_audit as ta


# ---------- classify_objective ----------

def test_classify_already_known_when_pre_high():
    # pre 2/2 → the packet had nothing to add; outcome is already_known even if post dips
    assert ta.classify_objective(2.0, 1.0, 2.0, "taught") == "already_known"


def test_classify_learned_when_pre_low_post_high():
    assert ta.classify_objective(0.0, 2.0, 2.0, "taught") == "learned"
    assert ta.classify_objective(0.5, 1.5, 2.0, "mentioned") == "learned"


def test_classify_not_taught_when_failed_and_absent():
    assert ta.classify_objective(0.0, 0.5, 2.0, "absent") == "not_taught"


def test_classify_not_learnable_when_failed_but_covered():
    assert ta.classify_objective(0.0, 1.0, 2.0, "taught") == "not_learnable"
    assert ta.classify_objective(0.5, 0.5, 2.0, "mentioned") == "not_learnable"


def test_classify_rejects_nonpositive_max():
    with pytest.raises(ValueError):
        ta.classify_objective(0.0, 0.0, 0.0, "taught")


# ---------- aggregate ----------

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


def test_aggregate_builds_per_objective_matrix():
    exam = _exam_two_objectives()
    graded = ta.GradedExam(grades=[
        _grade("Q1", "pre", "wrong"), _grade("Q2", "pre", "wrong"),
        _grade("Q1", "post", "correct"), _grade("Q2", "post", "correct"),
        _grade("Q3", "pre", "wrong"), _grade("Q4", "pre", "wrong"),
        _grade("Q3", "post", "wrong"), _grade("Q4", "post", "partial"),
    ])
    cov = ta.CoverageReport(coverages=[
        ta.ObjectiveCoverage(objective_id="O1", coverage="taught", evidence="fc-3"),
        ta.ObjectiveCoverage(objective_id="O2", coverage="absent", evidence="nowhere"),
    ])
    results, warnings = ta.aggregate(exam, graded, cov)
    assert warnings == []
    by_id = {r.objective_id: r for r in results}
    assert by_id["O1"].outcome == "learned"
    assert by_id["O1"].pre_score == 0.0 and by_id["O1"].post_score == 2.0
    assert by_id["O2"].outcome == "not_taught"
    assert by_id["O2"].coverage == "absent"


def test_aggregate_missing_grade_counts_wrong_and_warns():
    exam = _exam_two_objectives()
    graded = ta.GradedExam(grades=[  # Q4/post missing entirely
        _grade("Q1", "pre", "correct"), _grade("Q2", "pre", "correct"),
        _grade("Q1", "post", "correct"), _grade("Q2", "post", "correct"),
        _grade("Q3", "pre", "wrong"), _grade("Q4", "pre", "wrong"),
        _grade("Q3", "post", "correct"),
    ])
    cov = ta.CoverageReport(coverages=[
        ta.ObjectiveCoverage(objective_id="O1", coverage="taught", evidence="e"),
        ta.ObjectiveCoverage(objective_id="O2", coverage="taught", evidence="e"),
    ])
    results, warnings = ta.aggregate(exam, graded, cov)
    assert any("Q4" in w and "post" in w for w in warnings)
    by_id = {r.objective_id: r for r in results}
    assert by_id["O2"].post_score == 1.0  # correct(1) + missing(0)


def test_aggregate_missing_coverage_warns_and_defaults_taught():
    # Conservative default: an uncovered objective_id is treated as "taught" so a
    # coverage-call miss can only produce not_learnable (never a spurious not_taught).
    exam = _exam_two_objectives()
    graded = ta.GradedExam(grades=[
        _grade(q, s, "wrong") for q in ("Q1", "Q2", "Q3", "Q4") for s in ("pre", "post")
    ])
    cov = ta.CoverageReport(coverages=[
        ta.ObjectiveCoverage(objective_id="O1", coverage="taught", evidence="e"),
    ])
    results, warnings = ta.aggregate(exam, graded, cov)
    assert any("O2" in w for w in warnings)
    assert {r.objective_id: r.outcome for r in results}["O2"] == "not_learnable"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/services/test_teaching_audit.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.teaching_audit'` (collection error).

- [ ] **Step 3: Write the implementation**

```python
# app/services/teaching_audit.py
"""Teaching-equivalence + learnability audit (closed-book simulated-student exam).

Measures whether a generated homework packet teaches what the TEXTBOOK lesson
teaches. Protocol per lesson (5 `agent.run_phase` calls, `transport=api`):

  1. examiner derives objectives + a short-answer exam FROM THE TEXTBOOK PAGES
     ONLY (the packet never influences the exam — anti-circularity),
  2. a simulated student sits the exam closed-book with prior-grade knowledge
     only (pre-test = knowledge-leak control),
  3. the same student sits it again after "studying" ONLY the packet,
  4. the examiner grades both sittings against the textbook-derived key,
  5. the examiner checks, per objective, whether the packet taught / mentioned /
     omitted it.

Per-objective outcomes: already_known · learned · not_taught (coverage failure)
· not_learnable (content present but not absorbable). Engagement is explicitly
out of scope.

Standalone / offline like `golden_eval`: read-only DB via local imports, no
pipeline/worker/schema coupling. Unlike golden_eval this module FAILS LOUD
(`TeachingAuditError`) — a dead scorer makes an audit run worthless.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel

from app.services import agent


class TeachingAuditError(RuntimeError):
    """Any unrecoverable audit failure (missing data, dead scorer, unparsed call)."""


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
    sitting: Literal["pre", "post"]
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

Outcome = Literal["already_known", "learned", "not_taught", "not_learnable"]


def classify_objective(
    pre_score: float, post_score: float, max_score: float, coverage: str
) -> Outcome:
    """Map one objective's (pre, post, coverage) to its outcome column.

    Thresholds are fractions of the objective's max score so the rule is
    independent of questions-per-objective. `already_known` wins over a post
    dip — the packet had nothing left to teach there.
    """
    if max_score <= 0:
        raise ValueError(f"max_score must be positive, got {max_score!r}")
    if pre_score / max_score >= _KNOWN_FRACTION:
        return "already_known"
    if post_score / max_score >= _KNOWN_FRACTION:
        return "learned"
    return "not_taught" if coverage == "absent" else "not_learnable"


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
    exam: ExamSpec, graded: GradedExam, coverage_report: CoverageReport
) -> tuple[list[ObjectiveResult], list[str]]:
    """Join grades + coverage back onto the exam's objectives.

    Missing grade → scored 0.0 ("wrong") + warning. Missing coverage row →
    conservative "taught" + warning (a coverage-call miss may only produce
    `not_learnable`, never a spurious `not_taught`).
    """
    warnings: list[str] = []
    grade_by_key = {(g.question_id, g.sitting): g for g in graded.grades}
    coverage_by_id = {c.objective_id: c.coverage for c in coverage_report.coverages}

    results: list[ObjectiveResult] = []
    for obj in exam.objectives:
        questions = [q for q in exam.questions if q.objective_id == obj.id]
        if not questions:
            warnings.append(f"objective {obj.id} has no questions — skipped")
            continue
        scores = {"pre": 0.0, "post": 0.0}
        for q in questions:
            for sitting in ("pre", "post"):
                grade = grade_by_key.get((q.id, sitting))
                if grade is None:
                    warnings.append(f"missing grade for {q.id}/{sitting} — counted wrong")
                    continue
                scores[sitting] += _VERDICT_SCORE[grade.verdict]
        coverage = coverage_by_id.get(obj.id)
        if coverage is None:
            warnings.append(f"missing coverage for {obj.id} — defaulted to 'taught'")
            coverage = "taught"
        max_score = float(len(questions))
        results.append(
            ObjectiveResult(
                objective_id=obj.id,
                statement=obj.statement,
                pre_score=scores["pre"],
                post_score=scores["post"],
                max_score=max_score,
                coverage=coverage,
                outcome=classify_objective(scores["pre"], scores["post"], max_score, coverage),
            )
        )
    return results, warnings


@dataclass(frozen=True)
class AuditResult:
    """One lesson's full audit: per-objective matrix + verdicts + call usages."""

    job_id: str
    lesson_title: str
    subject: str
    grade: Optional[str]
    language: str
    gutted: bool
    objectives: list[ObjectiveResult]
    warnings: list[str]
    # one entry per LLM call: {"step", "provider", "model", "usage"} — the CLI
    # sums pricing.cost_usd over these (mirrors golden_eval's DimensionScore.usage).
    calls: list[dict] = field(default_factory=list)

    @property
    def teaching_equivalent(self) -> bool:
        return all(r.outcome != "not_taught" for r in self.objectives)

    @property
    def learnable(self) -> bool:
        return all(r.outcome != "not_learnable" for r in self.objectives)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/services/test_teaching_audit.py -q`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add app/services/teaching_audit.py tests/services/test_teaching_audit.py
git commit -m "feat(teaching-audit): call schemas + per-objective classification"
```

---

### Task 2: Prompt builders (pure, isolation-enforcing)

**Files:**
- Modify: `app/services/teaching_audit.py` (append a "Prompt builders" section)
- Test: `tests/services/test_teaching_audit.py` (append)

**Interfaces:**
- Consumes: `ExamSpec`, `StudentAnswers`, `Objective` from Task 1.
- Produces: `build_exam_prompt(*, textbook_text, lesson_title, subject, grade, language) -> str`, `build_pretest_prompt(*, exam, subject, grade, language) -> str`, `build_posttest_prompt(*, exam, packet_md, subject, grade, language) -> str`, `build_grading_prompt(*, exam, pre, post, language) -> str`, `build_coverage_prompt(*, objectives, packet_md, language) -> str`, plus `_format_questions(exam) -> str`.

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
    # per-objective question count is pinned in the instructions
    assert "2" in p


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


def test_grading_prompt_has_keys_and_both_sittings_but_no_packet():
    pre = ta.StudentAnswers(answers=[ta.StudentAnswer(question_id="Q1", answer="pre-javob")])
    post = ta.StudentAnswers(answers=[ta.StudentAnswer(question_id="Q1", answer="post-javob")])
    p = ta.build_grading_prompt(exam=_exam_min(), pre=pre, post=post, language="uz")
    assert "Javob bir" in p and "pre-javob" in p and "post-javob" in p
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
#   post-test  ← questions + packet (no textbook, no answer keys)
#   grading    ← questions + keys + both sittings (no packet, no textbook)
#   coverage   ← objectives + packet (no textbook)
# --------------------------------------------------------------------------

_QUESTIONS_PER_OBJECTIVE = 2


def _format_questions(exam: ExamSpec) -> str:
    return "\n".join(f"- [{q.id}] {q.question}" for q in exam.questions)


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
        f"and decoration. Ids O1, O2, …\n"
        f"2. Write EXACTLY {_QUESTIONS_PER_OBJECTIVE} short-answer exam questions per "
        f"objective, ids Q1, Q2, … Prefer LESSON-SPECIFIC facts, terms, methods and the "
        f"textbook's own examples over anything answerable by general reasoning — the exam "
        f"must discriminate 'studied this lesson' from 'is generally clever'.\n"
        f"3. For each question give a concise answer_key (and grading_notes when partial "
        f"credit is possible). Questions, keys and objectives in language '{language}'.\n\n"
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
        f"Respond with the StudentAnswers JSON schema only (one entry per question id)."
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
        f"Respond with the StudentAnswers JSON schema only (one entry per question id)."
    )


def _format_sitting(label: str, answers: StudentAnswers) -> str:
    lines = "\n".join(f"- [{a.question_id}] {a.answer}" for a in answers.answers)
    return f"--- {label} SITTING ANSWERS ---\n{lines}"


def build_grading_prompt(
    *, exam: ExamSpec, pre: StudentAnswers, post: StudentAnswers, language: str
) -> str:
    keyed = "\n".join(
        f"- [{q.id}] {q.question}\n  KEY: {q.answer_key}"
        + (f"\n  NOTES: {q.grading_notes}" if q.grading_notes else "")
        for q in exam.questions
    )
    return (
        f"You are the examiner grading TWO sittings of the same short-answer exam "
        f"(language '{language}'). Grade each answer STRICTLY against its KEY: "
        f"'correct' (matches the key's substance), 'partial' (half-right per the "
        f"key/notes), 'wrong' (anything else — 'I don't know' is wrong). Judge ONLY "
        f"against the key, never your own knowledge. A missing answer is wrong.\n"
        f"Return one grade per (question, sitting) pair — sitting is 'pre' or 'post' — "
        f"with one-line evidence each.\n\n"
        f"--- QUESTIONS + KEYS ---\n{keyed}\n\n"
        f"{_format_sitting('PRE', pre)}\n\n{_format_sitting('POST', post)}\n\n"
        f"Respond with the GradedExam JSON schema only."
    )


def build_coverage_prompt(
    *, objectives: list[Objective], packet_md: str, language: str
) -> str:
    objs = "\n".join(f"- [{o.id}] {o.statement}" for o in objectives)
    return (
        f"You are auditing a homework packet (language '{language}') against a lesson's "
        f"learning objectives. For EACH objective decide:\n"
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
Expected: 13 passed.

- [ ] **Step 5: Commit**

```bash
git add app/services/teaching_audit.py tests/services/test_teaching_audit.py
git commit -m "feat(teaching-audit): isolation-enforcing prompt builders"
```

---

### Task 3: Input loaders (job → textbook text + packet markdown)

**Files:**
- Modify: `app/services/teaching_audit.py` (append a "Loaders" section)
- Test: `tests/services/test_teaching_audit.py` (append)

**Interfaces:**
- Produces: `AuditInputs` dataclass (`job_id, book_id, subject, grade, language, lesson_title, textbook_text, phases: list[tuple[str, str]]`), `packet_md(phases, *, exclude=frozenset()) -> str`, `GUTTED_PHASES = frozenset({"case-based-preview", "flashcards"})`, `async load_audit_inputs(job_id) -> AuditInputs`.
- Consumes (verified upstream): `agent.read_page_range_text(pdf_path, page_start, page_end)` (`agent.py:1491`), `storage.book_pdf_path(book_id)`, `phase_outputs.list_for_job(session, job_id)` (`phase_outputs.py:91`), `TOCEntry.page_start/page_end/section_title`, `Book.subject/.grade`, `HomeworkJob.output_language`.

- [ ] **Step 1: Write the failing tests** (pure part only — `packet_md`; the DB loader mirrors `golden_eval._load_phases_from_db`, which has no unit test either, and is exercised by the Task 6 smoke)

```python
# append to tests/services/test_teaching_audit.py

def test_packet_md_renders_sections_and_skips_empty():
    phases = [("case-based-preview", "cbp matni"), ("flashcards", ""), ("boss-arena", "boss matni")]
    md = ta.packet_md(phases)
    assert "## case-based-preview" in md and "cbp matni" in md
    assert "boss matni" in md
    assert "## flashcards" not in md  # empty output → omitted


def test_packet_md_gutted_excludes_teaching_phases():
    phases = [("case-based-preview", "cbp"), ("flashcards", "fc"), ("boss-arena", "boss")]
    md = ta.packet_md(phases, exclude=ta.GUTTED_PHASES)
    assert "cbp" not in md and "fc" not in md and "boss" in md
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/services/test_teaching_audit.py -q`
Expected: 2 new FAIL (`AttributeError: … 'packet_md'`).

- [ ] **Step 3: Write the implementation**

```python
# append to app/services/teaching_audit.py

# --------------------------------------------------------------------------
# Loaders (read-only; DB imports local so the module stays import-light,
# mirroring golden_eval._load_phases_from_db)
# --------------------------------------------------------------------------

GUTTED_PHASES = frozenset({"case-based-preview", "flashcards"})


def packet_md(phases: list[tuple[str, str]], *, exclude: frozenset = frozenset()) -> str:
    """Render (phase_name, output_md) pairs as one study-packet document.

    `exclude` powers `--gutted` sensitivity runs (drop the teaching phases and
    the instrument must show fewer 'learned' objectives, or it is broken).
    Phases with empty output are omitted.
    """
    parts = [
        f"## {name}\n\n{md.strip()}"
        for name, md in phases
        if name not in exclude and (md or "").strip()
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
    phases: list[tuple[str, str]]  # (phase_name, output_md), job's phase rows in order


async def load_audit_inputs(job_id: UUID | str) -> AuditInputs:
    """Load everything the audit needs for one job. FAILS LOUD on any gap —
    missing job/book/TOC row, NULL page range, unreadable PDF or empty page
    text, or a packet with no phase output at all."""
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
        phases = [(r.phase_name, r.output_md or "") for r in rows]
        subject, grade, language = job.subject, book.grade, job.output_language
        lesson_title, book_id = toc.section_title, str(job.book_id)
        page_start, page_end = toc.page_start, toc.page_end

    if not any(md.strip() for _, md in phases):
        raise TeachingAuditError(f"job {job_id} has no phase output to audit")

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
Expected: 15 passed.

- [ ] **Step 5: Commit**

```bash
git add app/services/teaching_audit.py tests/services/test_teaching_audit.py
git commit -m "feat(teaching-audit): audit-input loaders + gutted packet renderer"
```

---

### Task 4: Orchestrator `audit_job`

**Files:**
- Modify: `app/services/teaching_audit.py` (append)
- Test: `tests/services/test_teaching_audit.py` (append)

**Interfaces:**
- Consumes: everything above, plus `agent.run_phase` (`agent.py:926`) — call shape copied from `golden_eval._score_via_rubric` (`golden_eval.py:401`): `phase_name="__teach__"`, `homework_job_id=None`, `phase_output_id=None`, `schema=<BaseModel>`, `operation="teach:<step>"`, `transport=…`; result is `PhaseResult` with `.parsed` / `.usage`.
- Produces: `async audit_job(job_id, *, provider="gemini", examiner_model="gemini-2.5-pro", student_model="gemini-2.5-flash", transport="api", gutted=False, inputs=None) -> AuditResult`. The `inputs` kwarg lets tests (and gutted re-runs sharing loads) inject `AuditInputs` without a DB.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/services/test_teaching_audit.py


def _inputs() -> ta.AuditInputs:
    return ta.AuditInputs(
        job_id="job-1", book_id="book-1", subject="matematika", grade="8", language="uz",
        lesson_title="Parallelogramm", textbook_text=TEXTBOOK_SENTINEL,
        phases=[("case-based-preview", PACKET_SENTINEL), ("boss-arena", "boss matni")],
    )


def _fake_run_phase_factory(captured):
    """Returns a schema-dispatching fake: each call is recorded and answered
    with a minimal valid object of the requested schema."""
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
            parsed = ta.GradedExam(grades=[
                _grade("Q1", "pre", "wrong"), _grade("Q2", "pre", "wrong"),
                _grade("Q1", "post", "correct"), _grade("Q2", "post", "correct"),
            ])
        elif schema is ta.CoverageReport:
            parsed = ta.CoverageReport(coverages=[
                ta.ObjectiveCoverage(objective_id="O1", coverage="taught", evidence="cbp"),
            ])
        else:  # pragma: no cover
            raise AssertionError(f"unexpected schema {schema}")

        class R:
            pass

        r = R()
        r.parsed = parsed
        r.usage = {"prompt_tokens": 10, "output_tokens": 5, "cached_tokens": 0,
                   "total_tokens": 15, "raw": {}}
        return r

    return fake_run_phase


async def test_audit_job_five_calls_isolation_and_aggregation(monkeypatch):
    captured = []
    monkeypatch.setattr(ta.agent, "run_phase", _fake_run_phase_factory(captured))
    result = await ta.audit_job("job-1", inputs=_inputs())

    assert [kw["operation"] for kw in captured] == [
        "teach:exam", "teach:pretest", "teach:posttest", "teach:grade", "teach:coverage",
    ]
    by_op = {kw["operation"]: kw for kw in captured}
    # anti-circularity + closed-book isolation, checked at the CALL boundary:
    assert TEXTBOOK_SENTINEL in by_op["teach:exam"]["phase_prompt"]
    assert PACKET_SENTINEL not in by_op["teach:exam"]["phase_prompt"]
    assert TEXTBOOK_SENTINEL not in by_op["teach:pretest"]["phase_prompt"]
    assert PACKET_SENTINEL not in by_op["teach:pretest"]["phase_prompt"]
    assert PACKET_SENTINEL in by_op["teach:posttest"]["phase_prompt"]
    assert PACKET_SENTINEL not in by_op["teach:grade"]["phase_prompt"]
    # examiner vs student model routing:
    assert by_op["teach:exam"]["model"] == "gemini-2.5-pro"
    assert by_op["teach:pretest"]["model"] == "gemini-2.5-flash"
    assert by_op["teach:grade"]["model"] == "gemini-2.5-pro"
    # every call is out-of-pipeline + api:
    assert all(kw["homework_job_id"] is None for kw in captured)
    assert all(kw["transport"] == "api" for kw in captured)

    assert result.teaching_equivalent and result.learnable
    assert [r.outcome for r in result.objectives] == ["learned"]
    assert len(result.calls) == 5 and result.calls[0]["step"] == "exam"


async def test_audit_job_gutted_strips_teaching_phases_from_posttest(monkeypatch):
    captured = []
    monkeypatch.setattr(ta.agent, "run_phase", _fake_run_phase_factory(captured))
    result = await ta.audit_job("job-1", inputs=_inputs(), gutted=True)
    by_op = {kw["operation"]: kw for kw in captured}
    assert PACKET_SENTINEL not in by_op["teach:posttest"]["phase_prompt"]  # cbp stripped
    assert "boss matni" in by_op["teach:posttest"]["phase_prompt"]
    # coverage is judged against the SAME gutted packet the student saw
    assert PACKET_SENTINEL not in by_op["teach:coverage"]["phase_prompt"]
    assert result.gutted is True


async def test_audit_job_fails_loud_on_unparsed_call(monkeypatch):
    async def dead(**kw):
        class R:
            parsed = None
            usage = {}
        return R()

    monkeypatch.setattr(ta.agent, "run_phase", dead)
    with pytest.raises(ta.TeachingAuditError, match="teach:exam"):
        await ta.audit_job("job-1", inputs=_inputs())
```

Note: `pyproject.toml` sets `asyncio_mode = "auto"` — async tests need NO marker.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/services/test_teaching_audit.py -q`
Expected: 3 new FAIL (`AttributeError: … 'audit_job'`).

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
    the whole audit worthless."""
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


async def audit_job(
    job_id: UUID | str,
    *,
    provider: str = "gemini",
    examiner_model: str = "gemini-2.5-pro",
    student_model: str = "gemini-2.5-flash",
    transport: str = "api",
    gutted: bool = False,
    inputs: Optional[AuditInputs] = None,
) -> AuditResult:
    """Run the full 5-call protocol for one job's packet.

    `gutted=True` strips GUTTED_PHASES from the study packet (post-test AND
    coverage see the same gutted document) — the instrument-sensitivity mode:
    a working instrument must report fewer 'learned' objectives than the
    normal run on the same lesson.
    """
    data = inputs if inputs is not None else await load_audit_inputs(job_id)
    study_md = packet_md(data.phases, exclude=GUTTED_PHASES if gutted else frozenset())
    calls: list[dict] = []

    exam: ExamSpec = await _call(
        "exam",
        build_exam_prompt(
            textbook_text=data.textbook_text, lesson_title=data.lesson_title,
            subject=data.subject, grade=data.grade, language=data.language,
        ),
        ExamSpec,
        provider=provider, model=examiner_model, transport=transport, calls=calls,
    )
    if not exam.objectives or not exam.questions:
        raise TeachingAuditError("examiner returned an empty exam")

    pre: StudentAnswers = await _call(
        "pretest",
        build_pretest_prompt(
            exam=exam, subject=data.subject, grade=data.grade, language=data.language,
        ),
        StudentAnswers,
        provider=provider, model=student_model, transport=transport, calls=calls,
    )
    post: StudentAnswers = await _call(
        "posttest",
        build_posttest_prompt(
            exam=exam, packet_md=study_md,
            subject=data.subject, grade=data.grade, language=data.language,
        ),
        StudentAnswers,
        provider=provider, model=student_model, transport=transport, calls=calls,
    )
    graded: GradedExam = await _call(
        "grade",
        build_grading_prompt(exam=exam, pre=pre, post=post, language=data.language),
        GradedExam,
        provider=provider, model=examiner_model, transport=transport, calls=calls,
    )
    coverage: CoverageReport = await _call(
        "coverage",
        build_coverage_prompt(
            objectives=exam.objectives, packet_md=study_md, language=data.language,
        ),
        CoverageReport,
        provider=provider, model=examiner_model, transport=transport, calls=calls,
    )

    objectives, warnings = aggregate(exam, graded, coverage)
    return AuditResult(
        job_id=data.job_id,
        lesson_title=data.lesson_title,
        subject=data.subject,
        grade=data.grade,
        language=data.language,
        gutted=gutted,
        objectives=objectives,
        warnings=warnings,
        calls=calls,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/services/test_teaching_audit.py -q`
Expected: 18 passed.

- [ ] **Step 5: Commit**

```bash
git add app/services/teaching_audit.py tests/services/test_teaching_audit.py
git commit -m "feat(teaching-audit): 5-call audit orchestrator (fail-loud, gutted mode)"
```

---

### Task 5: Report renderer + CLI script

**Files:**
- Modify: `app/services/teaching_audit.py` (append `render_markdown`, `result_to_dict`)
- Create: `scripts/teaching_audit.py`
- Test: `tests/services/test_teaching_audit.py` (append renderer tests)

**Interfaces:**
- Consumes: `AuditResult`, `ObjectiveResult`, `pricing.cost_usd(provider, model, usage)` (`pricing.py:83`).
- Produces: `render_markdown(result) -> str`, `result_to_dict(result) -> dict` (JSON-safe), CLI `uv run python scripts/teaching_audit.py --job <id> [--gutted] [--out PATH] [--provider gemini] [--examiner-model gemini-2.5-pro] [--student-model gemini-2.5-flash] [--transport api] [--strict]`.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/services/test_teaching_audit.py

def _result() -> ta.AuditResult:
    return ta.AuditResult(
        job_id="job-1", lesson_title="Parallelogramm", subject="matematika", grade="8",
        language="uz", gutted=False,
        objectives=[
            ta.ObjectiveResult("O1", "ta'rif", 0.0, 2.0, 2.0, "taught", "learned"),
            ta.ObjectiveResult("O2", "xossa", 0.0, 0.5, 2.0, "absent", "not_taught"),
        ],
        warnings=["missing grade for Q4/post — counted wrong"],
        calls=[{"step": "exam", "provider": "gemini", "model": "gemini-2.5-pro",
                "usage": {"prompt_tokens": 10, "output_tokens": 5}}],
    )


def test_render_markdown_has_matrix_verdicts_and_warnings():
    md = ta.render_markdown(_result())
    assert "O1" in md and "learned" in md
    assert "not_taught" in md
    assert "teaching-equivalent: NO" in md and "learnable: YES" in md
    assert "Q4/post" in md


def test_result_round_trips_to_dict():
    d = ta.result_to_dict(_result())
    assert d["job_id"] == "job-1" and d["teaching_equivalent"] is False
    assert d["objectives"][1]["outcome"] == "not_taught"
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
        "gutted": result.gutted,
        "teaching_equivalent": result.teaching_equivalent,
        "learnable": result.learnable,
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
        "warnings": list(result.warnings),
        "calls": [dict(c) for c in result.calls],
    }


def render_markdown(result: AuditResult) -> str:
    lines = [
        f"# Teaching audit — {result.lesson_title} "
        f"({result.subject}, grade {result.grade or '?'}, {result.language})"
        + (" [GUTTED]" if result.gutted else ""),
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
    if result.warnings:
        lines += ["", "**Warnings:**"] + [f"- {w}" for w in result.warnings]
    return "\n".join(lines)
```

```python
# scripts/teaching_audit.py
"""Closed-book simulated-student audit for one generated homework packet.

Usage:
  uv run python scripts/teaching_audit.py --job <job-id>
  uv run python scripts/teaching_audit.py --job <job-id> --gutted   # sensitivity run

Derives an exam from the TEXTBOOK lesson pages, sits a simulated closed-book
student before and after "studying" the packet, and reports the per-objective
matrix (already_known / learned / not_taught / not_learnable) + $ cost.
Exit 0 always unless --strict (then 1 when not teaching-equivalent/learnable).
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


def _total_cost(result: ta.AuditResult) -> float:
    return sum(
        pricing.cost_usd(c["provider"], c["model"], c["usage"])
        for c in result.calls
        if c.get("usage")
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--job", required=True, help="homework_jobs id of the packet to audit")
    p.add_argument("--gutted", action="store_true",
                   help="strip flashcards + case-based-preview (instrument-sensitivity run)")
    p.add_argument("--out", default=None,
                   help="JSON report path (default var/teaching_audit/<job8>[-gutted].json)")
    p.add_argument("--provider", default="gemini")
    p.add_argument("--examiner-model", default="gemini-2.5-pro")
    p.add_argument("--student-model", default="gemini-2.5-flash")
    p.add_argument("--transport", default="api", choices=["api", "cli"])
    p.add_argument("--strict", action="store_true",
                   help="exit 1 when the packet is not teaching-equivalent or not learnable")
    return p.parse_args(argv)


async def _run(args: argparse.Namespace) -> int:
    result = await ta.audit_job(
        args.job,
        provider=args.provider,
        examiner_model=args.examiner_model,
        student_model=args.student_model,
        transport=args.transport,
        gutted=args.gutted,
    )
    print(ta.render_markdown(result))
    cost = _total_cost(result)
    print(f"\ncost: ${cost:.4f} across {len(result.calls)} calls "
          f"(examiner {args.examiner_model}, student {args.student_model}, {args.transport})")

    suffix = "-gutted" if args.gutted else ""
    out = pathlib.Path(args.out) if args.out else (
        _REPO_ROOT / "var" / "teaching_audit" / f"{args.job[:8]}{suffix}.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = ta.result_to_dict(result)
    payload["cost_usd"] = cost
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"report: {out}")

    if args.strict and not (result.teaching_equivalent and result.learnable):
        return 1
    return 0


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

Run: `uv run python -m pytest tests/services/test_teaching_audit.py -q` → 20 passed.
Run: `uv run python -m pytest tests/ -q` → green (same skip count as base).
Run: `uv run python scripts/teaching_audit.py --help` → usage text, exit 0.

- [ ] **Step 5: Commit**

```bash
git add app/services/teaching_audit.py scripts/teaching_audit.py tests/services/test_teaching_audit.py
git commit -m "feat(teaching-audit): report renderer + CLI (--gutted, --strict, cost print)"
```

---

### Task 6: Acceptance gate — real bounded smoke + instrument-sensitivity validation

**Controller-run, not a subagent task.** This is the CLAUDE.md acceptance gate: a real model call over `transport=api`, bounded, cost-reported. Money rule: ONE lesson, two runs (normal + gutted) = 10 calls total, expected ≈ $0.10–0.30.

- [ ] **Step 1:** Pick one `done` job on the production DB (`edu_copy`) whose TOC entry has a page range — e.g. from the audited G8-math golden set (`tests/golden/manifest.json` has job ids with `source_pages`). Verify with:

```bash
docker exec edu-postgres psql -U edu -d edu_copy -c "SELECT j.id, t.section_title, t.page_start, t.page_end FROM homework_jobs j JOIN toc_entries t ON t.id = j.toc_entry_id WHERE j.status='done' AND t.page_start IS NOT NULL LIMIT 5;"
```

(Adjust container/DB names to the live environment; DATABASE_URL must point at the same DB when running the script.)

- [ ] **Step 2:** Normal run — `uv run python scripts/teaching_audit.py --job <id>`. Record: the matrix, both verdicts, warnings, $ cost. Sanity-read the JSON report: objectives must be real lesson content (not generic), pre-test should show mostly wrong/idk (if the pre-test aces everything, the exam is not lesson-specific enough — iterate on `build_exam_prompt` wording before proceeding).

- [ ] **Step 3:** Sensitivity run — `uv run python scripts/teaching_audit.py --job <id> --gutted`. **The gate:** gutted `learned` count < normal `learned` count. If not, the instrument can't distinguish a good packet from a gutted one — STOP, report to the user, do not ship as a working instrument.

- [ ] **Step 4:** Verify usage attribution: the run wrote `agent_usages` rows with `operation IN ('teach:exam','teach:pretest','teach:posttest','teach:grade','teach:coverage')`, `auth_mode='api'`, `homework_job_id IS NULL`.

```bash
docker exec edu-postgres psql -U edu -d edu_copy -c "SELECT operation, provider, model_name, auth_mode, success FROM agent_usages WHERE operation LIKE 'teach:%' ORDER BY created_at DESC LIMIT 12;"
```

- [ ] **Step 5:** Commit any prompt-wording fixes that came out of Step 2/3 (with test updates if builder contracts changed), message `fix(teaching-audit): calibrate exam/persona prompts from live smoke`.

---

### Task 7: Finish (docs + worklog + plan archive)

- [ ] **Step 1:** De-stale live-system docs: add the tool to `docs/CODE_MAP.md` (module + script, one-paragraph) and a short "Teaching-equivalence audit" subsection in `docs/HOW_IT_WORKS.md` under quality/eval (alongside the CQ-E harness description). `README.md` only if it lists the scripts inventory.
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
