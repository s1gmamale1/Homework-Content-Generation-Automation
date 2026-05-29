"""Unit tests for the Flow v2 content schemas (``app.schemas.flow_v2``).

These encode the structural content rules from the Pure Content Automation
plan (§6 CBP, §12 GenerationProfile) and the CBP
generation standard: difficulty is metadata (never branch-skipping), the
Decision Process Explanation is never an MCQ, a Case-Based Preview has
exactly 3 checkpoints with a required DPE, and the final simulation carries
both a correct and a wrong path.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.flow_v2 import (
    CaseBasedPreview,
    CaseCheckpoint,
    CaseSetup,
    CaseSimulation,
    CompletionRules,
    DecisionProcessExplanation,
    FeedbackSummary,
    GenerationProfile,
    SourceConcept,
    SourceMap,
)


# ─────────────────────────────────────────────────────────────────────
# GenerationProfile — difficulty is metadata, not a phase switch
# ─────────────────────────────────────────────────────────────────────


def test_generation_profile_defaults() -> None:
    gp = GenerationProfile(subject_family="math_family")
    assert gp.difficulty == "standard"  # metadata default, no skipping
    assert gp.target_skills == []
    assert gp.grade_band is None


def test_generation_profile_accepts_arbitrary_difficulty() -> None:
    """Difficulty is free-form metadata — not the old easy/hard enum that
    branched the phase list."""
    assert GenerationProfile(subject_family="math_family", difficulty="olympiad").difficulty == "olympiad"
    assert GenerationProfile(subject_family="sciences", difficulty="easy").difficulty == "easy"


# ─────────────────────────────────────────────────────────────────────
# DecisionProcessExplanation — open-ended, never an MCQ
# ─────────────────────────────────────────────────────────────────────


def _valid_dpe_kwargs() -> dict:
    return dict(
        prompt="Explain how you decided which operation to use.",
        expected_components=["concept", "method", "mistake"],
        rubric={"concept": 1, "method": 1, "mistake": 1},
        sample_acceptable_answer="I divided because the whole is shared equally.",
    )


def test_dpe_valid_defaults() -> None:
    dpe = DecisionProcessExplanation(**_valid_dpe_kwargs())
    assert dpe.eval_mode == "ai"
    assert dpe.min_chars == 60
    assert dpe.options is None


def test_dpe_rejects_options_must_never_be_mcq() -> None:
    with pytest.raises(ValidationError):
        DecisionProcessExplanation(**_valid_dpe_kwargs(), options=["a", "b", "c"])


def test_dpe_rejects_unknown_expected_component() -> None:
    kwargs = _valid_dpe_kwargs()
    kwargs["expected_components"] = ["concept", "vibes"]
    with pytest.raises(ValidationError):
        DecisionProcessExplanation(**kwargs)


# ─────────────────────────────────────────────────────────────────────
# CaseBasedPreview — exactly 3 checkpoints, DPE required, simulation paths
# ─────────────────────────────────────────────────────────────────────


def _checkpoint(intent: str = "identify") -> CaseCheckpoint:
    return CaseCheckpoint(
        intent=intent,
        form="mcq",
        question="Which operation applies?",
        options=["add", "divide"],
        correct_index=1,
        feedback="Divide — the quantity is shared equally.",
    )


def _valid_cbp_kwargs() -> dict:
    return dict(
        title="Sharing juice at the class event",
        student_role="planner",
        case_type="practical_problem",
        source_concept_ids=["c1"],
        case_setup=CaseSetup(
            narrative="You help share juice equally.",
            student_role="planner",
            task="Decide how much each cup gets.",
        ),
        checkpoints=[
            _checkpoint("identify"),
            _checkpoint("decide"),
            _checkpoint("justify_or_avoid_mistake"),
        ],
        decision_process_explanation=DecisionProcessExplanation(**_valid_dpe_kwargs()),
        final_simulation=CaseSimulation(
            correct_path="3/5 ÷ 3 = 1/5 ℓ per cup.",
            wrong_path="3/5 × 3 = 9/5 ℓ, impossible.",
        ),
        feedback_summary=FeedbackSummary(
            understood="Chose division.",
            mistake="Multiplying instead.",
            review="Dividing a fraction by a whole.",
        ),
        completion_rules=CompletionRules(
            pass_condition="≥2/3 checkpoints + DPE attempted.",
            retry_condition="Otherwise needs retry.",
        ),
    )


def test_cbp_valid() -> None:
    cbp = CaseBasedPreview(**_valid_cbp_kwargs())
    assert len(cbp.checkpoints) == 3
    assert cbp.decision_process_explanation.options is None


def test_cbp_requires_exactly_three_checkpoints() -> None:
    kwargs = _valid_cbp_kwargs()
    kwargs["checkpoints"] = [_checkpoint("identify"), _checkpoint("decide")]
    with pytest.raises(ValidationError):
        CaseBasedPreview(**kwargs)

    kwargs["checkpoints"] = [_checkpoint() for _ in range(4)]
    with pytest.raises(ValidationError):
        CaseBasedPreview(**kwargs)


def test_cbp_requires_dpe() -> None:
    kwargs = _valid_cbp_kwargs()
    del kwargs["decision_process_explanation"]
    with pytest.raises(ValidationError):
        CaseBasedPreview(**kwargs)


def test_cbp_requires_nonempty_source_concept_ids() -> None:
    kwargs = _valid_cbp_kwargs()
    kwargs["source_concept_ids"] = []
    with pytest.raises(ValidationError):
        CaseBasedPreview(**kwargs)


def test_final_simulation_requires_both_paths() -> None:
    with pytest.raises(ValidationError):
        CaseSimulation(correct_path="only the correct path given")


# ─────────────────────────────────────────────────────────────────────
# SourceMap mock contract — the shared anchor that unblocks parallel work
# ─────────────────────────────────────────────────────────────────────


def test_source_map_mock_contract() -> None:
    sm = SourceMap(
        subject_family="math_family",
        chapter="Oddiy kasrlar",
        section="To'g'ri kasrni natural songa bo'lish",
        concepts=[
            SourceConcept(id="c1", label="proper fraction"),
            SourceConcept(id="c2", label="divide fraction by whole", kind="process"),
        ],
    )
    assert sm.concept_ids() == ["c1", "c2"]
    assert sm.concepts[1].kind == "process"


def test_source_map_requires_at_least_one_concept() -> None:
    with pytest.raises(ValidationError):
        SourceMap(
            subject_family="math_family",
            chapter="Ch",
            section="Sec",
            concepts=[],
        )
