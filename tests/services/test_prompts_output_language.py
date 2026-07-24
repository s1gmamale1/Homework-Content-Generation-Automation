import pytest
from app.services import prompts, subjects

NON_L2 = "matematika"  # language == "uz" (verify via registry below)
L2 = "english"         # language == "english"

def _uz_subject():
    # pick any subject whose registry language is "uz"
    for c, d in subjects.REGISTRY.items():
        if d.language == "uz":
            return c
    raise AssertionError("no uz subject in registry")

def test_uz_default_is_byte_identical_to_legacy_default():
    subj = _uz_subject()
    body = prompts.get_prompt(subj, "flashcards", output_language="uz")
    assert prompts.LANGUAGE_RULES["_default"] in body
    # the en/ru medium blocks must NOT leak into a uz render
    assert prompts.MEDIUM_RULES["en"] not in body
    assert prompts.MEDIUM_RULES["ru"] not in body

def test_en_medium_injects_english_block_for_non_l2_subject():
    subj = _uz_subject()
    body = prompts.get_prompt(subj, "flashcards", output_language="en")
    assert prompts.MEDIUM_RULES["en"] in body
    assert prompts.LANGUAGE_RULES["_default"] not in body

def test_ru_medium_injects_russian_block_for_non_l2_subject():
    subj = _uz_subject()
    body = prompts.get_prompt(subj, "flashcards", output_language="ru")
    assert prompts.MEDIUM_RULES["ru"] in body

# Frozen copies of the pre-change L2 blocks (uz bridge). Copied verbatim from
# app/services/prompts.py @ origin/Nggaev-v2. Do NOT edit to match a code change —
# if the builder drifts, THIS is the ground truth and the test must fail.
_FROZEN_LANG_ENGLISH = (
    "This is an English (L2) lesson for native-Uzbek learners.\n"
    "Governing principle: the thing being LEARNED is in English; everything that "
    "HELPS them learn it is in Uzbek (\"Siz\").\n"
    "- In English: the target vocabulary, example sentences, passages/texts, "
    "collocations, grammar items, and anything the learner must read or produce.\n"
    "- In formal Uzbek (\"Siz\"): all scaffolding — task instructions, framing, "
    "hints, explanations, feedback, and the DPE/reasoning prompts (the UZ bridge).\n"
    "- CEFR (A1–B1+): if the source shows a grade, level the English via "
    "G5→A1, G6→A1+, G7→A2, G8→A2, G9→A2+, G10→B1, G11→B1+ (the Uzbek national "
    "curriculum keeps A2 across the G5–9 band; B1 only after G9); otherwise infer "
    "the level from the source's own complexity (default to A2 if truly "
    "indeterminate). CEFR controls sentence length, tenses, and vocabulary range — "
    "never exceed the level (no B1 vocabulary in an A1/G5 lesson)."
)
_FROZEN_LANG_RUSSIAN = (
    "This is a Russian (L2) lesson for native-Uzbek learners.\n"
    "Governing principle: the thing being LEARNED is in Russian; everything that "
    "HELPS them learn it is in formal Uzbek (\"Siz\").\n"
    "- In Russian: the target vocabulary, example sentences, passages/texts, "
    "collocations, grammar items, and anything the learner must read or produce.\n"
    "- In formal Uzbek (\"Siz\"): all scaffolding — task instructions, framing, "
    "hints, explanations, feedback, and the DPE/reasoning prompts (the UZ bridge).\n"
    "- Level the Russian to the lesson's own complexity and the source's grade; "
    "never exceed what the source uses (no advanced constructions in an early "
    "lesson). Preserve every term, example, and form exactly as in the source; "
    "translate idiomatically into Uzbek, never word-for-word."
)


def test_l2_uz_bridge_is_byte_identical_to_frozen_legacy():
    # RED-provable: change a character in the module's builder/base → this fails.
    assert prompts._l2_rule("english", "uz") == _FROZEN_LANG_ENGLISH
    assert prompts._l2_rule("russian", "uz") == _FROZEN_LANG_RUSSIAN


def test_l2_bridge_follows_medium():
    # l2-bridge-follows-medium: the L2 TARGET stays english/russian, but the
    # scaffolding BRIDGE follows output_language.
    uz_body = prompts.get_prompt("english", "flashcards", output_language="uz")
    ru_body = prompts.get_prompt("english", "flashcards", output_language="ru")
    en_body = prompts.get_prompt("russian", "flashcards", output_language="en")

    # uz medium: the frozen uz-bridge block is present unchanged
    assert _FROZEN_LANG_ENGLISH in uz_body
    assert 'formal Uzbek ("Siz")' in uz_body

    # ru medium (english class): bridge becomes Russian, uz-bridge phrasing gone
    assert "formal Russian" in ru_body
    assert 'formal Uzbek ("Siz")' not in ru_body
    assert "Uzbek (\"Siz\")" not in ru_body   # the bare governing-line bridge too
    # still an L2 english rule, NOT the ru MEDIUM rule for non-L2 subjects
    assert prompts.MEDIUM_RULES["ru"] not in ru_body

    # en medium (russian class): bridge becomes English
    assert "formal English" in en_body
    assert 'formal Uzbek ("Siz")' not in en_body

def test_unknown_language_falls_back_to_uz():
    subj = _uz_subject()
    body = prompts.get_prompt(subj, "flashcards", output_language="zz")
    assert prompts.MEDIUM_RULES["uz"] in body

def test_hash_differs_by_language():
    subj = _uz_subject()
    assert prompts.get_prompt_hash(subj, "flashcards", "uz") != \
           prompts.get_prompt_hash(subj, "flashcards", "en")


def test_ru_medium_appends_heading_localization_directive():
    subj = _uz_subject()
    body = prompts.get_prompt(subj, "case-based-preview", output_language="ru")
    assert "in the output language" in body.lower()
    low = body.lower()
    assert "heading" in low and "title" in low and "subject name" in low


def test_en_medium_appends_heading_localization_directive():
    subj = _uz_subject()
    body = prompts.get_prompt(subj, "boss-arena", output_language="en")
    assert "in the output language" in body.lower()


def test_uz_medium_has_no_heading_localization_directive():
    subj = _uz_subject()
    body = prompts.get_prompt(subj, "reflection", output_language="uz")
    assert prompts._LOCALIZE_HEADINGS_CLAUSE not in body


# --- Task 3: un-freeze uz — append a uz-language label-localization clause
# (user-approved, 2026-07-23). Append-only: the frozen #83 tests above stay green.

def test_uz_medium_gets_uz_localize_clause():
    subj = _uz_subject()
    body = prompts.get_prompt(subj, "flashcards", output_language="uz")
    assert prompts._LOCALIZE_HEADINGS_CLAUSE_UZ in body
    # frozen base still present (append-only un-freeze)
    assert prompts.LANGUAGE_RULES["_default"] in body


def test_en_ru_keep_their_own_clause_not_uz():
    subj = _uz_subject()
    body = prompts.get_prompt(subj, "flashcards", output_language="ru")
    assert prompts._LOCALIZE_HEADINGS_CLAUSE in body
    assert prompts._LOCALIZE_HEADINGS_CLAUSE_UZ not in body


def test_l2_subject_uz_medium_also_gets_uz_clause():
    """INTENTIONAL side effect: an English/Russian class packet rendered with the uz
    medium localizes its student-read labels into Uzbek too — labels are scaffolding,
    and the L2 scaffolding bridge is Uzbek."""
    body = prompts.get_prompt("english", "flashcards", output_language="uz")
    assert prompts._LOCALIZE_HEADINGS_CLAUSE_UZ in body
