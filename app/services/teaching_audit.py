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
