"""Unit tests for the Flow v2 content schemas (``app.schemas.flow_v2``).

These encode the structural content rules from the Pure Content Automation
plan (§6 CBP, §10 WeakPointSignal, §12 GenerationProfile) and the CBP
generation standard: difficulty is metadata (never branch-skipping), the
Decision Process Explanation is never an MCQ, a Case-Based Preview has
exactly 3 checkpoints with a required DPE, and the final simulation carries
both a correct and a wrong path.

Phase 3 additions (Learning Sections):
- Flashcard.id is required and must be non-empty (stable ID for Memory Check refs)
- MemoryCheckItem.flashcard_id is required — every item must trace to a card
- MemoryCheckItem.kind is locked to
  {"multiple_choice", "fill_blank", "choose_correct_explanation"}
- MemoryCheckPack.pass_threshold defaults to 0.60
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.flashcards import Flashcard, FlashcardsPack
from app.schemas.memory_check import MemoryCheckItem, MemoryCheckPack
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
    WeakPointSignal,
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
# WeakPointSignal
# ─────────────────────────────────────────────────────────────────────


def _valid_signal_kwargs() -> dict:
    return dict(
        concept_id="c1",
        source_phase="case_based_preview",
        evidence=[{"checkpoint": 2, "selected": "add"}],
        severity="medium",
        target_accounts=["teacher", "parent"],
        recommended_action="Re-teach dividing a fraction by a whole.",
    )


def test_weak_point_signal_valid() -> None:
    sig = WeakPointSignal(**_valid_signal_kwargs())
    assert sig.severity == "medium"
    assert "teacher" in sig.target_accounts


def test_weak_point_signal_rejects_bad_severity() -> None:
    kwargs = _valid_signal_kwargs()
    kwargs["severity"] = "catastrophic"
    with pytest.raises(ValidationError):
        WeakPointSignal(**kwargs)


def test_weak_point_signal_rejects_bad_target_account() -> None:
    kwargs = _valid_signal_kwargs()
    kwargs["target_accounts"] = ["teacher", "principal"]
    with pytest.raises(ValidationError):
        WeakPointSignal(**kwargs)


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


# ─────────────────────────────────────────────────────────────────────
# Flashcard stable IDs (Phase 3)
# ─────────────────────────────────────────────────────────────────────


def test_flashcard_requires_id() -> None:
    """Phase 3: every card must carry a stable id for Memory Check refs."""
    with pytest.raises(ValidationError):
        Flashcard(front="Front", back="Back")  # missing id


def test_flashcard_id_must_be_nonempty() -> None:
    with pytest.raises(ValidationError):
        Flashcard(id="", front="Front", back="Back")


def test_flashcard_valid_with_id() -> None:
    card = Flashcard(id="card_1", front="Fotosintez", back="CO₂ + H₂O → O₂ + shakar")
    assert card.id == "card_1"
    assert card.hint is None


def test_flashcards_pack_preserves_ids() -> None:
    pack = FlashcardsPack(cards=[
        Flashcard(id="card_1", front="A", back="B"),
        Flashcard(id="card_2", front="C", back="D"),
    ])
    assert [c.id for c in pack.cards] == ["card_1", "card_2"]


def test_flashcards_pack_rejects_duplicate_ids() -> None:
    with pytest.raises(ValidationError):
        FlashcardsPack(cards=[
            Flashcard(id="card_1", front="A", back="B"),
            Flashcard(id="card_1", front="C", back="D"),
        ])


# ─────────────────────────────────────────────────────────────────────
# Memory Check (Phase 3) — 3 supported kinds + flashcard ID refs
# ─────────────────────────────────────────────────────────────────────


def _mc_item(**kwargs) -> dict:
    base = dict(
        flashcard_id="card_1",
        prompt="Fotosintez nima?",
        kind="multiple_choice",
        options=["A", "B", "C", "D"],
        correct_index=0,
    )
    base.update(kwargs)
    return base


def test_memory_check_item_valid_multiple_choice() -> None:
    item = MemoryCheckItem(**_mc_item())
    assert item.kind == "multiple_choice"
    assert item.flashcard_id == "card_1"


def test_memory_check_item_valid_fill_blank() -> None:
    item = MemoryCheckItem(
        **_mc_item(
            kind="fill_blank",
            prompt="Fotosintez uchun pigment nomi: _____.",
            options=[],
            correct_index=None,
            explanation="xlorofill",
        )
    )
    assert item.kind == "fill_blank"


def test_memory_check_item_valid_choose_correct_explanation() -> None:
    item = MemoryCheckItem(**_mc_item(kind="choose_correct_explanation"))
    assert item.kind == "choose_correct_explanation"


def test_memory_check_item_rejects_unsupported_kind() -> None:
    """STOP: MC emits unsupported type → ValidationError."""
    with pytest.raises(ValidationError):
        MemoryCheckItem(**_mc_item(kind="ynng"))


def test_memory_check_item_rejects_empty_flashcard_id() -> None:
    """Every item must reference a flashcard — empty string is rejected."""
    with pytest.raises(ValidationError):
        MemoryCheckItem(**_mc_item(flashcard_id=""))


def test_memory_check_item_requires_flashcard_id() -> None:
    kwargs = _mc_item()
    del kwargs["flashcard_id"]
    with pytest.raises(ValidationError):
        MemoryCheckItem(**kwargs)


def test_memory_check_pack_defaults() -> None:
    pack = MemoryCheckPack(items=[MemoryCheckItem(**_mc_item())])
    assert pack.pass_threshold == 0.60


def test_memory_check_pack_rejects_threshold_other_than_sixty_percent() -> None:
    with pytest.raises(ValidationError):
        MemoryCheckPack(
            items=[MemoryCheckItem(**_mc_item())],
            pass_threshold=0.80,
        )


def test_memory_check_pack_multiple_kinds() -> None:
    items = [
        MemoryCheckItem(**_mc_item(flashcard_id="card_1", kind="multiple_choice")),
        MemoryCheckItem(**_mc_item(flashcard_id="card_2", kind="fill_blank",
                                   options=[], correct_index=None)),
        MemoryCheckItem(**_mc_item(flashcard_id="card_3", kind="choose_correct_explanation")),
    ]
    pack = MemoryCheckPack(items=items)
    kinds = {it.kind for it in pack.items}
    assert kinds == {"multiple_choice", "fill_blank", "choose_correct_explanation"}
