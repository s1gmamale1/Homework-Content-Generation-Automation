"""Source facts are corrected only for the reviewed subject/lesson pair."""
import importlib
from pathlib import Path

import pytest

HISTORY_ID = "768820b7-54ea-45d2-bbb4-d95275ef95e6"
TECHNOLOGY_ID = "d93f33a7-8120-4895-bc51-d2055c8ef7d4"
FIXTURES = Path(__file__).parents[1] / "fixtures" / "lesson_errata"


def original(subject):
    return (FIXTURES / f"{subject}-original.md").read_text(encoding="utf-8")


def apply(text, section_id=HISTORY_ID, subject="history"):
    assert importlib.util.find_spec("app.services.lesson_errata"), "lesson errata not implemented"
    return importlib.import_module("app.services.lesson_errata").apply_lesson_errata(
        text, section_id=section_id, subject=subject)


def test_original_history_preserves_facts_and_qualifies_only_reviewed_issues():
    out = apply(original("history"))
    assert "Xuanxe" not in out
    assert "Sian shahridan" in out
    assert "lojuvard" not in out
    assert "La’l yo‘li" in out and "qimmatbaho toshlar" in out
    assert "ikki yo‘nalishi" in out and "biri" in out and "ikkinchisi" in out
    assert "Darslikda" in out and "$12000$" in out and "o‘n yetti asr" in out
    for fact in ("Doro I", "Baqtriya", "Oltoy", "Hindiston", "Pomir", "Misr",
                 "miloddan avvalgi II asr", "$3-2$", "Karvonsaroy", "asosan, ipak"):
        assert fact in out
    for heading in ("Concepts & terms", "Worked-example types", "Key facts",
                    "Vocabulary & set phrases", "Source sentences & passages"):
        assert f"## {heading}" in out
    assert "uzluksiz" not in out and "keyinchalik" not in out


def test_original_technology_omits_sand_without_guessing_publisher_intent():
    before = original("texnologiya")
    out = apply(before, TECHNOLOGY_ID, "texnologiya")
    assert out == before.replace(
        "органических остатков и увеличивается количество песка, восстанавливая структуру почвы",
        "органических остатков, восстанавливая структуру почвы")


@pytest.mark.parametrize("subject, section_id", [("history", HISTORY_ID), ("texnologiya", TECHNOLOGY_ID)])
def test_canonical_correction_is_idempotent_and_paraphrase_independent(subject, section_id):
    corrected = apply(original(subject), section_id, subject)
    assert apply(corrected, section_id, subject) == corrected
    assert apply("Another stochastic paraphrase of this reviewed source.", section_id, subject) == corrected
    assert "https://" not in corrected and "errata" not in corrected


@pytest.mark.parametrize("subject, section_id", [("history", HISTORY_ID), ("texnologiya", TECHNOLOGY_ID)])
def test_other_lesson_or_subject_is_byte_identical(subject, section_id):
    text = original(subject)
    assert apply(text, "unrelated-lesson", subject) == text
    assert apply(text, section_id, "mathematics") == text
