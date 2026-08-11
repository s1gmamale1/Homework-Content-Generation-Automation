import pytest

from app.services import flows, pipeline


def test_teacher_material_flow_for_returns_single_phase():
    assert flows.teacher_material_flow_for("physics") == ["teacher-deck"]


def test_teacher_material_flow_for_unknown_subject_raises():
    with pytest.raises(KeyError):
        flows.teacher_material_flow_for("chemistry-unknown")


def test_teacher_deck_not_in_phase_deps():
    # extract flows into teacher-deck via lesson_context, not prior_outputs —
    # absent from PHASE_DEPS means no declared deps, which is correct.
    assert "teacher-deck" not in flows.PHASE_DEPS


def test_pipeline_plans_teacher_material_sequence():
    full_flow = pipeline._plan_full_flow("teacher_material", "physics")
    sequence = ["extract", *full_flow]
    assert sequence == ["extract", "teacher-deck"]


def test_pipeline_plans_homework_sequence_unchanged():
    # kind defaults to 'homework' and must still yield flow_for(subject).
    full_flow = pipeline._plan_full_flow("homework", "physics")
    assert full_flow == flows.flow_for("physics")
    assert "teacher-deck" not in full_flow
