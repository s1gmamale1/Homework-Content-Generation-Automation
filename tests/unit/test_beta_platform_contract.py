"""Beta export must conform to the platform RealLifeChallengeCase contract.

Builds a valid canonical RLC, runs it through the beta adapter, and asserts the
platform validator accepts it and the 5-step invariants hold. Also checks the
reverse-test variant is validated (formula present in answer_key, never leaked
into the student body), and that a malformed canonical is rejected
(stop-the-line).
"""

import json

import pytest
from pydantic import ValidationError

from app.schemas.platform import RealLifeChallengeCase
from app.schemas.platform.real_life_challenge import REQUIRED_STEP_ORDER
from app.schemas.real_life import (
    RealLifeChallenge,
    RLCAnswerKey,
    RLCConceptChip,
    RLCConceptSelectStep,
    RLCDecisionOption,
    RLCDecisionStep,
    RLCReasoningStep,
)
from app.services import beta_export
from app.services.beta_export import ReverseTestLeakError


def _decision_step(prompt: str) -> RLCDecisionStep:
    return RLCDecisionStep(
        prompt=prompt,
        options=[
            RLCDecisionOption(label="right call", is_correct=True, consequence="ok"),
            RLCDecisionOption(label="wrong call", is_correct=False),
        ],
        expected_reasoning=["present_perfect"],
    )


def make_canonical(
    variant: str = "expert_case_5_step",
    answer_key: RLCAnswerKey | None = None,
) -> RealLifeChallenge:
    return RealLifeChallenge(
        scenario_id="rlc_eng_g7_001",
        variant=variant,
        answer_key=answer_key,
        role="BBC Tashkent junior reporter",
        task="Write a 3-sentence update on the IT Park expansion.",
        context="You have visited the site. Your accuracy meets Global Standards.",
        grade_band="G7-9",
        pisa="L4",
        source_concept_ids=["present_perfect"],
        prediction_prompt="Before you write, what tense will the update need?",
        decision=_decision_step("Which sentence reports what you saw?"),
        info_request=_decision_step("What do you need to confirm before filing?"),
        final_decision=_decision_step("File which version to your editor?"),
        concept_select=RLCConceptSelectStep(
            prompt="Which lesson concept did this test?",
            concept_chips=[
                RLCConceptChip(label="present perfect", is_correct=True),
                RLCConceptChip(label="past continuous", is_correct=False),
                RLCConceptChip(label="future simple", is_correct=False),
            ],
        ),
        reasoning=RLCReasoningStep(
            prompt="Explain why the present perfect fits here.",
            min_chars=60,
            acceptable_keywords=["unfinished time", "result now"],
            sample_acceptable_answer="The action connects to now, so present perfect fits.",
        ),
        expert_feedback="A senior editor would accept this.",
        final_summary_template="You reasoned like a real reporter.",
    )


class TestPlatformContract:
    def test_adapter_output_validates_against_platform(self):
        canonical = make_canonical()
        case = beta_export.to_platform_case(canonical)
        assert isinstance(case, RealLifeChallengeCase)
        assert tuple(s.kind for s in case.steps) == REQUIRED_STEP_ORDER

    def test_accepts_dict_input(self):
        canonical = make_canonical().model_dump()
        case = beta_export.to_platform_case(canonical)
        assert len(case.steps) == 5

    def test_reverse_test_without_answer_key_is_rejected(self):
        # reverse-test variant requires answer_key.inferred_formula (stop-the-line).
        canonical = make_canonical(variant="reverse_test_same_story_new_numbers")
        with pytest.raises(ReverseTestLeakError):
            beta_export.adapt_to_beta(canonical)

    def test_reverse_test_clean_validates_and_hides_formula(self):
        canonical = make_canonical(
            variant="reverse_test_same_story_new_numbers",
            answer_key=RLCAnswerKey(
                inferred_formula="distance = speed × time",
                derivation="From the table, distance scales linearly with elapsed time.",
                concept_tags=["proportional_reasoning"],
            ),
        )
        payload, warning = beta_export.adapt_to_beta(canonical)
        assert warning is None  # no longer a compatibility-bridge stub
        # the reveal must never reach the student payload
        assert "distance = speed" not in json.dumps(payload).lower()

    def test_reverse_test_formula_leak_is_rejected(self):
        # The formula appears verbatim in the student-visible task → Strip Test fails.
        canonical = make_canonical(
            variant="reverse_test_same_story_new_numbers",
            answer_key=RLCAnswerKey(
                inferred_formula="distance = speed × time",
                derivation="…",
            ),
        )
        canonical.task = "Use distance = speed × time to predict the arrival."
        with pytest.raises(ReverseTestLeakError):
            beta_export.adapt_to_beta(canonical)

    def test_expert_variant_has_no_warning(self):
        _payload, warning = beta_export.adapt_to_beta(make_canonical())
        assert warning is None

    def test_decision_step_without_correct_option_is_rejected(self):
        canonical = make_canonical()
        for opt in canonical.decision.options:
            opt.is_correct = False
        with pytest.raises(ValidationError):
            beta_export.to_platform_case(canonical)

    def test_concept_select_needs_three_chips(self):
        canonical = make_canonical()
        canonical.concept_select.concept_chips = canonical.concept_select.concept_chips[:2]
        with pytest.raises(ValidationError):
            beta_export.to_platform_case(canonical)

    def test_reasoning_min_chars_out_of_range_rejected(self):
        canonical = make_canonical()
        canonical.reasoning.min_chars = 5000
        with pytest.raises(ValidationError):
            beta_export.to_platform_case(canonical)
