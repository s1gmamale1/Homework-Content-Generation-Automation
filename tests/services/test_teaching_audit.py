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


# ---------- prompt builders ----------

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


# ---------- loaders (input assembly) ----------


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
