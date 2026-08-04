"""Shared helpers for content_json schemas.

`norm` MUST match mobile's SentenceFill `norm()` exactly:
    s.trim().toLowerCase().replace(/\\s+/g, " ")
Any divergence makes our uniqueness check disagree with the runtime's
chip-consumption behaviour.
"""
from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict

_WS = re.compile(r"\s+")


def norm(s: str) -> str:
    return _WS.sub(" ", s.strip().lower())


def all_unique_normalized(values: list[str]) -> bool:
    seen = [norm(v) for v in values]
    return len(set(seen)) == len(seen)


class StrictModel(BaseModel):
    """Reject unknown keys and loose types everywhere."""

    model_config = ConfigDict(extra="forbid", strict=True)
