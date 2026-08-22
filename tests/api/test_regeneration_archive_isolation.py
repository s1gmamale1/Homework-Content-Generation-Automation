"""The three OPERATOR archive entry points must refuse a revision.

`notion_archive.archive_job` carries an intrinsic guard (see
`tests/services/test_regeneration_archive_isolation.py`), but a guard that only
records a skip reason answers the operator with `204`/`{"queued": 1}` and a
silently-skipped job. These routes therefore refuse SYNCHRONOUSLY — before the
background task is even created — so "re-archive this" on a revision is a loud
409 naming the versioned publisher instead of an invisible no-op.

The batch sweep is defence in depth: `ck_homework_jobs_revision_no_batch` means
a revision cannot be a batch member at all, so the selection can never return
one — the filter is there so a future widening of the batch link cannot quietly
push campaign revisions at the legacy archive.
"""
from __future__ import annotations

import os
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.api.v1 import batch as batch_api
from app.api.v1 import jobs as jobs_api

db_only = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)


def _revision(**kw):
    return SimpleNamespace(
        id=kw.get("id", uuid.uuid4()),
        status=kw.get("status", "done"),
        notion_archived_at=kw.get("notion_archived_at"),
        revision_of_job_id=kw.get("revision_of_job_id", uuid.uuid4()),
        regeneration_target_id=uuid.uuid4(),
    )


@pytest.fixture()
def guarded(monkeypatch):
    ns = SimpleNamespace(archived=[], tasks=[])

    async def _never(*a, **k):
        ns.archived.append((a, k))

    monkeypatch.setattr(jobs_api.notion_archive, "archive_job", _never)
    monkeypatch.setattr(
        jobs_api.asyncio, "create_task",
        MagicMock(side_effect=lambda coro: (
            coro.close(), ns.tasks.append(coro))[0]))
    jobs_api._FORCE_REARCHIVE_TASKS.clear()
    return ns


@pytest.mark.parametrize("force", [False, True])
async def test_retry_archive_refuses_a_revision_synchronously(
    guarded, monkeypatch, force
):
    job = _revision()
    monkeypatch.setattr(jobs_api.jobs_repo, "get", AsyncMock(return_value=job))
    with pytest.raises(HTTPException) as exc:
        await jobs_api.retry_archive_job(
            job.id, force=force, session=AsyncMock(), user={})
    assert exc.value.status_code == 409
    assert "versioned publisher" in str(exc.value.detail)
    assert guarded.archived == [], "no archive call may be made for a revision"
    assert guarded.tasks == [], "no background force task may be created either"
    assert job.id not in jobs_api._FORCE_REARCHIVE_TASKS


async def test_retry_archive_refuses_an_ALREADY_ARCHIVED_revision_as_a_revision(
    guarded, monkeypatch
):
    """The revision refusal must come FIRST — a revision that somehow carries a
    `notion_archived_at` must not be reported as a plain already-archived job,
    which would hide the real problem."""
    job = _revision(notion_archived_at="2026-08-01T00:00:00Z")
    monkeypatch.setattr(jobs_api.jobs_repo, "get", AsyncMock(return_value=job))
    with pytest.raises(HTTPException) as exc:
        await jobs_api.retry_archive_job(
            job.id, force=False, session=AsyncMock(), user={})
    assert exc.value.status_code == 409
    assert "versioned publisher" in str(exc.value.detail)


async def test_retry_archive_still_works_for_an_ordinary_job(guarded, monkeypatch):
    job = _revision(revision_of_job_id=None)
    monkeypatch.setattr(jobs_api.jobs_repo, "get", AsyncMock(return_value=job))
    monkeypatch.setattr(jobs_api, "_job_out", AsyncMock(return_value={"ok": True}))
    session = AsyncMock()
    session.expire_all = MagicMock()
    out = await jobs_api.retry_archive_job(
        job.id, force=False, session=session, user={})
    assert out == {"ok": True}
    assert len(guarded.archived) == 1


async def test_force_rearchive_still_backgrounds_for_an_ordinary_job(
    guarded, monkeypatch
):
    job = _revision(revision_of_job_id=None)
    monkeypatch.setattr(jobs_api.jobs_repo, "get", AsyncMock(return_value=job))
    out = await jobs_api.retry_archive_job(
        job.id, force=True, session=AsyncMock(), user={})
    assert out["queued"] == 1
    assert len(guarded.tasks) == 1
    jobs_api._FORCE_REARCHIVE_TASKS.clear()


async def test_batch_sweep_selection_drops_a_revision_defensively(monkeypatch):
    revision_id, ordinary_id = uuid.uuid4(), uuid.uuid4()
    batch_id = uuid.uuid4()
    swept: list[list] = []

    async def _exclude(session, job_ids):
        assert list(job_ids) == [ordinary_id, revision_id]
        return [ordinary_id]

    monkeypatch.setattr(batch_api.jobs_repo, "exclude_revisions", _exclude)
    monkeypatch.setattr(
        batch_api.batches_repo, "done_unarchived_job_ids",
        AsyncMock(return_value=[ordinary_id, revision_id]))
    monkeypatch.setattr(
        batch_api.asyncio, "create_task",
        MagicMock(side_effect=lambda coro: (coro.close(), None)[0]))

    session = AsyncMock()
    session.get = AsyncMock(return_value=SimpleNamespace(id=batch_id))
    batch_api._REARCHIVE_TASKS.clear()

    captured = {}
    real_sweep = batch_api._rearchive_sweep

    def _spy(bid, job_ids, *, force=False):
        captured["job_ids"] = list(job_ids)
        swept.append(list(job_ids))
        return real_sweep(bid, job_ids, force=force)

    monkeypatch.setattr(batch_api, "_rearchive_sweep", _spy)
    out = await batch_api.retry_archive_batch(batch_id, session=session)
    assert out["queued"] == 1
    assert captured["job_ids"] == [ordinary_id]
    batch_api._REARCHIVE_TASKS.clear()


@db_only
async def test_exclude_revisions_filters_against_the_real_table():
    from sqlalchemy import delete

    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.homework_job import HomeworkJob
    from app.models.regeneration_campaign import RegenerationCampaign
    from app.models.regeneration_target import RegenerationTarget
    from app.models.toc_entry import TOCEntry
    from app.repositories import jobs as jobs_repo
    from app.services.regeneration_planner import build_phase_plan

    plan = build_phase_plan(
        subject="math-algebra", selected_phases=["flashcards"]).to_json()
    async with SessionLocal() as session:
        book = Book(
            subject="math-algebra", original_filename="regen_exclude.pdf",
            content_sha256=uuid.uuid4().hex * 2, file_size_bytes=1,
            status="toc_ready")
        session.add(book)
        await session.flush()
        toc = TOCEntry(book_id=book.id, section_title="L1", order_index=0)
        session.add(toc)
        await session.flush()
        v1 = HomeworkJob(
            book_id=book.id, toc_entry_id=toc.id, subject="math-algebra",
            status="done", provider="gemini", output_language="uz")
        session.add(v1)
        await session.flush()
        campaign = RegenerationCampaign(
            status="draft", selection_spec={}, requested_phases=[],
            excluded_phases=[], launch_contract={})
        session.add(campaign)
        await session.flush()
        target = RegenerationTarget(
            campaign_id=campaign.id, toc_entry_id=toc.id, output_language="uz",
            phase_plan=plan, source_job_id=v1.id, status="generating")
        session.add(target)
        await session.flush()
        revision = HomeworkJob(
            book_id=book.id, toc_entry_id=toc.id, subject="math-algebra",
            status="done", provider="gemini", output_language="uz",
            revision_of_job_id=v1.id, regeneration_target_id=target.id,
            session_limit_strategy="pause")
        session.add(revision)
        await session.commit()
        try:
            assert await jobs_repo.exclude_revisions(
                session, [v1.id, revision.id]) == [v1.id]
            assert await jobs_repo.exclude_revisions(session, []) == []
        finally:
            await session.execute(
                delete(HomeworkJob).where(HomeworkJob.id == revision.id))
            await session.execute(
                delete(RegenerationTarget).where(RegenerationTarget.id == target.id))
            await session.execute(
                delete(RegenerationCampaign).where(
                    RegenerationCampaign.id == campaign.id))
            await session.execute(
                delete(HomeworkJob).where(HomeworkJob.book_id == book.id))
            await session.execute(
                delete(TOCEntry).where(TOCEntry.book_id == book.id))
            await session.execute(delete(Book).where(Book.id == book.id))
            await session.commit()
