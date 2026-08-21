"""Real-DB proof for the Task 5 read paths.

The unit tests in ``tests/repositories/test_regeneration_repositories.py`` and
``tests/services/test_regeneration_discovery.py`` assert the SQL a statement
CARRIES; this file proves what that SQL SELECTS, against a real Postgres:

* a failed job, a ``teacher_material`` job and a revision job are never V1
  sources, and languages do not leak into one another;
* an unpublished or abandoned revision is never chosen as a source, while the
  version it reserved is still counted by ``next_expected_version``;
* the lineage lock genuinely blocks a second transaction;
* a purged lineage (``source_job_id IS NULL``) is refused on that predicate,
  end to end through the discovery service;
* the estimator's 30-day window boundary, ``success`` / ``auth_mode='api'``
  filters and phase-row join select exactly the rows they claim to;
* ordinary-Fleet prior cost skips a revision's spend while the campaign's own
  actual-cost read counts exactly it.

Run:
  DATABASE_URL=postgresql+asyncpg://macmini5@127.0.0.1:5432/hcga_regen_lane_e_test \\
  RUN_DB_INTEGRATION=1 REGEN_REQUIRE_DB=1 \\
  uv run python -m pytest tests/integration/test_regeneration_source_and_version_queries.py -q
"""

from __future__ import annotations

import asyncio
import copy
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, text

from app.services.regeneration_planner import build_phase_plan

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)

SUBJECT = "math-algebra"
_MARKER = "pytest-regen-sources"
_PLAN = build_phase_plan(subject=SUBJECT, selected_phases=["flashcards"]).to_json()
CANONICAL = build_phase_plan(
    subject=SUBJECT, selected_phases=["flashcards"]
).canonical_phases


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _seed_book(session, *, grade="7"):
    from app.models.book import Book
    from app.models.toc_entry import TOCEntry

    book = Book(
        subject=SUBJECT,
        grade=grade,
        original_filename="regen_sources.pdf",
        content_sha256=uuid.uuid4().hex * 2,
        file_size_bytes=1,
        status="toc_ready",
    )
    session.add(book)
    await session.flush()
    toc = TOCEntry(
        book_id=book.id, section_number="1", section_title="Kirish", order_index=0
    )
    session.add(toc)
    await session.flush()
    return book, toc


async def _add_job(session, *, book, toc, **overrides):
    from app.models.homework_job import HomeworkJob

    kwargs = dict(
        book_id=book.id,
        toc_entry_id=toc.id,
        subject=SUBJECT,
        status="done",
        provider="gemini",
        model="gemini-3.5-flash",
        transport="api",
        output_language="uz",
        kind="homework",
    )
    kwargs.update(overrides)
    job = HomeworkJob(**kwargs)
    session.add(job)
    await session.flush()
    return job


async def _add_snapshot(session, job, *, complete=True):
    """A full set of `phase_outputs` rows for `job` (12 canonical phases)."""
    from app.models.phase_output import PhaseOutput

    rows = []
    names = CANONICAL if complete else CANONICAL[:-1]
    for order, name in enumerate(names):
        row = PhaseOutput(
            job_id=job.id,
            phase_name=name,
            phase_order=order,
            prompt_hash="builtin:test",
            model_name="gemini-3.5-flash",
            provider="gemini",
            output_md=f"# {name}",
            status="done",
        )
        session.add(row)
        rows.append(row)
    await session.flush()
    return rows


async def _add_campaign(session, *, approved=True):
    from app.models.regeneration_campaign import RegenerationCampaign

    campaign = RegenerationCampaign(
        status="approved" if approved else "draft",
        selection_spec={"mode": "test"},
        requested_phases=["flashcards"],
        excluded_phases=[],
        launch_contract={"provider": "gemini"},
        approved_at=_now() if approved else None,
        app_git_revision=_MARKER,
    )
    session.add(campaign)
    await session.flush()
    return campaign


async def _add_target(session, *, campaign, toc, **overrides):
    from app.models.regeneration_target import RegenerationTarget

    kwargs = dict(
        campaign_id=campaign.id,
        toc_entry_id=toc.id,
        output_language="uz",
        phase_plan=copy.deepcopy(_PLAN),
        status="planned",
    )
    kwargs.update(overrides)
    target = RegenerationTarget(**kwargs)
    session.add(target)
    await session.flush()
    return target


async def _purge(book_id):
    """Child-first: every regeneration FK is RESTRICT on purpose."""
    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.homework_job import HomeworkJob
    from app.models.regeneration_campaign import RegenerationCampaign
    from app.models.toc_entry import TOCEntry

    async with SessionLocal() as s:
        await s.execute(
            text(
                "DELETE FROM agent_usages WHERE homework_job_id IN "
                "(SELECT id FROM homework_jobs WHERE book_id = :b)"
            ),
            {"b": book_id},
        )
        await s.execute(
            text(
                "DELETE FROM phase_outputs WHERE job_id IN "
                "(SELECT id FROM homework_jobs WHERE book_id = :b)"
            ),
            {"b": book_id},
        )
        await s.execute(
            text(
                "DELETE FROM homework_jobs WHERE regeneration_target_id IN "
                "(SELECT t.id FROM regeneration_targets t JOIN toc_entries e "
                "ON e.id = t.toc_entry_id WHERE e.book_id = :b)"
            ),
            {"b": book_id},
        )
        await s.execute(
            text(
                "DELETE FROM regeneration_targets WHERE toc_entry_id IN "
                "(SELECT id FROM toc_entries WHERE book_id = :b)"
            ),
            {"b": book_id},
        )
        await s.execute(
            delete(RegenerationCampaign).where(
                RegenerationCampaign.app_git_revision == _MARKER,
                ~RegenerationCampaign.targets.any(),
            )
        )
        await s.execute(delete(HomeworkJob).where(HomeworkJob.book_id == book_id))
        await s.execute(delete(TOCEntry).where(TOCEntry.book_id == book_id))
        await s.execute(delete(Book).where(Book.id == book_id))
        await s.commit()


# ───────────────────────── V1 source selection ───────────────────────


@pytest.mark.asyncio
async def test_only_a_completed_ordinary_homework_is_a_v1_source():
    """Four decoys, each newer than the real source: a failed homework, a
    teacher deck, a cancelled job and a revision job."""
    from app.db import SessionLocal
    from app.repositories import regeneration_sources as repo

    async with SessionLocal() as s:
        book, toc = await _seed_book(s)
        real = await _add_job(s, book=book, toc=toc, created_at=_now() - timedelta(hours=5))
        await _add_job(s, book=book, toc=toc, status="failed")
        await _add_job(s, book=book, toc=toc, status="cancelled")
        await _add_job(s, book=book, toc=toc, kind="teacher_material")
        campaign = await _add_campaign(s)
        target = await _add_target(s, campaign=campaign, toc=toc, source_job_id=real.id)
        await _add_job(
            s, book=book, toc=toc,
            revision_of_job_id=real.id,
            regeneration_target_id=target.id,
            session_limit_strategy="pause",
        )
        await s.commit()
    try:
        async with SessionLocal() as s:
            got = await repo.latest_v1_source_job(
                s, toc_entry_id=toc.id, output_language="uz"
            )
        assert got is not None and got.id == real.id
    finally:
        await _purge(book.id)


@pytest.mark.asyncio
async def test_v1_sources_and_versions_are_isolated_per_language():
    from app.db import SessionLocal
    from app.repositories import regeneration_sources as repo

    async with SessionLocal() as s:
        book, toc = await _seed_book(s)
        uz = await _add_job(s, book=book, toc=toc, output_language="uz")
        ru = await _add_job(s, book=book, toc=toc, output_language="ru")
        campaign = await _add_campaign(s)
        await _add_target(
            s, campaign=campaign, toc=toc, output_language="uz",
            source_job_id=uz.id, status="publication_failed",
            publication_version=2, publication_released_at=_now(),
        )
        await s.commit()
    try:
        async with SessionLocal() as s:
            assert (
                await repo.latest_v1_source_job(
                    s, toc_entry_id=toc.id, output_language="uz"
                )
            ).id == uz.id
            assert (
                await repo.latest_v1_source_job(
                    s, toc_entry_id=toc.id, output_language="ru"
                )
            ).id == ru.id
            assert (
                await repo.next_expected_version(
                    s, toc_entry_id=toc.id, output_language="uz"
                )
                == 3
            )
            assert (
                await repo.next_expected_version(
                    s, toc_entry_id=toc.id, output_language="ru"
                )
                == 2
            )
    finally:
        await _purge(book.id)


# ─────────────────── published-source selection / versions ───────────


@pytest.mark.asyncio
async def test_an_unpublished_or_abandoned_revision_is_never_the_source():
    """V2 reserved its number and then failed delivery; V3 was abandoned. Only
    the published V4 may be a source — but 2 and 3 stay consumed forever."""
    from app.db import SessionLocal
    from app.repositories import regeneration_sources as repo

    async with SessionLocal() as s:
        book, toc = await _seed_book(s)
        v1 = await _add_job(s, book=book, toc=toc)
        # One campaign per version: `uq_regeneration_targets_campaign_toc_language`
        # allows a campaign only ONE target per (lesson, language), and real
        # versions come from successive campaigns anyway.
        await _add_target(
            s, campaign=await _add_campaign(s), toc=toc, source_job_id=v1.id,
            status="abandoned", publication_version=3,
            publication_released_at=_now(), terminal_at=_now(),
            terminal_reason="test",
        )
        failed = await _add_target(
            s, campaign=await _add_campaign(s), toc=toc, source_job_id=v1.id,
            status="publication_failed", publication_version=2,
            publication_released_at=_now(),
        )
        await s.commit()

        # The lineage index allows only ONE non-terminal target, so the
        # still-open V2 must converge before the published V4 can be added.
        async with SessionLocal() as s2:
            assert (
                await repo.latest_published_target(
                    s2, toc_entry_id=toc.id, output_language="uz"
                )
                is None
            ), "an unpublished/abandoned revision must never be a source"
            assert (
                await repo.next_expected_version(
                    s2, toc_entry_id=toc.id, output_language="uz"
                )
                == 4
            ), "a reserved version is consumed even when delivery failed"
    try:
        async with SessionLocal() as s:
            await s.execute(
                text(
                    "UPDATE regeneration_targets SET status='abandoned', "
                    "terminal_at=now(), terminal_reason='converged' WHERE id=:i"
                ),
                {"i": failed.id},
            )
            published = await _add_target(
                s, campaign=await _add_campaign(s), toc=toc, source_job_id=v1.id,
                status="published", publication_version=4,
                publication_released_at=_now(), terminal_at=_now(),
                notion_page_id="page-v4",
            )
            await s.commit()

        async with SessionLocal() as s:
            got = await repo.latest_published_target(
                s, toc_entry_id=toc.id, output_language="uz"
            )
            assert got is not None and got.id == published.id
            assert (
                await repo.next_expected_version(
                    s, toc_entry_id=toc.id, output_language="uz"
                )
                == 5
            )
    finally:
        await _purge(book.id)


@pytest.mark.asyncio
async def test_lock_lineage_blocks_a_second_transaction_until_the_first_commits():
    """`next_expected_version` is a read-then-write; without a real lock two
    campaigns compute the same number."""
    from sqlalchemy.exc import DBAPIError

    from app.db import SessionLocal
    from app.repositories import regeneration_sources as repo

    async with SessionLocal() as s:
        book, toc = await _seed_book(s)
        v1 = await _add_job(s, book=book, toc=toc)
        campaign = await _add_campaign(s)
        await _add_target(s, campaign=campaign, toc=toc, source_job_id=v1.id)
        await s.commit()
    try:
        async with SessionLocal() as sa, SessionLocal() as sb:
            await sa.begin()
            held = await repo.lock_lineage(sa, toc_entry_id=toc.id, output_language="uz")
            assert len(held) == 1

            await sb.begin()
            await sb.execute(text("SET LOCAL lock_timeout = '400ms'"))
            with pytest.raises(DBAPIError) as excinfo:
                await repo.lock_lineage(sb, toc_entry_id=toc.id, output_language="uz")
            assert "lock timeout" in str(excinfo.value).lower()
            await sb.rollback()

            await sa.rollback()  # releases the lock

            await sb.begin()
            await sb.execute(text("SET LOCAL lock_timeout = '400ms'"))
            assert (
                len(await repo.lock_lineage(sb, toc_entry_id=toc.id, output_language="uz"))
                == 1
            )
            await sb.rollback()
    finally:
        await _purge(book.id)


# ───────────────────────── discovery end to end ──────────────────────


@pytest.mark.asyncio
async def test_discovery_prefers_the_published_revision_over_the_original():
    from app.db import SessionLocal
    from app.services import regeneration_discovery as discovery

    async with SessionLocal() as s:
        book, toc = await _seed_book(s)
        v1 = await _add_job(s, book=book, toc=toc)
        await _add_snapshot(s, v1)
        campaign = await _add_campaign(s)
        target = await _add_target(
            s, campaign=campaign, toc=toc, source_job_id=v1.id,
            status="published", publication_version=2,
            publication_released_at=_now(), terminal_at=_now(),
            notion_page_id="page-v2",
        )
        revision = await _add_job(
            s, book=book, toc=toc,
            revision_of_job_id=v1.id,
            regeneration_target_id=target.id,
            session_limit_strategy="pause",
        )
        await _add_snapshot(s, revision)
        await s.commit()
    try:
        async with SessionLocal() as s:
            sources = await discovery.list_eligible_sources(
                s, book_ids=[book.id], toc_entry_ids=None, output_languages=None
            )
            assert [x.source_job_id for x in sources] == [revision.id]
            assert sources[0].source_publication_version == 2
            assert sources[0].next_expected_version == 3
            assert sources[0].source_is_revision is True
            assert sources[0].grade == "7"

            resolved = await discovery.resolve_default_source(
                s, toc_entry_id=toc.id, output_language="uz"
            )
            assert resolved.id == revision.id
    finally:
        await _purge(book.id)


@pytest.mark.asyncio
async def test_discovery_refuses_a_purged_lineage_on_the_null_source_predicate():
    """The end-to-end shape of a completed child-first purge: the target row
    survives with a NULL source link and no revision job behind it."""
    from app.db import SessionLocal
    from app.services import regeneration_discovery as discovery

    async with SessionLocal() as s:
        book, toc = await _seed_book(s)
        v1 = await _add_job(s, book=book, toc=toc)
        await _add_snapshot(s, v1)
        campaign = await _add_campaign(s)
        purged = await _add_target(
            s, campaign=campaign, toc=toc, source_job_id=None,
            status="published", publication_version=2,
            publication_released_at=_now(), terminal_at=_now(),
            notion_page_id="page-v2",
        )
        await s.commit()
    try:
        async with SessionLocal() as s:
            assert (
                await discovery.list_eligible_sources(
                    s, book_ids=[book.id], toc_entry_ids=None, output_languages=None
                )
                == []
            )
            (candidate,) = await discovery.list_source_candidates(
                s, book_ids=[book.id], toc_entry_ids=None, output_languages=None
            )
            assert candidate.reasons == (discovery.SOURCE_JOB_ID_IS_NULL_REASON,)
            assert str(purged.id) in candidate.detail
            with pytest.raises(discovery.NoEligibleSource) as excinfo:
                await discovery.resolve_default_source(
                    s, toc_entry_id=toc.id, output_language="uz"
                )
            assert excinfo.value.reasons == (discovery.SOURCE_JOB_ID_IS_NULL_REASON,)
    finally:
        await _purge(book.id)


@pytest.mark.asyncio
async def test_discovery_reports_an_incomplete_snapshot_instead_of_offering_it():
    from app.db import SessionLocal
    from app.services import regeneration_discovery as discovery

    async with SessionLocal() as s:
        book, toc = await _seed_book(s)
        v1 = await _add_job(s, book=book, toc=toc)
        await _add_snapshot(s, v1, complete=False)  # `reflection` missing
        await s.commit()
    try:
        async with SessionLocal() as s:
            (candidate,) = await discovery.list_source_candidates(
                s, book_ids=[book.id], toc_entry_ids=None, output_languages=None
            )
        assert candidate.eligible is False
        assert candidate.reasons == ("missing phase row: reflection",)
    finally:
        await _purge(book.id)


@pytest.mark.asyncio
async def test_a_corrected_book_subject_does_not_split_one_lineage_in_two():
    """`homework_jobs.subject` is stamped from the book at launch and the book's
    subject is user-editable (`PATCH /books/{id}`), so two `done` jobs of ONE
    lineage can legitimately disagree on it. A lineage is
    `(toc_entry_id, output_language)` and nothing else: the older, stale-subject
    job must not produce a second candidate that double-prices the lesson,
    double-preflights it, and collides on
    `uq_regeneration_targets_campaign_toc_language` at campaign creation.
    """
    from app.db import SessionLocal
    from app.repositories import regeneration_sources as repo
    from app.services import regeneration_discovery as discovery

    async with SessionLocal() as s:
        book, toc = await _seed_book(s)
        # Generated while the book was mis-classified as `history` …
        stale = await _add_job(
            s, book=book, toc=toc,
            subject="history", created_at=_now() - timedelta(hours=5),
        )
        await _add_snapshot(s, stale)
        # … then the operator corrected the book, and the lesson was re-run.
        corrected = await _add_job(
            s, book=book, toc=toc,
            subject=SUBJECT, created_at=_now() - timedelta(hours=1),
        )
        await _add_snapshot(s, corrected)
        await s.commit()
    try:
        async with SessionLocal() as s:
            lineages = await repo.candidate_lineages(
                s, book_ids=[book.id], toc_entry_ids=None, output_languages=None
            )
            assert len(lineages) == 1, [
                (c.toc_entry_id, c.output_language) for c in lineages
            ]
            assert (lineages[0].toc_entry_id, lineages[0].output_language) == (
                toc.id,
                "uz",
            )

            sources = await discovery.list_eligible_sources(
                s, book_ids=[book.id], toc_entry_ids=None, output_languages=None
            )
            assert len(sources) == 1, [
                (x.source_job_id, x.subject) for x in sources
            ]
            # The one source is the newest job, carrying the corrected subject.
            assert sources[0].source_job_id == corrected.id
            assert sources[0].subject == SUBJECT
    finally:
        await _purge(book.id)


# ─────────────────── estimator observation window ────────────────────


@pytest.mark.asyncio
async def test_the_observation_window_selects_exactly_the_qualifying_rows():
    """Boundary and filters, on real rows: inclusive at ``now - 30d``, one
    second earlier is out, and cli / failed / phase-less rows never count."""
    from app.db import SessionLocal
    from app.repositories import agent_usage as usage_repo
    from app.services.regeneration_estimator import (
        observation_stmt,
        summarize_observations,
    )

    now = _now().replace(microsecond=0)
    edge = now - timedelta(days=30)

    async with SessionLocal() as s:
        book, toc = await _seed_book(s)
        job = await _add_job(s, book=book, toc=toc)
        rows = await _add_snapshot(s, job)
        by_name = {r.phase_name: r for r in rows}

        async def _usage(**kw):
            kw.setdefault("provider", "gemini")
            kw.setdefault("model_name", "gemini-3.5-flash")
            kw.setdefault("auth_mode", "api")
            kw.setdefault("homework_job_id", job.id)
            await usage_repo.create(s, **kw)

        # in-window, on the exact boundary
        await _usage(
            operation="phase.run",
            phase_output_id=by_name["reflection"].id,
            started_at=edge,
            prompt_tokens=10_000,
            output_tokens=1_000,
        )
        # one second older — out
        await _usage(
            operation="phase.run",
            phase_output_id=by_name["reflection"].id,
            started_at=edge - timedelta(seconds=1),
            prompt_tokens=999_999,
            output_tokens=999_999,
        )
        # failed call — out
        await _usage(
            operation="phase.run",
            phase_output_id=by_name["reflection"].id,
            started_at=now,
            prompt_tokens=999_999,
            success=False,
        )
        # cli call — out
        await _usage(
            operation="phase.run",
            phase_output_id=by_name["reflection"].id,
            started_at=now,
            auth_mode="cli",
            prompt_tokens=999_999,
        )
        # no phase row — out (the INNER JOIN drops it)
        await _usage(operation="phase.run", started_at=now, prompt_tokens=999_999)
        # a judge call hanging off the phase it inspected — in
        await _usage(
            operation="judge:flashcards",
            phase_output_id=by_name["flashcards"].id,
            model_name="gemini-3.6-flash",
            started_at=now,
            prompt_tokens=8_000,
            output_tokens=500,
        )
        await s.commit()
    try:
        async with SessionLocal() as s:
            fetched = (
                await s.execute(observation_stmt(window_start=edge, window_end=now))
            ).all()
        observed, notes = summarize_observations(fetched)
        assert notes == []
        assert observed[("authoring", "reflection", "gemini", "gemini-3.5-flash")].samples == 1
        assert observed[
            ("authoring", "reflection", "gemini", "gemini-3.5-flash")
        ].prompt_tokens == pytest.approx(10_000)
        assert observed[("judge", "flashcards", "gemini", "gemini-3.6-flash")].samples == 1
    finally:
        await _purge(book.id)


# ───────────────────────── cost-ledger isolation ─────────────────────


@pytest.mark.asyncio
async def test_prior_fleet_cost_skips_a_revision_while_the_campaign_read_counts_it():
    from app.db import SessionLocal
    from app.repositories import agent_usage as usage_repo
    from app.repositories.cost import campaign_actual_api_cost_usd, section_prior_api_cost

    async with SessionLocal() as s:
        book, toc = await _seed_book(s)
        v1 = await _add_job(s, book=book, toc=toc, created_at=_now() - timedelta(hours=3))
        campaign = await _add_campaign(s)
        target = await _add_target(s, campaign=campaign, toc=toc, source_job_id=v1.id)
        revision = await _add_job(
            s, book=book, toc=toc,
            revision_of_job_id=v1.id,
            regeneration_target_id=target.id,
            session_limit_strategy="pause",
        )
        # $1.50: 1M prompt tokens on gemini-3.5-flash
        await usage_repo.create(
            s, operation="phase.run", provider="gemini",
            model_name="gemini-3.5-flash", auth_mode="api",
            homework_job_id=v1.id, prompt_tokens=1_000_000, started_at=_now(),
        )
        # $9.00: 1M output tokens on the REVISION
        await usage_repo.create(
            s, operation="phase.run", provider="gemini",
            model_name="gemini-3.5-flash", auth_mode="api",
            homework_job_id=revision.id, output_tokens=1_000_000, started_at=_now(),
        )
        await s.commit()
    try:
        async with SessionLocal() as s:
            cost, had_done = await section_prior_api_cost(s, book.id, toc.id, "api")
            assert had_done is True
            assert cost == pytest.approx(1.50, rel=1e-6)

            assert await campaign_actual_api_cost_usd(s, campaign.id) == pytest.approx(
                9.00, rel=1e-6
            )
    finally:
        await _purge(book.id)


@pytest.mark.asyncio
async def test_two_lineages_can_be_locked_concurrently():
    """The lock is per lineage, not a global gate: two different lessons must
    not serialise behind each other."""
    from app.db import SessionLocal
    from app.repositories import regeneration_sources as repo
    from app.models.toc_entry import TOCEntry

    async with SessionLocal() as s:
        book, toc_a = await _seed_book(s)
        toc_b = TOCEntry(
            book_id=book.id, section_number="2", section_title="Ikkinchi", order_index=1
        )
        s.add(toc_b)
        await s.flush()
        v_a = await _add_job(s, book=book, toc=toc_a)
        v_b = await _add_job(s, book=book, toc=toc_b)
        campaign = await _add_campaign(s)
        await _add_target(s, campaign=campaign, toc=toc_a, source_job_id=v_a.id)
        await _add_target(s, campaign=campaign, toc=toc_b, source_job_id=v_b.id)
        await s.commit()
    try:

        async def _hold(toc_id):
            async with SessionLocal() as s:
                await s.begin()
                await s.execute(text("SET LOCAL lock_timeout = '2s'"))
                rows = await repo.lock_lineage(
                    s, toc_entry_id=toc_id, output_language="uz"
                )
                await asyncio.sleep(0.3)
                await s.rollback()
                return len(rows)

        assert await asyncio.gather(_hold(toc_a.id), _hold(toc_b.id)) == [1, 1]
    finally:
        await _purge(book.id)


# ─────────────────── publication_version_conflicts ───────────────────


def _eligible(toc_entry_id, output_language, *, source_publication_version=1):
    """One `EligibleRegenerationSource` for a lineage.

    Built directly rather than through discovery: what is under test here is
    the conflict QUERY, and driving a whole discovery pass for each case would
    make the verdict depend on source selection as well.
    """
    from app.services.regeneration_discovery import EligibleRegenerationSource

    return EligibleRegenerationSource(
        source_job_id=uuid.uuid4(),
        toc_entry_id=toc_entry_id,
        book_id=uuid.uuid4(),
        subject=SUBJECT,
        grade="7",
        output_language=output_language,
        source_publication_version=source_publication_version,
        next_expected_version=source_publication_version + 1,
        source_is_revision=source_publication_version > 1,
        book_filename="regen_sources.pdf",
        section_number="1",
        section_title="Kirish",
        chapter_title="",
        page_start=1,
        notion_lesson_page_id=None,
        order_index=0,
    )


@pytest.mark.asyncio
async def test_a_consumed_publication_version_is_seen_in_any_state_one_lineage_only():
    """A number is spent when it is RESERVED. An `abandoned` target that never
    reached Notion still owns V5, while the SAME lesson's other language does
    not — `uq_regeneration_targets_publication_version` is per
    `(toc_entry_id, output_language)`, and so is this read."""
    from app.db import SessionLocal
    from app.repositories import regeneration_sources as repo

    async with SessionLocal() as s:
        book, toc = await _seed_book(s)
        job = await _add_job(s, book=book, toc=toc)
        campaign = await _add_campaign(s)
        await _add_target(
            s, campaign=campaign, toc=toc, output_language="uz",
            source_job_id=job.id, status="abandoned", terminal_at=_now(),
            terminal_reason="pytest", publication_version=5,
            publication_released_at=_now(),
        )
        await s.commit()
    try:
        uz, ru = _eligible(toc.id, "uz"), _eligible(toc.id, "ru")
        async with SessionLocal() as s:
            conflicts = await repo.publication_version_conflicts(
                s, sources=[uz, ru], requested_version=5
            )
        assert [(c.output_language, c.reason) for c in conflicts] == [
            ("uz", "already_consumed")
        ]
        assert conflicts[0].existing_version == 5
        assert conflicts[0].toc_entry_id == toc.id

        async with SessionLocal() as s:
            assert await repo.publication_version_conflicts(
                s, sources=[uz, ru], requested_version=6
            ) == ()
    finally:
        await _purge(book.id)


@pytest.mark.asyncio
async def test_a_source_not_older_than_the_requested_version_is_refused():
    """Compared against the source's OWN published version, not against the
    table: a lineage already at V3 cannot produce another V3 even on a database
    where V3's row has since been purged."""
    from app.db import SessionLocal
    from app.repositories import regeneration_sources as repo

    async with SessionLocal() as s:
        book, toc = await _seed_book(s)
        await _add_job(s, book=book, toc=toc)
        await s.commit()
    try:
        at_v3 = _eligible(toc.id, "uz", source_publication_version=3)
        async with SessionLocal() as s:
            for requested in (2, 3):
                conflicts = await repo.publication_version_conflicts(
                    s, sources=[at_v3], requested_version=requested
                )
                assert [c.reason for c in conflicts] == ["source_not_older"], requested
                assert conflicts[0].existing_version == 3
                assert conflicts[0].requested_version == requested
            assert await repo.publication_version_conflicts(
                s, sources=[at_v3], requested_version=4
            ) == ()
    finally:
        await _purge(book.id)
