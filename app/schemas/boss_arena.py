"""Boss Arena content schema (Flow v2, PR-4).

Boss Arena is **reasoning content**, not a quiz with HP painted on it. Every
question carries a full Why -> How -> What chain and references the lesson's
source concepts (the `SourceMap` IDs from PR-1). Because it is open reasoning,
there is deliberately **no MCQ `options` field** — a Boss question cannot be a
pick-the-option quiz item.

Runtime adaptation / telemetry / HP mechanics belong to the downstream
homework-builder platform, not to this content factory. We only generate the
reasoning questions; the HP/damage numbers are metadata the platform may use.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

BossDifficulty = Literal["easy", "medium", "hard"]


class BossQuestion(BaseModel):
    # References the lesson's source concepts (SourceMap concept IDs) so the
    # question is traceable to the source — the "referenced question" rule.
    concept_ids: list[str] = Field(min_length=1)
    difficulty: BossDifficulty
    scenario: str
    # The Why -> How -> What chain. All three are mandatory and non-empty: a
    # question missing any one is a quiz/discussion question, not a Boss one.
    why: str = Field(min_length=1)
    how: str = Field(min_length=1)
    what: str = Field(min_length=1)
    bloom_level: Optional[str] = None
    pisa_level: Optional[str] = None
    base_damage: int = 20  # -10 easy / -20 medium / -30 hard (platform metadata)
    # Probing hints that point at the missing reasoning — never the answer.
    hints: list[str] = Field(default_factory=list)
    correct_feedback: str
    partial_feedback: str
    wrong_feedback: str


class BossArena(BaseModel):
    title: str = "Boss Arena"
    starting_hp: int = 100  # 50 / 100 / 150 by grade band (platform metadata)
    questions: list[BossQuestion] = Field(min_length=4)
