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
