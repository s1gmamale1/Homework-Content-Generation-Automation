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


# --- score_language calibration against the real audited rows --------------
# (tests/golden/manifest.json — content_lint alone reproduced only 1/5; these
# lock in the mixed-script + English-scaffolding signals added on top of it.)


def test_language_scorer_flags_boss_arena_scaffolding_headers():
    # real 3ca0da6f shape: boss-arena "three-part question" scaffolding
    md = (
        "**Scenario**\n\nSiz oshpazsiz...\n\n"
        "**The three-part question**\n\n"
        "- **Why:** Nima uchun...\n- **How:** Qanday...\n- **What:** Nima...\n\n"
        "**Feedback lines**\n\n- **Correct:** Ajoyib!\n- **Partial:** Siz...\n"
        "- **Wrong:** Hali emas.\n"
    )
    phases = [_pv("boss-arena", md)]
    s = score_language(phases, subject="matematika", language="uz")
    assert s.verdict == "flag"
    assert "english_scaffold" in s.detail


def test_language_scorer_flags_cyrillic_latin_splice_and_bare_field_labels():
    # real 263d99c5 shape: a Cyrillic-char splice inside a Latin word
    # ("bajariши") plus flashcard metadata using bare English "difficulty"/"hint"
    md = (
        "**difficulty:** easy\n**hint:** Maxrajda nol turishi mumkinmi?\n\n"
        "har bir ishchining bajariши kerak bo'lgan miqdor qanday o'zgaradi?"
    )
    phases = [_pv("flashcards", md)]
    s = score_language(phases, subject="matematika", language="uz")
    assert s.verdict == "flag"
    assert "mixed_script_token" in s.detail or "english_scaffold" in s.detail


def test_language_scorer_ignores_legitimate_loanword_model():
    # "model" (an ordinary Uzbek loanword) must NOT trip a bare "mode" match —
    # word-boundary precision, not substring matching.
    phases = [_pv("boss-arena", "Yangi model va grafik siljish paydo bo'ldi.")]
    assert score_language(phases, subject="matematika", language="uz").verdict == "pass"


def test_language_scorer_flags_red_herring_and_needs_retry():
    # real 9504ad94 shape
    phases = [_pv("boss-arena", "Bu variant bir red herring hisoblanadi. Needs Retry.")]
    s = score_language(phases, subject="matematika", language="uz")
    assert s.verdict == "flag"


def test_language_scorer_english_lesson_does_not_flag_scaffolding_words():
    # an English-class lesson (output_language="en") legitimately contains
    # these words as real prose/content, not leaked scaffolding — must NOT flag.
    md = "**Scenario:** You are a chef. **Why:** because. **Hint:** think about it."
    phases = [_pv("boss-arena", md)]
    assert score_language(phases, subject="ingliz-tili", language="en").verdict == "pass"


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
