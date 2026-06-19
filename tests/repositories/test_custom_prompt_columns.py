"""custom_prompts + selected_phases JSONB columns exist and are nullable (no DB)."""
from app.models.batch import Batch
from app.models.homework_job import HomeworkJob


def test_homework_job_has_custom_columns():
    for name in ("custom_prompts", "selected_phases"):
        col = HomeworkJob.__table__.columns[name]
        assert col.nullable is True
        assert col.server_default is None


def test_batch_has_custom_columns():
    for name in ("custom_prompts", "selected_phases"):
        col = Batch.__table__.columns[name]
        assert col.nullable is True
