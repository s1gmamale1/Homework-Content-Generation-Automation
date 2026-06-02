import inspect
from app.repositories import jobs as jobs_repo
from app.repositories import toc_entries as toc_repo


def test_repo_methods_exist_with_expected_signature():
    assert hasattr(jobs_repo, "set_notion_archived")
    assert hasattr(toc_repo, "set_notion_homework_page_id")
    jp = inspect.signature(jobs_repo.set_notion_archived).parameters
    assert "job_id" in jp and "notion_archived_at" in jp
    tp = inspect.signature(toc_repo.set_notion_homework_page_id).parameters
    assert "toc_entry_id" in tp and "page_id" in tp
