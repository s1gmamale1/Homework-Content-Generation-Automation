from app.services.golden_eval import (
    PhaseView,
    read_signals,
    score_error_detection_format,
    score_language,
    score_reflection,
)


def _pv(name, md, judge_status="ok", validation_warnings=None, solver_status=None):
    return PhaseView(
        phase_name=name,
        output_md=md,
        judge_status=judge_status,
        validation_warnings=validation_warnings,
        solver_status=solver_status,
    )


# --- score_language (brief Step 1) ------------------------------------------


def test_language_scorer_flags_mixed_script_and_english_template():
    # Cyrillic 'а' spliced into a Latin word + an English scaffolding token
    phases = [_pv("flashcards", "atamа bo'yicha. Mode: Hard")]
    s = score_language(phases, subject="matematika", language="uz")
    assert s.verdict == "flag" and s.mechanism == "deterministic"


def test_language_scorer_passes_clean_uzbek():
    phases = [_pv("flashcards", "Toza o'zbekcha matn, hech qanday aralashuv yo'q.")]
    assert score_language(phases, subject="matematika", language="uz").verdict == "pass"


# --- score_reflection (brief Step 1) ----------------------------------------


def test_reflection_scorer_flags_pre_asserted_outcome():
    phases = [_pv("reflection", "## Redo Route\nNeeds Retry. Ikkilanishlar kuzatildi.")]
    assert score_reflection(phases).verdict == "flag"


def test_reflection_scorer_passes_neutral_structure():
    phases = [_pv("reflection", "## Redo Route\nAgar ilova qayta ishlashni belgilasa...")]
    assert score_reflection(phases).verdict == "pass"


# --- score_reflection calibration against real audited rows ----------------


def test_reflection_conditional_needs_retry_is_pass():
    # real 1122356a shape: "Needs Retry" present but under an "Agar ... bo'lsa" conditional
    md = (
        "## 4. Redo Route\n"
        "Agar natijangiz \"Needs Retry\" (Attempt completed, homework not passed) "
        "holatida bo'lsa, zaif nuqtalarni takrorlang."
    )
    phases = [_pv("reflection", md)]
    assert score_reflection(phases).verdict == "pass"


def test_reflection_past_tense_performance_is_flag():
    # real 9504ad94 shape: unconditional past-tense outcome
    phases = [_pv("reflection", "natija \"Needs Retry\" deb baholandi. ikkilanishlar kuzatildi.")]
    assert score_reflection(phases).verdict == "flag"


def test_reflection_past_tense_sezdingiz_is_flag():
    # real 263d99c5 shape: unconditional past-tense "you felt difficulty"
    md = "Zaif tomonlarda — grafik siljishi va toqlik isbotida — qiyinchilik sezdingiz."
    phases = [_pv("reflection", md)]
    assert score_reflection(phases).verdict == "flag"


def test_reflection_generic_weak_point_redo_is_pass():
    # real 3ca0da6f / 8f734563 shape: generic redo instruction, no outcome claim
    md = (
        "Mashg'ulotni mustahkamlash uchun zaif tomonlarda ko'rsatilgan mavzular "
        "bo'yicha mashqlarni qayta ishlash tavsiya etiladi."
    )
    phases = [_pv("reflection", md)]
    assert score_reflection(phases).verdict == "pass"


def test_reflection_ignores_non_reflection_phases():
    # a fabrication marker sitting in an unrelated phase must not trip the scorer
    phases = [_pv("flashcards", "natija deb baholandi. kuzatildi.")]
    assert score_reflection(phases).verdict == "pass"


# --- score_error_detection_format --------------------------------------------


def test_error_detection_format_flags_multiple_broken_blocks():
    md = (
        "Blok 1 noto'g'ri. Blok 2 noto'g'ri.\n"
        "## Reveal\nXato blok 1."
    )
    phases = [_pv("practice-error-detection", md)]
    s = score_error_detection_format(phases, subject="matematika", language="uz")
    assert s.verdict == "flag" and s.dimension == "broken_question"


def test_error_detection_format_passes_single_broken_block():
    md = (
        "Blok 3 noto'g'ri.\n"
        "## Reveal\nXato blok 3."
    )
    phases = [_pv("practice-error-detection", md)]
    s = score_error_detection_format(phases, subject="matematika", language="uz")
    assert s.verdict == "pass"


def test_error_detection_format_ignores_other_phases():
    # even a badly-formatted OTHER phase must not trip this scorer
    md = "Blok 1 noto'g'ri. Blok 2 noto'g'ri."
    phases = [_pv("flashcards", md)]
    s = score_error_detection_format(phases, subject="matematika", language="uz")
    assert s.verdict == "pass"


# --- read_signals -------------------------------------------------------------


def test_read_signals_folds_warnings_judge_and_solver_status():
    phases = [
        _pv("flashcards", "x", judge_status="ok", validation_warnings=["lint:mixed_script: x"]),
        _pv("reflection", "y", judge_status="major_shipped", validation_warnings=["a", "b"], solver_status="verified"),
    ]
    signals = read_signals(phases)
    assert signals["validation_warning_count"] == 3
    assert signals["judge_statuses"] == ["ok", "major_shipped"]
    assert signals["solver_statuses"] == [None, "verified"]


def test_read_signals_handles_missing_solver_status_attribute():
    # a bare object without solver_status at all (pre-CQ-C shape) must not raise
    class _LegacyRow:
        def __init__(self):
            self.phase_name = "reflection"
            self.output_md = "z"
            self.judge_status = "ok"
            self.validation_warnings = None

    signals = read_signals([_LegacyRow()])
    assert signals["solver_statuses"] == [None]
    assert signals["validation_warning_count"] == 0
