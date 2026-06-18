# tests/repositories/test_phase_judge_status.py
import inspect

from app.models import PhaseOutput
from app.repositories import phase_outputs as phase_repo


def test_set_status_accepts_judge_status_param():
    # Assert the seam by signature (mirrors test_phase_validation_warnings.py).
    assert "judge_status" in inspect.signature(phase_repo.set_status).parameters


def test_model_has_judge_status_attribute():
    po = PhaseOutput(
        job_id=None, phase_name="flashcards", phase_order=1,
        prompt_hash="h", model_name="m", status="pending",
    )
    # Column attribute is addressable and defaults to None before flush.
    assert po.judge_status is None


def test_create_or_reset_clears_judge_status():
    # create_or_reset must zero per-attempt fields, including the new one.
    assert "judge_status = None" in inspect.getsource(phase_repo.create_or_reset)
