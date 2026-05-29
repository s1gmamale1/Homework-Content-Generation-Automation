"""Beta-platform-safe export for Real-Life Challenge.

Down-maps the canonical `RealLifeChallenge` onto the platform's
`real_life_challenge` key, validates against the platform contract BEFORE emit
(a stop-the-line condition per the Flow v2 plan §6), and strips server-only
answer fields so nothing leaks into the student browser (§10.1).

Reverse-test variant: the governing formula must never be named in the
student-visible body (spec §11/§12). The reveal lives only in `answer_key`,
which `to_platform_case` never maps into the payload — so it cannot reach the
browser. For this variant `adapt_to_beta` runs the Strip-Test (no-leak)
validation and treats a leak as stop-the-line, the same as a platform-contract
failure; there is no longer a "compatibility bridge" disclaimer.
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
from app.schemas.real_life import (
    RealLifeChallenge,
    reverse_test_conformance_errors,
)


class ReverseTestLeakError(ValueError):
    """Raised when the reverse-test inferred formula leaks into the
    student-visible body — a stop-the-line content-safety violation."""

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
    """Return (student_safe_payload, warning).

    The payload is the validated platform case with server-only fields stripped
    — safe for the student browser. The `answer_key` reveal is never mapped into
    the platform case, so the inferred formula cannot reach the browser.

    For the reverse-test variant we additionally run the Strip-Test: if the
    inferred formula leaked into any student-visible field, raise
    ``ReverseTestLeakError`` (stop-the-line). On success ``warning`` is ``None``
    — the reverse-test is now a validated path, not a compatibility bridge.
    """
    rl = _coerce(canonical)

    if rl.variant == "reverse_test_same_story_new_numbers":
        errors = reverse_test_conformance_errors(rl)
        if errors:
            raise ReverseTestLeakError(
                "reverse-test RLC failed the Strip-Test: " + "; ".join(errors)
            )

    case = to_platform_case(rl)
    payload = _strip_server_only(case.model_dump())
    return payload, None
