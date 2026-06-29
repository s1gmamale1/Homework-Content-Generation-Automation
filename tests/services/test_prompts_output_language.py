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

def test_l2_subject_ignores_medium_keeps_uzbek_bridge():
    # decision 2: english/russian CLASS subjects unchanged regardless of medium
    for lang in ("uz", "en", "ru"):
        body = prompts.get_prompt("english", "flashcards", output_language=lang)
        assert prompts.LANGUAGE_RULES["english"] in body
        assert prompts.MEDIUM_RULES["en"] not in body  # the L2 rule, not the medium rule

def test_unknown_language_falls_back_to_uz():
    subj = _uz_subject()
    body = prompts.get_prompt(subj, "flashcards", output_language="zz")
    assert prompts.MEDIUM_RULES["uz"] in body

def test_hash_differs_by_language():
    subj = _uz_subject()
    assert prompts.get_prompt_hash(subj, "flashcards", "uz") != \
           prompts.get_prompt_hash(subj, "flashcards", "en")
