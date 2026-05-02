"""Classify phase schema. Constrains Gemini to a single enum decision so
the parser can't drift on empty/ambiguous output, and there's no cap-vs-
thinking-tokens trap to tune."""

from typing import Literal

from pydantic import BaseModel

# Locked enum so structured-output decoding rejects anything else. The model
# cannot return "Hard", "Difficult", "Medium", or empty — only one of these
# two strings.
Difficulty = Literal["easy", "hard"]


class ClassifyDecision(BaseModel):
    difficulty: Difficulty
    reason: str = ""  # short rationale, optional, useful for debugging
