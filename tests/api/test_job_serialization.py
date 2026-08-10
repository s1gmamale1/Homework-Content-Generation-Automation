from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient

from app.schemas.job import PhaseOut
from main import app


def test_phaseout_serializes_judge_status():
    class _Row:
        phase_name = "preview"
        phase_order = 1
        status = "done"
        output_md = "x"
        tokens_input = 1
        tokens_output = 1
        started_at = None
        completed_at = None
        error_message = None
        validation_warnings = None
        judge_status = "refused"
        solver_status = None

    out = PhaseOut.model_validate(_Row())
    assert out.judge_status == "refused"


def test_phaseout_serializes_solver_status():
    class _Row:
        phase_name = "practice-error-detection"
        phase_order = 1
        status = "done"
        output_md = "x"
        tokens_input = 1
        tokens_output = 1
        started_at = None
        completed_at = None
        error_message = None
        validation_warnings = None
        judge_status = None
        solver_status = "mismatch_regen"

    out = PhaseOut.model_validate(_Row())
    assert out.solver_status == "mismatch_regen"


def test_phaseout_serializes_failed_mismatch_blocked_status():
    class _Row:
        phase_name = "memory-check"
        phase_order = 4
        status = "failed"
        output_md = "# Attempted repair retained for inspection"
        tokens_input = 100
        tokens_output = 50
        started_at = None
        completed_at = None
        error_message = "persistent answer-key mismatch"
        validation_warnings = ["[high] q1: wrong key"]
        judge_status = "ok"
        solver_status = "mismatch_blocked"

    out = PhaseOut.model_validate(_Row())

    assert out.status == "failed"
    assert out.solver_status == "mismatch_blocked"
    assert out.model_dump()["solver_status"] == "mismatch_blocked"


def test_get_failed_job_retains_blocked_phase_diagnostics():
    """Removing failed phases or their retained markdown from ``_job_out``
    would make a blocked answer key invisible to the operator API."""
    job_id = uuid4()
    phase = SimpleNamespace(
        phase_name="memory-check",
        phase_order=4,
        status="failed",
        output_md="# Attempted repair retained for inspection",
        tokens_input=100,
        tokens_output=50,
        started_at=None,
        completed_at=None,
        error_message="persistent answer-key mismatch",
        validation_warnings=["[high] q1: wrong key"],
        judge_status="ok",
        solver_status="mismatch_blocked",
    )
    job = SimpleNamespace(
        id=job_id,
        book_id=uuid4(),
        toc_entry_id=uuid4(),
        subject="biology",
        status="failed",
        phase_outputs=[phase],
        selected_phases=["memory-check"],
    )

    with patch(
        "app.api.v1.jobs.jobs_repo.get_with_phases",
        AsyncMock(return_value=job),
    ):
        response = TestClient(app).get(f"/api/v1/jobs/{job_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert len(body["phases"]) == 1
    blocked = body["phases"][0]
    assert blocked["status"] == "failed"
    assert blocked["solver_status"] == "mismatch_blocked"
    assert blocked["output_md"] == "# Attempted repair retained for inspection"
    assert blocked["error_message"] == "persistent answer-key mismatch"
