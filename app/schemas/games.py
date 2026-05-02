"""Structured games schema. Different game types share roughly the same
shape so Gemini's response_schema is happy and the frontend can render
generically with type-specific branches."""

from typing import Literal, Optional

from pydantic import BaseModel

# Locked enum so Gemini's structured-output constrained decoding rejects
# any other type string (including human display names like "Adaptive Quiz").
GameType = Literal["adaptive_quiz", "tile_match", "memory_match", "sentence_fill"]


class GameQuestion(BaseModel):
    prompt: str
    options: list[str] = []
    correct_index: Optional[int] = None  # index into options for MC; 0/1 for TF
    answer: Optional[str] = None  # free-text answer for fill-in
    explanation: Optional[str] = None


class GamePair(BaseModel):
    left: str
    right: str


class GameCard(BaseModel):
    text: str
    pair_id: int  # cards with the same pair_id are matches


class Game(BaseModel):
    type: GameType
    title: str
    questions: list[GameQuestion] = []
    pairs: list[GamePair] = []
    cards: list[GameCard] = []


class GamesPack(BaseModel):
    games: list[Game]
