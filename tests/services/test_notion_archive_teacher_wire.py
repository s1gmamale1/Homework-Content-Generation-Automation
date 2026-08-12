"""Task 6 — `archive_job` wired for `kind=teacher_material` jobs.

Mocked-seam integration tests (mirrors `test_notion_archive_stamp.py`'s style,
per the Task 6 brief's fallback option): `jobs_repo`/`books_repo`/`toc_repo`/
`phase_repo` are monkeypatched with hand-built `SimpleNamespace` rows, and
`na._push_with_retry` / `na._push_teacher_with_retry` are `AsyncMock`s so no
real Notion call ever happens. The setter mocks mutate the shared `section`/
`job` objects in place, so two sequential `archive_job` calls against the SAME
section see each other's stamps — this is what makes the order-independence
assertions (#2/#3 below) meaningful instead of trivially true.

Load-bearing: #2/#3 (order-independent Lesson-Topic adoption via
`notion_lesson_page_id`) and #4 (no-deck-content skip, never pushes).
"""
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

import app.services.notion_archive as na

FIXTURE_PATH = "tests/fixtures/teacher_deck/hindiston_topic19.json"


def _deck_content_json() -> dict:
    with open(FIXTURE_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def _job(kind="homework", *, job_id=None, toc_entry_id=None, archived=False,
         created_at=None):
    return SimpleNamespace(
        id=job_id or uuid4(), book_id=uuid4(), toc_entry_id=toc_entry_id or uuid4(),
        subject="jahon-tarixi-g11", output_language="uz", kind=kind, status="done",
        claim_token=None,
        created_at=created_at or datetime(2026, 6, 1, tzinfo=timezone.utc),
        notion_archived_at=(datetime.now(timezone.utc) if archived else None),
    )


def _section(toc_entry_id, *, homework_page_id=None, lesson_page_id=None,
             archived_job_id=None, teacher_deck_job_id=None):
    return SimpleNamespace(
        id=toc_entry_id, section_number="19", section_title="Hindiston Respublikasi",
        page_start=200, order_index=0,
        notion_homework_page_id=homework_page_id,
        notion_lesson_page_id=lesson_page_id,
        notion_archived_job_id=archived_job_id,
        notion_teacher_deck_job_id=teacher_deck_job_id,
    )


def _book():
    return SimpleNamespace(grade="11", original_filename="g11.pdf", id=uuid4())


_SENTINEL = object()


def _deck_phase(content_json=_SENTINEL, status="done"):
    return SimpleNamespace(
        phase_name="teacher-deck", status=status,
        content_json=_deck_content_json() if content_json is _SENTINEL else content_json,
    )


def _hw_phase():
    return SimpleNamespace(phase_name="case-based-preview", status="done", output_md="# CBP")


def _wire(monkeypatch):
    monkeypatch.setattr(na.settings, "notion_enabled", True)
    monkeypatch.setattr(na.settings, "notion_api_key", "ntn_x")
    monkeypatch.setattr(na.settings, "notion_subject_pages", {"jahon-tarixi-g11|11": "subj"})
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(na, "SessionLocal", MagicMock(return_value=session))


def _mutating_setters(section, jobs_by_id):
    """Setter mocks that actually mutate the shared section/job objects, so a
    second archive_job call against the same section observes the first
    call's stamps (this is what makes order-independence assertions real)."""
    def _set_lesson_page_id(_session, _section_id, page_id):
        section.notion_lesson_page_id = page_id

    def _set_teacher_deck_job(_session, _section_id, job_id):
        section.notion_teacher_deck_job_id = job_id

    def _set_homework_page_id(_session, _section_id, page_id):
        section.notion_homework_page_id = page_id

    def _set_archived_job(_session, _section_id, job_id):
        section.notion_archived_job_id = job_id

    def _set_archived(_session, job_id, ts):
        jobs_by_id[job_id].notion_archived_at = ts

    return {
        "set_notion_lesson_page_id": AsyncMock(side_effect=_set_lesson_page_id),
        "set_notion_teacher_deck_job": AsyncMock(side_effect=_set_teacher_deck_job),
        "set_notion_homework_page_id": AsyncMock(side_effect=_set_homework_page_id),
        "set_notion_archived_job": AsyncMock(side_effect=_set_archived_job),
        "set_notion_archived": AsyncMock(side_effect=_set_archived),
    }


@pytest.mark.asyncio
async def test_teacher_archives_stamps_lesson_and_deck_job_not_homework_columns(monkeypatch):
    job = _job(kind="teacher_material")
    section = _section(job.toc_entry_id)
    book = _book()
    _wire(monkeypatch)
    setters = _mutating_setters(section, {job.id: job})
    with patch.object(na.jobs_repo, "get", AsyncMock(return_value=job)), \
         patch.object(na.books_repo, "get", AsyncMock(return_value=book)), \
         patch.object(na.toc_repo, "titles_for_subject_grade", AsyncMock(return_value=[])), \
         patch.object(na.toc_repo, "get", AsyncMock(return_value=section)), \
         patch.object(na.phase_repo, "list_for_job", AsyncMock(return_value=[_deck_phase()])), \
         patch.object(na.toc_repo, "set_notion_lesson_page_id", setters["set_notion_lesson_page_id"]), \
         patch.object(na.toc_repo, "set_notion_teacher_deck_job", setters["set_notion_teacher_deck_job"]), \
         patch.object(na.toc_repo, "set_notion_homework_page_id", AsyncMock()) as hw_page, \
         patch.object(na.toc_repo, "set_notion_archived_job", AsyncMock()) as hw_job, \
         patch.object(na.jobs_repo, "set_notion_archived", setters["set_notion_archived"]), \
         patch.object(na, "NotionClientWrapper", MagicMock()), \
         patch.object(na, "_push_teacher_with_retry",
                       AsyncMock(return_value=("L1", "deck1"))) as push, \
         patch.object(na, "_push_with_retry", AsyncMock()) as hw_push:
        await na.archive_job(job.id)

    push.assert_awaited_once()
    hw_push.assert_not_awaited()
    assert section.notion_lesson_page_id == "L1"
    assert section.notion_teacher_deck_job_id == job.id
    assert job.notion_archived_at is not None
    # teacher path must NEVER touch the homework-only columns
    assert section.notion_homework_page_id is None
    assert section.notion_archived_job_id is None
    hw_page.assert_not_awaited()
    hw_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_order_independence_teacher_then_homework_adopts_lesson_page(monkeypatch):
    toc_entry_id = uuid4()
    section = _section(toc_entry_id)
    book = _book()
    teacher_job = _job(kind="teacher_material", toc_entry_id=toc_entry_id)
    hw_job = _job(kind="homework", toc_entry_id=toc_entry_id,
                  created_at=datetime(2026, 6, 2, tzinfo=timezone.utc))
    jobs_by_id = {teacher_job.id: teacher_job, hw_job.id: hw_job}
    _wire(monkeypatch)
    setters = _mutating_setters(section, jobs_by_id)

    with patch.object(na.jobs_repo, "get", AsyncMock(side_effect=lambda s, jid: jobs_by_id.get(jid))), \
         patch.object(na.books_repo, "get", AsyncMock(return_value=book)), \
         patch.object(na.toc_repo, "titles_for_subject_grade", AsyncMock(return_value=[])), \
         patch.object(na.toc_repo, "get", AsyncMock(return_value=section)), \
         patch.object(na.toc_repo, "set_notion_lesson_page_id", setters["set_notion_lesson_page_id"]), \
         patch.object(na.toc_repo, "set_notion_teacher_deck_job", setters["set_notion_teacher_deck_job"]), \
         patch.object(na.toc_repo, "set_notion_homework_page_id", setters["set_notion_homework_page_id"]), \
         patch.object(na.toc_repo, "set_notion_archived_job", setters["set_notion_archived_job"]), \
         patch.object(na.jobs_repo, "set_notion_archived", setters["set_notion_archived"]), \
         patch.object(na, "NotionClientWrapper", MagicMock()), \
         patch.object(na, "_push_teacher_with_retry",
                       AsyncMock(return_value=("L1", "deck1"))), \
         patch.object(na, "_push_with_retry", AsyncMock(return_value=(None, "hw1"))) as hw_push:
        # teacher archives first
        with patch.object(na.phase_repo, "list_for_job", AsyncMock(return_value=[_deck_phase()])):
            await na.archive_job(teacher_job.id)
        assert section.notion_lesson_page_id == "L1"

        # homework archives second, on the SAME section
        with patch.object(na.phase_repo, "list_for_job", AsyncMock(return_value=[_hw_phase()])):
            await na.archive_job(hw_job.id)

    hw_push.assert_awaited_once()
    assert hw_push.await_args.kwargs["lesson_page_id"] == "L1"


@pytest.mark.asyncio
async def test_order_independence_homework_then_teacher_adopts_lesson_page(monkeypatch):
    toc_entry_id = uuid4()
    section = _section(toc_entry_id)
    book = _book()
    hw_job = _job(kind="homework", toc_entry_id=toc_entry_id)
    teacher_job = _job(kind="teacher_material", toc_entry_id=toc_entry_id,
                       created_at=datetime(2026, 6, 2, tzinfo=timezone.utc))
    jobs_by_id = {hw_job.id: hw_job, teacher_job.id: teacher_job}
    _wire(monkeypatch)
    setters = _mutating_setters(section, jobs_by_id)

    with patch.object(na.jobs_repo, "get", AsyncMock(side_effect=lambda s, jid: jobs_by_id.get(jid))), \
         patch.object(na.books_repo, "get", AsyncMock(return_value=book)), \
         patch.object(na.toc_repo, "titles_for_subject_grade", AsyncMock(return_value=[])), \
         patch.object(na.toc_repo, "get", AsyncMock(return_value=section)), \
         patch.object(na.toc_repo, "set_notion_lesson_page_id", setters["set_notion_lesson_page_id"]), \
         patch.object(na.toc_repo, "set_notion_teacher_deck_job", setters["set_notion_teacher_deck_job"]), \
         patch.object(na.toc_repo, "set_notion_homework_page_id", setters["set_notion_homework_page_id"]), \
         patch.object(na.toc_repo, "set_notion_archived_job", setters["set_notion_archived_job"]), \
         patch.object(na.jobs_repo, "set_notion_archived", setters["set_notion_archived"]), \
         patch.object(na, "NotionClientWrapper", MagicMock()), \
         patch.object(na, "_push_with_retry", AsyncMock(return_value=("L2", "hw1"))), \
         patch.object(na, "_push_teacher_with_retry",
                       AsyncMock(return_value=("ignored", "deck2"))) as teacher_push:
        # homework archives first
        with patch.object(na.phase_repo, "list_for_job", AsyncMock(return_value=[_hw_phase()])):
            await na.archive_job(hw_job.id)
        assert section.notion_lesson_page_id == "L2"

        # teacher archives second, on the SAME section
        with patch.object(na.phase_repo, "list_for_job", AsyncMock(return_value=[_deck_phase()])):
            await na.archive_job(teacher_job.id)

    teacher_push.assert_awaited_once()
    assert teacher_push.await_args.kwargs["lesson_page_id"] == "L2"


@pytest.mark.asyncio
async def test_no_deck_content_records_skip_never_pushes(monkeypatch):
    job = _job(kind="teacher_material")
    section = _section(job.toc_entry_id)
    book = _book()
    _wire(monkeypatch)
    set_skip = AsyncMock()
    with patch.object(na.jobs_repo, "get", AsyncMock(return_value=job)), \
         patch.object(na.books_repo, "get", AsyncMock(return_value=book)), \
         patch.object(na.toc_repo, "titles_for_subject_grade", AsyncMock(return_value=[])), \
         patch.object(na.toc_repo, "get", AsyncMock(return_value=section)), \
         patch.object(na.phase_repo, "list_for_job",
                       AsyncMock(return_value=[_deck_phase(content_json=None)])), \
         patch.object(na.jobs_repo, "set_notion_skip_reason", set_skip), \
         patch.object(na, "NotionClientWrapper", MagicMock()), \
         patch.object(na, "_push_teacher_with_retry", AsyncMock()) as push:
        await na.archive_job(job.id)  # must not raise

    set_skip.assert_awaited_once()
    assert set_skip.await_args.args[2] == "no teacher deck content"
    push.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_deck_json_treated_as_no_deck_content(monkeypatch):
    """A `content_json` that fails TeacherDeck validation is treated the same
    as absent content (never crashes archive_job on a malformed row)."""
    job = _job(kind="teacher_material")
    section = _section(job.toc_entry_id)
    book = _book()
    _wire(monkeypatch)
    set_skip = AsyncMock()
    with patch.object(na.jobs_repo, "get", AsyncMock(return_value=job)), \
         patch.object(na.books_repo, "get", AsyncMock(return_value=book)), \
         patch.object(na.toc_repo, "titles_for_subject_grade", AsyncMock(return_value=[])), \
         patch.object(na.toc_repo, "get", AsyncMock(return_value=section)), \
         patch.object(na.phase_repo, "list_for_job",
                       AsyncMock(return_value=[_deck_phase(content_json={"nonsense": True})])), \
         patch.object(na.jobs_repo, "set_notion_skip_reason", set_skip), \
         patch.object(na, "NotionClientWrapper", MagicMock()), \
         patch.object(na, "_push_teacher_with_retry", AsyncMock()) as push:
        await na.archive_job(job.id)  # must not raise

    set_skip.assert_awaited_once()
    assert set_skip.await_args.args[2] == "no teacher deck content"
    push.assert_not_awaited()


@pytest.mark.asyncio
async def test_non_done_deck_phase_treated_as_no_deck_content(monkeypatch):
    """A `teacher-deck` phase carrying stale `content_json` from a reset/retried
    run (status != 'done') must not be archived — mirrors the homework
    `phase_md` branch's `status == "done"` filter."""
    job = _job(kind="teacher_material")
    section = _section(job.toc_entry_id)
    book = _book()
    _wire(monkeypatch)
    set_skip = AsyncMock()
    with patch.object(na.jobs_repo, "get", AsyncMock(return_value=job)), \
         patch.object(na.books_repo, "get", AsyncMock(return_value=book)), \
         patch.object(na.toc_repo, "titles_for_subject_grade", AsyncMock(return_value=[])), \
         patch.object(na.toc_repo, "get", AsyncMock(return_value=section)), \
         patch.object(na.phase_repo, "list_for_job",
                       AsyncMock(return_value=[_deck_phase(status="failed")])), \
         patch.object(na.jobs_repo, "set_notion_skip_reason", set_skip), \
         patch.object(na, "NotionClientWrapper", MagicMock()), \
         patch.object(na, "_push_teacher_with_retry", AsyncMock()) as push:
        await na.archive_job(job.id)  # must not raise

    set_skip.assert_awaited_once()
    assert set_skip.await_args.args[2] == "no teacher deck content"
    push.assert_not_awaited()


@pytest.mark.asyncio
async def test_teacher_idempotent_already_archived_skips_push(monkeypatch):
    job = _job(kind="teacher_material", archived=True)
    _wire(monkeypatch)
    with patch.object(na.jobs_repo, "get", AsyncMock(return_value=job)), \
         patch.object(na.books_repo, "get", AsyncMock()) as books_get, \
         patch.object(na, "_push_teacher_with_retry", AsyncMock()) as push:
        await na.archive_job(job.id)  # force=False (default)

    books_get.assert_not_awaited()   # early return, never reaches book/section fetch
    push.assert_not_awaited()
