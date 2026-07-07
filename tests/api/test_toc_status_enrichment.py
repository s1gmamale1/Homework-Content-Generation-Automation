"""The book TOC endpoint and its SSE `toc_ready` replay must return the SAME
enriched entries (with `latest_job_status`). They used to diverge — the SSE
emitted status-less entries that raced in and wiped the section-list badges.
This pins the shared enrichment helper both now use."""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

from app.api.v1 import books as books_api


def _entry(order: int, section_title: str = None, page_start: int = None, page_end: int = None):
    return SimpleNamespace(
        id=uuid4(),
        chapter_number="1",
        chapter_title="Ch",
        section_number=f"1.{order}",
        section_title=section_title if section_title is not None else f"Section {order}",
        page_start=page_start if page_start is not None else order,
        page_end=page_end if page_end is not None else order + 1,
        order_index=order,
    )


def test_enriched_toc_entries_attaches_latest_status(monkeypatch):
    e1, e2 = _entry(0), _entry(1)
    book = SimpleNamespace(id=uuid4(), toc_entries=[e1, e2])
    job = SimpleNamespace(id=uuid4(), status="running")

    async def fake_latest(session, book_id, output_language=None):
        # only the first section has a job
        return {e1.id: job}

    monkeypatch.setattr(books_api.jobs_repo, "latest_by_section", fake_latest)

    result = asyncio.run(books_api._enriched_toc_entries(None, book))

    # section with a job → enriched
    assert result[0].latest_job_status == "running"
    assert result[0].latest_job_id == job.id
    # section without a job → null status (no badge)
    assert result[1].latest_job_status is None
    assert result[1].latest_job_id is None


def test_enriched_toc_entries_threads_output_language(monkeypatch):
    """The Fleet/Section launchers fetch per-language completion: the chosen
    output_language must flow through to latest_by_section so a book complete in
    uz doesn't read 'complete' under ru/en (language-blind status was the bug)."""
    e1 = _entry(0)
    book = SimpleNamespace(id=uuid4(), toc_entries=[e1])
    captured = {}

    async def fake_latest(session, book_id, output_language=None):
        captured["output_language"] = output_language
        return {}

    monkeypatch.setattr(books_api.jobs_repo, "latest_by_section", fake_latest)
    asyncio.run(books_api._enriched_toc_entries(None, book, output_language="ru"))
    assert captured["output_language"] == "ru"

    # default (None) preserves the all-language behavior for non-launcher callers
    asyncio.run(books_api._enriched_toc_entries(None, book))
    assert captured["output_language"] is None


def test_enriched_toc_entries_attaches_entry_class(monkeypatch):
    """entry_class is computed on-the-fly (no DB column) at the same choke
    point that attaches latest_job_status, so both enrichments coexist."""
    header = _entry(0, section_title="Bob 1", page_start=1, page_end=50)
    child_a = _entry(1, section_title="1.1-mavzu", page_start=1, page_end=10)
    child_b = _entry(2, section_title="1.2-mavzu", page_start=11, page_end=20)
    javoblar = _entry(3, section_title="Javoblar", page_start=51, page_end=55)
    lesson = _entry(4, section_title="2.1-mavzu", page_start=56, page_end=60)
    book = SimpleNamespace(
        id=uuid4(), toc_entries=[header, child_a, child_b, javoblar, lesson]
    )
    job = SimpleNamespace(id=uuid4(), status="running")

    async def fake_latest(session, book_id, output_language=None):
        # only the plain lesson row has a job — proves entry_class coexists
        # with the pre-existing latest_job_status enrichment.
        return {lesson.id: job}

    monkeypatch.setattr(books_api.jobs_repo, "latest_by_section", fake_latest)

    result = asyncio.run(books_api._enriched_toc_entries(None, book))

    by_id = {r.id: r for r in result}
    assert by_id[header.id].entry_class == "header"
    assert by_id[child_a.id].entry_class == "lesson"
    assert by_id[child_b.id].entry_class == "lesson"
    assert by_id[javoblar.id].entry_class == "other"
    assert by_id[lesson.id].entry_class == "lesson"

    # no-regression check: latest_job_status enrichment still works alongside
    # the new entry_class field.
    assert by_id[lesson.id].latest_job_status == "running"
    assert by_id[lesson.id].latest_job_id == job.id
    assert by_id[header.id].latest_job_status is None
