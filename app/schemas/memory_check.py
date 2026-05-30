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

from pydantic import BaseModel, Field, field_validator, model_validator

# Three and only three supported kinds — the renderer knows exactly these.
MemoryCheckKind = Literal[
    "multiple_choice",
    "fill_blank",
    "choose_correct_explanation",
]


class MemoryCheckOption(BaseModel):
    text: str = Field(min_length=1)
    is_correct: bool = False
    reason: Optional[str] = None  # why this distractor is wrong (MCQ) / flawed reasoning (CCE)


class MemoryCheckBlank(BaseModel):
    answer: str = Field(min_length=1)
    accepted_variations: list[str] = Field(default_factory=list)


_OPTION_KINDS = {"multiple_choice", "choose_correct_explanation"}


class MemoryCheckItem(BaseModel):
    flashcard_id: str = Field(min_length=1)
    kind: MemoryCheckKind
    prompt: str = Field(min_length=1)
    options: list[MemoryCheckOption] = Field(default_factory=list)
    blanks: list[MemoryCheckBlank] = Field(default_factory=list)
    why_prompt: Optional[str] = None
    expected_reasoning_keywords: list[str] = Field(default_factory=list)
    correct_feedback: Optional[str] = None
    wrong_feedback: Optional[str] = None
    explanation: Optional[str] = None

    @model_validator(mode="after")
    def _shape_matches_kind(self) -> "MemoryCheckItem":
        if self.kind in _OPTION_KINDS:
            if len(self.options) != 4:
                raise ValueError(f"{self.kind} requires exactly 4 options, got {len(self.options)}")
            if sum(1 for o in self.options if o.is_correct) != 1:
                raise ValueError(f"{self.kind} requires exactly one correct option")
            if self.blanks:
                raise ValueError(f"{self.kind} must not carry blanks")
        elif self.kind == "fill_blank":
            if not self.blanks:
                raise ValueError("fill_blank requires at least one blank")
            if self.options:
                raise ValueError("fill_blank must not carry options")
        return self


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
