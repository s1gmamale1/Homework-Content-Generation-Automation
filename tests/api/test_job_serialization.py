from app.schemas.job import PhaseOut


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
