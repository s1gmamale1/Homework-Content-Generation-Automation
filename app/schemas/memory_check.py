"""Memory Check schema — Quizlet-style recall test after flashcard review.

Supports exactly 3 item kinds (multiple_choice / fill_blank /
choose_correct_explanation). Every item MUST reference the flashcard it tests
via ``flashcard_id`` so the runtime can trace weak cards back to the deck. The
``pass_threshold`` is locked at 0.60 (60%) — the student must hit this to
unlock the Practice Arc.

STOP conditions (enforced by schema):
- item.kind not in the three supported kinds  → ValidationError
- item.flashcard_id is empty                  → ValidationError
- pass_threshold != 0.60                      → ValidationError
"""

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

# Three and only three supported kinds — the renderer knows exactly these.
MemoryCheckKind = Literal[
    "multiple_choice",
    "fill_blank",
    "choose_correct_explanation",
]


class MemoryCheckItem(BaseModel):
    # Required: every item must trace back to a specific flashcard.
    flashcard_id: str = Field(min_length=1)
    prompt: str
    kind: MemoryCheckKind
    options: list[str] = Field(default_factory=list)
    correct_index: Optional[int] = None
    explanation: Optional[str] = None


class MemoryCheckPack(BaseModel):
    items: list[MemoryCheckItem]
    # Hard-coded 60% threshold — do NOT lower; matches the Unlock Gate rule.
    pass_threshold: float = Field(default=0.60, ge=0.0, le=1.0)

    @field_validator("pass_threshold")
    @classmethod
    def _must_be_sixty_percent(cls, value: float) -> float:
        if value != 0.60:
            raise ValueError("pass_threshold must be exactly 0.60")
        return value
