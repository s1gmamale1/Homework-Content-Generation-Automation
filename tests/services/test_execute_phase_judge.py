import inspect

from app.services import pipeline


def test_execute_phase_invokes_the_judge():
    src = inspect.getsource(pipeline._execute_phase)
    # _execute_phase now delegates to _judge_with_timeout (which wraps
    # phase_judge.judge in the per-attempt timeout) — check the indirection.
    assert "_judge_with_timeout" in src
    assert "produced_by" in src
    # Content generation now goes through _generate_artifact (structured attempt
    # → markdown fallback): initial generation + judge regen + solver regen.
    # The markdown leg still IS the failover driver — assert the indirection
    # rather than the old direct call, so "regen runs through failover" is still
    # covered end to end.
    assert src.count("await _generate(") >= 3, src.count("await _generate(")
    assert "_generate_artifact" in src
    assert "_run_with_failover" in inspect.getsource(pipeline._run_markdown_attempt)
    # extract keeps its own direct failover call (no structured lane)
    assert "_run_with_failover" in src
    # the regen is GUARDED — an exhausted regen must not fail the job
    assert "regen failed" in src
    # regen is gated on a MAJOR issue, not merely "not passed" (minor nits only warn)
    assert "has_major" in src


def test_execute_phase_no_longer_calls_deterministic_validator():
    src = inspect.getsource(pipeline._execute_phase)
    assert "phase_validator" not in src


def test_phase_validator_module_is_gone():
    import importlib
    try:
        importlib.import_module("app.services.phase_validator")
        raised = False
    except ModuleNotFoundError:
        raised = True
    assert raised, "phase_validator should be retired"
