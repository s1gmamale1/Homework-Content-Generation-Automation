"""The book TOC endpoint and its SSE `toc_ready` replay must return the SAME
enriched entries (with `latest_job_status`). They used to diverge — the SSE
emitted status-less entries that raced in and wiped the section-list badges.
This pins the shared enrichment helper both now use."""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

from app.api.v1 import books as books_api


def _entry(order: int):
    return SimpleNamespace(
        id=uuid4(),
        chapter_number="1",
        chapter_title="Ch",
        section_number=f"1.{order}",
        section_title=f"Section {order}",
        page_start=order,
        page_end=order + 1,
        order_index=order,
    )


def test_enriched_toc_entries_attaches_latest_status(monkeypatch):
    e1, e2 = _entry(0), _entry(1)
    book = SimpleNamespace(id=uuid4(), toc_entries=[e1, e2])
    job = SimpleNamespace(id=uuid4(), status="running")

    async def fake_latest(session, book_id):
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
