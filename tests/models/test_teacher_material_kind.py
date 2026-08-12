from app.models.homework_job import HomeworkJob
from app.models.batch import Batch


def test_homework_job_kind_column_defaults_homework():
    cols = HomeworkJob.__table__.c
    assert "kind" in cols
    assert cols["kind"].nullable is False
    assert cols["kind"].server_default is not None
    assert cols["kind"].server_default.arg == "homework"


def test_homework_job_kind_check_constraint():
    check_names = {
        c.name for c in HomeworkJob.__table__.constraints
        if c.__class__.__name__ == "CheckConstraint"
    }
    assert "ck_homework_jobs_kind" in check_names
    ck = next(
        c for c in HomeworkJob.__table__.constraints
        if getattr(c, "name", None) == "ck_homework_jobs_kind"
    )
    assert "teacher_material" in str(ck.sqltext)
    assert "homework" in str(ck.sqltext)


def test_batch_kind_column_defaults_homework():
    cols = Batch.__table__.c
    assert "kind" in cols
    assert cols["kind"].nullable is False
    assert cols["kind"].server_default is not None
    assert cols["kind"].server_default.arg == "homework"


def test_batch_unique_constraint_renamed_and_widened():
    unique_names = {
        c.name for c in Batch.__table__.constraints
        if c.__class__.__name__ == "UniqueConstraint"
    }
    assert "uq_batches_book_id_transport_output_language_kind" in unique_names
    assert "uq_batches_book_id_transport_output_language" not in unique_names
    uq = next(
        c for c in Batch.__table__.constraints
        if getattr(c, "name", None) == "uq_batches_book_id_transport_output_language_kind"
    )
    col_names = {col.name for col in uq.columns}
    assert col_names == {"book_id", "transport", "output_language", "kind"}
