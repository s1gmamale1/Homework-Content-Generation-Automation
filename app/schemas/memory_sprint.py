"""Memory Sprint schema. Quick tap-only recognition quiz, MC/TF/YNNG mix."""

from typing import Optional

from pydantic import BaseModel


class MemorySprintItem(BaseModel):
    prompt: str
    kind: str  # 'mc' | 'tf' | 'ynng'
    options: list[str] = []
    correct_index: int
    explanation: Optional[str] = None


class MemorySprintPack(BaseModel):
    items: list[MemorySprintItem]
