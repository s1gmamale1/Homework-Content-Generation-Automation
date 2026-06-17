from app.models import HomeworkJob, Batch


def test_new_columns_present_and_default_null():
    job = HomeworkJob(book_id=None, toc_entry_id=None, subject="biology", status="pending")
    for attr in ("extract_provider", "extract_model", "judge_provider", "judge_model"):
        assert getattr(job, attr) is None
    batch = Batch(book_id=None, subject="biology", provider="gemini", transport="cli")
    for attr in ("extract_provider", "extract_model", "judge_provider", "judge_model"):
        assert getattr(batch, attr) is None
