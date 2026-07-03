from app.models.homework_job import HomeworkJob
from app.models.batch import Batch
from app.models.launch_defaults import LaunchDefaults
from app.models.phase_output import PhaseOutput


def test_job_and_batch_have_solver_role_columns():
    for M in (HomeworkJob, Batch):
        cols = M.__table__.c
        assert "solver_transport" in cols and "solver_provider" in cols and "solver_model" in cols
        assert cols["solver_transport"].nullable is False
        assert cols["solver_transport"].server_default is not None


def test_launch_defaults_and_phase_output_columns():
    assert {"solver_provider", "solver_model", "solver_transport"} <= set(LaunchDefaults.__table__.c.keys())
    assert "solver_status" in PhaseOutput.__table__.c
    assert PhaseOutput.__table__.c["solver_status"].nullable is True
