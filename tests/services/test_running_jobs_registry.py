import inspect
from app.services import worker


def test_running_jobs_registry_exists_and_is_populated():
    assert hasattr(worker, "RUNNING_JOBS"), "module-level RUNNING_JOBS registry required"
    assert isinstance(worker.RUNNING_JOBS, dict)
    src = inspect.getsource(worker.Worker._execute_job)
    assert "RUNNING_JOBS[job_id]" in src, "_execute_job must register its task"
    assert "current_task()" in src, "register the running task handle"
    assert "RUNNING_JOBS.pop(job_id" in src, "must unregister in finally"
