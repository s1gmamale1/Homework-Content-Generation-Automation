from app.services import pipeline
from app.services.errors import PersistentSolverMismatch


def test_persistent_solver_mismatch_is_nonblank_and_bounded():
    exc = PersistentSolverMismatch(
        "memory-check",
        [f"[high] q{i}: wrong key" for i in range(10)],
    )
    assert exc.phase_name == "memory-check"
    assert len(exc.warnings) == 10
    assert "persistent answer-key mismatch" in str(exc)
    assert "memory-check" in str(exc)
    assert len(str(exc)) < 1000


def test_persistent_solver_mismatch_keeps_repair_cause():
    cause = ConnectionError("solver recheck disconnected")
    exc = PersistentSolverMismatch("practice-rlc", ["[high] step 2"], cause)
    assert exc.repair_error is cause
    assert "solver recheck disconnected" in str(exc)


def test_persistent_mismatch_is_not_queue_retry_worthy():
    exc = PersistentSolverMismatch("memory-check", ["[high] q1"])
    assert pipeline._requeue_worthy(exc) is False


def test_real_network_repair_failure_remains_queue_retry_worthy():
    assert pipeline._requeue_worthy(ConnectionError("connection reset")) is True
