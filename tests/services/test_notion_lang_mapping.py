"""Tests for Russian + English Notion subject-title mapping (Task 2).

TDD cycle:
1. Write tests (RED) — run to confirm they fail.
2. Implement (GREEN) — add ru/en keywords + _map_subject_for_language.
3. Bite-prove: empty algebra's ru_keywords → its RU assertion fails; restore.
"""
from __future__ import annotations

import pytest

from app.services.notion_fetch import _fold, _map_subject_for_language


# ---------------------------------------------------------------------------
# _fold — Cyrillic must survive, apostrophes stripped, lowercased
# ---------------------------------------------------------------------------

def test_fold_cyrillic_survives():
    """_fold must NOT strip Cyrillic characters — only lowercases + strips apostrophes."""
    assert _fold("Алгебра") == "алгебра"


def test_fold_apostrophe_stripped():
    assert _fold("O'qish") == "oqish"


def test_fold_lowercases_latin():
    assert _fold("Biology") == "biology"


# ---------------------------------------------------------------------------
# Russian title → app subject
# ---------------------------------------------------------------------------

def test_ru_algebra():
    assert _map_subject_for_language("Алгебра", "ru") == "math-algebra"


def test_ru_geometry():
    assert _map_subject_for_language("Геометрия", "ru") == "geometriya-g7-11"


def test_ru_biology():
    assert _map_subject_for_language("Биология", "ru") == "biology"


def test_ru_history_world():
    """Всемирная история → history."""
    assert _map_subject_for_language("Всемирная история", "ru") == "history"


def test_ru_history_uzbekistan():
    """История Узбекистана → history."""
    assert _map_subject_for_language("История Узбекистана", "ru") == "history"


def test_ru_physics():
    assert _map_subject_for_language("Физика", "ru") == "physics"


# ---------------------------------------------------------------------------
# English title → app subject
# ---------------------------------------------------------------------------

def test_en_algebra():
    assert _map_subject_for_language("Algebra", "en") == "math-algebra"


def test_en_geometry():
    assert _map_subject_for_language("Geometry", "en") == "geometriya-g7-11"


def test_en_biology():
    assert _map_subject_for_language("Biology", "en") == "biology"


def test_en_physics():
    assert _map_subject_for_language("Physics", "en") == "physics"


# ---------------------------------------------------------------------------
# No cross-talk: a Uzbek-only title must NOT match under the ru mapper
# ---------------------------------------------------------------------------

def test_uz_title_no_match_under_ru():
    """'Ona tili' is a purely Uzbek keyword; the ru mapper must return None."""
    assert _map_subject_for_language("Ona tili", "ru") is None


def test_uz_title_no_match_under_en():
    """'Fizika' is the Uzbek keyword; the en mapper must return None."""
    assert _map_subject_for_language("Fizika", "en") is None


# ---------------------------------------------------------------------------
# Existing Uzbek path still works via _map_subject (regression)
# ---------------------------------------------------------------------------

def test_uz_algebra_still_works():
    from app.services.notion_fetch import _map_subject
    assert _map_subject("Algebra") == "math-algebra"


def test_uz_history_variant():
    from app.services.notion_fetch import _map_subject
    assert _map_subject("Ozbekiston tarixi") == "history"
