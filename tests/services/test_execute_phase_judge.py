import inspect

from app.services import pipeline


def test_execute_phase_invokes_the_judge():
    src = inspect.getsource(pipeline._execute_phase)
    assert "phase_judge.judge" in src
    assert "produced_by" in src
    assert src.count("_run_with_failover") >= 2
    # the regen is GUARDED — an exhausted regen must not fail the job
    assert "regen failed" in src


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
