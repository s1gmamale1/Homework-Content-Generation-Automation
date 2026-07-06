from pathlib import Path

import pytest

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


ED = "practice-error-detection"


def test_clean_real_errdet_outputs_have_no_format_findings():
    for name in ("errdet-clean-8f734563.md", "errdet-clean-3ca0da6f.md"):
        md = (FIX / name).read_text(encoding="utf-8")
        findings = cl.lint_phase(ED, md, subject="matematika", output_language="uz")
        assert not [f for f in findings if f.code.startswith("errdet_")], f"false positive on {name}: {findings}"


def test_zero_markers_flagged():
    md = (FIX / "errdet-zero-markers.md").read_text(encoding="utf-8")
    assert "errdet_no_broken_marker" in _codes(cl.lint_phase(ED, md, subject="matematika", output_language="uz"))


def test_two_markers_flagged():
    md = (FIX / "errdet-two-markers.md").read_text(encoding="utf-8")
    assert "errdet_multiple_broken" in _codes(cl.lint_phase(ED, md, subject="matematika", output_language="uz"))


def test_reveal_mismatch_flagged():
    md = (FIX / "errdet-reveal-mismatch.md").read_text(encoding="utf-8")
    assert "errdet_reveal_mismatch" in _codes(cl.lint_phase(ED, md, subject="matematika", output_language="uz"))


def test_errdet_check_only_runs_on_that_phase():
    md = (FIX / "errdet-zero-markers.md").read_text(encoding="utf-8")
    findings = cl.lint_phase("boss-arena", md, subject="matematika", output_language="uz")
    assert not [f for f in findings if f.code.startswith("errdet_")]


def test_findings_to_warnings_prefixes_lint():
    findings = cl.lint_phase("flashcards", "### Mode: Hard\n**misconception:** x", subject="matematika", output_language="uz")
    warnings = cl.findings_to_warnings(findings)
    assert warnings, "expected at least one warning string"
    assert all(w.startswith("lint:") for w in warnings)
    assert any(w.startswith("lint:english_template") for w in warnings)


# --- final-review regression guards (no false positives on real campaign content) ---

@pytest.mark.parametrize("word", ["pH-баланс", "IT-технологии", "HTML-код", "Fe/Cu-сплав"])
def test_hyphen_slash_biscript_abbrev_not_mixed_script(word):
    # legitimate RU STEM compounds — Latin abbrev + Cyrillic word joined by -/  → NOT a splice
    findings = cl.lint_phase("boss-arena", f"{word} muhim", subject="fizika", output_language="ru")
    assert "mixed_script" not in _codes(findings)


@pytest.mark.parametrize("splice", ["hisoblaniб", "atamа", "bajariши"])
def test_real_splice_without_delimiter_still_flags(splice):
    findings = cl.lint_phase("boss-arena", f"bu {splice} keng", subject="matematika", output_language="uz")
    assert "mixed_script" in _codes(findings)


def test_mode_statistical_value_not_flagged():
    # "Mode: 7" is a statistics answer (the mode), not the "Mode: Hard" difficulty template
    findings = cl.lint_phase("boss-arena", "Mode: 7 (eng ko'p uchraydigan qiymat)", subject="matematika", output_language="uz")
    assert "english_template" not in _codes(findings)


def test_mode_difficulty_template_still_flagged():
    findings = cl.lint_phase("flashcards", "### Mode: Hard\ncard", subject="matematika", output_language="uz")
    assert "english_template" in _codes(findings)


def test_english_template_and_calque_skipped_on_english_lesson():
    md = "This is a red herring. Scenario: a shop. qizil seld"
    findings = cl.lint_phase("boss-arena", md, subject="ingliz-tili", output_language="en")
    codes = _codes(findings)
    assert "english_template" not in codes and "calque" not in codes


def test_calque_still_flagged_on_uzbek_lesson():
    findings = cl.lint_phase("boss-arena", "bu yerda qizil seld bor", subject="matematika", output_language="uz")
    assert "calque" in _codes(findings)


def test_uzbek_ordinal_block_form_clean_no_false_positive():
    md = "## Bloklar\n**4-blok noto'g'ri.**\n## Ochish\n**Noto'g'ri blok: 4-blok.**"
    findings = cl.lint_phase(ED, md, subject="matematika", output_language="uz")
    assert not [f for f in findings if f.code.startswith("errdet_")]


def test_uzbek_ordinal_two_markers_flagged():
    md = "## Bloklar\n**4-blok noto'g'ri.**\n**6-blok noto'g'ri.**\n## Ochish\n**Noto'g'ri blok: 4-blok.**"
    assert "errdet_multiple_broken" in _codes(cl.lint_phase(ED, md, subject="matematika", output_language="uz"))


def test_feedback_prose_does_not_inject_spurious_block_id():
    # a praise line naming a DIFFERENT block ("Blok 3") must not create a false multiple/mismatch
    md = ("## Bloklar\n**Blok 4 noto'g'ri.**\n"
          "Ajoyib, siz noto'g'ri blokni (Blok 3) topdingiz.\n"
          "## Ochish\n**Noto'g'ri blok: Blok 4.**")
    findings = cl.lint_phase(ED, md, subject="matematika", output_language="uz")
    assert not [f for f in findings if f.code.startswith("errdet_")]


def test_misconception_untagged_is_aggregated_to_one_finding():
    md = "**misconception:** first untagged\n**misconception:** second untagged"
    findings = [f for f in cl.lint_phase("flashcards", md, subject="matematika", output_language="uz")
                if f.code == "misconception_untagged"]
    assert len(findings) == 1  # one aggregate finding, not one per card
    assert "2" in findings[0].message


# --- round-2 vocab + RU-leak guards (2026-07-03 audit false-positives) ---

def test_errdet_recognizes_yorliq_block_noun():
    md = (
        "# Xatoni top\n\n## Bloklar\n"
        "1-yorliq: a+b\n2-yorliq: a-b (Broken)\n3-yorliq: a*b\n\n"
        "## Oshkor qilish\n2-yorliq to'g'risi: ...\n"
    )
    codes = _codes(cl.lint_phase("practice-error-detection", md, subject="matematika", output_language="uz"))
    assert "errdet_no_broken_marker" not in codes


def test_errdet_recognizes_xato_postfix_and_paren_broken():
    md = "# t\n\n2-blok (BU BLOK XATO)\n\n## Reveal\n2-blok\n"
    codes = _codes(cl.lint_phase("practice-error-detection", md, subject="matematika", output_language="uz"))
    assert "errdet_no_broken_marker" not in codes


def test_errdet_oshkor_reveal_header_recognized_for_mismatch():
    md = "# t\n\nBlok 2 noto'g'ri\n\n## Oshkor qilish\nBlok 3 ...\n"
    codes = _codes(cl.lint_phase("practice-error-detection", md, subject="matematika", output_language="uz"))
    assert "errdet_reveal_mismatch" in codes


def test_ru_leak_flags_uzbek_template_tokens():
    md = "## Kuchli tomonlar\n...\n**Hali emas** — ...\n"
    codes = _codes(cl.lint_phase("practice-rlc", md, subject="matematika", output_language="ru"))
    assert "ru_uzbek_leak" in codes


def test_ru_leak_silent_on_uz_output():
    md = "## Kuchli tomonlar\n**Hali emas** — ...\n"
    codes = _codes(cl.lint_phase("practice-rlc", md, subject="matematika", output_language="uz"))
    assert "ru_uzbek_leak" not in codes


from app.services.content_lint import parse_extract_contract, contract_has_items

_CONTRACT = """Algebraik kasrlarni ko'paytirish va bo'lish haqidagi dars.

## Concepts & terms
- Algebraik kasr
- Teskari kasr
## Formulas
- a/b · c/d = ac/bd
## Worked-example types
- Ikki algebraik kasrni ko'paytirib qisqartirish
- Bo'lishni teskarisiga ko'paytirishga keltirish
## Key facts
- Maxraj noldan farqli bo'lishi shart
"""

def test_parse_extract_contract_sections_and_items():
    c = parse_extract_contract(_CONTRACT)
    assert set(c) >= {"concepts", "formulas", "worked_example_types", "key_facts"}
    assert c["worked_example_types"] == [
        "Ikki algebraik kasrni ko'paytirib qisqartirish",
        "Bo'lishni teskarisiga ko'paytirishga keltirish",
    ]
    assert c["key_facts"] == ["Maxraj noldan farqli bo'lishi shart"]

def test_parse_lenient_on_header_level_and_case():
    md = "### concepts\n- x\n### WORKED EXAMPLE TYPES\n- y\n"
    c = parse_extract_contract(md)
    assert c["concepts"] == ["x"]
    assert c["worked_example_types"] == ["y"]

def test_contract_has_items_true_for_compact_contract():
    # compact §5-style: short, but enumerated -> valid
    assert contract_has_items(_CONTRACT) is True

def test_contract_has_items_false_for_prose_or_refusal():
    assert contract_has_items("Manba fayli o'qib bo'lmadi.") is False
    assert contract_has_items("") is False
    assert contract_has_items("## Concepts & terms\n\n## Formulas\n") is False  # headers, no items
