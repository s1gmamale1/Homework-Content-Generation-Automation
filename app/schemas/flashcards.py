"""Structured flashcards schema. Extracted from the `flashcards` phase MD
output via Gemini's response_schema, persisted on homework_jobs, and
rendered as a flippable deck on the /preview/:id page."""

from typing import Optional

from pydantic import BaseModel, Field


class Flashcard(BaseModel):
    # Stable ID so Memory Check items can reference specific cards across sessions.
    # Format convention: "card_<N>" (e.g. "card_1", "card_12").
    id: str = Field(min_length=1)
    front: str
    back: str
    hint: Optional[str] = None
    cluster: Optional[str] = None  # optional grouping label (e.g., 'Names', 'Frameworks')


class FlashcardsPack(BaseModel):
    cards: list[Flashcard]
