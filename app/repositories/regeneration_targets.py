"""Target primitives shared by every regeneration lane.

Module-level async functions taking the session first, like every other
repository here. Task 6 uses these as-is; Tasks 7-8 extend this module
sequentially (version allocation, publisher sweeps) rather than defining their
own parallel accessors.
"""
from typing import Optional, Sequence
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.homework_job import HomeworkJob
from app.models.regeneration_target import RegenerationTarget
from app.models.toc_entry import TOCEntry


async def create_target(
    session: AsyncSession,
    *,
    campaign_id: UUID,
    toc_entry_id: UUID,
    output_language: str,
    phase_plan: dict,
    source_job_id: Optional[UUID] = None,
    is_canary: bool = False,
    status: str = "planned",
) -> RegenerationTarget:
    """Insert one lesson's target. The caller must be ready for an
    ``IntegrityError``: ``uq_regeneration_targets_active_lineage`` is what stops
    two campaigns owning the same (lesson, language) at once, and it fires here.

    ``phase_plan`` is the serialized object produced by
    ``app.services.regeneration_planner.RegenerationPhasePlan.to_json()`` — the
    planner is the only producer, and ``from_json`` the only reader. This
    repository deliberately does no serialization of its own, so there is
    exactly one definition of the stored shape."""
    target = RegenerationTarget(
        campaign_id=campaign_id,
        toc_entry_id=toc_entry_id,
        output_language=output_language,
        phase_plan=phase_plan,
        source_job_id=source_job_id,
        is_canary=is_canary,
        status=status,
    )
    session.add(target)
    await session.flush()
    return target


async def get_target_for_update(
    session: AsyncSession, target_id: UUID
) -> Optional[RegenerationTarget]:
    """Row-locked read (``FOR UPDATE``) — required before any read-then-write
    on a target (status convergence, publication release, abandonment)."""
    return await session.scalar(
        select(RegenerationTarget)
        .where(RegenerationTarget.id == target_id)
        .with_for_update()
    )


async def get_target_by_revision_job(
    session: AsyncSession, *, job_id: UUID
) -> Optional[RegenerationTarget]:
    """The target a revision job belongs to, via the unique job-side link."""
    return await session.scalar(
        select(RegenerationTarget)
        .join(HomeworkJob, HomeworkJob.regeneration_target_id == RegenerationTarget.id)
        .where(HomeworkJob.id == job_id)
    )


async def revision_job_for_target(
    session: AsyncSession, *, target_id: UUID
) -> Optional[HomeworkJob]:
    """The target's revision job, read through the unique
    ``homework_jobs.regeneration_target_id`` — the authoritative direction of
    the one-to-one link (the target holds no job id of its own)."""
    return await session.scalar(
        select(HomeworkJob).where(HomeworkJob.regeneration_target_id == target_id)
    )


async def history_for_toc_entry(
    session: AsyncSession, toc_entry_id: UUID
) -> list[RegenerationTarget]:
    """Every regeneration target that references one TOC entry, oldest first.

    ANY row counts, including terminal ones: a target is audit history that
    records a permanently consumed publication version, which is exactly why
    ``fk_regeneration_targets_toc_entry_id`` is ``RESTRICT``. The source-removing
    routes call this to refuse with a readable 409 instead of letting that
    RESTRICT surface as a raw foreign-key error.
    """
    return list((await session.execute(
        select(RegenerationTarget)
        .where(RegenerationTarget.toc_entry_id == toc_entry_id)
        .order_by(RegenerationTarget.created_at)
    )).scalars().all())


async def history_for_book(
    session: AsyncSession, book_id: UUID
) -> list[RegenerationTarget]:
    """The same, for every TOC entry of one book (book delete, TOC re-extract).

    Joined through ``toc_entries`` because a target has no ``book_id`` of its
    own — the lesson is the unit of regeneration, and the book is only its
    container.
    """
    return list((await session.execute(
        select(RegenerationTarget)
        .join(TOCEntry, TOCEntry.id == RegenerationTarget.toc_entry_id)
        .where(TOCEntry.book_id == book_id)
        .order_by(RegenerationTarget.created_at)
    )).scalars().all())


async def set_target_status(
    session: AsyncSession,
    *,
    target_id: UUID,
    new_status: str,
    expected_statuses: Sequence[str],
    expected_claim_token: Optional[UUID] = None,
    terminal_at=None,
    terminal_reason: Optional[str] = None,
    publication_released_at=None,
    publication_version: Optional[int] = None,
    notion_page_id: Optional[str] = None,
    publication_next_attempt_at=None,
    publication_last_error: Optional[str] = None,
    abandon_requested_at=None,
    abandon_requested_reason: Optional[str] = None,
    clear_publication_claim: bool = False,
) -> bool:
    """Fenced compare-and-set on a target.

    Returns True when the row moved, False when it was already out of
    ``expected_statuses`` (someone else converged it first).

    ``expected_claim_token`` additionally fences the write to the current
    publication claim owner, so a publisher whose lease expired and was taken
    over cannot write a stale outcome. Passing None means "do not fence on the
    claim" — never "expect NULL".

    Only non-None fields are written; ``clear_publication_claim=True`` is the
    explicit way to release a claim (a None token would otherwise be
    indistinguishable from "leave it alone").

    The database still has the last word: the publication-approval trigger and
    the terminality/published-completeness checks reject an illegal move even if
    the expected status matched.
    """
    values: dict = {"status": new_status, "updated_at": func.now()}
    for column, value in (
        ("terminal_at", terminal_at),
        ("terminal_reason", terminal_reason),
        ("publication_released_at", publication_released_at),
        ("publication_version", publication_version),
        ("notion_page_id", notion_page_id),
        ("publication_next_attempt_at", publication_next_attempt_at),
        ("publication_last_error", publication_last_error),
        ("abandon_requested_at", abandon_requested_at),
        ("abandon_requested_reason", abandon_requested_reason),
    ):
        if value is not None:
            values[column] = value
    if clear_publication_claim:
        values["publication_claim_token"] = None
        values["publication_claimed_at"] = None

    where = [
        RegenerationTarget.id == target_id,
        RegenerationTarget.status.in_(list(expected_statuses)),
    ]
    if expected_claim_token is not None:
        where.append(RegenerationTarget.publication_claim_token == expected_claim_token)

    result = await session.execute(
        update(RegenerationTarget).where(*where).values(**values)
    )
    return result.rowcount == 1


async def claim_target_publication(
    session: AsyncSession,
    *,
    target_id: UUID,
    claim_token: UUID,
    lease_seconds: int,
) -> bool:
    """Take (or take OVER) a publication claim and move the target to
    ``publishing``, atomically.

    The claim is durable — it lives on the row, not in the publisher's memory —
    so a publisher that dies mid-delivery keeps its target until the lease
    expires, and only then may another publisher take it over. That takeover is
    why ``publishing`` itself is claimable: a crashed publisher leaves the row
    in ``publishing`` and nothing else would ever move it out.

    Attempts are incremented on the CLAIM, not on the outcome, so a
    crash-looping publisher still exhausts its budget instead of retrying
    forever.

    Returns False when the target is not claimable: wrong status, backoff not
    yet due, or a live lease held by another publisher. It RAISES (rather than
    returning False) if the owning campaign is no longer approved — the
    publication-approval trigger refuses the transition into ``publishing``,
    and a caller looping over claimable targets must expect that.
    """
    now = func.now()
    lease_cutoff = now - func.make_interval(0, 0, 0, 0, 0, 0, lease_seconds)
    lease_expired = (RegenerationTarget.publication_claimed_at.is_(None)) | (
        RegenerationTarget.publication_claimed_at <= lease_cutoff
    )
    result = await session.execute(
        update(RegenerationTarget)
        .where(
            RegenerationTarget.id == target_id,
            (
                # Released work — nobody is holding it.
                RegenerationTarget.status.in_(
                    ("publication_pending", "publication_failed")
                )
                # ...or a dead publisher's row, reclaimable once its lease ends.
                | ((RegenerationTarget.status == "publishing") & lease_expired)
            ),
            (RegenerationTarget.publication_next_attempt_at.is_(None))
            | (RegenerationTarget.publication_next_attempt_at <= now),
        )
        .values(
            status="publishing",
            publication_claim_token=claim_token,
            publication_claimed_at=now,
            publication_attempts=RegenerationTarget.publication_attempts + 1,
            updated_at=now,
        )
    )
    return result.rowcount == 1
