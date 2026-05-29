"""Unit tests for the Boss Arena content schema (``app.schemas.boss_arena``).

Boss Arena is reasoning content (PR-4): a set of Why -> How -> What questions,
each grounded in the lesson's source concepts. These tests pin the spec's
non-negotiables: every question carries a full why/how/what chain (none blank),
references >=1 concept, and uses a fixed difficulty enum. It is open reasoning,
so there is no MCQ ``options`` field at all.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.boss_arena import BossArena, BossQuestion


def _question(**overrides) -> dict:
    base = dict(
        concept_ids=["divide-fraction-by-whole"],
        difficulty="medium",
        scenario="A ladder leans against a wall; base 5 m, ladder 13 m.",
        why="Why does the Pythagorean theorem apply to this situation?",
        how="How do you set up the equation to find the height?",
        what="What does the answer mean for whether the ladder is safe?",
        base_damage=20,
        hints=["What kind of triangle do the wall, ground, and ladder form?"],
        correct_feedback="Strong — you named the rule, applied it, and interpreted it.",
        partial_feedback="You got a number but didn't explain why the theorem applies.",
        wrong_feedback="Hali emas. Which side of that triangle is the longest?",
    )
    base.update(overrides)
    return base


def _arena(**overrides) -> dict:
    base = dict(
        title="Boss Arena",
        starting_hp=100,
        questions=[_question(), _question(difficulty="easy"),
                   _question(difficulty="hard"), _question()],
    )
    base.update(overrides)
    return base


def test_boss_arena_valid() -> None:
    arena = BossArena(**_arena())
    assert len(arena.questions) == 4
    assert arena.questions[0].why and arena.questions[0].how and arena.questions[0].what


def test_question_requires_nonempty_why_how_what() -> None:
    for field in ("why", "how", "what"):
        with pytest.raises(ValidationError):
            BossQuestion(**_question(**{field: ""}))


def test_question_requires_at_least_one_concept_id() -> None:
    with pytest.raises(ValidationError):
        BossQuestion(**_question(concept_ids=[]))


def test_question_rejects_unknown_difficulty() -> None:
    with pytest.raises(ValidationError):
        BossQuestion(**_question(difficulty="nightmare"))


def test_boss_arena_enforces_question_count_floor() -> None:
    # Fewer than the minimum (4) is not a real boss.
    with pytest.raises(ValidationError):
        BossArena(**_arena(questions=[_question(), _question()]))


def test_boss_question_is_reasoning_not_mcq() -> None:
    # Open reasoning: there is no `options` field on the model at all.
    assert "options" not in BossQuestion.model_fields
    assert "correct_index" not in BossQuestion.model_fields
