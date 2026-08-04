"""Shared helpers for content_json schemas.

`norm` MUST match mobile's SentenceFill `norm()` exactly:
    s.trim().toLowerCase().replace(/\\s+/g, " ")
Any divergence makes our uniqueness check disagree with the runtime's
chip-consumption behaviour.
"""
from __future__ import annotations

import re
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

_WS = re.compile(r"\s+")

# Every human-visible string and every id. `min_length=1` alone lets "   " through
# — pydantic measures the RAW string — so a whitespace-only title/prompt/id sails
# past `Field(min_length=1)` and ships a blank label to the student (or a blank id
# to the grader). Strip FIRST, then require non-empty; the stripped value is what
# gets stored, so the renderer and the platform see the same bytes.
StrippedStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


def norm(s: str) -> str:
    return _WS.sub(" ", s.strip().lower())


def all_unique_normalized(values: list[str]) -> bool:
    seen = [norm(v) for v in values]
    return len(set(seen)) == len(seen)


def first_duplicate_id(values: list[str]) -> "str | None":
    """First normalized-duplicate id in a pool, or None.

    Ids must be unique WITHIN THEIR POOL because the platform's `grade_rlc`
    resolves a submitted answer with
    ``next((o for o in opts if o.get("id") == ua), None)`` — the FIRST match
    wins. Two options sharing an id therefore award (or withhold) credit based
    on the one the student never saw. Normalized rather than exact, matching the
    step-id rule, so case/whitespace-confusable ids are rejected too.
    """
    seen: set[str] = set()
    for v in values:
        n = norm(v)
        if n in seen:
            return v
        seen.add(n)
    return None


class StrictModel(BaseModel):
    """Reject unknown keys and loose types everywhere."""

    model_config = ConfigDict(extra="forbid", strict=True)
