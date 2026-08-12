"""TOCEntry gains two nullable columns for the teacher-deck Notion archival
lane (migration 0059): the shared Lesson Topic page id (parent of both the
Homework sub-page and the Teacher Deck sub-page), and the teacher-side mirror
of notion_archived_job_id. Pure unit test — no DB."""
from app.models.toc_entry import TOCEntry


def test_toc_entry_exposes_new_notion_columns_defaulting_none():
    entry = TOCEntry()
    assert entry.notion_lesson_page_id is None
    assert entry.notion_teacher_deck_job_id is None
