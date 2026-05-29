"""Memory Matching — CBP interaction mode.

Match a valid source pair, then reconstruct its meaning after the cards are
hidden (the learning target is meaning recall, not card position). Spec:
docs/Infra_prompts/Gamified Practices/Memory Matching/MemoryMatching.md
"""

from typing import Literal

from pydantic import BaseModel

from app.schemas.practice_games.common import CaseBasedInteraction

RecallLabel = Literal["recalled", "guessed", "missed", "position_only"]


class MemoryCardPair(BaseModel):
    left: str
    right: str
    relationship: str  # e.g. "term↔meaning", "symbol↔rule" — source-supported


class MemoryMatching(CaseBasedInteraction):
    card_pairs: list[MemoryCardPair] = []  # 4-8, source-aligned
    reconstruction_slots: list[str] = []  # meanings to rebuild from memory
    recall_labels: list[RecallLabel] = []  # final-sim per-item outcome
