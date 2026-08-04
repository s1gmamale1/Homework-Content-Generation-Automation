from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import Field, model_validator

from .common import StrictModel, all_unique_normalized

EXPERT_ROLES = (
    "fire_inspector", "structural_engineer", "business_consultant",
    "medical_diagnostician", "agronomist", "teacher", "lawyer",
    "city_planner", "epidemiologist", "ethicist", "historian", "general",
)
STEP_ORDER = ("decision", "info_request", "final_decision", "concept_select", "reasoning")

MIN_CHARS_FLOOR = 20
MIN_CHARS_CEIL = 1000


class Choice(StrictModel):
    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    is_correct: bool = False


class Step(StrictModel):
    id: str = Field(min_length=1)
    kind: Literal[STEP_ORDER]  # type: ignore[valid-type]
    title: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    options: list[Choice] | None = None
    concept_chips: list[Choice] | None = None
    min_chars: int | None = None

    @model_validator(mode="after")
    def _per_kind(self):
        if self.kind in ("decision", "info_request", "final_decision"):
            opts = self.options or []
            if len(opts) < 2:
                raise ValueError(f"{self.kind}: options needs >=2 entries")
            if sum(1 for o in opts if o.is_correct) != 1:
                raise ValueError(f"{self.kind}: exactly 1 is_correct option required")
            if not all_unique_normalized([o.label for o in opts]):
                raise ValueError(f"{self.kind}: option labels must be normalized-unique")
        elif self.kind == "concept_select":
            chips = self.concept_chips or []
            if len(chips) < 2:
                raise ValueError("concept_select: concept_chips needs >=2 entries")
            if sum(1 for c in chips if c.is_correct) != 1:
                raise ValueError("concept_select: exactly 1 is_correct chip required")
            if not all_unique_normalized([c.label for c in chips]):
                raise ValueError("concept_select: chip labels must be normalized-unique")
        elif self.kind == "reasoning":
            n = self.min_chars
            if n is None or not (MIN_CHARS_FLOOR <= n <= MIN_CHARS_CEIL):
                raise ValueError(
                    f"reasoning.min_chars must be {MIN_CHARS_FLOOR}..{MIN_CHARS_CEIL}"
                )
        return self


class RlcConfig(StrictModel):
    # ClassVar, NOT a field: it must not appear in model_dump() — the version
    # travels in the envelope as content_schema_version, and an extra key inside
    # content_json would defeat the extra="forbid" containment.
    SCHEMA_VERSION: ClassVar[str] = "rlc_config@1"

    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    intro: str = Field(min_length=1)
    expert_role: Literal[EXPERT_ROLES]  # type: ignore[valid-type]
    steps: list[Step]


    @model_validator(mode="after")
    def _shape(self):
        if len(self.steps) != 5:
            raise ValueError("steps must contain exactly 5 entries")
        for i, (step, expected) in enumerate(zip(self.steps, STEP_ORDER)):
            if step.kind != expected:
                raise ValueError(f"steps[{i}].kind must be '{expected}'")
        if not all_unique_normalized([s.id for s in self.steps]):
            raise ValueError("step ids must be unique")
        return self
