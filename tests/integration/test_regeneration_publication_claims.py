"""Real-Postgres proof for the publication claim protocol and version allocator.

Everything here needs a real database because the thing under test IS database
behaviour: `FOR UPDATE SKIP LOCKED`, `FOR KEY SHARE` lock ordering against
`trg_regeneration_targets_publication_gate`, `pg_advisory_xact_lock`, and the
partial unique index `uq_regeneration_targets_publication_version`.

Two properties dominate the file.

**No lock-order inversion.** Task 7's campaign actions lock parent (campaign)
then child (target). The publication gate trigger takes `FOR KEY SHARE` on the
campaign from *inside* a target UPDATE, so a publisher that reaches the target
first inverts that order and can deadlock a concurrent cancel or rollup. The
claim protocol therefore establishes the campaign lock FIRST, and never waits
for it — a campaign someone else holds `FOR UPDATE` is skipped, not queued
behind.

**A version is consumed forever.** The allocator serialises per
`(toc_entry_id, output_language)` and the unique index is the final fence, so
two racing reservations for one lineage produce ONE number, and a lineage that
already reserved a number gets the same number back on every retry.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.services.flows import flow_for
from app.services.regeneration_planner import build_phase_plan

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)

_SUBJECT = "math-algebra"
_CANONICAL = ("extract", *flow_for(_SUBJECT))
_MARKER = "pytest-regen-publication-claims"
_PHASE_PLAN = build_phase_plan(
    subject=_SUBJECT, selected_phases=["flashcards"]).to_json()


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _seed(
    *,
    lessons: int = 1,
    approved: bool = True,
    campaign_status: str = "approved",
    target_status: str = "publication_pending",
    languages: tuple[str, ...] = ("uz",),
) -> dict:
    """One book, `lessons` TOC rows, a source job + complete phase snapshot per
    (lesson, language), one campaign and one target per pair.

    Seeded directly rather than through the campaign service: these tests are
    about the claim SQL, and driving a whole canary/approval cycle for each one
    would make them slow without exercising anything extra.
    """
    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.homework_job import HomeworkJob
    from app.models.phase_output import PhaseOutput
    from app.models.regeneration_campaign import RegenerationCampaign
    from app.models.regeneration_target import RegenerationTarget
    from app.models.toc_entry import TOCEntry

    published_states = ("publication_pending", "publishing", "published",
                        "publication_failed")
    async with SessionLocal() as session:
        book = Book(
            subject=_SUBJECT, original_filename="regen_publish.pdf",
            content_sha256=uuid.uuid4().hex * 2, file_size_bytes=1,
            status="toc_ready", grade="5",
        )
        session.add(book)
        await session.flush()
        campaign = RegenerationCampaign(
            status=campaign_status,
            selection_spec={"toc_entry_ids": [], "output_languages": list(languages)},
            requested_phases=["flashcards"], excluded_phases=[],
            launch_contract={}, canary_size=1, app_git_revision=_MARKER,
            approved_at=_now() if approved else None,
        )
        session.add(campaign)
        await session.flush()

        toc_ids, target_ids, job_ids = [], [], []
        for index in range(lessons):
            toc = TOCEntry(
                book_id=book.id, section_title=f"Lesson {index}",
                section_number=f"{index + 1}", order_index=index,
            )
            session.add(toc)
            await session.flush()
            toc_ids.append(toc.id)
            for language in languages:
                source = HomeworkJob(
                    book_id=book.id, toc_entry_id=toc.id, subject=_SUBJECT,
                    status="done", provider="gemini", model="gemini-3.6-flash",
                    transport="api", output_language=language,
                )
                session.add(source)
                await session.flush()
                target = RegenerationTarget(
                    campaign_id=campaign.id, toc_entry_id=toc.id,
                    output_language=language, phase_plan=_PHASE_PLAN,
                    source_job_id=source.id,
                    status=target_status,
                    publication_released_at=(
                        _now() if target_status in published_states else None),
                )
                session.add(target)
                await session.flush()
                revision = HomeworkJob(
                    book_id=book.id, toc_entry_id=toc.id, subject=_SUBJECT,
                    status="done", provider="gemini", model="gemini-3.6-flash",
                    transport="api", output_language=language,
                    revision_of_job_id=source.id, regeneration_target_id=target.id,
                    session_limit_strategy="pause",
                )
                session.add(revision)
                await session.flush()
                for order, name in enumerate(_CANONICAL):
                    session.add(PhaseOutput(
                        job_id=revision.id, phase_name=name, phase_order=order,
                        prompt_hash=f"builtin:{name}:v9", provider="gemini",
                        model_name="gemini-3.6-flash",
                        output_md=f"# {name}\nbody", status="done",
                    ))
                target_ids.append(target.id)
                job_ids.append(revision.id)
            await session.flush()
        await session.commit()
        return {
            "book_id": book.id, "campaign_id": campaign.id,
            "toc_ids": toc_ids, "target_ids": target_ids, "job_ids": job_ids,
        }


async def _purge(ids: dict) -> None:
    from sqlalchemy import delete, text

    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.homework_job import HomeworkJob
    from app.models.regeneration_campaign import RegenerationCampaign
    from app.models.toc_entry import TOCEntry

    async with SessionLocal() as session:
        await session.execute(
            text("DELETE FROM phase_outputs WHERE job_id IN "
                 "(SELECT id FROM homework_jobs WHERE book_id = :b)"),
            {"b": ids["book_id"]})
        await session.execute(
            text("DELETE FROM agent_usages WHERE homework_job_id IN "
                 "(SELECT id FROM homework_jobs WHERE book_id = :b)"),
            {"b": ids["book_id"]})
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


async def _target(target_id):
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models.regeneration_target import RegenerationTarget

    async with SessionLocal() as session:
        return await session.scalar(
            select(RegenerationTarget)
            .where(RegenerationTarget.id == target_id)
            .execution_options(populate_existing=True))


# ═════════════════════════ claim selection ═══════════════════════════════


async def test_claims_an_approved_publication_pending_target():
    from app.db import SessionLocal
    from app.repositories import regeneration_targets as targets_repo

    ids = await _seed()
    try:
        async with SessionLocal() as session:
            claim = await targets_repo.claim_next_publication(
                session, now=_now(), lease_seconds=300)
            await session.commit()
        assert claim is not None
        assert claim.target_id == ids["target_ids"][0]
        assert claim.campaign_id == ids["campaign_id"]
        assert claim.toc_entry_id == ids["toc_ids"][0]
        assert claim.output_language == "uz"
        assert claim.publication_version is None
        assert claim.publication_attempts == 1

        row = await _target(claim.target_id)
        assert row.status == "publishing"
        assert row.publication_claim_token == claim.claim_token
        assert row.publication_claimed_at is not None
    finally:
        await _purge(ids)


async def test_does_not_claim_a_target_whose_campaign_is_not_approved():
    """The gate trigger would RAISE on the transition into `publishing`; the
    claim must never select such a row in the first place."""
    from app.db import SessionLocal
    from app.repositories import regeneration_targets as targets_repo

    ids = await _seed(approved=False, campaign_status="bulk_running",
                      target_status="generating")
    try:
        async with SessionLocal() as session:
            assert await targets_repo.claim_next_publication(
                session, now=_now(), lease_seconds=300) is None
    finally:
        await _purge(ids)


async def test_claims_a_retry_due_publication_failed_target():
    from app.db import SessionLocal
    from app.models.regeneration_target import RegenerationTarget
    from app.repositories import regeneration_targets as targets_repo
    from sqlalchemy import update

    ids = await _seed(target_status="publication_failed")
    try:
        async with SessionLocal() as session:
            await session.execute(
                update(RegenerationTarget)
                .where(RegenerationTarget.id == ids["target_ids"][0])
                .values(publication_next_attempt_at=_now() - timedelta(seconds=5),
                        publication_last_error="notion 500",
                        publication_attempts=2))
            await session.commit()
        async with SessionLocal() as session:
            claim = await targets_repo.claim_next_publication(
                session, now=_now(), lease_seconds=300)
            await session.commit()
        assert claim is not None
        # a retry-due failure CONTINUES the cycle: attempts increment
        assert claim.publication_attempts == 3
    finally:
        await _purge(ids)


async def test_does_not_claim_a_publication_failed_target_whose_backoff_is_future():
    from app.db import SessionLocal
    from app.models.regeneration_target import RegenerationTarget
    from app.repositories import regeneration_targets as targets_repo
    from sqlalchemy import update

    ids = await _seed(target_status="publication_failed")
    try:
        async with SessionLocal() as session:
            await session.execute(
                update(RegenerationTarget)
                .where(RegenerationTarget.id == ids["target_ids"][0])
                .values(publication_next_attempt_at=_now() + timedelta(hours=1),
                        publication_last_error="notion 500"))
            await session.commit()
        async with SessionLocal() as session:
            assert await targets_repo.claim_next_publication(
                session, now=_now(), lease_seconds=300) is None
    finally:
        await _purge(ids)


async def test_never_auto_claims_an_exhausted_publication_failed_target():
    """`publication_next_attempt_at IS NULL` on a FAILED row means the automatic
    budget is spent (or it is a collision): operator-only. A NULL must not read
    as "due now" — that would loop a permanently failing delivery forever."""
    from app.db import SessionLocal
    from app.models.regeneration_target import RegenerationTarget
    from app.repositories import regeneration_targets as targets_repo
    from sqlalchemy import update

    ids = await _seed(target_status="publication_failed")
    try:
        async with SessionLocal() as session:
            await session.execute(
                update(RegenerationTarget)
                .where(RegenerationTarget.id == ids["target_ids"][0])
                .values(publication_next_attempt_at=None,
                        publication_last_error="collision",
                        publication_attempts=9))
            await session.commit()
        async with SessionLocal() as session:
            assert await targets_repo.claim_next_publication(
                session, now=_now(), lease_seconds=300) is None
    finally:
        await _purge(ids)


async def test_an_operator_retry_restores_the_full_automatic_attempt_budget():
    """Task 7's `retry_publication` deliberately PRESERVES cumulative attempts.
    The claim therefore restarts the counter for a `publication_pending` row
    carrying no outstanding failure — otherwise an operator retry after
    exhaustion buys zero real attempts."""
    from app.db import SessionLocal
    from app.repositories import regeneration_targets as targets_repo
    from app.services.regeneration_campaign import RegenerationCampaignService
    from app.models.regeneration_target import RegenerationTarget
    from sqlalchemy import update

    ids = await _seed(target_status="publication_failed")
    try:
        async with SessionLocal() as session:
            await session.execute(
                update(RegenerationTarget)
                .where(RegenerationTarget.id == ids["target_ids"][0])
                .values(publication_attempts=5,
                        publication_last_error="notion 500",
                        publication_next_attempt_at=None))
            await session.commit()

        await RegenerationCampaignService().retry_publication(ids["target_ids"][0])
        row = await _target(ids["target_ids"][0])
        assert row.status == "publication_pending"
        assert row.publication_attempts == 5, "Task 7 preserves the cumulative count"

        async with SessionLocal() as session:
            claim = await targets_repo.claim_next_publication(
                session, now=_now(), lease_seconds=300)
            await session.commit()
        assert claim is not None
        assert claim.publication_attempts == 1, (
            "the first attempt after an explicit retry must be attempt 1 of the "
            "configured budget, not attempt 6 of an exhausted one"
        )
    finally:
        await _purge(ids)


async def test_a_live_lease_is_not_stolen_but_an_expired_one_is_reclaimed():
    from app.db import SessionLocal
    from app.repositories import regeneration_targets as targets_repo

    ids = await _seed()
    try:
        async with SessionLocal() as session:
            first = await targets_repo.claim_next_publication(
                session, now=_now(), lease_seconds=300)
            await session.commit()
        assert first is not None

        async with SessionLocal() as session:
            assert await targets_repo.claim_next_publication(
                session, now=_now(), lease_seconds=300) is None

        # the same sweep, run after the lease has elapsed, takes it over
        async with SessionLocal() as session:
            second = await targets_repo.claim_next_publication(
                session, now=_now(), lease_seconds=0)
            await session.commit()
        assert second is not None
        assert second.target_id == first.target_id
        assert second.claim_token != first.claim_token
        assert second.publication_attempts == 2
    finally:
        await _purge(ids)


async def test_two_publishers_racing_one_target_produce_exactly_one_claim():
    from app.db import SessionLocal
    from app.repositories import regeneration_targets as targets_repo

    ids = await _seed()
    try:
        async def _claim():
            async with SessionLocal() as session:
                claim = await targets_repo.claim_next_publication(
                    session, now=_now(), lease_seconds=300)
                await session.commit()
                return claim

        results = await asyncio.gather(_claim(), _claim(), return_exceptions=True)
        assert not [r for r in results if isinstance(r, BaseException)], results
        winners = [r for r in results if r is not None]
        assert len(winners) == 1
        row = await _target(ids["target_ids"][0])
        assert row.publication_attempts == 1, "the loser must not burn an attempt"
    finally:
        await _purge(ids)


async def test_two_publishers_over_two_targets_each_claim_one():
    """`SKIP LOCKED` is what makes a second publisher useful rather than a
    queue behind the first."""
    from app.db import SessionLocal
    from app.repositories import regeneration_targets as targets_repo

    ids = await _seed(lessons=2)
    try:
        started = asyncio.Event()

        async def _slow():
            async with SessionLocal() as session:
                claim = await targets_repo.claim_next_publication(
                    session, now=_now(), lease_seconds=300)
                started.set()
                await asyncio.sleep(0.3)
                await session.commit()
                return claim

        async def _fast():
            await started.wait()
            async with SessionLocal() as session:
                claim = await targets_repo.claim_next_publication(
                    session, now=_now(), lease_seconds=300)
                await session.commit()
                return claim

        slow, fast = await asyncio.gather(_slow(), _fast())
        assert slow is not None and fast is not None
        assert slow.target_id != fast.target_id
    finally:
        await _purge(ids)


# ═════════════════════════ version allocation ════════════════════════════


async def test_first_reserved_version_is_two_and_is_reused_on_retry():
    from app.db import SessionLocal
    from app.repositories import regeneration_targets as targets_repo

    ids = await _seed()
    try:
        async with SessionLocal() as session:
            claim = await targets_repo.claim_next_publication(
                session, now=_now(), lease_seconds=300)
            await session.commit()

        async with SessionLocal() as session:
            version = await targets_repo.reserve_publication_version(
                session, target_id=claim.target_id, claim_token=claim.claim_token)
            await session.commit()
        assert version == 2, "logical V1 is the existing page and has no row"

        async with SessionLocal() as session:
            again = await targets_repo.reserve_publication_version(
                session, target_id=claim.target_id, claim_token=claim.claim_token)
            await session.commit()
        assert again == 2, "every retry uses the SAME reserved version"
        assert (await _target(claim.target_id)).publication_version == 2
    finally:
        await _purge(ids)


async def test_the_next_campaign_for_one_lineage_gets_the_next_version():
    """A consumed number is never reused, even by a target that was abandoned
    without ever reaching Notion."""
    from app.db import SessionLocal
    from app.models.regeneration_target import RegenerationTarget
    from app.repositories import regeneration_targets as targets_repo
    from sqlalchemy import update

    ids = await _seed()
    try:
        async with SessionLocal() as session:
            claim = await targets_repo.claim_next_publication(
                session, now=_now(), lease_seconds=300)
            await session.commit()
        async with SessionLocal() as session:
            assert await targets_repo.reserve_publication_version(
                session, target_id=claim.target_id,
                claim_token=claim.claim_token) == 2
            await session.commit()

        # that target is abandoned; a second campaign takes the lineage
        async with SessionLocal() as session:
            await session.execute(
                update(RegenerationTarget)
                .where(RegenerationTarget.id == claim.target_id)
                .values(status="abandoned", terminal_at=_now(),
                        terminal_reason="operator", publication_claim_token=None))
            await session.commit()

        second = await _seed_second_target(ids)
        async with SessionLocal() as session:
            claim2 = await targets_repo.claim_next_publication(
                session, now=_now(), lease_seconds=300)
            await session.commit()
        assert claim2 is not None and claim2.target_id == second
        async with SessionLocal() as session:
            assert await targets_repo.reserve_publication_version(
                session, target_id=second, claim_token=claim2.claim_token) == 3
            await session.commit()
    finally:
        await _purge(ids)


async def _seed_second_target(ids, *, status: str = "publication_pending"):
    """A LATER campaign's target on the same lineage as `ids['target_ids'][0]`.

    Its own campaign row, because `uq_regeneration_targets_campaign_toc_language`
    allows one (campaign, lesson, language) triple only — re-running a lesson is
    a new campaign, never a second row in the old one.
    """
    from app.db import SessionLocal
    from app.models.regeneration_campaign import RegenerationCampaign
    from app.models.regeneration_target import RegenerationTarget

    async with SessionLocal() as session:
        campaign = RegenerationCampaign(
            status="approved", selection_spec={}, requested_phases=["flashcards"],
            excluded_phases=[], launch_contract={}, canary_size=1,
            app_git_revision=_MARKER, approved_at=_now(),
        )
        session.add(campaign)
        await session.flush()
        target = RegenerationTarget(
            campaign_id=campaign.id, toc_entry_id=ids["toc_ids"][0],
            output_language="uz", phase_plan=_PHASE_PLAN,
            status=status, publication_released_at=_now(),
        )
        session.add(target)
        await session.flush()
        await session.commit()
        return target.id


async def test_uz_and_ru_v2_are_independent_publications():
    from app.db import SessionLocal
    from app.repositories import regeneration_targets as targets_repo

    ids = await _seed(languages=("uz", "ru"))
    try:
        versions = {}
        for _ in range(2):
            async with SessionLocal() as session:
                claim = await targets_repo.claim_next_publication(
                    session, now=_now(), lease_seconds=300)
                await session.commit()
            async with SessionLocal() as session:
                versions[claim.output_language] = (
                    await targets_repo.reserve_publication_version(
                        session, target_id=claim.target_id,
                        claim_token=claim.claim_token))
                await session.commit()
        assert versions == {"uz": 2, "ru": 2}
    finally:
        await _purge(ids)


async def test_a_stale_claim_token_cannot_reserve_a_version():
    from app.db import SessionLocal
    from app.repositories import regeneration_targets as targets_repo

    ids = await _seed()
    try:
        async with SessionLocal() as session:
            claim = await targets_repo.claim_next_publication(
                session, now=_now(), lease_seconds=300)
            await session.commit()
        async with SessionLocal() as session:
            with pytest.raises(targets_repo.StalePublicationClaim):
                await targets_repo.reserve_publication_version(
                    session, target_id=claim.target_id, claim_token=uuid.uuid4())
        assert (await _target(claim.target_id)).publication_version is None
    finally:
        await _purge(ids)


async def test_two_concurrent_reservations_on_one_lineage_agree():
    """The advisory lock serialises them; the unique index is the final fence.
    Both callers must come back with the SAME number and no IntegrityError."""
    from app.db import SessionLocal
    from app.repositories import regeneration_targets as targets_repo

    ids = await _seed()
    try:
        async with SessionLocal() as session:
            claim = await targets_repo.claim_next_publication(
                session, now=_now(), lease_seconds=300)
            await session.commit()

        async def _reserve():
            async with SessionLocal() as session:
                version = await targets_repo.reserve_publication_version(
                    session, target_id=claim.target_id,
                    claim_token=claim.claim_token)
                await session.commit()
                return version

        results = await asyncio.gather(_reserve(), _reserve(),
                                       return_exceptions=True)
        assert not [r for r in results if isinstance(r, BaseException)], results
        assert results == [2, 2]
    finally:
        await _purge(ids)


async def test_the_unique_index_refuses_a_duplicate_version_for_one_lineage():
    from sqlalchemy.exc import IntegrityError

    from app.db import SessionLocal
    from app.models.regeneration_target import RegenerationTarget

    ids = await _seed()
    try:
        async with SessionLocal() as session:
            first = await session.get(RegenerationTarget, ids["target_ids"][0])
            first.publication_version = 2
            first.status = "abandoned"
            first.terminal_at = _now()
            first.terminal_reason = "operator"
            await session.commit()
        second = await _seed_second_target(ids)
        with pytest.raises(IntegrityError):
            async with SessionLocal() as session:
                row = await session.get(RegenerationTarget, second)
                row.publication_version = 2
                await session.commit()
    finally:
        await _purge(ids)


# ═══════════════ stale creation-failure text (carried binding) ═══════════


async def test_a_generation_retry_drops_the_stale_creation_failure_text():
    """`terminal_reason` is the target's ONLY free-text column, so a wave that
    could not create a revision job writes its explanation there while the row
    is NOT terminal (`generation_failed`). Re-driving that row to `generating`
    must drop the sentence, or a retry that then succeeds carries "revision job
    could not be created: ..." all the way to `published`.

    Proven at the repository, because that is where both call sites — the
    operator retry and the job reconciler — go through.
    """
    from app.db import SessionLocal
    from app.models.regeneration_target import RegenerationTarget
    from app.repositories import regeneration_targets as targets_repo
    from sqlalchemy import update

    ids = await _seed(approved=False, campaign_status="canary_running",
                      target_status="generating")
    try:
        async with SessionLocal() as session:
            await session.execute(
                update(RegenerationTarget)
                .where(RegenerationTarget.id == ids["target_ids"][0])
                .values(status="generation_failed",
                        terminal_reason="revision job could not be created: boom"))
            await session.commit()

        async with SessionLocal() as session:
            assert await targets_repo.set_target_status(
                session, target_id=ids["target_ids"][0], new_status="generating",
                expected_statuses=["generation_failed"])
            await session.commit()

        row = await _target(ids["target_ids"][0])
        assert row.status == "generating"
        assert row.terminal_reason is None, (
            "a re-driven target must not report a creation failure it recovered from"
        )
    finally:
        await _purge(ids)


async def test_an_explicit_terminal_reason_still_wins_over_the_redrive_clear():
    """The clear is "drop stale text", not "forbid text": a caller replacing the
    explanation must still be able to."""
    from app.db import SessionLocal
    from app.models.regeneration_target import RegenerationTarget
    from app.repositories import regeneration_targets as targets_repo
    from sqlalchemy import update

    ids = await _seed(approved=False, campaign_status="canary_running",
                      target_status="generating")
    try:
        async with SessionLocal() as session:
            await session.execute(
                update(RegenerationTarget)
                .where(RegenerationTarget.id == ids["target_ids"][0])
                .values(status="generation_failed", terminal_reason="old"))
            await session.commit()
        async with SessionLocal() as session:
            assert await targets_repo.set_target_status(
                session, target_id=ids["target_ids"][0], new_status="generating",
                expected_statuses=["generation_failed"], terminal_reason="new")
            await session.commit()
        assert (await _target(ids["target_ids"][0])).terminal_reason == "new"
    finally:
        await _purge(ids)


async def test_clear_terminal_reason_is_available_explicitly():
    from app.db import SessionLocal
    from app.models.regeneration_target import RegenerationTarget
    from app.repositories import regeneration_targets as targets_repo
    from sqlalchemy import update

    ids = await _seed()
    try:
        async with SessionLocal() as session:
            await session.execute(
                update(RegenerationTarget)
                .where(RegenerationTarget.id == ids["target_ids"][0])
                .values(terminal_reason="stale"))
            await session.commit()
        async with SessionLocal() as session:
            assert await targets_repo.set_target_status(
                session, target_id=ids["target_ids"][0],
                new_status="publication_pending",
                expected_statuses=["publication_pending"],
                clear_terminal_reason=True)
            await session.commit()
        assert (await _target(ids["target_ids"][0])).terminal_reason is None
    finally:
        await _purge(ids)


# ═══════════ lock order vs the campaign actions (carried binding) ════════


async def test_claiming_never_deadlocks_a_campaign_action_holding_the_campaign():
    """The precise inversion, forced.

    A campaign action holds `regeneration_campaigns` FOR UPDATE and then reaches
    for its target FOR UPDATE. A publisher that reached the TARGET first would
    take a row-exclusive lock on it and only then have
    `trg_regeneration_targets_publication_gate` reach BACK for the campaign's
    FOR KEY SHARE — closing the cycle, and PostgreSQL kills one of them.

    With the parent lock established first (and skipped rather than waited on),
    the publisher simply finds nothing claimable this tick.
    """
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models.regeneration_campaign import RegenerationCampaign
    from app.models.regeneration_target import RegenerationTarget
    from app.repositories import regeneration_targets as targets_repo

    ids = await _seed()
    try:
        holder_has_campaign = asyncio.Event()
        publisher_done = asyncio.Event()
        outcome: dict = {}

        async def _campaign_action():
            """cancel/roll_up's shape: campaign FOR UPDATE, then the target."""
            async with SessionLocal() as session:
                await session.scalar(
                    select(RegenerationCampaign)
                    .where(RegenerationCampaign.id == ids["campaign_id"])
                    .with_for_update())
                holder_has_campaign.set()
                await asyncio.sleep(0.4)  # let the publisher get as far as it can
                await session.scalar(
                    select(RegenerationTarget)
                    .where(RegenerationTarget.id == ids["target_ids"][0])
                    .with_for_update())
                outcome["campaign_action"] = "completed"
                await session.commit()

        async def _publish():
            await holder_has_campaign.wait()
            async with SessionLocal() as session:
                outcome["claim"] = await targets_repo.claim_next_publication(
                    session, now=_now(), lease_seconds=300)
                await session.commit()
            publisher_done.set()

        results = await asyncio.gather(
            _campaign_action(), _publish(), return_exceptions=True)
        errors = [r for r in results if isinstance(r, BaseException)]
        assert not errors, f"neither side may fail: {errors}"
        assert outcome["campaign_action"] == "completed"
        assert outcome["claim"] is None, (
            "a campaign held FOR UPDATE is skipped, never queued behind"
        )
        # and the publisher did not wait for it either
        assert publisher_done.is_set()
    finally:
        await _purge(ids)


async def test_a_claim_sweep_racing_cancel_and_rollup_never_deadlocks():
    """The same property through the real service entry points, repeatedly."""
    from app.db import SessionLocal
    from app.repositories import regeneration_targets as targets_repo
    from app.services.regeneration_campaign import RegenerationCampaignService

    ids = await _seed(lessons=4)
    try:
        service = RegenerationCampaignService()

        async def _claim():
            async with SessionLocal() as session:
                claim = await targets_repo.claim_next_publication(
                    session, now=_now(), lease_seconds=300)
                await session.commit()
                return claim

        results = await asyncio.gather(
            _claim(), service.roll_up(ids["campaign_id"]), _claim(),
            service.roll_up(ids["campaign_id"]),
            return_exceptions=True)
        assert not [r for r in results if isinstance(r, BaseException)], results

        results = await asyncio.gather(
            _claim(),
            service.cancel(ids["campaign_id"], actor="pytest", reason="stop"),
            _claim(),
            return_exceptions=True)
        errors = [r for r in results if isinstance(r, BaseException)]
        assert not errors, f"claim vs cancel must not deadlock: {errors}"

        campaign = await _campaign(ids["campaign_id"])
        assert campaign.cancel_requested_at is not None
        # Order-independent invariant: whether a claim won or was skipped is a
        # race, but a TERMINAL campaign may never hide a non-terminal target.
        live = [t for t in await _all_targets(ids["campaign_id"])
                if t.terminal_at is None]
        terminal_campaign = campaign.status in (
            "completed", "completed_with_abandonments", "rejected", "cancelled")
        assert not (terminal_campaign and live), (
            f"campaign {campaign.status!r} over {len(live)} live target(s)"
        )
    finally:
        await _purge(ids)


async def _campaign(campaign_id):
    from app.db import SessionLocal
    from app.repositories import regeneration_campaigns as campaigns_repo

    async with SessionLocal() as session:
        return await campaigns_repo.get_campaign(session, campaign_id)


async def _all_targets(campaign_id):
    from app.db import SessionLocal
    from app.repositories import regeneration_targets as targets_repo

    async with SessionLocal() as session:
        return await targets_repo.list_for_campaign(session, campaign_id)


# ═══════ cancellation convergence, from the PUBLISHER's side ═════════════
#
# Task 7 proves this trio by hand-writing the fenced target updates. Here the
# REAL publisher makes them, against the real armed trigger, so the claim is
# genuinely in flight when the cancel lands rather than simulated.


def _gated_writer(monkeypatch, notion, *, fail: bool):
    """Replace the versioned writer with one that parks in its worker thread
    until the test releases it — the only way to land a cancel at the exact
    moment a Notion request has an unknown outcome."""
    import threading

    import app.services.regeneration_publisher as pub

    reached, release = threading.Event(), threading.Event()
    real = pub.write_or_adopt_versioned_homework

    def _writer(**kwargs):
        reached.set()
        release.wait(30)
        if fail:
            raise RuntimeError("notion 500 while cancelling")
        return real(**kwargs)

    monkeypatch.setattr(pub, "write_or_adopt_versioned_homework", _writer)
    monkeypatch.setattr(
        pub.notion_archive, "_resolve_subject_page_id",
        lambda *a, **kw: "subject-page-pytest")
    notion.titles["subject-page-pytest"] = "Subject"
    notion.blocks["subject-page-pytest"] = []
    return reached, release


def _publisher(notion):
    import app.services.regeneration_publisher as pub
    from app.services.regeneration_campaign import RegenerationCampaignService

    return pub.RegenerationPublisher(
        campaign_service=RegenerationCampaignService(),
        client_factory=lambda: notion,
        interval_seconds=1, lease_seconds=300, max_attempts=3,
        backoff_base_seconds=60, backoff_max_seconds=3600,
    )


async def test_cancel_lets_an_in_flight_publication_finish(monkeypatch):
    """1/3. The gate RAISES once the campaign stops being approved, so a naive
    `set_campaign_status('cancelled')` would turn an in-flight delivery into a
    `check_violation`. The campaign parks in `attention_required` and the
    publisher's fenced `published` write lands — the page exists, and reporting
    it abandoned would be a lie."""
    from tests.services.test_notion_versioned_homework import FakeNotion

    from app.services.regeneration_campaign import RegenerationCampaignService

    ids = await _seed()
    notion = FakeNotion()
    reached, release = _gated_writer(monkeypatch, notion, fail=False)
    try:
        task = asyncio.create_task(_publisher(notion).run_once())
        assert await asyncio.to_thread(reached.wait, 30), "delivery never started"

        cancelled = await RegenerationCampaignService().cancel(
            ids["campaign_id"], actor="pytest", reason="stop")
        assert cancelled.status == "attention_required"
        assert cancelled.status != "cancelled"

        release.set()
        assert await asyncio.wait_for(task, timeout=30) is True

        target = await _target(ids["target_ids"][0])
        assert target.status == "published"
        assert target.notion_page_id is not None
        assert target.publication_version == 2
        assert target.terminal_at is not None
        assert (await _campaign(ids["campaign_id"])).status == "completed"
    finally:
        release.set()
        await _purge(ids)


async def test_cancel_with_a_failed_publication_rolls_up_to_cancelled(monkeypatch):
    """2/3. Same shape, delivery fails: the publisher resolves its own claim to
    terminal `abandoned` — keeping the reserved version, which is consumed
    forever — and only THEN may the campaign become terminal."""
    from tests.services.test_notion_versioned_homework import FakeNotion

    from app.services.regeneration_campaign import RegenerationCampaignService

    ids = await _seed()
    notion = FakeNotion()
    reached, release = _gated_writer(monkeypatch, notion, fail=True)
    try:
        task = asyncio.create_task(_publisher(notion).run_once())
        assert await asyncio.to_thread(reached.wait, 30), "delivery never started"

        cancelled = await RegenerationCampaignService().cancel(
            ids["campaign_id"], actor="pytest", reason="stop")
        assert cancelled.status == "attention_required"

        release.set()
        assert await asyncio.wait_for(task, timeout=30) is True

        target = await _target(ids["target_ids"][0])
        assert target.status == "abandoned"
        assert target.terminal_at is not None
        assert target.publication_version == 2, "the version stays consumed"
        assert "stop" in (target.terminal_reason or "")

        campaign = await _campaign(ids["campaign_id"])
        assert campaign.status == "cancelled"
        assert campaign.completed_at is not None
    finally:
        release.set()
        await _purge(ids)


async def test_a_direct_terminal_status_write_is_refused_by_the_service():
    """3/3. The guard lives at the service layer because the repository's
    compare-and-set is a deliberately dumb primitive that knows nothing about
    targets."""
    from app.services import regeneration_campaign as svc

    ids = await _seed()
    try:
        service = svc.RegenerationCampaignService()
        with pytest.raises(svc.TerminalCampaignWithLiveTargets):
            await service.set_campaign_status(ids["campaign_id"], "cancelled")
        assert (await _campaign(ids["campaign_id"])).status == "approved"
        assert (await _target(ids["target_ids"][0])).terminal_at is None
    finally:
        await _purge(ids)
