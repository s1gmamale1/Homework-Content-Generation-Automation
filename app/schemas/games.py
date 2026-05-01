"""Structured games schema. Permissive on purpose — different game types
fit roughly the same shape so Gemini's response_schema is happy and the
frontend can render generically with type-specific branches."""

from typing import Optional

from pydantic import BaseModel


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
    type: str  # 'adaptive_quiz' | 'tile_match' | 'memory_match' | 'sentence_fill'
    title: str
    questions: list[GameQuestion] = []
    pairs: list[GamePair] = []
    cards: list[GameCard] = []


class GamesPack(BaseModel):
    games: list[Game]
