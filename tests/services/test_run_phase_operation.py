import inspect

from app.services import agent


def test_run_phase_has_operation_param_defaulting_to_phase_run():
    sig = inspect.signature(agent.run_phase)
    assert "operation" in sig.parameters
    assert sig.parameters["operation"].default == "phase.run"


def test_run_phase_threads_operation_not_hardcoded():
    src = inspect.getsource(agent.run_phase)
    assert 'operation="phase.run"' not in src
    assert src.count("operation=operation") >= 6
