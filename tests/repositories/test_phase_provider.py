import inspect

from app.models import PhaseOutput
from app.repositories import phase_outputs as phase_repo


def test_set_status_accepts_provider_param():
    assert "provider" in inspect.signature(phase_repo.set_status).parameters


def test_model_has_provider_attribute():
    po = PhaseOutput(
        job_id=None, phase_name="flashcards", phase_order=1,
        prompt_hash="h", model_name="m", status="pending",
    )
    assert po.provider is None


def test_create_or_reset_clears_provider():
    assert "provider = None" in inspect.getsource(phase_repo.create_or_reset)
