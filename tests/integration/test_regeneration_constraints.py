"""Real-DB: the regeneration invariants that only PostgreSQL can enforce.

Everything here goes through the ORM/repositories exactly as the later lanes
will, and each test proves a rule that a service layer could otherwise forget:
one active lineage per (lesson, language), independent per-language versions,
no version reuse, no revision inside a Fleet batch, no orphaned revision, and
no publication before the campaign is approved.
"""
from __future__ import annotations

import asyncio
import copy
import os
import uuid

import pytest
from sqlalchemy import delete, text
from sqlalchemy.exc import IntegrityError

from app.services.regeneration_planner import RegenerationPhasePlan, build_phase_plan

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)

# Stamped on every campaign these tests create so the cleanup can find one that
# never got a target (the loser of the lineage race).
_MARKER = "pytest-regen"

# `phase_plan` holds the planner's serialized OBJECT, never a bare phase-name
# list. Built once, for the subject `_seed` actually seeds, so these rows carry
# a payload the later lanes could really read back.
_PLAN = build_phase_plan(subject="math-algebra", selected_phases=["flashcards"])
_PHASE_PLAN = _PLAN.to_json()


async def _seed(session, *, languages=("uz",)):
    """A book, one TOC entry and one done source job per language."""
    from app.models.book import Book
    from app.models.homework_job import HomeworkJob
    from app.models.toc_entry import TOCEntry

    book = Book(
        subject="math-algebra",
        original_filename="regen_constraints.pdf",
        content_sha256=uuid.uuid4().hex * 2,
        file_size_bytes=1,
        status="toc_ready",
    )
    session.add(book)
    await session.flush()
    toc = TOCEntry(book_id=book.id, section_title="L1", order_index=0)
    session.add(toc)
    await session.flush()
    jobs = {}
    for lang in languages:
        job = HomeworkJob(
            book_id=book.id,
            toc_entry_id=toc.id,
            subject="math-algebra",
            status="done",
            provider="gemini",
            transport="api",
            output_language=lang,
        )
        session.add(job)
        await session.flush()
        jobs[lang] = job.id
    await session.commit()
    return book.id, toc.id, jobs


async def _purge(book_id):
    """Child-first: every regeneration FK is RESTRICT on purpose."""
    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.homework_job import HomeworkJob
    from app.models.regeneration_campaign import RegenerationCampaign
    from app.models.regeneration_target import RegenerationTarget
    from app.models.toc_entry import TOCEntry

    async with SessionLocal() as s:
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
        # Campaigns are matched by the test marker, not through their targets:
        # a campaign that LOST the lineage race has no target to be found by.
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


def _campaign(**overrides):
    from app.models.regeneration_campaign import RegenerationCampaign

    kwargs = dict(
        status="draft",
        selection_spec={"mode": "test"},
        requested_phases=["flashcards"],
        excluded_phases=[],
        launch_contract={"provider": "gemini"},
        app_git_revision=_MARKER,
    )
    kwargs.update(overrides)
    return RegenerationCampaign(**kwargs)


def _target(**overrides):
    from app.models.regeneration_target import RegenerationTarget

    # deepcopy: every target gets its own payload, so one test mutating a row
    # can never reach into another's.
    kwargs = dict(status="planned", phase_plan=copy.deepcopy(_PHASE_PLAN))
    kwargs.update(overrides)
    return RegenerationTarget(**kwargs)


async def test_two_campaigns_race_for_one_lesson_and_language_only_one_wins():
    """The partial unique index is the ONLY thing standing between two
    operators launching overlapping campaigns; race it for real."""
    from app.db import SessionLocal

    async with SessionLocal() as s:
        book_id, toc_id, jobs = await _seed(s)
    try:
        campaign_ids = []
        async with SessionLocal() as s:
            for _ in range(2):
                c = _campaign()
                s.add(c)
                await s.flush()
                campaign_ids.append(c.id)
            await s.commit()

        async def _insert(campaign_id):
            async with SessionLocal() as s:
                s.add(
                    _target(
                        campaign_id=campaign_id,
                        toc_entry_id=toc_id,
                        output_language="uz",
                        source_job_id=jobs["uz"],
                    )
                )
                await s.commit()

        results = await asyncio.gather(
            _insert(campaign_ids[0]), _insert(campaign_ids[1]),
            return_exceptions=True,
        )
        failures = [r for r in results if isinstance(r, Exception)]
        assert len(failures) == 1, f"expected exactly one loser, got {results}"
        assert isinstance(failures[0], IntegrityError)

        async with SessionLocal() as s:
            live = await s.scalar(
                text(
                    "SELECT count(*) FROM regeneration_targets "
                    "WHERE toc_entry_id=:t AND output_language='uz' AND terminal_at IS NULL"
                ),
                {"t": toc_id},
            )
        assert live == 1
    finally:
        await _purge(book_id)


async def test_terminal_target_frees_the_lineage_for_the_next_campaign():
    """A published (terminal) target must NOT block the next campaign — the
    index is partial on terminal_at IS NULL for exactly this reason."""
    from app.db import SessionLocal
    from datetime import datetime, timezone

    async with SessionLocal() as s:
        book_id, toc_id, jobs = await _seed(s)
    try:
        now = datetime.now(timezone.utc)
        async with SessionLocal() as s:
            c1 = _campaign(status="approved", approved_at=now)
            s.add(c1)
            await s.flush()
            s.add(
                _target(
                    campaign_id=c1.id,
                    toc_entry_id=toc_id,
                    output_language="uz",
                    source_job_id=jobs["uz"],
                    status="published",
                    publication_released_at=now,
                    publication_version=2,
                    notion_page_id="page-v2",
                    terminal_at=now,
                    terminal_reason="published",
                )
            )
            await s.commit()

        async with SessionLocal() as s:
            c2 = _campaign()
            s.add(c2)
            await s.flush()
            s.add(
                _target(
                    campaign_id=c2.id,
                    toc_entry_id=toc_id,
                    output_language="uz",
                    source_job_id=jobs["uz"],
                )
            )
            await s.commit()  # must not raise
    finally:
        await _purge(book_id)


async def test_uz_v2_and_ru_v2_are_independent_but_a_version_never_repeats():
    from app.db import SessionLocal
    from datetime import datetime, timezone

    async with SessionLocal() as s:
        book_id, toc_id, jobs = await _seed(s, languages=("uz", "ru"))
    try:
        now = datetime.now(timezone.utc)
        async with SessionLocal() as s:
            c = _campaign(status="approved", approved_at=now)
            s.add(c)
            await s.flush()
            for lang in ("uz", "ru"):
                s.add(
                    _target(
                        campaign_id=c.id,
                        toc_entry_id=toc_id,
                        output_language=lang,
                        source_job_id=jobs[lang],
                        status="published",
                        publication_released_at=now,
                        publication_version=2,
                        notion_page_id=f"page-{lang}-v2",
                        terminal_at=now,
                        terminal_reason="published",
                    )
                )
            await s.commit()  # UZ V2 and RU V2 coexist

        # A later campaign may not reuse UZ V2.
        async with SessionLocal() as s:
            c2 = _campaign(status="approved", approved_at=now)
            s.add(c2)
            await s.flush()
            s.add(
                _target(
                    campaign_id=c2.id,
                    toc_entry_id=toc_id,
                    output_language="uz",
                    source_job_id=jobs["uz"],
                    status="publication_pending",
                    publication_released_at=now,
                    publication_version=2,
                )
            )
            with pytest.raises(IntegrityError):
                await s.commit()

        # V3 in the same language is fine.
        async with SessionLocal() as s:
            c3 = _campaign(status="approved", approved_at=now)
            s.add(c3)
            await s.flush()
            s.add(
                _target(
                    campaign_id=c3.id,
                    toc_entry_id=toc_id,
                    output_language="uz",
                    source_job_id=jobs["uz"],
                    status="publication_pending",
                    publication_released_at=now,
                    publication_version=3,
                )
            )
            await s.commit()
    finally:
        await _purge(book_id)


async def test_publication_before_campaign_approval_is_refused():
    from app.db import SessionLocal
    from datetime import datetime, timezone

    async with SessionLocal() as s:
        book_id, toc_id, jobs = await _seed(s)
    try:
        now = datetime.now(timezone.utc)
        async with SessionLocal() as s:
            c = _campaign(status="canary_running")
            s.add(c)
            await s.flush()
            t = _target(
                campaign_id=c.id,
                toc_entry_id=toc_id,
                output_language="uz",
                source_job_id=jobs["uz"],
                status="awaiting_canary_approval",
            )
            s.add(t)
            await s.commit()
            campaign_id, target_id = c.id, t.id

        from app.repositories import regeneration_targets as targets_repo

        async with SessionLocal() as s:
            with pytest.raises(IntegrityError) as exc:
                await targets_repo.set_target_status(
                    s,
                    target_id=target_id,
                    new_status="publication_pending",
                    expected_statuses=("awaiting_canary_approval",),
                    publication_released_at=now,
                )
                await s.commit()
        # The refusal is the approval TRIGGER, not some other constraint.
        assert "not approved" in str(exc.value)

        from app.repositories import regeneration_campaigns as campaigns_repo

        async with SessionLocal() as s:
            approved = await campaigns_repo.set_campaign_status(
                s,
                campaign_id=campaign_id,
                new_status="approved",
                expected_statuses=("canary_running", "awaiting_canary_approval"),
                approved_at=now,
            )
            await s.commit()
        assert approved is True

        async with SessionLocal() as s:
            ok = await targets_repo.set_target_status(
                s,
                target_id=target_id,
                new_status="publication_pending",
                expected_statuses=("awaiting_canary_approval",),
                publication_released_at=now,
            )
            await s.commit()
        assert ok is True
    finally:
        await _purge(book_id)


async def test_revision_job_may_not_belong_to_a_fleet_batch():
    from app.db import SessionLocal
    from app.models.batch import Batch
    from app.models.homework_job import HomeworkJob

    async with SessionLocal() as s:
        book_id, toc_id, jobs = await _seed(s)
    try:
        async with SessionLocal() as s:
            c = _campaign()
            s.add(c)
            await s.flush()
            t = _target(
                campaign_id=c.id,
                toc_entry_id=toc_id,
                output_language="uz",
                source_job_id=jobs["uz"],
            )
            s.add(t)
            batch = Batch(
                book_id=book_id,
                subject="math-algebra",
                provider="gemini",
                transport="api",
                output_language="uz",
            )
            s.add(batch)
            await s.commit()
            target_id, batch_id = t.id, batch.id

        async with SessionLocal() as s:
            s.add(
                HomeworkJob(
                    book_id=book_id,
                    toc_entry_id=toc_id,
                    subject="math-algebra",
                    status="pending",
                    provider="gemini",
                    transport="api",
                    output_language="uz",
                    revision_of_job_id=jobs["uz"],
                    regeneration_target_id=target_id,
                    batch_id=batch_id,   # a revision is never a Fleet batch member
                    # A VALID concrete strategy on purpose: this row must be
                    # rejected for its batch membership, not incidentally by
                    # ck_homework_jobs_revision_session_limit_strategy.
                    session_limit_strategy="pause",
                )
            )
            with pytest.raises(IntegrityError) as exc:
                await s.commit()
        assert "ck_homework_jobs_revision_no_batch" in str(exc.value)

        # Half a revision is not a revision: both columns or neither.
        async with SessionLocal() as s:
            s.add(
                HomeworkJob(
                    book_id=book_id,
                    toc_entry_id=toc_id,
                    subject="math-algebra",
                    status="pending",
                    provider="gemini",
                    transport="api",
                    output_language="uz",
                    revision_of_job_id=jobs["uz"],
                    session_limit_strategy="pause",
                )
            )
            with pytest.raises(IntegrityError) as exc:
                await s.commit()
        assert "ck_homework_jobs_revision_pair" in str(exc.value)

        async with SessionLocal() as s:
            await s.execute(delete(Batch).where(Batch.id == batch_id))
            await s.commit()
    finally:
        await _purge(book_id)


async def test_one_revision_job_per_target_and_source_deletion_is_refused():
    from app.db import SessionLocal
    from app.models.homework_job import HomeworkJob
    from app.repositories import regeneration_targets as targets_repo

    async with SessionLocal() as s:
        book_id, toc_id, jobs = await _seed(s)
    try:
        async with SessionLocal() as s:
            c = _campaign()
            s.add(c)
            await s.flush()
            t = _target(
                campaign_id=c.id,
                toc_entry_id=toc_id,
                output_language="uz",
                source_job_id=jobs["uz"],
            )
            s.add(t)
            await s.flush()
            target_id = t.id
            revision = HomeworkJob(
                book_id=book_id,
                toc_entry_id=toc_id,
                subject="math-algebra",
                status="pending",
                provider="gemini",
                transport="api",
                output_language="uz",
                revision_of_job_id=jobs["uz"],
                regeneration_target_id=target_id,
                # Required on a revision: it has no batch row to resolve one
                # from, and 'inherit' is refused there.
                session_limit_strategy="pause",
            )
            s.add(revision)
            await s.commit()
            revision_id = revision.id

        # The one-to-one link is read back through the unique job column.
        async with SessionLocal() as s:
            got = await targets_repo.revision_job_for_target(s, target_id=target_id)
            assert got is not None and got.id == revision_id
            back = await targets_repo.get_target_by_revision_job(s, job_id=revision_id)
            assert back is not None and back.id == target_id

        # A second job may not claim the same target.
        async with SessionLocal() as s:
            s.add(
                HomeworkJob(
                    book_id=book_id,
                    toc_entry_id=toc_id,
                    subject="math-algebra",
                    status="pending",
                    provider="gemini",
                    transport="api",
                    output_language="uz",
                    revision_of_job_id=jobs["uz"],
                    regeneration_target_id=target_id,
                    # Valid on purpose: the refusal under test is the unique
                    # target link, not the new session-limit rule.
                    session_limit_strategy="pause",
                )
            )
            with pytest.raises(IntegrityError) as exc:
                await s.commit()
        assert "uq_homework_jobs_regeneration_target_id" in str(exc.value)

        # Deleting the source out from under a live revision must fail cleanly,
        # and it must be the REVISION CHILD that refuses. Asserting only
        # "some IntegrityError" is not enough: while the target also held a
        # RESTRICT on the source, this delete was rejected by
        # fk_regeneration_targets_source_job_id and the revision-child guard was
        # never exercised at all — the test passed for the wrong reason.
        async with SessionLocal() as s:
            with pytest.raises(IntegrityError) as exc:
                await s.execute(delete(HomeworkJob).where(HomeworkJob.id == jobs["uz"]))
                await s.commit()
            assert "fk_homework_jobs_revision_of_job_id" in str(exc.value)

        # ...and so must deleting the lesson the audit history hangs off.
        async with SessionLocal() as s:
            from app.models.toc_entry import TOCEntry

            with pytest.raises(IntegrityError):
                await s.execute(delete(TOCEntry).where(TOCEntry.id == toc_id))
                await s.commit()
    finally:
        await _purge(book_id)


async def test_child_first_purge_frees_the_source_and_keeps_the_report():
    """Spec §8.3: an explicitly ordered child-first purge must actually work.

    Delete the revision child first, and the source job becomes deletable. The
    target survives as campaign-reporting history with a NULL source link — its
    consumed publication version is NOT freed and its lineage is NOT erased.

    With RESTRICT on both foreign keys this sequence is impossible: the target
    goes on referencing the source forever, so the source can never be purged
    and `source_job_id` can never reach the null state the column allows.
    """
    from datetime import datetime, timezone

    from app.db import SessionLocal
    from app.models.homework_job import HomeworkJob
    from app.models.regeneration_target import RegenerationTarget

    async with SessionLocal() as s:
        book_id, toc_id, jobs = await _seed(s)
    try:
        now = datetime.now(timezone.utc)
        # A *published* target, so the purge is proven against real audit
        # history: a consumed version number and a live Notion page ID.
        async with SessionLocal() as s:
            c = _campaign(status="approved", approved_at=now)
            s.add(c)
            await s.flush()
            t = _target(
                campaign_id=c.id,
                toc_entry_id=toc_id,
                output_language="uz",
                source_job_id=jobs["uz"],
                status="published",
                publication_released_at=now,
                publication_version=2,
                notion_page_id="page-abc",
                terminal_at=now,
                terminal_reason="published",
            )
            s.add(t)
            await s.flush()
            target_id = t.id
            revision = HomeworkJob(
                book_id=book_id,
                toc_entry_id=toc_id,
                subject="math-algebra",
                status="done",
                provider="gemini",
                transport="api",
                output_language="uz",
                revision_of_job_id=jobs["uz"],
                regeneration_target_id=target_id,
                session_limit_strategy="pause",
            )
            s.add(revision)
            await s.commit()
            revision_id = revision.id

        # Step 1 — the source is protected while the revision child is alive.
        async with SessionLocal() as s:
            with pytest.raises(IntegrityError) as exc:
                await s.execute(delete(HomeworkJob).where(HomeworkJob.id == jobs["uz"]))
                await s.commit()
            assert "fk_homework_jobs_revision_of_job_id" in str(exc.value)

        # Step 2 — child first: the revision goes.
        async with SessionLocal() as s:
            await s.execute(delete(HomeworkJob).where(HomeworkJob.id == revision_id))
            await s.commit()

        # Step 3 — NOW the source deletes cleanly. No raw FK error.
        async with SessionLocal() as s:
            await s.execute(delete(HomeworkJob).where(HomeworkJob.id == jobs["uz"]))
            await s.commit()

        async with SessionLocal() as s:
            assert await s.get(HomeworkJob, jobs["uz"]) is None
            # Step 4 — the report survives, with the source link nulled and
            # every other audit field untouched.
            survivor = await s.get(RegenerationTarget, target_id)
            assert survivor is not None
            assert survivor.source_job_id is None
            assert survivor.publication_version == 2
            assert survivor.notion_page_id == "page-abc"
            assert survivor.status == "published"
            assert survivor.terminal_at is not None

        # Step 5 — nulling the source did NOT free the consumed version for
        # reuse; the lesson/language/version index still refuses a second V2.
        async with SessionLocal() as s:
            c2 = _campaign(status="approved", approved_at=now)
            s.add(c2)
            await s.flush()
            s.add(
                _target(
                    campaign_id=c2.id,
                    toc_entry_id=toc_id,
                    output_language="uz",
                    source_job_id=None,
                    status="published",
                    publication_released_at=now,
                    publication_version=2,
                    notion_page_id="page-def",
                    terminal_at=now,
                    terminal_reason="published",
                )
            )
            with pytest.raises(IntegrityError):
                await s.commit()
    finally:
        await _purge(book_id)


async def test_for_update_locks_and_fenced_status_updates():
    from app.db import SessionLocal
    from app.repositories import regeneration_campaigns as campaigns_repo
    from app.repositories import regeneration_targets as targets_repo

    async with SessionLocal() as s:
        book_id, toc_id, jobs = await _seed(s)
    try:
        async with SessionLocal() as s:
            campaign = await campaigns_repo.create_campaign(
                s,
                selection_spec={"mode": "test"},
                requested_phases=["flashcards"],
                excluded_phases=[],
                launch_contract={"provider": "gemini"},
                canary_size=1,
                app_git_revision=_MARKER,
            )
            target = await targets_repo.create_target(
                s,
                campaign_id=campaign.id,
                toc_entry_id=toc_id,
                output_language="uz",
                source_job_id=jobs["uz"],
                is_canary=True,
                phase_plan=copy.deepcopy(_PHASE_PLAN),
            )
            await s.commit()
            campaign_id, target_id = campaign.id, target.id

        async with SessionLocal() as s:
            locked = await campaigns_repo.get_campaign_for_update(s, campaign_id)
            assert locked is not None and locked.status == "draft"
            locked_t = await targets_repo.get_target_for_update(s, target_id)
            assert locked_t is not None and locked_t.status == "planned"
            assert locked_t.is_canary is True
            assert locked_t.publication_attempts == 0
            await s.rollback()

        # Fenced: a status write from the wrong expected state is a no-op.
        async with SessionLocal() as s:
            assert await campaigns_repo.set_campaign_status(
                s,
                campaign_id=campaign_id,
                new_status="approved",
                expected_statuses=("awaiting_canary_approval",),
            ) is False
            await s.commit()

        async with SessionLocal() as s:
            assert await targets_repo.set_target_status(
                s,
                target_id=target_id,
                new_status="generating",
                expected_statuses=("published",),
            ) is False
            assert await targets_repo.set_target_status(
                s,
                target_id=target_id,
                new_status="generating",
                expected_statuses=("planned",),
            ) is True
            await s.commit()

        async with SessionLocal() as s:
            t = await targets_repo.get_target_for_update(s, target_id)
            assert t.status == "generating"
            await s.rollback()
    finally:
        await _purge(book_id)


async def test_terminality_and_published_completeness_are_enforced():
    """The partial lineage index is only correct if `terminal_at` and `status`
    can never disagree, so the database — not a service — enforces the pairing."""
    from app.db import SessionLocal
    from datetime import datetime, timezone

    async with SessionLocal() as s:
        book_id, toc_id, jobs = await _seed(s)
    try:
        now = datetime.now(timezone.utc)
        async with SessionLocal() as s:
            c = _campaign(status="approved", approved_at=now)
            s.add(c)
            await s.flush()
            campaign_id = c.id
            await s.commit()

        # A non-terminal status may not carry a terminal stamp...
        async with SessionLocal() as s:
            s.add(
                _target(
                    campaign_id=campaign_id,
                    toc_entry_id=toc_id,
                    output_language="uz",
                    source_job_id=jobs["uz"],
                    status="generating",
                    terminal_at=now,
                )
            )
            with pytest.raises(IntegrityError):
                await s.commit()

        # ...and `abandoned` may not be missing one.
        async with SessionLocal() as s:
            s.add(
                _target(
                    campaign_id=campaign_id,
                    toc_entry_id=toc_id,
                    output_language="uz",
                    source_job_id=jobs["uz"],
                    status="abandoned",
                    terminal_reason="operator abandoned",
                )
            )
            with pytest.raises(IntegrityError):
                await s.commit()

        # `published` without a Notion page is not published.
        async with SessionLocal() as s:
            s.add(
                _target(
                    campaign_id=campaign_id,
                    toc_entry_id=toc_id,
                    output_language="uz",
                    source_job_id=jobs["uz"],
                    status="published",
                    publication_released_at=now,
                    publication_version=2,
                    terminal_at=now,
                )
            )
            with pytest.raises(IntegrityError):
                await s.commit()

        # A publication state without a release stamp is refused too.
        async with SessionLocal() as s:
            s.add(
                _target(
                    campaign_id=campaign_id,
                    toc_entry_id=toc_id,
                    output_language="uz",
                    source_job_id=jobs["uz"],
                    status="publication_pending",
                )
            )
            with pytest.raises(IntegrityError):
                await s.commit()
    finally:
        await _purge(book_id)


async def test_publication_claim_is_durable_leased_and_fenced():
    from app.db import SessionLocal
    from datetime import datetime, timezone
    from app.repositories import regeneration_targets as targets_repo

    async with SessionLocal() as s:
        book_id, toc_id, jobs = await _seed(s)
    try:
        now = datetime.now(timezone.utc)
        async with SessionLocal() as s:
            c = _campaign(status="approved", approved_at=now)
            s.add(c)
            await s.flush()
            t = _target(
                campaign_id=c.id,
                toc_entry_id=toc_id,
                output_language="uz",
                source_job_id=jobs["uz"],
                status="publication_pending",
                publication_released_at=now,
                publication_version=2,
            )
            s.add(t)
            await s.commit()
            target_id = t.id

        first, second = uuid.uuid4(), uuid.uuid4()
        async with SessionLocal() as s:
            assert await targets_repo.claim_target_publication(
                s, target_id=target_id, claim_token=first, lease_seconds=300
            ) is True
            await s.commit()

        # A live lease is not stealable.
        async with SessionLocal() as s:
            assert await targets_repo.claim_target_publication(
                s, target_id=target_id, claim_token=second, lease_seconds=300
            ) is False
            await s.commit()

        async with SessionLocal() as s:
            t = await targets_repo.get_target_for_update(s, target_id)
            assert t.status == "publishing"
            assert t.publication_claim_token == first
            assert t.publication_attempts == 1  # counted on the claim, not on success
            await s.rollback()

        # ...but a DEAD publisher's row must not stay stuck in `publishing`
        # forever: once the lease expires another publisher takes it over.
        async with SessionLocal() as s:
            await s.execute(
                text(
                    "UPDATE regeneration_targets SET publication_claimed_at "
                    "= now() - interval '1 hour' WHERE id = :id"
                ),
                {"id": target_id},
            )
            await s.commit()

        async with SessionLocal() as s:
            assert await targets_repo.claim_target_publication(
                s, target_id=target_id, claim_token=second, lease_seconds=300
            ) is True
            await s.commit()

        async with SessionLocal() as s:
            t = await targets_repo.get_target_for_update(s, target_id)
            assert t.publication_claim_token == second
            assert t.publication_attempts == 2
            await s.rollback()

        # The backoff schedule holds a target back even when nothing holds it.
        async with SessionLocal() as s:
            await s.execute(
                text(
                    "UPDATE regeneration_targets SET status='publication_failed',"
                    " publication_claimed_at=NULL, publication_claim_token=NULL,"
                    " publication_next_attempt_at = now() + interval '1 hour'"
                    " WHERE id = :id"
                ),
                {"id": target_id},
            )
            await s.commit()

        async with SessionLocal() as s:
            assert await targets_repo.claim_target_publication(
                s, target_id=target_id, claim_token=uuid.uuid4(), lease_seconds=300
            ) is False
            await s.commit()

        async with SessionLocal() as s:
            await s.execute(
                text(
                    "UPDATE regeneration_targets SET status='publishing',"
                    " publication_claim_token=:tok, publication_claimed_at=now(),"
                    " publication_next_attempt_at=NULL WHERE id = :id"
                ),
                {"id": target_id, "tok": first},
            )
            await s.commit()

        # A stale publisher may not write its outcome after being fenced out.
        async with SessionLocal() as s:
            assert await targets_repo.set_target_status(
                s,
                target_id=target_id,
                new_status="published",
                expected_statuses=("publishing",),
                expected_claim_token=second,
                notion_page_id="page-v2",
                terminal_at=now,
                terminal_reason="published",
            ) is False
            await s.commit()

        async with SessionLocal() as s:
            assert await targets_repo.set_target_status(
                s,
                target_id=target_id,
                new_status="published",
                expected_statuses=("publishing",),
                expected_claim_token=first,
                notion_page_id="page-v2",
                terminal_at=now,
                terminal_reason="published",
                clear_publication_claim=True,
            ) is True
            await s.commit()

        async with SessionLocal() as s:
            t = await targets_repo.get_target_for_update(s, target_id)
            assert t.status == "published"
            assert t.publication_claim_token is None
            assert t.terminal_at is not None
            await s.rollback()
    finally:
        await _purge(book_id)


async def test_stored_phase_plan_survives_the_jsonb_round_trip():
    """The column shape and the planner's serializer must agree for real.

    This row used to store a bare `["flashcards"]`, which carries none of what
    the later lanes read back — the copied/regenerated split, the auto-included
    and acknowledged-excluded sets, the broken dependency edges,
    `refresh_extraction` — and nothing proved the JSONB column could return the
    planner's object unchanged. `from_json` is strict, so it is the assertion:
    anything the database gave back that `build_phase_plan` could not have
    produced raises instead of quietly comparing equal.

    Note it is the PLAN that must round-trip, not the JSON text: PostgreSQL
    `jsonb` normalizes key order, so the byte-stability `to_json` guarantees
    holds before storage, not after it.
    """
    from app.db import SessionLocal
    from app.models.regeneration_target import RegenerationTarget
    from app.repositories import regeneration_campaigns as campaigns_repo
    from app.repositories import regeneration_targets as targets_repo

    async with SessionLocal() as s:
        book_id, toc_id, jobs = await _seed(s)
    try:
        async with SessionLocal() as s:
            campaign = await campaigns_repo.create_campaign(
                s,
                selection_spec={"mode": "test"},
                requested_phases=["flashcards"],
                excluded_phases=[],
                launch_contract={"provider": "gemini"},
                canary_size=1,
                app_git_revision=_MARKER,
            )
            target = await targets_repo.create_target(
                s,
                campaign_id=campaign.id,
                toc_entry_id=toc_id,
                output_language="uz",
                source_job_id=jobs["uz"],
                phase_plan=copy.deepcopy(_PHASE_PLAN),
            )
            await s.commit()
            target_id = target.id

        async with SessionLocal() as s:
            stored = await s.get(RegenerationTarget, target_id)
            assert isinstance(stored.phase_plan, dict), stored.phase_plan
            assert RegenerationPhasePlan.from_json(stored.phase_plan) == _PLAN
            # The split the orchestrator actually reads survived the column.
            assert "flashcards" in stored.phase_plan["regenerated_phases"]
            assert "case-based-preview" in stored.phase_plan["copied_phases"]
    finally:
        await _purge(book_id)


async def test_revision_job_must_store_a_concrete_session_limit_strategy():
    """A revision job carries its OWN session-limit strategy, or it has none.

    `session_limit_strategy` otherwise lives on `batches` and in `settings`,
    and `ck_homework_jobs_revision_no_batch` forces every revision to have
    `batch_id IS NULL` — so the approved, frozen `LaunchContract` value had
    nothing to be written to and always fell through to the mutable fleet-wide
    default.

    The revision rule is `IN ('pause','switch')`, not `IS NOT NULL`: `'inherit'`
    re-resolves against `settings.session_limit_strategy` at run time and
    reproduces exactly that no-op, so the database refuses to store it on a
    revision. Ordinary jobs are untouched — NULL (or an explicit `'inherit'`)
    keeps the existing batch-then-global resolution.
    """
    from app.db import SessionLocal
    from app.models.homework_job import HomeworkJob

    async with SessionLocal() as s:
        book_id, toc_id, jobs = await _seed(s, languages=("uz", "ru"))

    def _job(**overrides):
        kwargs = dict(
            book_id=book_id,
            toc_entry_id=toc_id,
            subject="math-algebra",
            status="pending",
            provider="gemini",
            transport="api",
            output_language="uz",
        )
        kwargs.update(overrides)
        return HomeworkJob(**kwargs)

    try:
        # ── ordinary jobs: unchanged behavior ────────────────────────────
        for value in (None, "inherit"):
            async with SessionLocal() as s:
                s.add(_job(session_limit_strategy=value))
                await s.commit()  # must not raise

        async with SessionLocal() as s:
            s.add(_job(session_limit_strategy="bogus"))
            with pytest.raises(IntegrityError) as exc:
                await s.commit()
        assert "ck_homework_jobs_session_limit_strategy" in str(exc.value)

        # ── one target per (lesson, language); the lineage index allows uz
        #    and ru to be live at the same time. ──────────────────────────
        async with SessionLocal() as s:
            c = _campaign()
            s.add(c)
            await s.flush()
            uz = _target(
                campaign_id=c.id,
                toc_entry_id=toc_id,
                output_language="uz",
                source_job_id=jobs["uz"],
            )
            ru = _target(
                campaign_id=c.id,
                toc_entry_id=toc_id,
                output_language="ru",
                source_job_id=jobs["ru"],
            )
            s.add_all([uz, ru])
            await s.commit()
            uz_target_id, ru_target_id = uz.id, ru.id

        def _revision(target_id, **overrides):
            return _job(
                revision_of_job_id=jobs["uz"],
                regeneration_target_id=target_id,
                **overrides,
            )

        # A revision with NO strategy is refused — a failed insert rolls back,
        # so the target stays free for the next attempt.
        async with SessionLocal() as s:
            s.add(_revision(uz_target_id, session_limit_strategy=None))
            with pytest.raises(IntegrityError) as exc:
                await s.commit()
        assert "ck_homework_jobs_revision_session_limit_strategy" in str(exc.value)

        # ...and so is 'inherit', which the general check happily allows.
        async with SessionLocal() as s:
            s.add(_revision(uz_target_id, session_limit_strategy="inherit"))
            with pytest.raises(IntegrityError) as exc:
                await s.commit()
        assert "ck_homework_jobs_revision_session_limit_strategy" in str(exc.value)

        # Both concrete values commit.
        async with SessionLocal() as s:
            s.add(_revision(uz_target_id, session_limit_strategy="pause"))
            await s.commit()

        async with SessionLocal() as s:
            s.add(
                _revision(
                    ru_target_id, output_language="ru", session_limit_strategy="switch"
                )
            )
            await s.commit()

        # Scoped to THIS test's two targets. An unfiltered
        # `regeneration_target_id IS NOT NULL` reads every revision row in the
        # database, so any other test that leaves one behind (or simply runs
        # first) turns this assertion into a failure that has nothing to do
        # with the constraint under test.
        async with SessionLocal() as s:
            stored = (
                await s.execute(
                    text(
                        "SELECT session_limit_strategy FROM homework_jobs "
                        "WHERE regeneration_target_id IN (:uz, :ru) "
                        "ORDER BY session_limit_strategy"
                    ),
                    {"uz": uz_target_id, "ru": ru_target_id},
                )
            ).scalars().all()
        assert stored == ["pause", "switch"]
    finally:
        await _purge(book_id)


# ── guided regeneration: campaign version + reviewed Notion destination ─────
# `publication_version` exists on BOTH regeneration tables — 0063 put one on
# `regeneration_targets` (the per-lesson allocation) and 0064 adds one to
# `regeneration_campaigns` (the version the whole campaign publishes). Every
# assertion below names the table it means.
_CAMPAIGN_VERSION_CHECK = "ck_regeneration_campaigns_publication_version"
_DESTINATION_CHECK = "ck_regeneration_targets_notion_parent_decision"

# The two complete, legal reviewed destinations. `container` is the page that
# holds Lesson Topics; `parent` is the Lesson Topic itself, under which the
# `Homework V2` sibling is written.
_REUSE_DESTINATION = dict(
    notion_container_policy="reuse",
    reviewed_notion_container_page_id="container-page-1",
    notion_parent_policy="reuse",
    reviewed_notion_lesson_page_id="lesson-page-1",
    reviewed_notion_lesson_title="7 Photosynthesis",
)
_CREATE_DESTINATION = dict(
    notion_container_policy="create",
    reviewed_notion_container_page_id=None,
    notion_parent_policy="create",
    reviewed_notion_lesson_page_id=None,
    reviewed_notion_lesson_title="7 Photosynthesis",
)

# Every shape the rule must refuse, keyed by what is wrong with it. Shared by
# the database test (the CHECK refuses each) and the repository test (the
# repository refuses each BEFORE the row is built) so the two can never drift.
_REFUSED_DESTINATIONS = {
    # 'reuse' with nothing to reuse.
    "container reuse without a page id": {
        **_REUSE_DESTINATION,
        "reviewed_notion_container_page_id": None,
    },
    "lesson reuse without a page id": {
        **_REUSE_DESTINATION,
        "reviewed_notion_lesson_page_id": None,
    },
    # 'create' carrying a page id it would never use.
    "container create with a page id": {
        **_CREATE_DESTINATION,
        "reviewed_notion_container_page_id": "container-page-1",
    },
    "lesson create with a page id": {
        **_CREATE_DESTINATION,
        "reviewed_notion_lesson_page_id": "lesson-page-1",
    },
    "unknown lesson policy": {
        **_REUSE_DESTINATION,
        "notion_parent_policy": "adopt",
    },
    "unknown container policy": {
        **_REUSE_DESTINATION,
        "notion_container_policy": "adopt",
    },
    # A brand-new container has no children, so there is no existing Lesson
    # Topic inside it to reuse.
    "reused lesson under a created container": {
        **_REUSE_DESTINATION,
        "notion_container_policy": "create",
        "reviewed_notion_container_page_id": None,
    },
    # Whatever is written, the operator must have seen its title.
    "no reviewed title": {
        **_REUSE_DESTINATION,
        "reviewed_notion_lesson_title": None,
    },
    # A reviewed value with no policy at all is a decision nobody made.
    "container page id with no policy at all": {
        "reviewed_notion_container_page_id": "container-page-1",
    },
    "lesson page id with no policy at all": {
        "reviewed_notion_lesson_page_id": "lesson-page-1",
    },
}

# The two refusals that only hold if every comparison in the CHECK is TOTAL —
# see `test_destination_check_is_total_for_a_missing_policy`. Kept apart from
# the table above because they are the specific proof of that property.
_NULL_POLICY_DESTINATIONS = {
    "lesson policy with no container policy beside it": {
        "notion_container_policy": None,
        "reviewed_notion_container_page_id": None,
        "notion_parent_policy": "reuse",
        "reviewed_notion_lesson_page_id": "lesson-page-1",
        "reviewed_notion_lesson_title": "7 Photosynthesis",
    },
    "reviewed title with no policy at all": {
        "reviewed_notion_lesson_title": "7 Photosynthesis",
    },
}


async def _accepts_destination(campaign_id, toc_id, job_id, **destination) -> None:
    """Commit one target carrying `destination`, then delete it again.

    Deleting is what lets several ACCEPTED shapes be proven against the SAME
    (lesson, language) inside one test: `uq_regeneration_targets_active_lineage`
    allows exactly one non-terminal target per lineage, and the subject here is
    the CHECK, not that index.
    """
    from app.db import SessionLocal
    from app.models.regeneration_target import RegenerationTarget

    async with SessionLocal() as s:
        target = _target(
            campaign_id=campaign_id,
            toc_entry_id=toc_id,
            output_language="uz",
            source_job_id=job_id,
            **destination,
        )
        s.add(target)
        await s.commit()
        target_id = target.id
    async with SessionLocal() as s:
        await s.execute(
            delete(RegenerationTarget).where(RegenerationTarget.id == target_id)
        )
        await s.commit()


async def _refuses_destination(campaign_id, toc_id, job_id, **destination) -> str:
    """Try to commit a target with `destination`; return the refusal text."""
    from app.db import SessionLocal

    async with SessionLocal() as s:
        s.add(
            _target(
                campaign_id=campaign_id,
                toc_entry_id=toc_id,
                output_language="uz",
                source_job_id=job_id,
                **destination,
            )
        )
        with pytest.raises(IntegrityError) as exc:
            await s.commit()
    return str(exc.value)


async def test_campaign_version_must_be_two_or_more_or_absent():
    """Logical V1 is the pre-existing `Homework` page, which no campaign ever
    produced — so a campaign may claim version 2 upwards, or nothing at all.

    NULL stays legal because historical campaigns predate the column and must
    not be retro-assigned a version nobody published.
    """
    from app.db import SessionLocal

    async with SessionLocal() as s:
        book_id, toc_id, jobs = await _seed(s)
    try:
        for refused in (1, 0, -3):
            async with SessionLocal() as s:
                s.add(_campaign(publication_version=refused))
                with pytest.raises(IntegrityError) as exc:
                    await s.commit()
            assert _CAMPAIGN_VERSION_CHECK in str(exc.value), refused

        for accepted in (None, 2, 7):
            async with SessionLocal() as s:
                s.add(_campaign(publication_version=accepted))
                await s.commit()  # must not raise
    finally:
        await _purge(book_id)


async def test_destination_check_accepts_only_legacy_reuse_or_create_shapes():
    """The reviewed destination is three fields per level, and only whole,
    coherent combinations may reach the database.

    A half-filled destination is how a publisher ends up writing `Homework V2`
    somewhere nobody approved, so the rule is a CHECK rather than a convention
    the services agree to honour.
    """
    from app.db import SessionLocal

    async with SessionLocal() as s:
        book_id, toc_id, jobs = await _seed(s)
    try:
        async with SessionLocal() as s:
            c = _campaign()
            s.add(c)
            await s.flush()
            campaign_id = c.id
            await s.commit()

        job_id = jobs["uz"]

        # ── accepted ────────────────────────────────────────────────────────
        # Legacy: a target from before the guided wizard carries no decision.
        await _accepts_destination(campaign_id, toc_id, job_id)
        await _accepts_destination(campaign_id, toc_id, job_id, **_REUSE_DESTINATION)
        await _accepts_destination(campaign_id, toc_id, job_id, **_CREATE_DESTINATION)
        # Reuse the container, create a NEW Lesson Topic inside it — legal in
        # the other direction, which is why the two levels are separate fields.
        await _accepts_destination(
            campaign_id,
            toc_id,
            job_id,
            notion_container_policy="reuse",
            reviewed_notion_container_page_id="container-page-1",
            notion_parent_policy="create",
            reviewed_notion_lesson_page_id=None,
            reviewed_notion_lesson_title="7 Photosynthesis",
        )

        # ── refused ─────────────────────────────────────────────────────────
        for label, destination in _REFUSED_DESTINATIONS.items():
            message = await _refuses_destination(
                campaign_id, toc_id, job_id, **destination
            )
            assert _DESTINATION_CHECK in message, f"{label} was not refused by name"
    finally:
        await _purge(book_id)


async def test_destination_check_is_total_for_a_missing_policy():
    """A half-filled destination whose POLICY is NULL must still be refused.

    This is the specific hole a literal reading of the rule leaves open. SQL is
    three-valued and a CHECK constraint is SATISFIED by UNKNOWN, so a predicate
    written with bare `notion_container_policy = 'reuse'` comparisons evaluates
    to NULL — not FALSE — whenever a policy is missing, and PostgreSQL ACCEPTS
    the row. Both shapes below are exactly that case, and both are ones the rule
    is meant to reject, so they are the proof that every comparison in the
    stored constraint is total (`IS NOT DISTINCT FROM` / `IS NOT NULL`).
    """
    from app.db import SessionLocal

    async with SessionLocal() as s:
        book_id, toc_id, jobs = await _seed(s)
    try:
        async with SessionLocal() as s:
            c = _campaign()
            s.add(c)
            await s.flush()
            campaign_id = c.id
            await s.commit()

        job_id = jobs["uz"]

        for label, destination in _NULL_POLICY_DESTINATIONS.items():
            message = await _refuses_destination(
                campaign_id, toc_id, job_id, **destination
            )
            assert _DESTINATION_CHECK in message, f"{label} was not refused"
    finally:
        await _purge(book_id)


async def test_repository_refuses_a_partial_destination_and_a_v1_campaign_version():
    """The repositories validate the same rules BEFORE the row is built.

    The database CHECK is the authority; these raise `ValueError` so a caller
    gets a readable refusal instead of an `IntegrityError` from a flush several
    statements later. Neither repository exposes a way to CHANGE the reviewed
    decision afterwards — it is what the operator approved.
    """
    import inspect as _inspect

    from app.db import SessionLocal
    from app.models.regeneration_target import RegenerationTarget
    from app.repositories import regeneration_campaigns as campaigns_repo
    from app.repositories import regeneration_targets as targets_repo

    async with SessionLocal() as s:
        book_id, toc_id, jobs = await _seed(s)
    try:
        async with SessionLocal() as s:
            with pytest.raises(ValueError, match=">= 2"):
                await campaigns_repo.create_campaign(
                    s,
                    selection_spec={"mode": "test"},
                    requested_phases=["flashcards"],
                    excluded_phases=[],
                    launch_contract={"provider": "gemini"},
                    app_git_revision=_MARKER,
                    publication_version=1,
                )
            await s.rollback()

        async with SessionLocal() as s:
            campaign = await campaigns_repo.create_campaign(
                s,
                selection_spec={"mode": "test"},
                requested_phases=["flashcards"],
                excluded_phases=[],
                launch_contract={"provider": "gemini"},
                app_git_revision=_MARKER,
                publication_version=2,
            )
            await s.commit()
            campaign_id = campaign.id

        # Parity: every shape the CHECK refuses is refused here too, before any
        # SQL is emitted. Sharing the tables is what keeps the two in step — a
        # rule added to one and forgotten in the other fails this loop.
        async with SessionLocal() as s:
            for label, destination in {
                **_REFUSED_DESTINATIONS,
                **_NULL_POLICY_DESTINATIONS,
            }.items():
                try:
                    await targets_repo.create_target(
                        s,
                        campaign_id=campaign_id,
                        toc_entry_id=toc_id,
                        output_language="uz",
                        phase_plan=copy.deepcopy(_PHASE_PLAN),
                        source_job_id=jobs["uz"],
                        **destination,
                    )
                except ValueError:
                    continue
                pytest.fail(f"create_target accepted {label!r}")
            await s.rollback()

        # The whole shape is stored verbatim.
        async with SessionLocal() as s:
            target = await targets_repo.create_target(
                s,
                campaign_id=campaign_id,
                toc_entry_id=toc_id,
                output_language="uz",
                phase_plan=copy.deepcopy(_PHASE_PLAN),
                source_job_id=jobs["uz"],
                **_REUSE_DESTINATION,
            )
            await s.commit()
            target_id = target.id

        async with SessionLocal() as s:
            stored = await s.get(RegenerationTarget, target_id)
            for field, value in _REUSE_DESTINATION.items():
                assert getattr(stored, field) == value, field
            campaign = await campaigns_repo.get_campaign(s, campaign_id)
            assert campaign.publication_version == 2

        # No setter: the reviewed decision is immutable after creation.
        status_params = _inspect.signature(targets_repo.set_target_status).parameters
        for field in _REUSE_DESTINATION:
            assert field not in status_params, f"{field} must not be settable later"
    finally:
        await _purge(book_id)
