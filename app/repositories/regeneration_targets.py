"""Target primitives shared by every regeneration lane.

Module-level async functions taking the session first, like every other
repository here. Task 6 uses these as-is; Tasks 7-8 extend this module
sequentially (version allocation, publisher sweeps) rather than defining their
own parallel accessors.
"""
from typing import Optional, Sequence
from uuid import UUID

from sqlalchemy import func, or_, select, update
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
    on a target (status convergence, publication release, abandonment).

    ``populate_existing=True`` for the same reason as the campaign twin:
    ``SessionLocal`` is ``expire_on_commit=False``, so a session that already
    loaded this target would be handed its own stale in-memory copy and would
    take the lock while reading a status another transaction has since moved.
    A campaign action deciding "this target is still ``generating``" from a
    stale object then writes a compare-and-set that quietly matches nothing.
    """
    return await session.scalar(
        select(RegenerationTarget)
        .where(RegenerationTarget.id == target_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


async def list_for_campaign(
    session: AsyncSession, campaign_id: UUID, *, for_update: bool = False
) -> list[RegenerationTarget]:
    """Every target of one campaign in the campaign's canonical order:
    ``(book, TOC order, output language, target id)``.

    That order is the one the canary selection and the launch stagger are
    defined against, so it lives in SQL rather than in each caller — two
    callers sorting "the same way" is how a canary set stops being
    reproducible.

    ``for_update`` locks ONLY ``regeneration_targets`` (``FOR UPDATE OF``); the
    joined ``toc_entries`` row is read for ordering and must not be locked by a
    campaign action.
    """
    stmt = (
        select(RegenerationTarget)
        .join(TOCEntry, TOCEntry.id == RegenerationTarget.toc_entry_id)
        .where(RegenerationTarget.campaign_id == campaign_id)
        .order_by(
            TOCEntry.book_id,
            TOCEntry.order_index,
            RegenerationTarget.output_language,
            RegenerationTarget.id,
        )
    )
    if for_update:
        stmt = stmt.with_for_update(of=RegenerationTarget)
    result = await session.execute(stmt.execution_options(populate_existing=True))
    return list(result.scalars().all())


async def active_targets_for_lineages(
    session: AsyncSession, lineages: Sequence[tuple[UUID, str]]
) -> list[RegenerationTarget]:
    """Every NON-terminal target owning one of these ``(toc_entry_id,
    output_language)`` lineages.

    The pre-flight form of ``uq_regeneration_targets_active_lineage``: campaign
    creation asks this first so an operator gets one actionable list of
    conflicting lessons instead of an ``IntegrityError`` on the first colliding
    insert. The index remains the authority — two creators racing can both pass
    this read — so the caller must still handle the integrity error.
    """
    if not lineages:
        return []
    predicate = or_(*[
        (RegenerationTarget.toc_entry_id == toc_entry_id)
        & (RegenerationTarget.output_language == language)
        for toc_entry_id, language in lineages
    ])
    result = await session.execute(
        select(RegenerationTarget)
        .where(RegenerationTarget.terminal_at.is_(None))
        .where(predicate)
        .order_by(RegenerationTarget.created_at)
    )
    return list(result.scalars().all())


async def target_ids_with_revision_job(
    session: AsyncSession, campaign_id: UUID
) -> set[UUID]:
    """The campaign's targets that already own a revision job, in one query.

    Read through the job side's unique ``regeneration_target_id`` — the
    authoritative direction of the one-to-one link. A wave asks this once
    instead of a per-target round trip, and it is what makes a repeated launch
    or approval create nothing.
    """
    result = await session.execute(
        select(HomeworkJob.regeneration_target_id)
        .join(
            RegenerationTarget,
            RegenerationTarget.id == HomeworkJob.regeneration_target_id,
        )
        .where(RegenerationTarget.campaign_id == campaign_id)
    )
    return {row[0] for row in result.all()}


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
    clear_publication_backoff: bool = False,
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

    ``clear_publication_backoff=True`` is the same idea for the two retry
    columns: it NULLs ``publication_next_attempt_at`` and
    ``publication_last_error``. An operator retry has to clear both — a
    surviving ``publication_next_attempt_at`` keeps the target unclaimable
    until the old exponential backoff elapses, which looks exactly like a
    retry that did nothing, and a surviving ``publication_last_error`` reports
    a failure that is no longer current. Neither can be expressed by passing
    None, which means "leave it alone". Passing an explicit
    ``publication_last_error`` alongside it is contradictory; the clear wins.

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
    if clear_publication_backoff:
        values["publication_next_attempt_at"] = None
        values["publication_last_error"] = None

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
