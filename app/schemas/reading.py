"""Reading (English HARD) schema. Continuous narrative passage with inline
comprehension checkpoints injected between paragraphs."""

from typing import Optional

from pydantic import BaseModel


class ReadingCheckpoint(BaseModel):
    after_paragraph: int  # 0-based — the checkpoint shows after this paragraph
    prompt: str
    options: list[str] = []
    correct_index: Optional[int] = None
    correct_answer: Optional[str] = None
    explanation: Optional[str] = None


class ReadingPassage(BaseModel):
    passage_md: str  # the full narrative as Markdown (paragraphs separated by blank lines)
    checkpoints: list[ReadingCheckpoint] = []
    cefr_level: Optional[str] = None
