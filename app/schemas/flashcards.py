"""Structured flashcards schema. Extracted from the `flashcards` phase MD
output via Gemini's response_schema, persisted on homework_jobs, and
rendered as a flippable deck on the /preview/:id page."""

from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

FlashcardType = Literal[
    "definition", "term_to_meaning", "formula", "process_step",
    "question_answer", "misconception", "image_label", "vocabulary",
    "grammar", "example",
]
FlashcardDifficulty = Literal["easy", "medium", "hard"]


class Flashcard(BaseModel):
    # Stable ID so Memory Check items can reference specific cards across sessions.
    # Format convention: "card_<N>" (e.g. "card_1", "card_12").
    id: str = Field(min_length=1)
    front: str = Field(min_length=1)
    back: str = Field(min_length=1)
    type: FlashcardType
    difficulty: FlashcardDifficulty
    hint: Optional[str] = None
    explanation: Optional[str] = None
    example: Optional[str] = None
    misconception: Optional[str] = None
    cluster: Optional[str] = None  # optional grouping label (e.g., 'Names', 'Frameworks')


class FlashcardsPack(BaseModel):
    cards: list[Flashcard]

    @model_validator(mode="after")
    def _ids_must_be_unique(self) -> "FlashcardsPack":
        ids = [card.id for card in self.cards]
        if len(ids) != len(set(ids)):
            raise ValueError("flashcard ids must be unique")
        return self
