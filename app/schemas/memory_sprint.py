"""Memory Sprint schema. Quick tap-only recognition quiz, MC/TF/YNNG mix."""

from typing import Literal, Optional

from pydantic import BaseModel

# Locked enum so model output stays in snake-case keys the renderer expects.
MemorySprintKind = Literal["mc", "tf", "ynng"]


class MemorySprintItem(BaseModel):
    prompt: str
    kind: MemorySprintKind
    options: list[str] = []
    correct_index: int
    explanation: Optional[str] = None


class MemorySprintPack(BaseModel):
    items: list[MemorySprintItem]
