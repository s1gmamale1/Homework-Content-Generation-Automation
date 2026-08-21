"""Real-DB races and convergence for the regeneration campaign orchestrator.

Everything here needs a real PostgreSQL because the thing under test IS the
database behaviour: `SELECT … FOR UPDATE` serialisation, compare-and-set
`UPDATE … WHERE status IN (…)`, the partial unique lineage index, and
`trg_regeneration_targets_publication_gate`, which *raises* (it does not return
False) once the owning campaign stops being approved.

The convergence trio at the bottom is the reason cancellation is not allowed to
write `cancelled` directly: a naive terminal write turns every in-flight
publication into a `check_violation` in the middle of an irreversible Notion
delivery.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timezone

import pytest

from app.schemas.regeneration_contract import LaunchContract, ResolvedLaunchContract
from app.services import regeneration_campaign as svc
from app.services.flows import flow_for

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)

_SUBJECT = "math-algebra"
_MARKER = "pytest-regen-campaign-race"

_CONTRACT = ResolvedLaunchContract(
    provider="gemini", model="gemini-3.6-flash", transport="api",
    extract_provider="gemini", extract_model="gemini-3.5-flash-lite",
    extract_transport="api",
    judge_provider="gemini", judge_model="gemini-3.5-flash", judge_transport="api",
    solver_provider="gemini", solver_model="gemini-3.1-pro-preview",
    solver_transport="api",
    session_limit_strategy="pause",
)


@pytest.fixture(autouse=True)
def _notion_destinations(monkeypatch):
    """A configured Notion destination for the seeded book.

    `launch_canary` preflights every destination first, and preflight requires
    the target language's own `{lang}:{subject}|{grade}` subject page for EVERY
    lesson — the seeded `notion_lesson_page_id` is a language-blind pointer and
    does not excuse it. These tests are about row locks and convergence, so the
    mapping is pinned here (not inherited from the ambient
    `NOTION_SUBJECT_PAGES`) to keep the launch reaching the code under test.
    """
    from app.config import settings

    monkeypatch.setattr(
        settings, "notion_subject_pages", {f"{_SUBJECT}|5": "page-uz-5"},
        raising=False,
    )


async def _seed(*, lessons: int = 1, languages=("uz",)):
    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.homework_job import HomeworkJob
    from app.models.phase_output import PhaseOutput
    from app.models.toc_entry import TOCEntry

    canonical = ("extract", *flow_for(_SUBJECT))
    async with SessionLocal() as session:
        book = Book(
            subject=_SUBJECT, original_filename="regen_race.pdf",
            content_sha256=uuid.uuid4().hex * 2, file_size_bytes=1,
            status="toc_ready", grade="5",
        )
        session.add(book)
        await session.flush()
        toc_ids = []
        for index in range(lessons):
            toc = TOCEntry(
                book_id=book.id, section_title=f"Lesson {index}", order_index=index,
                notion_lesson_page_id=f"page-{uuid.uuid4()}",
            )
            session.add(toc)
            await session.flush()
            toc_ids.append(toc.id)
            for language in languages:
                job = HomeworkJob(
                    book_id=book.id, toc_entry_id=toc.id, subject=_SUBJECT,
                    status="done", provider="gemini", model="gemini-3.6-flash",
                    transport="api", output_language=language,
                )
                session.add(job)
                await session.flush()
                for order, name in enumerate(canonical):
                    session.add(PhaseOutput(
                        job_id=job.id, phase_name=name, phase_order=order,
                        prompt_hash=f"builtin:{name}:v9", provider="gemini",
                        model_name="gemini-3.6-flash",
                        output_md=f"# {name}\nbody", status="done",
                    ))
                await session.flush()
        await session.commit()
        return {"book_id": book.id, "toc_ids": toc_ids}


async def _purge(ids: dict) -> None:
    from sqlalchemy import delete, select, text

    from app.db import SessionLocal
    from app.models.agent_usage import AgentUsage
    from app.models.book import Book
    from app.models.homework_job import HomeworkJob
    from app.models.phase_output import PhaseOutput
    from app.models.regeneration_campaign import RegenerationCampaign
    from app.models.regeneration_target import RegenerationTarget
    from app.models.toc_entry import TOCEntry

    async with SessionLocal() as session:
        job_ids = list((await session.execute(
            select(HomeworkJob.id).where(HomeworkJob.book_id == ids["book_id"])
        )).scalars().all()) or [uuid.uuid4()]
        await session.execute(
            delete(AgentUsage).where(AgentUsage.homework_job_id.in_(job_ids)))
        await session.execute(
            delete(PhaseOutput).where(PhaseOutput.job_id.in_(job_ids)))
        await session.execute(
            delete(HomeworkJob)
            .where(HomeworkJob.book_id == ids["book_id"])
            .where(HomeworkJob.revision_of_job_id.is_not(None)))
        await session.execute(
            text("DELETE FROM regeneration_targets WHERE toc_entry_id IN "
                 "(SELECT id FROM toc_entries WHERE book_id = :b)"),
            {"b": ids["book_id"]})
        await session.execute(
            delete(RegenerationCampaign)
            .where(RegenerationCampaign.app_git_revision == _MARKER)
            .where(~RegenerationCampaign.targets.any()))
        await session.execute(
            delete(HomeworkJob).where(HomeworkJob.book_id == ids["book_id"]))
        await session.execute(
            delete(TOCEntry).where(TOCEntry.book_id == ids["book_id"]))
        await session.execute(delete(Book).where(Book.id == ids["book_id"]))
        await session.commit()


def _spec(ids, *, canary_size=1, lessons=None):
    toc_ids = ids["toc_ids"] if lessons is None else ids["toc_ids"][:lessons]
    return svc.CreateCampaignSpec(
        selection=svc.CampaignSelection(
            toc_entry_ids=tuple(toc_ids), output_languages=("uz",)),
        contract=LaunchContract(**_CONTRACT.model_dump()),
        selected_phases=("flashcards",),
        canary_size=canary_size,
        app_git_revision=_MARKER,
        actor="pytest",
    )


async def _targets(campaign_id):
    from app.db import SessionLocal
    from app.repositories import regeneration_targets as targets_repo

    async with SessionLocal() as session:
        return await targets_repo.list_for_campaign(session, campaign_id)


async def _revision_job_count(campaign_id) -> int:
    from sqlalchemy import func, select

    from app.db import SessionLocal
    from app.models.homework_job import HomeworkJob
    from app.models.regeneration_target import RegenerationTarget

    async with SessionLocal() as session:
        return await session.scalar(
            select(func.count()).select_from(HomeworkJob)
            .join(RegenerationTarget,
                  RegenerationTarget.id == HomeworkJob.regeneration_target_id)
            .where(RegenerationTarget.campaign_id == campaign_id))


async def _campaign(campaign_id):
    from app.db import SessionLocal
    from app.repositories import regeneration_campaigns as campaigns_repo

    async with SessionLocal() as session:
        return await campaigns_repo.get_campaign(session, campaign_id)


async def _finish_canary(campaign_id):
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models.homework_job import HomeworkJob
    from app.models.regeneration_target import RegenerationTarget
    from app.repositories import jobs as jobs_repo
    from app.services import regeneration_job_state

    async with SessionLocal() as session:
        job_ids = list((await session.execute(
            select(HomeworkJob.id)
            .join(RegenerationTarget,
                  RegenerationTarget.id == HomeworkJob.regeneration_target_id)
            .where(RegenerationTarget.campaign_id == campaign_id)
        )).scalars().all())
    from app.models.phase_output import PhaseOutput
    from app.models.regeneration_target import RegenerationTarget as _Target
    from app.repositories import phase_outputs as phase_repo
    from app.services.regeneration_planner import RegenerationPhasePlan

    canonical = ("extract", *flow_for(_SUBJECT))
    for job_id in job_ids:
        async with SessionLocal() as session:
            # write the phase rows the pipeline would have regenerated, so the
            # revision is a COMPLETE snapshot (no model call involved)
            job = await jobs_repo.get(session, job_id)
            target = await session.get(_Target, job.regeneration_target_id)
            plan = RegenerationPhasePlan.from_json(target.phase_plan)
            have = {r.phase_name for r in await phase_repo.list_for_job(
                session, job_id)}
            for name in plan.regenerated_phases:
                if name in have:
                    continue
                session.add(PhaseOutput(
                    job_id=job_id, phase_name=name,
                    phase_order=canonical.index(name),
                    prompt_hash=f"builtin:{name}:v9", provider="gemini",
                    model_name="gemini-3.6-flash",
                    output_md=f"# regenerated {name}\nbody", status="done"))
            await session.flush()
            await jobs_repo.set_status(session, job_id, "done")
            await session.commit()
            await regeneration_job_state.reconcile_revision_job(session, job_id)
            await session.commit()


# ═══════════════════════ stale identity map (expire_on_commit=False) ═════


async def test_locked_campaign_reload_is_fresh_not_the_stale_identity_map():
    """`SessionLocal` is `expire_on_commit=False`, so a session that already
    loaded a campaign keeps that Python object forever. Without
    `populate_existing`, the row-locked read hands back the STALE object and
    the caller decides an approval/cancel against a status that has moved."""
    from app.db import SessionLocal
    from app.repositories import regeneration_campaigns as campaigns_repo

    ids = await _seed()
    try:
        campaign = await svc.RegenerationCampaignService().create_campaign(_spec(ids))
        async with SessionLocal() as session:
            stale = await campaigns_repo.get_campaign(session, campaign.id)
            assert stale.status == "draft"

            async with SessionLocal() as other:
                await campaigns_repo.set_campaign_status(
                    other, campaign_id=campaign.id, new_status="canary_running",
                    expected_statuses=["draft"],
                    canary_launched_at=datetime.now(timezone.utc))
                await other.commit()

            locked = await campaigns_repo.get_campaign_for_update(
                session, campaign.id)
            assert locked is stale, "same session → same identity-map object"
            assert locked.status == "canary_running"
            assert locked.canary_launched_at is not None
    finally:
        await _purge(ids)


async def test_locked_target_reload_is_fresh_not_the_stale_identity_map():
    from app.db import SessionLocal
    from app.repositories import regeneration_targets as targets_repo

    ids = await _seed()
    try:
        campaign = await svc.RegenerationCampaignService().create_campaign(_spec(ids))
        (target,) = await _targets(campaign.id)
        async with SessionLocal() as session:
            stale = await targets_repo.list_for_campaign(session, campaign.id)
            assert stale[0].status == "planned"

            async with SessionLocal() as other:
                await targets_repo.set_target_status(
                    other, target_id=target.id, new_status="generating",
                    expected_statuses=["planned"])
                await other.commit()

            locked = await targets_repo.get_target_for_update(session, target.id)
            assert locked is stale[0]
            assert locked.status == "generating"
    finally:
        await _purge(ids)


# ═════════════════════════════ races ═════════════════════════════════════


async def test_two_campaigns_racing_for_one_lineage_only_one_wins():
    """The partial unique index is the real guard; the service must surface it
    as an actionable conflict, not a raw IntegrityError."""
    ids = await _seed()
    try:
        service = svc.RegenerationCampaignService()
        results = await asyncio.gather(
            service.create_campaign(_spec(ids)),
            service.create_campaign(_spec(ids)),
            return_exceptions=True,
        )
        failures = [r for r in results if isinstance(r, BaseException)]
        winners = [r for r in results if not isinstance(r, BaseException)]
        assert len(winners) == 1 and len(failures) == 1
        assert isinstance(failures[0], svc.ActiveLineageConflict)

        from sqlalchemy import text

        from app.db import SessionLocal
        async with SessionLocal() as session:
            live = await session.scalar(
                text("SELECT count(*) FROM regeneration_targets "
                     "WHERE toc_entry_id=:t AND terminal_at IS NULL"),
                {"t": ids["toc_ids"][0]})
        assert live == 1
    finally:
        await _purge(ids)


async def test_two_concurrent_approvals_approve_once_and_create_one_wave():
    ids = await _seed(lessons=3)
    try:
        service = svc.RegenerationCampaignService()
        campaign = await service.create_campaign(_spec(ids, canary_size=1))
        await service.launch_canary(campaign.id)
        await _finish_canary(campaign.id)

        results = await asyncio.gather(
            service.approve_canary(campaign.id, actor="a"),
            service.approve_canary(campaign.id, actor="b"),
            return_exceptions=True,
        )
        assert [r for r in results if isinstance(r, BaseException)] == []
        approved = await _campaign(campaign.id)
        assert approved.approved_at is not None
        assert len({r.approved_at for r in results}) == 1
        # exactly one revision job per target, canary included
        assert await _revision_job_count(campaign.id) == 3
        statuses = sorted(t.status for t in await _targets(campaign.id))
        assert statuses == ["generating", "generating", "publication_pending"]
    finally:
        await _purge(ids)


async def test_two_concurrent_launches_create_one_job_per_target():
    """Two operators (or a retried request) may launch the same canary at once;
    `homework_jobs.regeneration_target_id` is UNIQUE and the target is locked,
    so the loser must adopt the existing job rather than fail or duplicate."""
    ids = await _seed(lessons=2)
    try:
        service = svc.RegenerationCampaignService()
        campaign = await service.create_campaign(_spec(ids, canary_size=2))
        results = await asyncio.gather(
            service.launch_canary(campaign.id),
            service.launch_canary(campaign.id),
            return_exceptions=True,
        )
        assert [r for r in results if isinstance(r, BaseException)] == []
        assert await _revision_job_count(campaign.id) == 2
    finally:
        await _purge(ids)


async def test_two_concurrent_revision_job_creators_produce_one_job():
    """The primitive underneath, raced directly."""
    from app.db import SessionLocal
    from app.services import regeneration_snapshot

    ids = await _seed()
    try:
        service = svc.RegenerationCampaignService()
        campaign = await service.create_campaign(_spec(ids))
        (target,) = await _targets(campaign.id)

        async def create():
            async with SessionLocal() as session:
                return await regeneration_snapshot.create_revision_job(
                    session, target_id=target.id,
                    launch_contract=campaign.launch_contract)

        results = await asyncio.gather(create(), create(), return_exceptions=True)
        made = [r for r in results if not isinstance(r, BaseException)]
        assert len({j.id for j in made}) == 1
        assert await _revision_job_count(campaign.id) == 1
    finally:
        await _purge(ids)


async def test_approval_racing_cancellation_never_leaves_an_illegal_state():
    """Whichever wins, the invariants hold: a cancelled campaign never becomes
    terminal over a live target, and an approved campaign never publishes an
    abandoned one."""
    ids = await _seed(lessons=3)
    try:
        service = svc.RegenerationCampaignService()
        campaign = await service.create_campaign(_spec(ids, canary_size=1))
        await service.launch_canary(campaign.id)
        await _finish_canary(campaign.id)

        results = await asyncio.gather(
            service.approve_canary(campaign.id, actor="a"),
            service.cancel(campaign.id, actor="b", reason="stop"),
            return_exceptions=True,
        )
        errors = [r for r in results if isinstance(r, BaseException)]
        assert all(isinstance(e, svc.CampaignError) for e in errors), errors

        after = await _campaign(campaign.id)
        targets = await _targets(campaign.id)
        live = [t for t in targets if t.terminal_at is None]
        if after.status in ("cancelled", "completed", "completed_with_abandonments",
                            "rejected"):
            assert live == []
        if after.cancel_requested_at is not None:
            # nothing may be released for publication after a cancel converged
            assert not any(
                t.status == "publication_pending" and t.abandon_requested_at is None
                for t in targets
            ) or after.approved_at is not None
    finally:
        await _purge(ids)


# ═══════════════ cancellation convergence (the armed trigger) ════════════


async def test_cancel_lets_an_in_flight_publication_finish():
    """1/3. `claim_target_publication`'s gate RAISES once the campaign is no
    longer approved, so a naive `set_campaign_status('cancelled')` would turn
    an in-flight Notion delivery into a `check_violation`. The campaign parks
    in `attention_required` and the fenced `published` write succeeds."""
    from app.db import SessionLocal
    from app.repositories import regeneration_targets as targets_repo

    ids = await _seed(lessons=2)
    try:
        service = svc.RegenerationCampaignService()
        campaign = await service.create_campaign(_spec(ids, canary_size=1))
        await service.launch_canary(campaign.id)
        await _finish_canary(campaign.id)
        await service.approve_canary(campaign.id, actor="pytest")

        (publishing,) = [t for t in await _targets(campaign.id)
                         if t.status == "publication_pending"]
        token = uuid.uuid4()
        async with SessionLocal() as session:
            assert await targets_repo.claim_target_publication(
                session, target_id=publishing.id, claim_token=token,
                lease_seconds=300)
            await session.commit()

        cancelled = await service.cancel(campaign.id, actor="pytest", reason="stop")
        assert cancelled.status == "attention_required"
        assert cancelled.status != "cancelled"

        # the publisher's fenced terminal write still lands
        async with SessionLocal() as session:
            assert await targets_repo.set_target_status(
                session, target_id=publishing.id, new_status="published",
                expected_statuses=["publishing"], expected_claim_token=token,
                publication_version=2, notion_page_id="page-v2",
                terminal_at=datetime.now(timezone.utc),
                terminal_reason="published", clear_publication_claim=True)
            await session.commit()

        rolled = await service.roll_up(campaign.id)
        assert rolled.status == "completed_with_abandonments"
        assert [t.status for t in await _targets(campaign.id)].count("published") == 1
    finally:
        await _purge(ids)


async def test_cancel_with_a_failed_publication_rolls_up_to_cancelled():
    """2/3. Same shape, delivery fails: the publisher resolves its claim to
    `abandoned` (the abandon intent is already stamped) and only THEN may the
    campaign become terminal."""
    from app.db import SessionLocal
    from app.repositories import regeneration_targets as targets_repo

    ids = await _seed()
    try:
        service = svc.RegenerationCampaignService()
        campaign = await service.create_campaign(_spec(ids))
        await service.launch_canary(campaign.id)
        await _finish_canary(campaign.id)
        await service.approve_canary(campaign.id, actor="pytest")
        (target,) = await _targets(campaign.id)
        token = uuid.uuid4()
        async with SessionLocal() as session:
            assert await targets_repo.claim_target_publication(
                session, target_id=target.id, claim_token=token, lease_seconds=300)
            await session.commit()

        cancelled = await service.cancel(campaign.id, actor="pytest", reason="stop")
        assert cancelled.status == "attention_required"
        async with SessionLocal() as session:
            fresh = await targets_repo.get_target_for_update(session, target.id)
            assert fresh.abandon_requested_at is not None
            assert fresh.status == "publishing"

        # delivery failed → the claim resolves to abandoned, version preserved
        async with SessionLocal() as session:
            assert await targets_repo.set_target_status(
                session, target_id=target.id, new_status="abandoned",
                expected_statuses=["publishing"], expected_claim_token=token,
                terminal_at=datetime.now(timezone.utc),
                terminal_reason="cancelled before delivery",
                publication_last_error="notion 500",
                clear_publication_claim=True)
            await session.commit()

        rolled = await service.roll_up(campaign.id)
        assert rolled.status == "cancelled"
        assert rolled.completed_at is not None
    finally:
        await _purge(ids)


async def test_a_direct_terminal_status_write_is_refused_by_the_service():
    """3/3. The guard exists at the service layer because
    `set_campaign_status` is a deliberately dumb compare-and-set."""
    from app.db import SessionLocal
    from app.repositories import regeneration_targets as targets_repo

    ids = await _seed()
    try:
        service = svc.RegenerationCampaignService()
        campaign = await service.create_campaign(_spec(ids))
        await service.launch_canary(campaign.id)
        await _finish_canary(campaign.id)
        await service.approve_canary(campaign.id, actor="pytest")
        (target,) = await _targets(campaign.id)
        async with SessionLocal() as session:
            await targets_repo.claim_target_publication(
                session, target_id=target.id, claim_token=uuid.uuid4(),
                lease_seconds=300)
            await session.commit()

        with pytest.raises(svc.TerminalCampaignWithLiveTargets):
            await service.set_campaign_status(campaign.id, "cancelled")
        assert (await _campaign(campaign.id)).status != "cancelled"
    finally:
        await _purge(ids)
