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

import hashlib
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
_MIN_OBJECTIVES = 3  # the examiner prompt declares "3 to 6" objectives; enforced fail-loud
_MAX_OBJECTIVES = 6

# Extra pages read on each side of the TOC's recorded range.
#
# `toc_entries.page_start/page_end` hold the book's PRINTED page numbers, while
# `read_page_range_text` slices PHYSICAL PDF pages. Front matter makes these
# disagree on some books, so a bare slice can land on a NEIGHBOURING lesson and
# the audit then issues a confident verdict about the wrong lesson
# (teaching-audit-page-offset-1; specimen book a92e62ae, a G9 history text whose
# printed numbers run ahead of its physical pages).
#
# Deterministic repair was measured and rejected: on that specimen only 19 of 35
# section titles are findable in extracted page text at all (headings are styled,
# split across lines, or image-only), so a title-presence check would falsely cry
# "offset" on ~46% of correct slices; and the implied offsets scatter
# (-3x7, -2x5, -1x1, +1x5, +2x1 — modal -3 at just 37% agreement), so a single
# per-book correction would mis-slice too. Instead the window is widened here and
# the EXAMINER is anchored to the lesson title (see `build_exam_prompt`), because a
# model reading the page recognizes a heading that string matching cannot. If the
# titled lesson is absent from the window the examiner returns zero objectives and
# `_validate_exam` fails loud — correct-or-loud, never confidently wrong.
_PAGE_WINDOW_MARGIN = 4  # covers the observed -3..+2 spread with slack

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


def _validate_objective_count(exam: ExamSpec) -> None:
    """Enforce the examiner prompt's declared 3-6 objective bound (gate-4 review).
    Enforced at the examiner-output boundary (`_exam_and_pretest`), not inside
    the structural `_validate_exam`/`validate_protocol` consistency checks (which
    stay meaningful for any objective count). A 1-objective response omits most of
    a lesson yet would otherwise produce a clean teaching-equivalence verdict; a
    runaway count is equally a malformed exam. Fail loud rather than under-measure."""
    n = len(exam.objectives)
    if not (_MIN_OBJECTIVES <= n <= _MAX_OBJECTIVES):
        raise TeachingAuditError(
            f"examiner returned {n} objectives; expected {_MIN_OBJECTIVES}-{_MAX_OBJECTIVES} "
            f"(the declared bound) — a malformed or lesson-omitting exam, refusing to audit"
        )


def _validate_exam(exam: ExamSpec) -> None:
    """Structural integrity of the examiner's output. Called right after the
    exam call (so a broken exam doesn't burn student/grader calls) and again
    inside validate_protocol."""
    obj_ids = [o.id for o in exam.objectives]
    if not obj_ids:
        raise TeachingAuditError(
            "exam has no objectives — the anchored examiner reports the titled "
            "lesson is not within the sliced pages (page offset suspected: this "
            "book's TOC likely uses PRINTED page numbers offset from the PDF's "
            "physical pages, specimen a92e62ae). No verdict issued."
        )
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
    book_id: str
    toc_entry_id: str
    page_start: int
    page_end: int
    lesson_title: str
    subject: str
    grade: Optional[str]
    language: str
    variant: str  # "full" (real packet) or "control" (empty negative-control packet)
    # PRIMARY evidence (gate-4 review): the exact textbook excerpt the exam was
    # derived from and the exact study document this leg's student saw — snapshotted
    # so a human can re-verify exam-vs-textbook and coverage-vs-packet even after
    # the source PDF or the packet outputs change. result_to_dict adds sha256s.
    textbook_text: str
    study_md: str
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
        f"You are a strict {subject} examiner. Below is a WINDOW of OFFICIAL TEXTBOOK "
        f"PAGES around the grade-{grade or '?'} lesson '{lesson_title}' (language "
        f"'{language}'). The window is deliberately wider than the lesson and may "
        f"include neighbouring lessons — some books' printed page numbers are offset "
        f"from the PDF's physical pages.\n\n"
        f"1. FIRST, locate the lesson titled '{lesson_title}' within these pages "
        f"(headings may vary slightly in punctuation/case). Derive everything below "
        f"ONLY from the lesson with that title — never from a neighbouring lesson. "
        f"If NO lesson with this title appears in these pages, return ZERO objectives "
        f"and ZERO questions (empty lists) — do NOT substitute another lesson.\n"
        f"2. Derive that lesson's LEARNING OBJECTIVES — the {_MIN_OBJECTIVES} to "
        f"{_MAX_OBJECTIVES} distinct things it actually teaches (concepts, "
        f"definitions, methods, facts). Ignore exercises and decoration. Ids O1, O2, … "
        f"Every objective needs a non-empty statement.\n"
        f"3. Write EXACTLY {_QUESTIONS_PER_OBJECTIVE} short-answer exam questions per "
        f"objective, ids Q1, Q2, … (every id unique, every question non-empty). Prefer "
        f"LESSON-SPECIFIC facts, terms, methods and the textbook's own examples over "
        f"anything answerable by general reasoning — the exam must discriminate 'studied "
        f"this lesson' from 'is generally clever'.\n"
        f"4. For each question give a concise non-empty answer_key (and grading_notes when "
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
    toc_entry_id: str
    page_start: int
    page_end: int
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
        toc_entry_id = str(toc.id)
        page_start, page_end = toc.page_start, toc.page_end

    if not phases:
        raise TeachingAuditError(
            f"job {job_id} has no completed deliverable phases to audit"
        )

    pdf_path = storage.book_pdf_path(book_id)
    if not pdf_path.exists():
        raise TeachingAuditError(f"source PDF missing: {pdf_path}")
    textbook_text = agent.read_page_range_text(
        pdf_path, page_start, page_end, margin=_PAGE_WINDOW_MARGIN
    )
    if not textbook_text:
        raise TeachingAuditError(
            f"pages {page_start}-{page_end} (±{_PAGE_WINDOW_MARGIN}) of {pdf_path.name} "
            f"yielded no text (image-only scan?) — cannot derive a textbook exam"
        )

    return AuditInputs(
        job_id=str(job_id),
        book_id=book_id,
        toc_entry_id=toc_entry_id,
        page_start=page_start,
        page_end=page_end,
        subject=subject,
        grade=grade,
        language=language,
        lesson_title=lesson_title,
        textbook_text=textbook_text,
        phases=phases,
    )


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
    _validate_objective_count(exam)  # enforce the declared 3-6 bound (gate-4 review)
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


def _result(data, *, variant, study_md, objectives, artifacts, calls) -> AuditResult:
    return AuditResult(
        job_id=data.job_id,
        book_id=data.book_id,
        toc_entry_id=data.toc_entry_id,
        page_start=data.page_start,
        page_end=data.page_end,
        lesson_title=data.lesson_title,
        subject=data.subject,
        grade=data.grade,
        language=data.language,
        variant=variant,
        textbook_text=data.textbook_text,
        study_md=study_md,
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
    return _result(data, variant="full", study_md=study, objectives=objectives,
                   artifacts=artifacts, calls=calls)


@dataclass(frozen=True)
class PairedResult:
    """Sensitivity experiment: same exam, same pre-test, ONE shared grade set,
    two study documents (real packet vs empty control)."""

    normal: AuditResult
    control: AuditResult
    calls: list[dict]  # all 7 calls (legs carry calls=[]; this is the one ledger)

    @property
    def _student_path_ok(self) -> bool:
        """The empty control must teach NOTHING — ANY learned objective on a
        no-material packet is proof the post-test path is leaking latent
        knowledge (gate-4 review: `control < normal` wrongly passed a leaking
        control when normal learned more, and wrongly failed a genuinely
        ineffective packet where both learned 0). Normal-packet effectiveness is
        a SEPARATE question, reported by `normal.teaching_equivalent`/`.learnable`."""
        return self.control.learned_count == 0

    @property
    def _coverage_path_ok(self) -> bool:
        """Every objective's coverage on the EMPTY control is 'absent' — a
        coverage scorer that finds teaching in a blank document is broken."""
        return all(r.coverage == "absent" for r in self.control.objectives)

    @property
    def sensitivity_pass(self) -> bool:
        """DUAL gate (gate-3 blocker 2): both the student path and the coverage
        path must hold, since coverage is part of the instrument. This validates
        the INSTRUMENT (does an empty packet correctly measure as zero teaching?),
        NOT whether the real packet is any good."""
        return self._student_path_ok and self._coverage_path_ok

    def sensitivity_failures(self) -> list[str]:
        """Distinct, human-readable reasons — so a failure tells you WHICH path
        broke (student-path leakage vs coverage-path hallucination)."""
        reasons: list[str] = []
        if not self._student_path_ok:
            reasons.append(
                f"student-path leak: the empty control 'learned' {self.control.learned_count} "
                f"objective(s) (expected 0) — the post-test path is leaking latent knowledge "
                f"(for context the real packet learned {self.normal.learned_count})"
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
        data, variant="full", study_md=study,
        objectives=aggregate(exam, graded, cov_normal, pre_label="pre", post_label="post_normal"),
        artifacts={"exam": exam.model_dump(), "pre": pre.model_dump(),
                   "post": post_normal.model_dump(), "graded": graded_dump,
                   "coverage": cov_normal.model_dump()},
        calls=[],
    )
    control = _result(
        data, variant="control", study_md=CONTROL_STUDY_MD,
        objectives=aggregate(exam, graded, cov_control, pre_label="pre", post_label="post_control"),
        artifacts={"exam": exam.model_dump(), "pre": pre.model_dump(),
                   "post": post_control.model_dump(), "graded": graded_dump,
                   "coverage": cov_control.model_dump()},
        calls=[],
    )
    return PairedResult(normal=normal, control=control, calls=calls)


# --------------------------------------------------------------------------
# Report rendering
# --------------------------------------------------------------------------


def _sha256(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def result_to_dict(result: AuditResult) -> dict:
    return {
        "job_id": result.job_id,
        "book_id": result.book_id,
        "toc_entry_id": result.toc_entry_id,
        "page_start": result.page_start,
        "page_end": result.page_end,
        "lesson_title": result.lesson_title,
        "subject": result.subject,
        "grade": result.grade,
        "language": result.language,
        "variant": result.variant,
        # PRIMARY evidence (gate-4 review): the exact textbook excerpt + the exact
        # study document, each with a sha256, so a human can re-verify exam↔textbook
        # and coverage↔packet independently of any later source/output change.
        "source": {
            "textbook_text": result.textbook_text,
            "textbook_sha256": _sha256(result.textbook_text),
            "study_md": result.study_md,
            "study_sha256": _sha256(result.study_md),
        },
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
        f"job `{result.job_id}` · book `{result.book_id}` · toc `{result.toc_entry_id}` "
        f"· textbook pp{result.page_start}-{result.page_end}",
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
