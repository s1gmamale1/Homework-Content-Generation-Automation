"""Final Challenge (boss fight) schema. HP-based scoring with damage per
question and an optional hint ladder that costs HP."""

from typing import Literal, Optional

from pydantic import BaseModel

# Locked enum so Gemini's structured-output rejects display names like
# "Multiple Choice" — the renderer switches on these snake-case keys.
BossQuestionKind = Literal["mc", "tf", "ynng", "open"]


class BossQuestion(BaseModel):
    prompt: str
    kind: BossQuestionKind
    options: list[str] = []  # MC: 4 options; TF: ["True","False"]; YNNG: 3
    correct_index: Optional[int] = None  # for mc/tf/ynng
    correct_answer: Optional[str] = None  # for 'open' free-text
    damage: int = 20  # -10 (Easy) / -20 (Medium) / -30 (Hard)
    bloom_level: Optional[str] = None
    pisa_level: Optional[str] = None
    explanation: Optional[str] = None
    hints: list[str] = []  # up to 3, each costing -5 HP


class FinalChallenge(BaseModel):
    title: str = "Final Challenge"
    starting_hp: int = 100  # 100 for G5-8, 150 for G9-11
    questions: list[BossQuestion]
