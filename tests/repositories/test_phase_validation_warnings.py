# tests/repositories/test_phase_validation_warnings.py
import inspect

from app.models import PhaseOutput
from app.repositories import phase_outputs as phase_repo


def test_set_status_accepts_validation_warnings_param():
    # The repo suite is DB-free (see tests/conftest.py) — assert the seam by
    # signature, the way test_notion_repo_methods.py / test_books_grade.py do.
    assert "validation_warnings" in inspect.signature(phase_repo.set_status).parameters


def test_model_has_validation_warnings_attribute():
    po = PhaseOutput(
        job_id=None, phase_name="flashcards", phase_order=1,
        prompt_hash="h", model_name="m", status="pending",
    )
    # Column attribute is addressable and defaults to None before flush.
    assert po.validation_warnings is None


def test_create_or_reset_clears_validation_warnings():
    # No DB in this harness, so assert the reset is present in the source —
    # create_or_reset must zero per-attempt fields, including the new one.
    assert "validation_warnings = None" in inspect.getsource(phase_repo.create_or_reset)
