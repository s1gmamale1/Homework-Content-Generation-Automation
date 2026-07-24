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


# --- Task 2: errdet lint family INVERTED — the contract now forbids marking
# the broken block inside the student-visible blocks list; the marker belongs
# only in the answer-key region (The correct version / Reveal). ---

def test_errdet_marker_in_student_region_is_spoiler():
    # real production shape (G8 electroenergetika): marker inline in the blocks list
    md = ("# The blocks\n1. ok\n2. broken **(XATO BLOK)**\n"
          "# The correct version\n2-blok: to'g'ri matn\n# Reveal\n2-blok")
    assert "errdet_inline_spoiler" in _codes(cl.lint_phase(ED, md, subject="geografiya", output_language="uz"))


def test_errdet_clean_body_key_names_block_no_findings():
    md = ("# The blocks\n1. ok\n2. subtle slip\n"
          "## To'g'ri versiya\n2-blok noto'g'ri edi: ...\n## Reveal\n2-blok")
    assert not [c for c in _codes(cl.lint_phase(ED, md, subject="geografiya", output_language="uz"))
                if c.startswith("errdet_")]


def test_errdet_no_boundary_heading_never_spoilers():
    # conservative: no recognized answer-key boundary -> no spoiler finding
    md = "# The blocks\n1. ok\n2. broken (XATO)\nprose with no key heading"
    assert "errdet_inline_spoiler" not in _codes(cl.lint_phase(ED, md, subject="geografiya", output_language="uz"))


def test_errdet_key_region_names_no_block_warns():
    md = "# The blocks\n1. ok\n2. slip\n# The correct version\nto'g'ri matn, raqamsiz"
    assert "errdet_no_broken_marker" in _codes(cl.lint_phase(ED, md, subject="geografiya", output_language="uz"))


def test_errdet_real_g8_electroenergetika_output_is_spoiler():
    # real production output (job 83852be1-c31b-43cb-9b69-790be8fc57f6, G8
    # electroenergetika, uz): the old contract's inline "(XATO BLOK)" marker
    # sits in the student-visible "# The blocks" section.
    md = (FIX / "errdet-inline-spoiler-83852be1.md").read_text(encoding="utf-8")
    assert "errdet_inline_spoiler" in _codes(cl.lint_phase(ED, md, subject="geografiya", output_language="uz"))


def test_errdet_real_ru_output_recognizes_russian_boundary_and_marker():
    # real production RU output (job 477a937b-bf4c-46ea-9de6-f31eed7deb19):
    # RU boundary heading "# Правильная версия" + inline Russian spoiler
    # marker "(БРОКОВАННЫЙ БЛОК)" in the student-visible region. Proves the
    # RU path produces errdet_inline_spoiler via the RU boundary, not the
    # conservative no-boundary fallback (which would silently suppress it).
    md = (FIX / "errdet-ru-inline-spoiler-b1e40004.md").read_text(encoding="utf-8")
    findings = cl.lint_phase(ED, md, subject="geografiya", output_language="ru")
    assert "errdet_inline_spoiler" in _codes(findings)
    # Gap B (merge-gate follow-up): this real output's Reveal section ("#
    # Раскрытие") never restates the block id at all — genuinely non-compliant
    # under the bounded per-section naming contract, distinct from the
    # spoiler finding this fixture exists to prove. Asserted here because
    # it's true of the real sampled output, not invented for the test.
    assert any("Reveal section names no block id" in f.message for f in findings)


# --- Gap A (merge-gate follow-up): digit-bearing English spoiler form ---

def test_errdet_digit_bearing_english_broken_block_is_spoiler():
    md = ("# The blocks\n1. ok\n2. slip\n### Broken block: 2\n"
          "## To'g'ri versiya\n2-blok xato edi.\n## Reveal\nXato blok 2.")
    assert "errdet_inline_spoiler" in _codes(cl.lint_phase(ED, md, subject="geografiya", output_language="en"))


def test_errdet_digit_bearing_english_prose_form_no_colon_is_spoiler():
    md = ("# The blocks\n1. ok\n2. Broken block 2 sits here.\n"
          "## To'g'ri versiya\n2-blok xato edi.\n## Reveal\nXato blok 2.")
    assert "errdet_inline_spoiler" in _codes(cl.lint_phase(ED, md, subject="geografiya", output_language="en"))


def test_errdet_canonical_title_echo_still_not_spoiler():
    # the phase's own canonical title contains the bare "broken block" fragment
    # with zero spoiler intent and NO digit — must stay immune post Gap-A widening.
    md = ("# Error Detection — spot the broken block, type the correction — Subject\n"
          "# The blocks\n1. ok\n2. slip\n"
          "## To'g'ri versiya\n2-blok xato edi.\n## Reveal\nXato blok 2.")
    assert "errdet_inline_spoiler" not in _codes(cl.lint_phase(ED, md, subject="geografiya", output_language="uz"))


# --- Gap B (merge-gate follow-up): bounded per-section naming ---

def test_errdet_reveal_only_id_still_warns_correct_version_omission():
    # Gap B: reveal-only naming must NOT silently satisfy the correct-version
    # section's own naming requirement — each PRESENT section is bounded to
    # its own region, not the combined answer-key region.
    md = ("# The blocks\n1. ok\n2. slip\n"
          "## To'g'ri versiya\nXato: o'rta had ishorasi noto'g'ri edi.\n"
          "## Reveal\n2-blok.")
    findings = cl.lint_phase(ED, md, subject="geografiya", output_language="uz")
    codes = _codes(findings)
    assert "errdet_no_broken_marker" in codes
    assert any("correct version" in f.message.lower() for f in findings)


def test_errdet_correct_version_only_id_still_warns_reveal_omission():
    md = ("# The blocks\n1. ok\n2. slip\n"
          "## To'g'ri versiya\n2-blok xato edi.\n"
          "## Reveal\nXato: tasdiqlaymiz, tuzatildi.")
    findings = cl.lint_phase(ED, md, subject="geografiya", output_language="uz")
    codes = _codes(findings)
    assert "errdet_no_broken_marker" in codes
    assert any("reveal" in f.message.lower() for f in findings)


def test_errdet_reveal_only_heading_missing_id_flags_reveal_only_not_correct_version():
    # only ONE heading (Reveal) exists at all — do not invent a finding for the
    # absent correct-version section, only flag the PRESENT one that fails.
    md = "# The blocks\n1. ok\n2. slip\n## Reveal\nXato: tasdiqlaymiz.\n"
    findings = cl.lint_phase(ED, md, subject="geografiya", output_language="uz")
    codes = _codes(findings)
    assert "errdet_no_broken_marker" in codes
    assert any("reveal" in f.message.lower() for f in findings)
    assert not any("correct version" in f.message.lower() for f in findings)


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
    # INVERTED (Task 2): the marker now belongs in the answer-key region, not
    # the student-visible blocks list — moved here to prove the ordinal form
    # is still recognized cleanly post-boundary.
    md = ("## Bloklar\n1. ok\n2. slip\n"
          "## To'g'ri versiya\n**4-blok noto'g'ri.**\n"
          "## Ochish\n**Noto'g'ri blok: 4-blok.**")
    findings = cl.lint_phase(ED, md, subject="matematika", output_language="uz")
    assert not [f for f in findings if f.code.startswith("errdet_")]


def test_uzbek_ordinal_two_markers_flagged():
    # INVERTED (Task 2): both markers now live in the answer-key region.
    md = ("## Bloklar\n1. ok\n"
          "## To'g'ri versiya\n**4-blok noto'g'ri.**\n**6-blok noto'g'ri.**\n"
          "## Ochish\n**Noto'g'ri blok: 4-blok.**")
    assert "errdet_multiple_broken" in _codes(cl.lint_phase(ED, md, subject="matematika", output_language="uz"))


def test_feedback_prose_does_not_inject_spurious_block_id():
    # INVERTED (Task 2): moved into the answer-key region. A praise line naming
    # a DIFFERENT block ("Blok 3") must still not create a false multiple/mismatch.
    md = ("## Bloklar\n1. ok\n"
          "## To'g'ri versiya\n**Blok 4 noto'g'ri.**\n"
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
    # INVERTED (Task 2): reveal_mismatch now needs BOTH a correct-version
    # heading and the reveal heading present; "oshkor" is recognized as the
    # reveal boundary and "to'g'ri versiya" as the correct-version boundary.
    md = "# t\n\n## To'g'ri versiya\nBlok 2 ...\n\n## Oshkor qilish\nBlok 3 ...\n"
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


from app.services.content_lint import lint_coverage, findings_to_warnings

_COV_CONTRACT = """## Worked-example types
- Izotoplar massa ulushi orqali o'rtacha atom massasini hisoblash
- Element tarkibi va valentlik orqali noma'lum elementni aniqlash
## Key facts
- Davriy qonun elementlarni tartiblaydi
"""

def test_coverage_flags_uncovered_worked_example_type():
    packet = "Davriy qonun haqida savollar. Elementlarni tartiblang."  # no isotope/valence vocab
    findings = lint_coverage(_COV_CONTRACT, packet)
    assert len(findings) == 1
    w = findings_to_warnings(findings)[0]
    assert w.startswith("lint:coverage_thin")
    assert "izotop" in w.lower() or "massa" in w.lower()

def test_coverage_clean_when_items_present():
    packet = ("Izotoplar massa ulushi masalasi: o'rtacha atom massasini hisoblang. "
              "Noma'lum elementni tarkibi va valentlik orqali aniqlang. Davriy qonun.")
    assert lint_coverage(_COV_CONTRACT, packet) == []

def test_coverage_formulas_excluded_and_empty_contract_noop():
    assert lint_coverage("## Formulas\n- a/b · c/d = ac/bd\n", "hech narsa") == []
    assert lint_coverage("", "anything") == []


# --- Task 3: english_heading_leak — English structural labels left untranslated
# in a heading, on non-en output (un-freeze #83 companion detector) ------------

def test_english_heading_leak_fires_on_uz_heading():
    md = "## How to play\nMatn.\n"
    findings = cl.lint_phase("boss-arena", md, subject="matematika", output_language="uz")
    assert "english_heading_leak" in _codes(findings)


def test_english_heading_leak_silent_on_en_output():
    md = "## How to play\nText.\n"
    findings = cl.lint_phase("boss-arena", md, subject="ingliz-tili", output_language="en")
    assert "english_heading_leak" not in _codes(findings)


def test_english_heading_leak_silent_on_boss_arena_heading():
    md = "# Boss Arena\nMatn.\n"
    findings = cl.lint_phase("boss-arena", md, subject="matematika", output_language="uz")
    assert "english_heading_leak" not in _codes(findings)


def test_english_heading_leak_silent_on_body_prose_mention():
    # "Scenario" mentioned in body prose, not as a heading -> must NOT fire
    md = "## Vaziyat\nBu yerda Scenario so'zi matn ichida uchraydi, sarlavha emas.\n"
    findings = cl.lint_phase("boss-arena", md, subject="matematika", output_language="uz")
    assert "english_heading_leak" not in _codes(findings)


def test_english_heading_leak_silent_on_ru_output_when_translated():
    md = "## Сценарий\nТекст.\n"
    findings = cl.lint_phase("boss-arena", md, subject="matematika", output_language="ru")
    assert "english_heading_leak" not in _codes(findings)


@pytest.mark.parametrize("label", [
    "Scenario", "How to play", "Case-Based Preview", "Relationship types", "Role",
    "Task", "Checkpoint", "Learning Block", "Feedback summary", "Memory Check",
    "Reflection", "Decision Process",
])
def test_english_heading_leak_covers_full_label_list(label):
    md = f"### {label}\nMatn.\n"
    findings = cl.lint_phase("flashcards", md, subject="matematika", output_language="uz")
    assert "english_heading_leak" in _codes(findings), f"did not fire on heading {label!r}"


# --- Task 3 extension: numbered and DPE-qualified labels (2026-07-23) ---

def test_english_heading_leak_fires_on_checkpoint_numbered():
    """Numbered checkpoint form: # Checkpoint 1"""
    md = "# Checkpoint 1\nMatn.\n"
    findings = cl.lint_phase("boss-arena", md, subject="matematika", output_language="uz")
    assert "english_heading_leak" in _codes(findings)


def test_english_heading_leak_fires_on_learning_block_numbered():
    """Numbered learning block form: # Learning Block 2"""
    md = "# Learning Block 2\nMatn.\n"
    findings = cl.lint_phase("boss-arena", md, subject="matematika", output_language="uz")
    assert "english_heading_leak" in _codes(findings)


def test_english_heading_leak_fires_on_decision_process_explanation():
    """Decision Process with Explanation suffix: # Decision Process Explanation"""
    md = "# Decision Process Explanation\nMatn.\n"
    findings = cl.lint_phase("boss-arena", md, subject="matematika", output_language="uz")
    assert "english_heading_leak" in _codes(findings)


def test_english_heading_leak_fires_on_decision_process_explanation_with_dpe():
    """Decision Process with Explanation and DPE label: # Decision Process Explanation (DPE)"""
    md = "# Decision Process Explanation (DPE)\nMatn.\n"
    findings = cl.lint_phase("boss-arena", md, subject="matematika", output_language="uz")
    assert "english_heading_leak" in _codes(findings)


def test_english_heading_leak_silent_on_uzbek_word_containing_english_snippet():
    """Uzbek word 'Rolga' contains 'Rol' but it's NOT an English structural label in that context"""
    md = "# Rolga kirish: Task boshqaruvchisi\nMatn.\n"
    findings = cl.lint_phase("boss-arena", md, subject="matematika", output_language="uz")
    # Must NOT fire — "Rolga" is Uzbek, not the English "Role" label
    assert "english_heading_leak" not in _codes(findings)


def test_english_heading_leak_fires_on_scenario_heading_uz():
    """# Scenario on uz output should fire"""
    md = "### Scenario\nMatn.\n"
    findings = cl.lint_phase("flashcards", md, subject="matematika", output_language="uz")
    assert "english_heading_leak" in _codes(findings)


def test_english_heading_leak_fires_on_feedback_summary():
    """# Feedback summary should fire on uz"""
    md = "## Feedback summary\nMatn.\n"
    findings = cl.lint_phase("boss-arena", md, subject="matematika", output_language="uz")
    assert "english_heading_leak" in _codes(findings)


# --- Task 3 extension: new labels (Flash Cards, Real-Life Challenge) + dash suffix (2026-07-23) ---

def test_english_heading_leak_fires_on_flash_cards():
    """# Flash Cards should fire on uz"""
    md = "## Flash Cards\nMatn.\n"
    findings = cl.lint_phase("flashcards", md, subject="matematika", output_language="uz")
    assert "english_heading_leak" in _codes(findings)


def test_english_heading_leak_fires_on_flash_cards_with_dash_subject():
    """# Flash Cards — Geografiya should fire on uz (real leak case)"""
    md = "## Flash Cards — Geografiya\nMatn.\n"
    findings = cl.lint_phase("flashcards", md, subject="matematika", output_language="uz")
    assert "english_heading_leak" in _codes(findings)


def test_english_heading_leak_fires_on_rlc():
    """# Real-Life Challenge should fire on uz"""
    md = "## Real-Life Challenge\nMatn.\n"
    findings = cl.lint_phase("practice-rlc", md, subject="matematika", output_language="uz")
    assert "english_heading_leak" in _codes(findings)


def test_english_heading_leak_fires_on_rlc_with_en_dash_subject():
    """# Real-Life Challenge — Geography (Geografiya) should fire on uz"""
    md = "## Real-Life Challenge — Geography (Geografiya)\nMatn.\n"
    findings = cl.lint_phase("practice-rlc", md, subject="matematika", output_language="uz")
    assert "english_heading_leak" in _codes(findings)


def test_english_heading_leak_silent_on_boss_arena_with_dash_suffix():
    """# Boss Arena — Geografiya should NOT fire (Boss Arena exclusion must tolerate dash)"""
    md = "# Boss Arena — Geografiya\nMatn.\n"
    findings = cl.lint_phase("boss-arena", md, subject="matematika", output_language="uz")
    assert "english_heading_leak" not in _codes(findings)


def test_english_heading_leak_silent_on_colon_tail():
    """# Rolga kirish: Task boshqaruvchisi should NOT fire (colon tail, not dash)"""
    md = "# Rolga kirish: Task boshqaruvchisi\nMatn.\n"
    findings = cl.lint_phase("boss-arena", md, subject="matematika", output_language="uz")
    assert "english_heading_leak" not in _codes(findings)
