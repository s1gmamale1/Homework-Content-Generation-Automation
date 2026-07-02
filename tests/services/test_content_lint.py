from pathlib import Path
from app.services import content_lint as cl

FIX = Path(__file__).parent.parent / "fixtures" / "content_lint"


def _codes(findings):
    return {f.code for f in findings}


def test_mixed_script_flags_real_splice():
    md = (FIX / "rlc-mixedscript-8f734563.md").read_text(encoding="utf-8")
    findings = cl.lint_phase("practice-rlc", md, subject="matematika", output_language="uz")
    assert "mixed_script" in _codes(findings)
    assert any("hisoblaniб" in f.message for f in findings)


def test_english_template_flags_mode_label():
    md = (FIX / "flashcards-modeleak-3ca0da6f.md").read_text(encoding="utf-8")
    findings = cl.lint_phase("flashcards", md, subject="matematika", output_language="uz")
    assert "english_template" in _codes(findings)
    assert any("Mode:" in f.message for f in findings)


def test_pure_cyrillic_russian_word_is_not_mixed_script():
    findings = cl.lint_phase("boss-arena", "ПОВТОРЕНИЕ курса алгебры", subject="matematika", output_language="ru")
    assert "mixed_script" not in _codes(findings)


def test_pure_latin_uzbek_word_is_not_mixed_script():
    findings = cl.lint_phase("boss-arena", "Algebraik kasrlarni qisqartirish", subject="matematika", output_language="uz")
    assert "mixed_script" not in _codes(findings)


def test_calque_qizil_seld_flagged():
    findings = cl.lint_phase("boss-arena", "Bu yerda qizil seld bor.", subject="matematika", output_language="uz")
    assert "calque" in _codes(findings)


def test_english_word_scenario_not_flagged_for_english_lesson():
    # ambiguous bare English words must not false-positive on an L2 English lesson
    findings = cl.lint_phase("case-based-preview", "Scenario: a shop sells apples.", subject="ingliz-tili", output_language="en")
    assert "english_template" not in _codes(findings)


def test_extract_phase_is_skipped():
    findings = cl.lint_phase("extract", "Mode: Hard\nhisoblaniб", subject="matematika", output_language="uz")
    assert findings == []


def test_untagged_misconception_flagged_in_real_flashcards():
    md = (FIX / "flashcards-untagged-8f734563.md").read_text(encoding="utf-8")
    findings = cl.lint_phase("flashcards", md, subject="matematika", output_language="uz")
    assert "misconception_untagged" in _codes(findings)


def test_tagged_misconception_not_flagged():
    card = "**misconception:** a common slip (inferred)"
    findings = cl.lint_phase("flashcards", card, subject="matematika", output_language="uz")
    assert "misconception_untagged" not in _codes(findings)


def test_misconception_tag_check_only_runs_on_flashcards():
    md = "**misconception:** untagged mistake"
    findings = cl.lint_phase("boss-arena", md, subject="matematika", output_language="uz")
    assert "misconception_untagged" not in _codes(findings)
