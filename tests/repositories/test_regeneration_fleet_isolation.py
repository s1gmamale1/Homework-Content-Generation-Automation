"""Revision jobs must be invisible to every ORDINARY Fleet query.

A revision job is a `kind="homework"` row like any other — that is the whole
point (it reuses the pipeline, the phase machinery, the download and the SSE
stream). The ONLY thing that marks it is `revision_of_job_id IS NOT NULL`, so
every legacy read path that means "the lesson's Fleet job" has to say so
explicitly or it silently starts describing V2 as if it were V1: the launcher
would report a lesson "done" because a revision finished, a relaunch would try
to ADOPT the revision into a batch (a `ck_homework_jobs_revision_no_batch`
violation), and the coverage dashboard would count it twice.

Two kinds of assertion here, deliberately:

* **behavioral, against a real Postgres** for every query a revision can
  actually reach (they are all keyed on book/section, and a revision shares
  both with its source);
* **structural, on the compiled SQL** for the batch-scoped queries. A revision
  can never have a `batch_id` (the database refuses it), so no fixture can make
  those queries return one — the predicate there is defence in depth, and the
  only honest way to prove it is present is to read the statement.

`jobs_repo.list_for_book` is deliberately NOT filtered and has its own test:
`/toc/retry` relies on it to SEE revision history and refuse loudly.
"""
from __future__ import annotations

import os
import uuid

import pytest

from app.repositories import jobs as jobs_repo
from app.repositories import subject_coverage as coverage_repo
from app.services.regeneration_planner import build_phase_plan

_SUBJECT = "math-algebra"
_PHASE_PLAN = build_phase_plan(
    subject=_SUBJECT, selected_phases=["flashcards"]
).to_json()

db_only = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)


# ─────────────────────────────────────────────────────────────────────────
# structural: the predicate is IN the SQL (batch-scoped, unreachable by data)
# ─────────────────────────────────────────────────────────────────────────


def _compiled(stmt) -> str:
    return str(stmt.compile(compile_kwargs={"literal_binds": False}))


def test_resume_failed_in_batch_sql_excludes_revisions():
    """Batch resume selection must name the predicate itself.

    `ck_homework_jobs_revision_no_batch` already makes a revision unreachable
    here, so this is defence in depth — and defence in depth that is not in the
    statement is not defence at all.
    """
    from sqlalchemy import select

    from app.models import HomeworkJob

    sql = _compiled(
        select(HomeworkJob).where(
            HomeworkJob.batch_id == uuid.uuid4(),
            HomeworkJob.status.in_(["failed", "cancelled"]),
            HomeworkJob.revision_of_job_id.is_(None),
        )
    )
    assert "revision_of_job_id IS NULL" in sql  # sanity: this is the shape we look for

    import inspect

    source = inspect.getsource(jobs_repo.resume_failed_in_batch)
    assert "revision_of_job_id.is_(None)" in source, (
        "resume_failed_in_batch must exclude revision jobs explicitly"
    )


# ─────────────────────────────────────────────────────────────────────────
# behavioral: real Postgres
# ─────────────────────────────────────────────────────────────────────────


async def _seed(*, language: str = "uz"):
    """A book, one TOC entry, a done V1 job, and a NEWER done revision of it.

    The revision is created last on purpose: every `latest_*` query orders by
    `created_at DESC`, so an unfiltered query returns the REVISION and the test
    bites.
    """
    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.homework_job import HomeworkJob
    from app.models.regeneration_campaign import RegenerationCampaign
    from app.models.regeneration_target import RegenerationTarget
    from app.models.toc_entry import TOCEntry

    async with SessionLocal() as session:
        book = Book(
            subject=_SUBJECT,
            original_filename="regen_fleet_isolation.pdf",
            content_sha256=uuid.uuid4().hex * 2,
            file_size_bytes=1,
            status="toc_ready",
        )
        session.add(book)
        await session.flush()
        toc = TOCEntry(book_id=book.id, section_title="L1", order_index=0)
        session.add(toc)
        await session.flush()
        v1 = HomeworkJob(
            book_id=book.id, toc_entry_id=toc.id, subject=_SUBJECT,
            status="done", provider="gemini", transport="api",
            output_language=language,
        )
        session.add(v1)
        await session.flush()
        campaign = RegenerationCampaign(
            status="draft", selection_spec={}, requested_phases=[],
            excluded_phases=[], launch_contract={},
        )
        session.add(campaign)
        await session.flush()
        target = RegenerationTarget(
            campaign_id=campaign.id, toc_entry_id=toc.id,
            output_language=language, phase_plan=_PHASE_PLAN,
            source_job_id=v1.id, status="generating",
        )
        session.add(target)
        await session.flush()
        revision = HomeworkJob(
            book_id=book.id, toc_entry_id=toc.id, subject=_SUBJECT,
            status="done", provider="gemini", transport="api",
            output_language=language,
            revision_of_job_id=v1.id, regeneration_target_id=target.id,
            session_limit_strategy="pause",
        )
        session.add(revision)
        await session.commit()
        return {
            "book_id": book.id, "toc_id": toc.id, "v1_id": v1.id,
            "revision_id": revision.id, "campaign_id": campaign.id,
            "target_id": target.id,
        }


async def _purge(ids: dict) -> None:
    """Child-first: every regeneration FK is RESTRICT on purpose."""
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


@pytest.fixture()
async def seeded():
    ids = await _seed()
    try:
        yield ids
    finally:
        await _purge(ids)


@db_only
async def test_find_active_for_section_ignores_a_revision(seeded):
    """Dedup/adoption: `/generate` and batch launch must see V1, never V2."""
    from app.db import SessionLocal

    async with SessionLocal() as session:
        found = await jobs_repo.find_active_for_section(
            session, seeded["book_id"], seeded["toc_id"],
            transport="api", output_language="uz",
        )
    assert found is not None
    assert found.id == seeded["v1_id"], (
        "find_active_for_section returned the REVISION — a batch relaunch would "
        "try to adopt it into a batch (ck_homework_jobs_revision_no_batch)"
    )


@db_only
async def test_latest_for_section_ignores_a_revision(seeded):
    """Relaunch resume selection must never resume a revision."""
    from app.db import SessionLocal

    async with SessionLocal() as session:
        found = await jobs_repo.latest_for_section(
            session, seeded["book_id"], seeded["toc_id"],
            transport="api", output_language="uz",
        )
    assert found is not None
    assert found.id == seeded["v1_id"]


@db_only
async def test_latest_by_section_ignores_a_revision(seeded):
    """Book TOC status enrichment: the per-row badge is V1's, not V2's."""
    from app.db import SessionLocal

    async with SessionLocal() as session:
        latest = await jobs_repo.latest_by_section(
            session, seeded["book_id"], "uz")
    assert seeded["toc_id"] in latest
    assert latest[seeded["toc_id"]].id == seeded["v1_id"]


@db_only
async def test_toc_status_enrichment_caller_ignores_a_revision(seeded):
    """The CALLER too (`books._enriched_toc_entries`), not just the query."""
    from app.api.v1.books import _enriched_toc_entries
    from app.db import SessionLocal
    from app.repositories import books as books_repo

    async with SessionLocal() as session:
        book = await books_repo.get_with_toc(session, seeded["book_id"])
        entries = await _enriched_toc_entries(session, book, "uz")
    assert [e.latest_job_id for e in entries] == [seeded["v1_id"]]


@db_only
async def test_job_status_by_book_ignores_a_revision(seeded):
    """The coverage dashboard counts V1's status, never the revision's."""
    from app.db import SessionLocal

    async with SessionLocal() as session:
        statuses = await coverage_repo.job_status_by_book(session, "uz")
    per_entry = statuses.get(str(seeded["book_id"]), {})
    assert list(per_entry) == [str(seeded["toc_id"])]
    # Behaviorally identical statuses would hide the leak, so prove the row
    # that survived is V1's by flipping the revision to a DIFFERENT status.
    from sqlalchemy import update

    from app.models import HomeworkJob

    async with SessionLocal() as session:
        await session.execute(
            update(HomeworkJob)
            .where(HomeworkJob.id == seeded["revision_id"])
            .values(status="failed"))
        await session.commit()
        statuses = await coverage_repo.job_status_by_book(session, "uz")
    assert statuses[str(seeded["book_id"])][str(seeded["toc_id"])] == "done", (
        "the dashboard read the REVISION's status instead of V1's"
    )


@db_only
async def test_list_for_book_still_SEES_revisions(seeded):
    """The deliberate exception: `/toc/retry`'s blocking guard MUST see them."""
    from app.db import SessionLocal

    async with SessionLocal() as session:
        rows = await jobs_repo.list_for_book(session, seeded["book_id"])
    ids = {r.id for r in rows}
    assert seeded["revision_id"] in ids, (
        "list_for_book must stay unfiltered — /toc/retry uses it to refuse "
        "replacing a TOC that regeneration history references"
    )
    assert seeded["v1_id"] in ids


@db_only
@pytest.mark.xfail(
    reason=(
        "app/repositories/cost.py is Task 5's file; this pins the Integration "
        "Checkpoint 2 expectation and should XPASS once Task 5 is cherry-picked"
    ),
    strict=False,
)
async def test_section_prior_api_cost_ignores_a_revision(seeded):
    """Never-pay-twice must price V1's spend, not the revision's."""
    from app.db import SessionLocal
    from app.repositories import cost as cost_repo
    from app.models.agent_usage import AgentUsage

    async with SessionLocal() as session:
        session.add(AgentUsage(
            operation="phase.run", provider="gemini",
            model_name="gemini-3.5-flash", auth_mode="api",
            homework_job_id=seeded["revision_id"],
            prompt_tokens=1_000_000, output_tokens=1_000_000,
            total_tokens=2_000_000, duration="1s", success=True,
        ))
        await session.commit()
        cost, had_done = await cost_repo.section_prior_api_cost(
            session, seeded["book_id"], seeded["toc_id"], "api")
    assert had_done is True
    assert cost == 0.0, (
        "prior api cost was read off the REVISION job — a normal force-regenerate "
        f"would quote the campaign's spend as the lesson's rebill (got ${cost})"
    )
