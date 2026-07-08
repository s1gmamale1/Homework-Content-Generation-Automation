from dataclasses import dataclass
from types import SimpleNamespace

from app.services import toc_classifier as tc


@dataclass
class Row:
    section_number: str | None
    section_title: str
    page_start: int | None
    page_end: int | None


def _row(title, section_number="1", page_start=None, page_end=None):
    return Row(section_number=section_number, section_title=title, page_start=page_start, page_end=page_end)


def test_classes_constant_contains_all_seven():
    assert tc.CLASSES == {
        tc.LESSON,
        tc.HEADER,
        tc.RECALL,
        tc.PRACTICE,
        tc.REVISION,
        tc.TEST,
        tc.OTHER,
    }


def test_recall_keyword():
    rows = [_row("Eslang! Oldingi mavzu")]
    assert tc.classify_entries(rows) == [tc.RECALL]


def test_revision_keywords():
    titles = [
        "Takrorlash uchun mashqlar",
        "Bobga doir mashqlar",
        "Bobni takrorlash",
        "Повторение курса",
    ]
    rows = [_row(t) for t in titles]
    assert tc.classify_entries(rows) == [tc.REVISION] * 4


def test_test_keywords():
    titles = [
        "Nazorat ishi",
        "Bilimingizni sinab ko'ring",
        "Testlar",
        "Sinov nazorat ishi",
    ]
    rows = [_row(t) for t in titles]
    assert tc.classify_entries(rows) == [tc.TEST] * 4


def test_test_word_boundary_prefix_match_but_not_midword():
    # Positive: "Testlar"/"Test" match via \btest (prefix-at-word-boundary,
    # deliberately catches Uzbek plural/case forms). Negative: "Protest
    # harakatlari" genuinely contains "test" MID-WORD (preceded by a word
    # char, "pro"), so \b must reject it -- unlike the old "Kontekst"
    # negative, which contained no "test" substring at all and passed
    # vacuously regardless of the \b guard.
    rows = [_row("Testlar"), _row("Test"), _row("Protest harakatlari")]
    result = tc.classify_entries(rows)
    assert result[0] == tc.TEST
    assert result[1] == tc.TEST
    assert result[2] != tc.TEST


def test_other_keywords():
    titles = [
        "Tarixiy ma'lumot",
        "Javoblar",
        "Ответы",
        "Ilova",
        "Loyiha ishi",
        "Atamalar lug'ati",
        "Lug'at",
        "Mundarija",
    ]
    rows = [_row(t) for t in titles]
    assert tc.classify_entries(rows) == [tc.OTHER] * len(titles)


def test_apostrophe_variants_all_three_map_to_other():
    rows = [_row("Lug'at"), _row("Lugʼat"), _row("Lug`at")]
    assert tc.classify_entries(rows) == [tc.OTHER, tc.OTHER, tc.OTHER]


def test_containment_header_with_two_children():
    parent = _row("1-bob. Algebra", section_number="1", page_start=1, page_end=50)
    child_a = _row("1.1-mavzu", section_number="1.1", page_start=1, page_end=10)
    child_b = _row("1.2-mavzu", section_number="1.2", page_start=11, page_end=20)
    rows = [parent, child_a, child_b]
    result = tc.classify_entries(rows)
    assert result[0] == tc.HEADER
    assert result[1] == tc.LESSON
    assert result[2] == tc.LESSON


def test_containment_counts_keyword_classified_children_too():
    # Regression: a parent's contained-child count must include rows that
    # already matched a keyword class in Pass 1 (e.g. a recall row), not
    # only rows left unclassified after Pass 1. Otherwise a chapter with
    # one plain lesson child + one "Eslang!" recall child undercounts to 1
    # and is misclassified as `lesson` instead of `header`.
    parent = _row("1-bob. Algebra", section_number="1", page_start=1, page_end=50)
    child_a = _row("1.1-mavzu", section_number="1.1", page_start=1, page_end=10)
    child_b = _row("Eslang! Oldingi mavzu", section_number="1.2", page_start=11, page_end=20)
    rows = [parent, child_a, child_b]
    result = tc.classify_entries(rows)
    assert result[0] == tc.HEADER
    assert result[1] == tc.LESSON
    assert result[2] == tc.RECALL


def test_containment_not_header_with_only_one_child():
    parent = _row("1-bob. Algebra", section_number="1", page_start=1, page_end=50)
    child_a = _row("1.1-mavzu", section_number="1.1", page_start=1, page_end=10)
    other = _row("2-bob. Geometriya", section_number="2", page_start=51, page_end=60)
    rows = [parent, child_a, other]
    result = tc.classify_entries(rows)
    assert result[0] == tc.LESSON
    assert result[1] == tc.LESSON
    assert result[2] == tc.LESSON


def test_single_page_duplicate_ranges_do_not_flip_to_header():
    # Pathological edge: several single-page rows sharing an IDENTICAL [p, p]
    # range mutually "contain" each other under the raw page-bounds check
    # (each is <= and >= the others). A containment HEADER candidate must
    # span MORE THAN ONE page (a chapter umbrella always does), so none of
    # these plain single-page lessons may flip to `header`.
    row_a = _row("1.1-mavzu Something", section_number="1.1", page_start=5, page_end=5)
    row_b = _row("1.2-mavzu Something else", section_number="1.2", page_start=5, page_end=5)
    row_c = _row("1.3-mavzu Third thing", section_number="1.3", page_start=5, page_end=5)
    result = tc.classify_entries([row_a, row_b, row_c])
    assert result == [tc.LESSON, tc.LESSON, tc.LESSON]


def test_normal_multipage_parent_still_becomes_header_with_guard():
    # No-regression companion to the guard above: a genuine multi-page
    # chapter umbrella (page_end > page_start) containing >=2 children must
    # still classify HEADER.
    parent = _row("1-bob. Algebra", section_number="1", page_start=1, page_end=50)
    child_a = _row("1.1-mavzu", section_number="1.1", page_start=1, page_end=10)
    child_b = _row("1.2-mavzu", section_number="1.2", page_start=11, page_end=20)
    result = tc.classify_entries([parent, child_a, child_b])
    assert result[0] == tc.HEADER
    assert result[1] == tc.LESSON
    assert result[2] == tc.LESSON


def test_none_page_end_on_parent_is_not_header():
    parent = _row("1-bob. Algebra", section_number="1", page_start=1, page_end=None)
    child_a = _row("1.1-mavzu", section_number="1.1", page_start=1, page_end=10)
    child_b = _row("1.2-mavzu", section_number="1.2", page_start=11, page_end=20)
    rows = [parent, child_a, child_b]
    result = tc.classify_entries(rows)
    assert result[0] == tc.LESSON


def test_all_caps_multi_page_is_lesson_g10_guard():
    rows = [_row("KVADRAT TENGLAMALAR", section_number="5", page_start=100, page_end=110)]
    assert tc.classify_entries(rows) == [tc.LESSON]


def test_all_caps_single_page_is_other():
    rows = [_row("MUNDARIJA BOSHI", section_number="0", page_start=5, page_end=5)]
    assert tc.classify_entries(rows) == [tc.OTHER]


def test_plain_numbered_mavzu_is_lesson():
    rows = [_row("1.3-mavzu. Chiziqli tenglamalar", section_number="1.3", page_start=21, page_end=25)]
    assert tc.classify_entries(rows) == [tc.LESSON]


def test_output_order_alignment_when_input_is_page_shuffled():
    parent = _row("1-bob. Algebra", section_number="1", page_start=1, page_end=50)
    child_a = _row("1.1-mavzu", section_number="1.1", page_start=1, page_end=10)
    child_b = _row("1.2-mavzu", section_number="1.2", page_start=11, page_end=20)
    # Input order deliberately NOT sorted by page: child_b, parent, child_a
    rows = [child_b, parent, child_a]
    result = tc.classify_entries(rows)
    assert result[0] == tc.LESSON  # child_b
    assert result[1] == tc.HEADER  # parent
    assert result[2] == tc.LESSON  # child_a


def test_duck_typed_simplenamespace_rows_work():
    rows = [
        SimpleNamespace(section_number="1", section_title="Eslang! Takrorlash", page_start=None, page_end=None)
    ]
    assert tc.classify_entries(rows) == [tc.RECALL]


def test_none_section_number_does_not_crash():
    rows = [_row("Nazorat ishi", section_number=None)]
    assert tc.classify_entries(rows) == [tc.TEST]


def test_classes_constant_contains_practice():
    assert tc.PRACTICE == "practice"
    assert tc.PRACTICE in tc.CLASSES
    assert len(tc.CLASSES) == 7


def test_practice_keywords():
    titles = [
        "Laboratoriya ishi. Elektr zanjirini yigʻish",  # physics prefix form
        "1-laboratoriya mashg'uloti.",  # biology numbered form (trailing dot)
        "Amaliy mashg'ulot",  # geografiya bare form
        "Amaliy mashg'ulot. Reostat yordamida tok kuchini rostlash",  # physics
        "Лабораторная работа",  # RU parity
        "Практическая работа",  # RU parity
    ]
    rows = [_row(t) for t in titles]
    assert tc.classify_entries(rows) == [tc.PRACTICE] * 6


def test_masalalar_yechish_whole_title_only():
    # Bare whole-title "Masalalar yechish" is a problem-solving session
    # (practice) — on physics AND math books alike (C1 decision). The SAME
    # phrase inside a longer title is a real math lesson (g8alg/g8geo
    # fixture rows) and must NOT be excluded.
    rows = [
        _row("Masalalar yechish"),
        _row("Masalalar yechish."),  # trailing punctuation tolerated
        _row("Решение задач"),
        _row("Kvadrat tenglamalar yordamida masalalar yechish"),
        _row("To'g'ri chiziq tenglamasi. Geometrik masalalar yechishning koordinatalar usuli"),
    ]
    result = tc.classify_entries(rows)
    assert result[:3] == [tc.PRACTICE] * 3
    assert result[3] == tc.LESSON
    assert result[4] == tc.LESSON


def test_amaliy_mashq_lesson_not_practice():
    # g8geo true-lesson title: "mashq" != "mashg'ulot" — must stay lesson.
    rows = [_row("Amaliy mashq va tatbiq")]
    assert tc.classify_entries(rows) == [tc.LESSON]


def test_muhim_xulosalar_revision():
    rows = [_row("I bob yuzasidan muhim xulosalar")]
    assert tc.classify_entries(rows) == [tc.REVISION]


def test_english_review_anchored():
    # "Review N" rows (Cambridge Prepare) are revision; "review" mid-title
    # must not match (anchored at title start).
    rows = [
        _row("Review 3 (Units 9–12)"),
        _row("Peer review in science"),
    ]
    result = tc.classify_entries(rows)
    assert result[0] == tc.REVISION
    assert result[1] == tc.LESSON


def test_english_backmatter_other():
    titles = [
        "Extra Activities",
        "Vocabulary List",
        "Grammar Reference and Practice",
        "List of Irregular Verbs",
        "Darslikdan foydalanish qoidalari",
    ]
    rows = [_row(t) for t in titles]
    assert tc.classify_entries(rows) == [tc.OTHER] * 5


def test_homoglyph_fold_latin_word_with_cyrillic_letters():
    # A Latin keyword still matches when OCR/extraction swapped in Cyrillic
    # lookalike letters (а=a, о=o, е=e).
    poisoned = "Lаborаtoriya ishi. Tajriba"  # Cyrillic а twice
    rows = [_row(poisoned)]
    assert tc.classify_entries(rows) == [tc.PRACTICE]


def test_homoglyph_fold_keeps_russian_keywords_matching():
    # The fold is applied to keyword tables too — pure-Cyrillic RU keywords
    # must keep matching pure-Cyrillic RU titles.
    rows = [_row("Повторение курса алгебры"), _row("Ответы")]
    assert tc.classify_entries(rows) == [tc.REVISION, tc.OTHER]
