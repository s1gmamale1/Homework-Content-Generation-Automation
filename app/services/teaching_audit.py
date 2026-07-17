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
