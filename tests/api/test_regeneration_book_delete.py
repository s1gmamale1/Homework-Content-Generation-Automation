"""Deleting a source out from under regeneration history is a clean 409.

Every regeneration foreign key is `ON DELETE RESTRICT` on purpose: a target row
records a publication version that is consumed FOREVER, so letting a book or a
TOC-entry delete cascade it away would silently free that version for reuse and
break the immutability guarantee the whole feature rests on.

RESTRICT alone is not a good answer to an operator, though — it surfaces as a
raw `ForeignKeyViolation`, which the routes turn into a 500 (book delete) or a
book flipped to `failed` with the old TOC still in place (`/toc/retry`). So the
three source-removing routes check FIRST and refuse with a structured 409
naming what blocks them, and the repositories keep their restrictive behavior
untouched underneath as the real backstop.

`jobs_repo.list_for_book` stays UNFILTERED for exactly this reason: `/toc/retry`
uses it to see revision jobs.
"""
from __future__ import annotations

import os
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.auth import get_current_user
from main import app

client = TestClient(app)

db_only = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)


@pytest.fixture(autouse=True)
def _auth_override():
    app.dependency_overrides[get_current_user] = lambda: {"user": "test"}
    yield
    app.dependency_overrides.pop(get_current_user, None)


def _history(n=1):
    return [
        SimpleNamespace(
            id=uuid.uuid4(), campaign_id=uuid.uuid4(), toc_entry_id=uuid.uuid4(),
            status="published", output_language="uz", publication_version=2 + i,
        )
        for i in range(n)
    ]


# ─────────────────────────────────────────────────────────────────────────
# routes: structured 409 BEFORE any repository delete
# ─────────────────────────────────────────────────────────────────────────


def test_book_delete_with_regeneration_history_is_a_clean_409():
    book_id = uuid.uuid4()
    delete_spy = AsyncMock(return_value=True)
    with patch("app.api.v1.books.books_repo.get",
               AsyncMock(return_value=SimpleNamespace(id=book_id, status="toc_ready"))), \
         patch("app.api.v1.books.books_repo.delete", delete_spy), \
         patch("app.api.v1.books.jobs_repo.count_active_for_book",
               AsyncMock(return_value=0)), \
         patch("app.api.v1.books.targets_repo.history_for_book",
               AsyncMock(return_value=_history(2))):
        r = client.delete(f"/api/v1/books/{book_id}")
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["error"] == "book_delete_blocked_by_regeneration"
    assert detail["count"] == 2
    assert "regeneration" in detail["message"].lower()
    delete_spy.assert_not_awaited(), "the guard must precede the repository delete"


def test_book_delete_without_regeneration_history_still_works():
    book_id = uuid.uuid4()
    delete_spy = AsyncMock(return_value=True)
    with patch("app.api.v1.books.books_repo.get",
               AsyncMock(return_value=SimpleNamespace(id=book_id, status="toc_ready"))), \
         patch("app.api.v1.books.books_repo.delete", delete_spy), \
         patch("app.api.v1.books.jobs_repo.count_active_for_book",
               AsyncMock(return_value=0)), \
         patch("app.api.v1.books.targets_repo.history_for_book",
               AsyncMock(return_value=[])), \
         patch("app.api.v1.books.shutil.rmtree", lambda *a, **k: None):
        r = client.delete(f"/api/v1/books/{book_id}")
    assert r.status_code == 204
    delete_spy.assert_awaited_once()


def test_toc_entry_delete_with_regeneration_history_is_a_clean_409():
    book_id, entry_id = uuid.uuid4(), uuid.uuid4()
    delete_spy = AsyncMock(return_value=True)
    with patch("app.api.v1.books.toc_repo.get",
               AsyncMock(return_value=SimpleNamespace(id=entry_id, book_id=book_id))), \
         patch("app.api.v1.books.toc_repo.delete", delete_spy), \
         patch("app.api.v1.books.targets_repo.history_for_toc_entry",
               AsyncMock(return_value=_history(1))):
        r = client.delete(f"/api/v1/books/{book_id}/toc/{entry_id}")
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["error"] == "toc_entry_delete_blocked_by_regeneration"
    assert detail["count"] == 1
    delete_spy.assert_not_awaited()


def test_toc_retry_with_regeneration_history_is_a_clean_409(tmp_path, monkeypatch):
    """A target whose source job was purged leaves NO blocking job — only the
    target row — so the existing job guard would wave the re-extract through and
    the TOC delete would then die on `fk_regeneration_targets_toc_entry_id`."""
    from app.services import storage

    book_id = uuid.uuid4()
    pdf = tmp_path / "source.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    monkeypatch.setattr(storage, "book_pdf_path", lambda bid: pdf)
    set_status = AsyncMock()
    with patch("app.api.v1.books.books_repo.get",
               AsyncMock(return_value=SimpleNamespace(
                   id=book_id, status="toc_ready", subject="math-algebra"))), \
         patch("app.api.v1.books.books_repo.set_status", set_status), \
         patch("app.api.v1.books.jobs_repo.list_for_book",
               AsyncMock(return_value=[])), \
         patch("app.api.v1.books.targets_repo.history_for_book",
               AsyncMock(return_value=_history(3))):
        r = client.post(f"/api/v1/books/{book_id}/toc/retry")
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["error"] == "toc_retry_blocked_by_regeneration"
    assert detail["count"] == 3
    set_status.assert_not_awaited(), (
        "the book must keep its status — a blocked retry is not a state change")


def test_toc_retry_still_reports_blocking_JOBS_separately():
    """The pre-existing job guard is untouched (and still sees revision jobs,
    because `list_for_book` is deliberately unfiltered)."""
    book_id = uuid.uuid4()
    revision = SimpleNamespace(id=uuid.uuid4(), status="done")
    with patch("app.api.v1.books.books_repo.get",
               AsyncMock(return_value=SimpleNamespace(
                   id=book_id, status="toc_ready", subject="math-algebra"))), \
         patch("app.services.storage.book_pdf_path",
               lambda bid: __import__("pathlib").Path(__file__)), \
         patch("app.api.v1.books.jobs_repo.list_for_book",
               AsyncMock(return_value=[revision])), \
         patch("app.api.v1.books.targets_repo.history_for_book",
               AsyncMock(return_value=[])):
        r = client.post(f"/api/v1/books/{book_id}/toc/retry")
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "toc_retry_blocked_by_jobs"


# ─────────────────────────────────────────────────────────────────────────
# the repositories keep their restrictive behavior underneath
# ─────────────────────────────────────────────────────────────────────────


async def _seed(*, published: bool = False):
    """Seed one lesson with a V1 source, a regeneration target and its revision
    job. `published=True` gives the target real, irreplaceable audit history: an
    approved campaign and a consumed `publication_version` that may never be
    reused."""
    from datetime import datetime, timezone

    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.homework_job import HomeworkJob
    from app.models.regeneration_campaign import RegenerationCampaign
    from app.models.regeneration_target import RegenerationTarget
    from app.models.toc_entry import TOCEntry
    from app.services.regeneration_planner import build_phase_plan

    plan = build_phase_plan(
        subject="math-algebra", selected_phases=["flashcards"]).to_json()
    async with SessionLocal() as session:
        book = Book(
            subject="math-algebra", original_filename="regen_delete.pdf",
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
        now = datetime.now(timezone.utc)
        campaign = RegenerationCampaign(
            status="approved" if published else "draft",
            approved_at=now if published else None,
            selection_spec={}, requested_phases=[],
            excluded_phases=[], launch_contract={})
        session.add(campaign)
        await session.flush()
        target_extra = dict(
            status="published", publication_version=2,
            notion_page_id="notion-page", publication_released_at=now,
            terminal_at=now,
        ) if published else dict(status="generating")
        target = RegenerationTarget(
            campaign_id=campaign.id, toc_entry_id=toc.id, output_language="uz",
            phase_plan=plan, source_job_id=v1.id, **target_extra)
        session.add(target)
        await session.flush()
        revision = HomeworkJob(
            book_id=book.id, toc_entry_id=toc.id, subject="math-algebra",
            status="done", provider="gemini", output_language="uz",
            revision_of_job_id=v1.id, regeneration_target_id=target.id,
            session_limit_strategy="pause")
        session.add(revision)
        await session.commit()
        return {
            "book_id": book.id, "toc_id": toc.id, "v1_id": v1.id,
            "campaign_id": campaign.id, "target_id": target.id,
            "revision_id": revision.id,
        }


async def _purge(ids):
    from sqlalchemy import delete

    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.homework_job import HomeworkJob
    from app.models.regeneration_campaign import RegenerationCampaign
    from app.models.regeneration_target import RegenerationTarget
    from app.models.toc_entry import TOCEntry

    async with SessionLocal() as session:
        await session.execute(
            delete(HomeworkJob).where(HomeworkJob.id == ids["revision_id"]))
        await session.execute(
            delete(RegenerationTarget).where(RegenerationTarget.id == ids["target_id"]))
        await session.execute(
            delete(RegenerationCampaign).where(
                RegenerationCampaign.id == ids["campaign_id"]))
        await session.execute(
            delete(HomeworkJob).where(HomeworkJob.book_id == ids["book_id"]))
        await session.execute(
            delete(TOCEntry).where(TOCEntry.book_id == ids["book_id"]))
        await session.execute(delete(Book).where(Book.id == ids["book_id"]))
        await session.commit()


@db_only
async def test_books_repo_delete_still_REJECTS_without_the_route_guard():
    from sqlalchemy.exc import IntegrityError

    from app.db import SessionLocal
    from app.repositories import books as books_repo

    ids = await _seed()
    try:
        async with SessionLocal() as session:
            with pytest.raises(IntegrityError):
                await books_repo.delete(session, ids["book_id"])
                await session.commit()
            await session.rollback()
    finally:
        await _purge(ids)


@db_only
async def test_toc_repo_delete_still_REJECTS_without_the_route_guard():
    from sqlalchemy.exc import IntegrityError

    from app.db import SessionLocal
    from app.models.homework_job import HomeworkJob
    from app.repositories import toc_entries as toc_repo
    from sqlalchemy import delete as sa_delete

    ids = await _seed()
    try:
        async with SessionLocal() as session:
            # The route deletes the section's jobs first; the TARGET row is what
            # must still stop it.
            await session.execute(
                sa_delete(HomeworkJob).where(
                    HomeworkJob.id == ids["revision_id"]))
            await session.execute(
                sa_delete(HomeworkJob).where(
                    HomeworkJob.toc_entry_id == ids["toc_id"]))
            with pytest.raises(IntegrityError):
                await toc_repo.delete(session, ids["toc_id"])
                await session.commit()
            await session.rollback()
    finally:
        await _purge(ids)


@db_only
async def test_history_helpers_see_the_real_rows():
    from app.db import SessionLocal
    from app.repositories import regeneration_targets as targets_repo

    ids = await _seed()
    try:
        async with SessionLocal() as session:
            by_book = await targets_repo.history_for_book(session, ids["book_id"])
            by_entry = await targets_repo.history_for_toc_entry(
                session, ids["toc_id"])
        assert [t.id for t in by_book] == [ids["target_id"]]
        assert [t.id for t in by_entry] == [ids["target_id"]]
        async with SessionLocal() as session:
            assert await targets_repo.history_for_book(session, uuid.uuid4()) == []
    finally:
        await _purge(ids)


# ─────────────────────────────────────────────────────────────────────────
# the two source-deletion routes must remove jobs and the TOC entry in ONE
# transaction (Integration Checkpoint 2, item 4)
# ─────────────────────────────────────────────────────────────────────────


async def _delete_via_books_repo(session, ids):
    from app.repositories import books as books_repo

    return await books_repo.delete(session, ids["book_id"])


async def _delete_via_toc_repo(session, ids):
    from app.repositories import toc_entries as toc_repo

    return await toc_repo.delete(session, ids["toc_id"])


@db_only
@pytest.mark.parametrize(
    "route",
    [_delete_via_books_repo, _delete_via_toc_repo],
    ids=["books_repo.delete", "toc_repo.delete"],
)
async def test_source_deletion_routes_are_co_transactional_and_never_strand_a_target(route):
    """Both source-deletion routes delete the jobs AND the TOC entry inside one
    transaction, so `fk_regeneration_targets_toc_entry_id`'s RESTRICT aborts the
    whole unit of work and `fk_regeneration_targets_source_job_id`'s SET NULL
    never reaches disk.

    This pins the REASONING, not today's line order. The dangerous future edit
    is a child-first purge route that deletes the source job on its own, in its
    own transaction, outside the one that removes the TOC entry: nothing would
    then RESTRICT, the SET NULL would commit, and the target would survive as a
    published row with a consumed version number and no snapshot behind it.
    Stage 3 below runs exactly that job-only delete and shows it DOES strand the
    target — which is what makes stages 1 and 2 non-vacuous.
    """
    from sqlalchemy import delete as sa_delete
    from sqlalchemy import func, select
    from sqlalchemy.exc import IntegrityError

    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.homework_job import HomeworkJob
    from app.models.regeneration_target import RegenerationTarget
    from app.models.toc_entry import TOCEntry

    async def _state():
        """Everything the rollback has to have preserved, read fresh."""
        async with SessionLocal() as session:
            target = await session.get(RegenerationTarget, ids["target_id"])
            revision = await session.get(HomeworkJob, ids["revision_id"])
            return {
                "book": await session.get(Book, ids["book_id"]) is not None,
                "toc": await session.get(TOCEntry, ids["toc_id"]) is not None,
                "source": await session.get(HomeworkJob, ids["v1_id"]) is not None,
                "revision": revision is not None,
                "revision_of": revision.revision_of_job_id if revision else None,
                "revision_target": revision.regeneration_target_id if revision else None,
                "target": target is not None,
                "source_link": target.source_job_id if target else None,
                "version": target.publication_version if target else None,
                # The strand this whole rule exists to prevent.
                "stranded": await session.scalar(
                    select(func.count())
                    .select_from(RegenerationTarget)
                    .where(
                        RegenerationTarget.id == ids["target_id"],
                        RegenerationTarget.source_job_id.is_(None),
                    )
                ),
            }

    ids = await _seed(published=True)
    try:
        # Precondition: the rows this test reasons about really are on disk, so
        # a later "everything survived" assertion cannot pass over an empty DB.
        before = await _state()
        assert before == {
            "book": True, "toc": True, "source": True, "revision": True,
            "revision_of": ids["v1_id"], "revision_target": ids["target_id"],
            "target": True, "source_link": ids["v1_id"], "version": 2,
            "stranded": 0,
        }, before

        # ── stage 1: with the revision child alive, the shared transaction is
        # refused outright. WHICH of the two RESTRICTs refuses is deliberately
        # not pinned here. Both routes ORM-delete every job of the lesson in one
        # unit of work, and the jobs come back from an ORDER BY-less SELECT
        # (books.py / toc_entries.py), so their delete order is Postgres heap
        # order, not a declared dependency — `HomeworkJob` has no relationship
        # on `revision_of_job_id`. Source job first => the live revision child
        # trips `fk_homework_jobs_revision_of_job_id`; revision job first => that
        # key is satisfied, the source delete's SET NULL is attempted, and the
        # TOC entry's `fk_regeneration_targets_toc_entry_id` refuses instead.
        # (Verified: one ordinary lifecycle UPDATE on the source job rewrites its
        # tuple to the end of the heap and flips the observed key on both
        # routes.) Both are correct refusals of the same unit of work, so
        # ordering is irrelevant to what stage 1 claims — the full before-state
        # equality below is the real assertion: nothing at all reached disk.
        # Stage 2 then pins the exact key on the state that actually matters,
        # where only one job is left and no ordering can vary.
        async with SessionLocal() as session:
            with pytest.raises(IntegrityError) as exc:
                await route(session, ids)
                await session.commit()
            await session.rollback()
        assert (
            "fk_homework_jobs_revision_of_job_id" in str(exc.value)
            or "fk_regeneration_targets_toc_entry_id" in str(exc.value)
        ), (
            "the delete must be refused by one of the two RESTRICTs guarding "
            f"this lineage, not by some unrelated failure: {exc.value}")
        assert await _state() == before, "a refused delete must change nothing"

        # ── stage 2: after the documented child-first purge (spec §8.3) removes
        # the revision job, the TOC-entry RESTRICT is the ONLY thing left, and
        # it must still abort the same transaction that deletes the source job.
        async with SessionLocal() as session:
            await session.execute(
                sa_delete(HomeworkJob).where(HomeworkJob.id == ids["revision_id"]))
            await session.commit()

        async with SessionLocal() as session:
            with pytest.raises(IntegrityError) as exc:
                await route(session, ids)
                await session.commit()
            await session.rollback()
        assert "fk_regeneration_targets_toc_entry_id" in str(exc.value), (
            "the TOC entry's RESTRICT must be what refuses here — if some other "
            "key fires first, the SET NULL half of this rule is untested")

        after = await _state()
        assert after["book"] and after["toc"] and after["source"], after
        assert after["target"] and after["version"] == 2, after
        assert after["source_link"] == ids["v1_id"], (
            "the SET NULL was rolled back with the rest of the transaction")
        assert after["stranded"] == 0

        # ── stage 3: the counterfactual. Deleting the source job ALONE, in its
        # own transaction, commits — proving the SET NULL in stage 2 was real,
        # live, and stopped only by sharing a transaction with the TOC delete.
        async with SessionLocal() as session:
            await session.execute(
                sa_delete(HomeworkJob).where(HomeworkJob.id == ids["v1_id"]))
            await session.commit()

        stranded = await _state()
        assert stranded["source"] is False
        assert stranded["toc"] and stranded["target"] and stranded["version"] == 2
        assert stranded["source_link"] is None and stranded["stranded"] == 1, (
            "a job-only purge outside the TOC transaction is exactly the "
            "regression this test exists to catch")
    finally:
        await _purge(ids)
