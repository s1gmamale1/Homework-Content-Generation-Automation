"""Tests for app/services/flow_division_mapper.py — pure logic, no DB/network."""

import pytest

from app.services.flow_division_mapper import (
    V1_ACTIVE_GAMES,
    build_division_plan,
    family_for_subject,
)
from app.services.flows import SUPPORTED_SUBJECTS

_FAMILIES = {"math_family", "sciences", "languages", "humanities"}


class TestFamilyMap:
    @pytest.mark.parametrize("subject", SUPPORTED_SUBJECTS)
    def test_every_supported_subject_has_a_family(self, subject):
        assert family_for_subject(subject) in _FAMILIES

    @pytest.mark.parametrize(
        "subject,family",
        [
            ("math-algebra", "math_family"),
            ("geometriya-g7-11", "math_family"),
            ("physics", "sciences"),
            ("kimyo-g7-11", "sciences"),
            ("biology", "sciences"),
            ("english", "languages"),
            ("history", "humanities"),
        ],
    )
    def test_family_mapping(self, subject, family):
        assert family_for_subject(subject) == family

    def test_unknown_subject_falls_back_humanities(self):
        assert family_for_subject("not-a-subject") == "humanities"


class TestBuildDivisionPlan:
    def test_history_hard_divisions(self):
        p = build_division_plan(subject="history", difficulty=None)
        assert p.difficulty == "hard"  # history has no easy pipeline
        assert p.family == "humanities"
        assert p.divisions["boss"] == ["final-challenge"]
        assert "flashcards" in p.divisions["learning"]
        assert "reflection" in p.divisions["reflection"]

    def test_math_easy_has_no_boss(self):
        p = build_division_plan(subject="math-algebra", difficulty="easy")
        assert p.difficulty == "easy"
        assert p.divisions["boss"] == []
        assert "final-challenge" not in p.phases

    def test_english_learning_has_reading(self):
        p = build_division_plan(subject="english", difficulty=None)
        assert "reading" in p.divisions["learning"]
        assert p.family == "languages"

    def test_games_only_when_game_breaks_present(self):
        p = build_division_plan(subject="history", difficulty=None)
        assert "game-breaks" in p.phases
        assert p.practice_games
        for g in p.practice_games:
            assert g in V1_ACTIVE_GAMES

    def test_enabled_items_includes_phases_and_games(self):
        p = build_division_plan(subject="history", difficulty=None)
        items = p.enabled_items()
        assert "final-challenge" in items
        assert any(i.startswith("game:") for i in items)

    def test_unknown_subject_raises(self):
        with pytest.raises(KeyError):
            build_division_plan(subject="not-a-subject", difficulty="hard")

    def test_to_dict_has_expected_keys(self):
        d = build_division_plan(subject="physics", difficulty="hard").to_dict()
        assert set(d) >= {
            "subject",
            "family",
            "difficulty",
            "phases",
            "divisions",
            "practice_games",
        }
