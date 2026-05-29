"""Beta-platform-safe export for Real-Life Challenge.

Down-maps the canonical `RealLifeChallenge` onto the platform's
`real_life_challenge` key, validates against the platform contract BEFORE emit
(a stop-the-line condition per the Flow v2 plan §6), and strips server-only
answer fields so nothing leaks into the student browser (§10.1).

Reverse-test variant: per §17 the 5-step contract's `concept_select` hands the
student concept chips, which does not faithfully express reverse-test pedagogy
(inferring the unnamed method). We adapt it into the 5-step shape only as a
labeled compatibility bridge — `adapt_to_beta` returns a warning the caller can
surface, and never claims runtime fidelity.
"""

from __future__ import annotations

from typing import Any, Optional, Union

from app.schemas.platform import (
    ConceptChip,
    DecisionOption,
    RealLifeChallengeCase,
    RLCStep,
)
from app.schemas.platform.real_life_challenge import SERVER_ONLY_FIELDS
from app.schemas.real_life import RealLifeChallenge

CanonicalRLC = Union[RealLifeChallenge, dict[str, Any]]


def _coerce(canonical: CanonicalRLC) -> RealLifeChallenge:
    if isinstance(canonical, RealLifeChallenge):
        return canonical
    return RealLifeChallenge.model_validate(canonical)


def _decision_step(kind: str, step) -> RLCStep:
    return RLCStep(
        kind=kind,
        prompt=step.prompt,
        options=[
            DecisionOption(
                label=o.label, is_correct=o.is_correct, consequence=o.consequence
            )
            for o in step.options
        ],
    )


def to_platform_case(canonical: CanonicalRLC) -> RealLifeChallengeCase:
    """Down-map canonical → platform 5-step and VALIDATE.

    Raises pydantic.ValidationError if the result violates the platform
    RealLifeChallengeCase contract (wrong step order, too few options, no/many
    correct answers, reasoning min_chars out of range). Callers at generation
    time should treat that as stop-the-line.
    """
    rl = _coerce(canonical)
    case = RealLifeChallengeCase(
        steps=[
            _decision_step("decision", rl.decision),
            _decision_step("info_request", rl.info_request),
            _decision_step("final_decision", rl.final_decision),
            RLCStep(
                kind="concept_select",
                prompt=rl.concept_select.prompt,
                concept_chips=[
                    ConceptChip(label=c.label, is_correct=c.is_correct)
                    for c in rl.concept_select.concept_chips
                ],
            ),
            RLCStep(
                kind="reasoning",
                prompt=rl.reasoning.prompt,
                min_chars=rl.reasoning.min_chars,
                acceptable_keywords=rl.reasoning.acceptable_keywords,
            ),
        ]
    )
    return case


def _strip_server_only(value: Any) -> Any:
    """Recursively drop server-only keys (is_correct, consequence,
    acceptable_keywords) so the result is safe to send to the student."""
    if isinstance(value, dict):
        return {
            k: _strip_server_only(v)
            for k, v in value.items()
            if k not in SERVER_ONLY_FIELDS
        }
    if isinstance(value, list):
        return [_strip_server_only(v) for v in value]
    return value


def adapt_to_beta(canonical: CanonicalRLC) -> tuple[dict[str, Any], Optional[str]]:
    """Return (student_safe_payload, bridge_warning).

    The payload is the validated platform case with server-only fields stripped
    — safe for the student browser. `bridge_warning` is non-None only for the
    reverse-test variant, flagging that the 5-step adaptation is a compatibility
    bridge, not faithful reverse-test runtime.
    """
    rl = _coerce(canonical)
    case = to_platform_case(rl)
    payload = _strip_server_only(case.model_dump())

    warning: Optional[str] = None
    if rl.variant == "reverse_test_same_story_new_numbers":
        warning = (
            "reverse_test variant adapted into the 5-step expert-case contract "
            "as a compatibility bridge — concept_select reveals concept chips, "
            "so this is NOT a faithful reverse-test runtime (Flow v2 plan §17)."
        )
    return payload, warning
