"""Unit tests for the teaching-equivalence audit (closed-book simulated-student exam)."""
import pytest

from app.services import teaching_audit as ta


# ---------- shared fixtures ----------

def _exam_two_objectives() -> ta.ExamSpec:
    return ta.ExamSpec(
        objectives=[
            ta.Objective(id="O1", statement="Parallelogramm ta'rifi", tier="core"),
            ta.Objective(id="O2", statement="Diagonallar xossasi", tier="supporting"),
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


def test_objective_tier_is_required_with_no_default():
    # r24 T1 R1: tier has NO default — a model that ignores the tiering
    # instruction must fail schema validation (forcing a retry) rather than
    # silently defaulting into an all-supporting exam that would then fail
    # the R4 guard for a confusing reason.
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        ta.Objective(id="O1", statement="x")


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


def test_aggregate_propagates_tier_onto_every_objective_result():
    # r24 T1: tier is carried verbatim from the exam's Objective onto the
    # matching ObjectiveResult so downstream core-only verdicts can key to it.
    exam = _exam_two_objectives()  # O1 tier=core, O2 tier=supporting
    graded = ta.GradedExam(grades=[
        _grade("Q1", "pre", "wrong"), _grade("Q2", "pre", "wrong"),
        _grade("Q1", "post", "correct"), _grade("Q2", "post", "correct"),
        _grade("Q3", "pre", "wrong"), _grade("Q4", "pre", "wrong"),
        _grade("Q3", "post", "wrong"), _grade("Q4", "post", "wrong"),
    ])
    cov = _coverage([("O1", "taught"), ("O2", "absent")])
    results = ta.aggregate(exam, graded, cov)
    by_id = {r.objective_id: r for r in results}
    assert by_id["O1"].tier == "core"
    assert by_id["O2"].tier == "supporting"


# ---------- prompt builders ----------

TEXTBOOK_SENTINEL = "XTEXTBOOKX parallelogramm sahifa matni"
PACKET_SENTINEL = "XPACKETX flashcards matni"


def _exam_min() -> ta.ExamSpec:
    # 3 objectives / 6 questions = the minimum VALID exam (examiner bound is 3-6).
    # O1's Q1/Q2 keep their exact strings so the Task 2 builder assertions hold.
    objs = [
        ta.Objective(id="O1", statement="ta'rif", tier="core"),
        ta.Objective(id="O2", statement="xossa", tier="supporting"),
        ta.Objective(id="O3", statement="qo'llash", tier="supporting"),
    ]
    questions = [
        ta.ExamQuestion(id="Q1", objective_id="O1", question="Savol bir?", answer_key="Javob bir"),
        ta.ExamQuestion(id="Q2", objective_id="O1", question="Savol ikki?", answer_key="Javob ikki"),
        ta.ExamQuestion(id="Q3", objective_id="O2", question="Savol uch?", answer_key="Javob uch"),
        ta.ExamQuestion(id="Q4", objective_id="O2", question="Savol tort?", answer_key="Javob tort"),
        ta.ExamQuestion(id="Q5", objective_id="O3", question="Savol besh?", answer_key="Javob besh"),
        ta.ExamQuestion(id="Q6", objective_id="O3", question="Savol olti?", answer_key="Javob olti"),
    ]
    return ta.ExamSpec(objectives=objs, questions=questions)


def test_exam_prompt_contains_textbook_and_never_packet():
    p = ta.build_exam_prompt(
        textbook_text=TEXTBOOK_SENTINEL, lesson_title="Parallelogramm",
        subject="matematika", grade="8", language="uz",
    )
    assert TEXTBOOK_SENTINEL in p
    assert "Parallelogramm" in p
    assert "2" in p  # per-objective question count pinned in the instructions


def test_exam_prompt_contains_tiering_instruction_and_preserves_anchor_and_escape():
    # r24 T1 R2: the examiner now tiers each objective core/supporting. Anti-
    # circularity is load-bearing — the tiering must be derived from the
    # textbook window ONLY, so the prompt must gain no reference to the
    # packet, the extract, or generation. The pre-existing title anchor and
    # zero-objectives escape hatch (page-offset guard) must survive untouched.
    p = ta.build_exam_prompt(
        textbook_text=TEXTBOOK_SENTINEL, lesson_title="Parallelogramm",
        subject="matematika", grade="8", language="uz",
    )
    assert "core" in p and "supporting" in p
    assert "tier" in p.lower()
    assert "ONLY from the lesson" in p    # title anchor preserved verbatim
    assert "ZERO objectives" in p         # zero-objectives escape hatch preserved verbatim
    assert "neighbouring" in p
    assert PACKET_SENTINEL not in p
    assert "packet" not in p.lower()
    assert "extract" not in p.lower()
    assert "generat" not in p.lower()     # no "generate"/"generation" reference


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


# ---------- orchestrator (5-call + 7-call) ----------


def _inputs() -> ta.AuditInputs:
    return ta.AuditInputs(
        job_id="job-1", book_id="book-1", toc_entry_id="toc-1", page_start=63, page_end=65,
        subject="matematika", grade="8", language="uz",
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
                ta.StudentAnswer(question_id=q.id, answer="j") for q in exam.questions
            ])
        elif schema is ta.GradedExam:
            grades = []
            for i, meaning in enumerate(grade_meanings):
                label = f"s{i}"  # opaque — the grader never sees the semantics
                for q in exam.questions:
                    if meaning == "pre":
                        v = "wrong"
                    elif meaning == "post_control":
                        v = "correct" if control_learns else "wrong"
                    else:  # post / post_normal
                        v = "correct"
                    grades.append(_grade(q.id, label, v))
            if drop_grade:
                grades.pop()
            parsed = ta.GradedExam(grades=grades)
        elif schema is ta.CoverageReport:
            is_control = ta.CONTROL_STUDY_MD in kw["phase_prompt"]
            cov = "taught" if broken_coverage else ("absent" if is_control else "taught")
            parsed = ta.CoverageReport(coverages=[
                ta.ObjectiveCoverage(objective_id=o.id, coverage=cov, evidence="cbp")
                for o in exam.objectives
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
    assert [r.outcome for r in result.objectives] == ["learned", "learned", "learned"]
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
                ta.StudentAnswer(question_id=q.id, answer="j") for q in exam.questions
            ])
        elif schema is ta.GradedExam:
            grader_calls["n"] += 1
            # would give a DIFFERENT pre verdict if ever called a second time
            pre_v = "wrong" if grader_calls["n"] == 1 else "correct"
            grades = []
            for i, meaning in enumerate(("pre", "post_normal", "post_control")):
                label = f"s{i}"  # blinded opaque labels
                for q in exam.questions:
                    grades.append(_grade(q.id, label, pre_v if meaning == "pre" else "correct"))
            parsed = ta.GradedExam(grades=grades)
        elif schema is ta.CoverageReport:
            cov = "absent" if ta.CONTROL_STUDY_MD in kw["phase_prompt"] else "taught"
            parsed = ta.CoverageReport(coverages=[
                ta.ObjectiveCoverage(objective_id=o.id, coverage=cov, evidence="e")
                for o in exam.objectives])
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
    assert paired.normal.learned_count == 3 and paired.control.learned_count == 0
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
    assert paired.normal.learned_count == 3 and paired.control.learned_count == 0  # student path OK
    assert paired.sensitivity_pass is False                                        # coverage path broke
    reasons = paired.sensitivity_failures()
    assert any("coverage-path" in r for r in reasons)
    assert not any("student-path" in r for r in reasons)


# ---------- sensitivity gate semantics (gate-4 review, control must learn NOTHING) ----------

def _mk_leg(variant, outcomes_and_coverage) -> ta.AuditResult:
    # tier is irrelevant to these sensitivity-gate tests (which validate the
    # full-set instrument, not the core subset) — hardcode "core" throughout.
    objs = [ta.ObjectiveResult(f"O{i}", "s", "core", 0.0, 2.0, 2.0, cov, out)
            for i, (out, cov) in enumerate(outcomes_and_coverage, 1)]
    return ta.AuditResult(
        job_id="j", book_id="b", toc_entry_id="t", page_start=1, page_end=2,
        lesson_title="l", subject="s", grade="9", language="uz", variant=variant,
        textbook_text="tb", study_md="sm", objectives=objs)


def test_sensitivity_fails_when_control_learns_even_though_normal_learns_more():
    # gate-4: control learned=1, normal learned=3 previously PASSED (1<3); now the
    # empty control learning ANYTHING is caught as student-path leakage.
    normal = _mk_leg("full", [("learned", "taught")] * 3)
    control = _mk_leg("control", [("learned", "taught"), ("not_taught", "absent"),
                                  ("not_taught", "absent")])
    pr = ta.PairedResult(normal=normal, control=control, calls=[])
    assert normal.learned_count == 3 and control.learned_count == 1
    assert pr.sensitivity_pass is False
    assert any("student-path" in r for r in pr.sensitivity_failures())


def test_sensitivity_passes_on_ineffective_packet_when_control_is_clean():
    # gate-4: normal=0/control=0 previously FAILED (0<0 is false) and mislabeled a
    # weak packet as an instrument failure; now the instrument is validated on the
    # CONTROL alone (learned 0 + all coverage absent), independent of packet quality.
    normal = _mk_leg("full", [("not_taught", "absent")] * 3)
    control = _mk_leg("control", [("not_taught", "absent")] * 3)
    pr = ta.PairedResult(normal=normal, control=control, calls=[])
    assert normal.learned_count == 0 and control.learned_count == 0
    assert pr.sensitivity_pass is True
    assert pr.sensitivity_failures() == []


# ---------- core-tier verdicts (r24 T1 R5) ----------

def test_core_properties_key_only_to_core_subset_not_all_objectives():
    # Discriminating: O1 is the only 'core' objective and is 'learned'. O2/O3
    # are 'supporting' and would fail the full-set verdicts (not_taught /
    # not_learnable). The full-set properties must still see all three; the
    # core properties must see ONLY O1 — this must fail if core_* is wired to
    # the whole objective list instead of the tier=='core' subset.
    result = ta.AuditResult(
        job_id="j", book_id="b", toc_entry_id="t", page_start=1, page_end=2,
        lesson_title="l", subject="s", grade="9", language="uz", variant="full",
        textbook_text="tb", study_md="sm",
        objectives=[
            ta.ObjectiveResult("O1", "s1", "core", 0.0, 2.0, 2.0, "taught", "learned"),
            ta.ObjectiveResult("O2", "s2", "supporting", 0.0, 0.5, 2.0, "absent", "not_taught"),
            ta.ObjectiveResult("O3", "s3", "supporting", 0.0, 1.0, 2.0, "taught", "not_learnable"),
        ],
    )
    # full-set properties still see the supporting objectives' bad outcomes
    assert result.learned_count == 1
    assert result.teaching_equivalent is False   # O2 is not_taught
    assert result.learnable is False             # O3 is not_learnable
    # core properties key ONLY to the core subset (just O1, which is clean)
    assert [r.objective_id for r in result.core_objectives] == ["O1"]
    assert result.core_total == 1
    assert result.core_learned_count == 1
    assert result.core_teaching_equivalent is True
    assert result.core_learnable is True


# ---------- objective-count bound (gate-4 review) ----------

def _exam_with_objectives(n) -> ta.ExamSpec:
    objs = [ta.Objective(id=f"O{i}", statement="s", tier=("core" if i == 1 else "supporting"))
            for i in range(1, n + 1)]
    qs = [ta.ExamQuestion(id=f"Q{i}", objective_id=f"O{((i - 1) // 2) + 1}",
                          question="q", answer_key="k")
          for i in range(1, 2 * n + 1)]
    return ta.ExamSpec(objectives=objs, questions=qs)


def test_validate_objective_count_enforces_declared_3_to_6_bound():
    ta._validate_objective_count(_exam_with_objectives(3))  # min ok
    ta._validate_objective_count(_exam_with_objectives(6))  # max ok
    for bad in (1, 7):
        with pytest.raises(ta.TeachingAuditError):
            ta._validate_objective_count(_exam_with_objectives(bad))


# ---------- objective tiering bound (r24 T1 R4 — zero-core fails loud) ----------

def _exam_all_supporting() -> ta.ExamSpec:
    exam = _exam_min().model_copy(deep=True)
    for o in exam.objectives:
        o.tier = "supporting"
    return exam


def test_validate_objective_tiers_rejects_all_supporting_exam():
    # An exam with zero core objectives cannot answer the question the audit
    # now asks (core-teaching), so it must never produce a clean verdict.
    with pytest.raises(ta.TeachingAuditError, match="core"):
        ta._validate_objective_tiers(_exam_all_supporting())


def test_validate_objective_tiers_accepts_exam_with_at_least_one_core():
    ta._validate_objective_tiers(_exam_min())  # O1 is tier 'core' — must not raise


async def test_audit_job_rejects_out_of_bound_objective_count(monkeypatch):
    # the 3-6 gate is WIRED into the real flow and fires at the exam step,
    # BEFORE any student/grader call is made (not a defined-but-unwired guard).
    one_obj = _exam_with_objectives(1)

    async def fake(**kw):
        assert kw["schema"] is ta.ExamSpec  # only the exam call may run
        return _R(one_obj)

    monkeypatch.setattr(ta.agent, "run_phase", fake)
    with pytest.raises(ta.TeachingAuditError):
        await ta.audit_job("job-1", inputs=_inputs())


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


# ---------- renderer ----------


def _result_fixture() -> ta.AuditResult:
    return ta.AuditResult(
        job_id="job-1", book_id="book-1", toc_entry_id="toc-1", page_start=63, page_end=65,
        lesson_title="Parallelogramm", subject="matematika", grade="8",
        language="uz", variant="full",
        textbook_text="TEXTBOOK EXCERPT", study_md="## flashcards\n\nPACKET",
        objectives=[
            ta.ObjectiveResult("O1", "ta'rif", "core", 0.0, 2.0, 2.0, "taught", "learned"),
            ta.ObjectiveResult("O2", "xossa", "supporting", 0.0, 0.5, 2.0, "absent", "not_taught"),
        ],
        artifacts={"exam": {}, "pre": {}, "post": {}, "graded": {}, "coverage": {}},
        calls=[{"step": "exam", "provider": "gemini", "model": "gemini-2.5-pro",
                "usage": {"prompt_tokens": 10, "output_tokens": 5}}],
    )


def test_render_markdown_has_matrix_verdicts_and_identity():
    md = ta.render_markdown(_result_fixture())
    assert "O1" in md and "learned" in md
    assert "not_taught" in md
    assert "teaching-equivalent: NO" in md and "learnable: YES" in md
    assert "book-1" in md and "pp63-65" in md  # source identity in the header
    # r24 T1 R3: tier column + a core verdict line. O1 (tier=core) is
    # 'learned' and is the fixture's ONLY core objective.
    assert "supporting" in md   # O2's tier, visible only in the table row
    assert "core: teaching-equivalent YES" in md
    assert "1/1 core objectives learned" in md


def test_result_to_dict_retains_evidence_identity_and_roundtrips():
    import hashlib
    import json

    d = ta.result_to_dict(_result_fixture())
    assert d["job_id"] == "job-1" and d["teaching_equivalent"] is False
    assert d["variant"] == "full"
    assert d["objectives"][1]["outcome"] == "not_taught"
    # r24 T1 R3: tier per objective + top-level core figures
    assert d["objectives"][0]["tier"] == "core"
    assert d["objectives"][1]["tier"] == "supporting"
    assert d["core_total"] == 1
    assert d["core_learned_count"] == 1
    assert set(d["artifacts"]) == {"exam", "pre", "post", "graded", "coverage"}
    # gate-4: primary evidence — source identity + immutable snapshots + hashes
    assert d["book_id"] == "book-1" and d["toc_entry_id"] == "toc-1"
    assert d["page_start"] == 63 and d["page_end"] == 65
    assert d["source"]["textbook_text"] == "TEXTBOOK EXCERPT"
    assert d["source"]["study_md"] == "## flashcards\n\nPACKET"
    assert d["source"]["textbook_sha256"] == hashlib.sha256(b"TEXTBOOK EXCERPT").hexdigest()
    assert d["source"]["study_sha256"] == hashlib.sha256("## flashcards\n\nPACKET".encode()).hexdigest()
    json.dumps(d)  # must be JSON-serializable


# ---------- page-offset guard (teaching-audit-page-offset-1, worklog 0152) ----------

def test_exam_prompt_anchors_on_title_with_zero_objectives_escape():
    # The slice window may be offset (printed vs physical page numbers, specimen
    # a92e62ae) — the examiner must derive ONLY from the titled lesson and return
    # ZERO objectives when it is absent, never substitute a neighbouring lesson.
    p = ta.build_exam_prompt(
        textbook_text=TEXTBOOK_SENTINEL, lesson_title="Parallelogramm",
        subject="matematika", grade="8", language="uz",
    )
    assert "ONLY from the lesson" in p
    assert "ZERO objectives" in p
    assert "neighbouring" in p


def test_zero_objectives_raises_with_page_offset_suspicion():
    # Load-bearing guard: a zero-objective exam is the anchored examiner saying
    # "the titled lesson is not in these pages" — must fail LOUD naming the
    # suspected cause, never flow into a confident wrong verdict.
    with pytest.raises(ta.TeachingAuditError, match="page offset suspected"):
        ta._validate_exam(ta.ExamSpec(objectives=[], questions=[]))
