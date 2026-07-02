# tests/services/test_alpha_plausibility.py
from app.services.agent import _alpha_plausibility_ratio, _is_expected_alpha


def test_real_latin_uzbek_scores_high():
    txt = "Umumiy o‘rta ta’lim maktablarining 8-sinfi uchun darslik. Uchburchak perimetri."
    assert _alpha_plausibility_ratio(txt) >= 0.95


def test_real_cyrillic_scores_high():
    txt = "Ш. А. Алимов, О. Р. Холмухамедов. Алгебра. Учебник для 8 классов."
    assert _alpha_plausibility_ratio(txt) >= 0.95


def test_cp1251_mojibake_scores_low():
    # RU text mis-decoded cp1251-as-latin1: the real f20db30c failure shape.
    # Repeated to clear the _ALPHA_RATIO_MIN_SAMPLE=200 sample floor (a single
    # copy is only 45 alphabetic chars → below-floor → the "too little to judge"
    # 1.0 path); a real garbled book is thousands of such chars.
    txt = "Ó÷åáíèê äëÿ 8 êëàññîâ øêîë îáùåãî ñðåäíåãî îáðàçîâàíèÿ " * 6
    assert _alpha_plausibility_ratio(txt) < 0.30


def test_too_little_text_is_treated_plausible():
    # Below the sample floor we cannot judge — never false-fire.
    assert _alpha_plausibility_ratio("ab cd") == 1.0


def test_is_expected_alpha_blocks_latin1_accents():
    assert _is_expected_alpha("a") and _is_expected_alpha("Я") and _is_expected_alpha("ʻ")
    assert not _is_expected_alpha("÷") and not _is_expected_alpha("å")
