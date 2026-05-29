"""Platform RealLifeChallengeCase contract — PINNED MIRROR.

This mirrors the `Homeworks` runtime's `RealLifeChallengeCase` so the generator
can validate its beta export against the *exact* shape the platform accepts
before writing runtime content_json. It is a hand-mirror reconstructed from the
documented invariants in
`docs/nets_generator_flow_v2_transformation_plan_FINAL_PATCHED.md` §6.2–§6.3.

Pending sync: when the real schema is pulled from `s1gmamale1/Homeworks`,
replace the body of this file with the upstream definition. Nothing else in the
generator should need to change — the beta adapter and contract test both import
`RealLifeChallengeCase` from here.

Contract (from §6.2/§6.3):
  - steps length == 5, in order:
      decision → info_request → final_decision → concept_select → reasoning
  - decision / info_request / final_decision:
      options >= 2, exactly 1 option has is_correct = True
  - concept_select:
      concept_chips >= 3, exactly 1 chip has is_correct = True
  - reasoning:
      min_chars in [20, 1000]

Server-only fields (must be stripped before reaching the student browser, §10.1):
  is_correct, consequence, acceptable_keywords
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, model_validator

RLCStepKind = Literal[
    "decision", "info_request", "final_decision", "concept_select", "reasoning"
]

# Canonical order the platform runtime expects. Used by the validator below.
REQUIRED_STEP_ORDER: tuple[str, ...] = (
    "decision",
    "info_request",
    "final_decision",
    "concept_select",
    "reasoning",
)

# Field names the runtime treats as server-only — never sent to the client.
SERVER_ONLY_FIELDS: frozenset[str] = frozenset(
    {"is_correct", "consequence", "acceptable_keywords"}
)

# The three option-bearing steps that share the ">=2 options, exactly 1 correct"
# invariant.
_OPTION_STEPS: frozenset[str] = frozenset(
    {"decision", "info_request", "final_decision"}
)


class DecisionOption(BaseModel):
    label: str
    is_correct: bool = False  # server-only
    consequence: Optional[str] = None  # server-only


class ConceptChip(BaseModel):
    label: str
    is_correct: bool = False  # server-only


class RLCStep(BaseModel):
    kind: RLCStepKind
    prompt: str
    # decision / info_request / final_decision
    options: list[DecisionOption] = []
    # concept_select
    concept_chips: list[ConceptChip] = []
    # reasoning
    min_chars: Optional[int] = None
    acceptable_keywords: list[str] = []  # server-only

    @model_validator(mode="after")
    def _check_step_invariants(self) -> RLCStep:
        if self.kind in _OPTION_STEPS:
            if len(self.options) < 2:
                raise ValueError(f"{self.kind} step requires >= 2 options")
            correct = sum(1 for o in self.options if o.is_correct)
            if correct != 1:
                raise ValueError(
                    f"{self.kind} step requires exactly 1 correct option, got {correct}"
                )
        elif self.kind == "concept_select":
            if len(self.concept_chips) < 3:
                raise ValueError("concept_select step requires >= 3 concept_chips")
            correct = sum(1 for c in self.concept_chips if c.is_correct)
            if correct != 1:
                raise ValueError(
                    f"concept_select requires exactly 1 correct chip, got {correct}"
                )
        elif self.kind == "reasoning":
            if self.min_chars is None or not (20 <= self.min_chars <= 1000):
                raise ValueError("reasoning step requires min_chars in [20, 1000]")
        return self


class RealLifeChallengeCase(BaseModel):
    steps: list[RLCStep]

    @model_validator(mode="after")
    def _check_case_contract(self) -> RealLifeChallengeCase:
        kinds = [s.kind for s in self.steps]
        if tuple(kinds) != REQUIRED_STEP_ORDER:
            raise ValueError(
                "steps must be exactly "
                f"{list(REQUIRED_STEP_ORDER)} in order, got {kinds}"
            )
        return self
