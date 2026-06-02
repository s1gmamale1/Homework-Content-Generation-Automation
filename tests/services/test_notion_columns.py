from app.models.book import Book
from app.models.homework_job import HomeworkJob
from app.models.toc_entry import TOCEntry


def test_new_columns_exist_on_models():
    assert "grade" in Book.__table__.columns
    assert "notion_archived_at" in HomeworkJob.__table__.columns
    assert "notion_homework_page_id" in TOCEntry.__table__.columns
