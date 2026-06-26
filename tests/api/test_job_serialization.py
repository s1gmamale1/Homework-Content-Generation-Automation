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

    out = PhaseOut.model_validate(_Row())
    assert out.judge_status == "refused"
