"""The subject registry is the single source of truth for supported subjects.

Guards: the 7 legacy subjects never drift; every subject resolves to a valid
family/game/language; Notion title -> code mapping is correct and shadow-safe.
"""
from __future__ import annotations

import pytest

from app.services import subjects

_VALID_FAMILIES = {"sciences", "math", "languages", "humanities", "default"}
_VALID_GAMES = {
    "practice-memory-match", "practice-tictactoe",
    "practice-jigsaw", "practice-sentence",
}
_VALID_LANGS = {"uz", "english", "russian"}

# Regression: the original 7 codes keep their exact classification. DB rows and
# downstream behavior depend on these never changing.
_LEGACY = {
    "biology": ("sciences", "practice-memory-match", "Biology (Biologiya)"),
    "english": ("languages", "practice-sentence", "English"),
    "geometriya-g7-11": ("math", "practice-jigsaw", "Geometry (Geometriya)"),
    "history": ("humanities", "practice-memory-match", "History (Tarix)"),
    "kimyo-g7-11": ("sciences", "practice-tictactoe", "Chemistry (Kimyo)"),
    "math-algebra": ("math", "practice-tictactoe",
                     "Mathematics / Algebra (Matematika / Algebra)"),
    "physics": ("sciences", "practice-tictactoe", "Physics (Fizika)"),
}


def test_legacy_subjects_unchanged():
    for code, (family, game, label) in _LEGACY.items():
        d = subjects.REGISTRY[code]
        assert (d.family, d.game, d.label) == (family, game, label), code


def test_registry_entries_well_formed():
    assert len(subjects.REGISTRY) == len(subjects.SUBJECT_CODES)
    for code, d in subjects.REGISTRY.items():
        assert d.code == code
        assert d.family in _VALID_FAMILIES, code
        assert d.game in _VALID_GAMES, code
        assert d.language in _VALID_LANGS, code
        assert code and code == code.lower(), code
        assert d.label.strip(), code
        assert d.keywords, code
        for kw in d.keywords:
            assert kw == kw.lower(), (code, kw)
            assert not any(ch in kw for ch in "'‘’ʻ`"), (code, kw)


def test_all_curriculum_subjects_present():
    # Sanity: the new subjects beyond the legacy 7 are registered.
    for code in ("matematika", "ona-tili", "adabiyot", "russian", "geografiya",
                 "informatika", "huquq", "iqtisodiyot", "astronomiya",
                 "tabiiy-fanlar", "chizmachilik", "musiqa", "tasviriy-sanat"):
        assert code in subjects.REGISTRY, code
    assert len(subjects.REGISTRY) >= 30


def test_notion_keyword_pairs_longest_first():
    pairs = subjects.notion_keyword_pairs()
    lengths = [len(kw) for kw, _ in pairs]
    assert lengths == sorted(lengths, reverse=True)
    # every code contributes at least one pair
    codes_with_pairs = {code for _, code in pairs}
    assert set(subjects.REGISTRY) <= codes_with_pairs


def test_keyword_shadowing_order():
    # Compound "...tarbiya" must be matched before bare "tarbiya".
    order = [kw for kw, _ in subjects.notion_keyword_pairs()]
    assert order.index("jismoniy tarbiya") < order.index("tarbiya")
    assert order.index("axloqiy tarbiya") < order.index("tarbiya")


@pytest.mark.parametrize("title,expected", [
    ("Fizika", "physics"),
    ("Biologiya", "biology"),
    ("Kimyo", "kimyo-g7-11"),
    ("Geometriya", "geometriya-g7-11"),
    ("Algebra", "math-algebra"),
    ("Algebra va analiz asoslari", "math-algebra"),
    ("Matematika", "matematika"),
    ("Ona tili", "ona-tili"),
    ("Adabiyot", "adabiyot"),
    ("Rus tili", "russian"),
    ("Rus tili / Ikkinchi til", "russian"),
    ("Ingliz tili", "english"),
    ("Jahon tarixi", "history"),
    ("O‘zbekiston tarixi", "history"),
    ("Geografiya", "geografiya"),
    ("Informatika / Dasturlash", "informatika"),
    ("Tasviriy san'at", "tasviriy-sanat"),
    ("Axloqiy tarbiya", "odobnoma"),
    ("Jismoniy tarbiya", "jismoniy-tarbiya"),
    ("Tarbiya", "tarbiya"),
    ("Astronomiya", "astronomiya"),
    ("Huquq", "huquq"),
    ("Rules", None),
    ("Kelajak soati", "kelajak-soati"),
])
def test_notion_title_maps_to_code(title, expected):
    from app.services import notion_fetch
    assert notion_fetch._map_subject(title) == expected
