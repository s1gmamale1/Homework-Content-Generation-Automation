"""Target primitives shared by every regeneration lane.

Module-level async functions taking the session first, like every other
repository here. Task 6 uses these as-is; Tasks 7-8 extend this module
sequentially (version allocation, publisher sweeps) rather than defining their
own parallel accessors.

**Lock order is parent (campaign) then child (target), everywhere.** Every
campaign-level action (`regeneration_campaign`, `regeneration_job_state`) takes
that direction, and `trg_regeneration_targets_publication_gate` takes
`FOR KEY SHARE` on the campaign from INSIDE a target UPDATE. A publisher that
reached the target first would therefore invert the order and deadlock a
concurrent cancel or rollup, so :func:`claim_next_publication` and
:func:`lock_owning_campaign` exist to establish the parent lock first.
"""
import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Sequence
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.homework_job import HomeworkJob
from app.models.regeneration_campaign import RegenerationCampaign
from app.models.regeneration_target import (
    NOTION_DESTINATION_POLICIES,
    RegenerationTarget,
)
from app.models.toc_entry import TOCEntry

# How many claimable rows one sweep inspects before giving up for this tick. A
# bound, not a cap on throughput: a publisher loops, so a full scan that claims
# nothing (every candidate locked by a peer) simply retries on the next pass.
_CLAIM_SCAN_LIMIT = 50

# Advisory-lock namespace for publication version allocation. The two-int form
# of `pg_advisory_xact_lock` is used so this namespace can never collide with a
# single-bigint advisory lock taken elsewhere in the app.
_VERSION_LOCK_NAMESPACE = 0x52454756  # 'REGV'


class StalePublicationClaim(RuntimeError):
    """The caller's publication claim token is no longer the one on the row.

    Raised rather than returned because every caller is mid-delivery: a lease
    that was taken over means this process must stop writing, not branch.
    """


class PublicationVersionUnavailable(RuntimeError):
    """The version this campaign declared cannot be reserved for this target.

    Deliberately NOT a subclass of :class:`StalePublicationClaim`.
    ``regeneration_publisher._TAKEOVER`` catches that one around the whole
    delivery and returns having written NOTHING — correct for a lease that was
    taken over, and catastrophic here: a spent version cannot be freed by
    retrying, so the target would be re-claimed every lease forever instead of
    parking for an operator. It is an operator-facing refusal, so the message
    names the number.
    """


@dataclass(frozen=True)
class ClaimedRegenerationTarget:
    """One claimed publication, flattened.

    A value object rather than the ORM row on purpose: the publisher closes its
    DB session before any Notion call, and a detached ORM instance whose
    attributes lazily re-load is exactly the shape that turns a remote-I/O path
    into a surprise database access.
    """

    target_id: UUID
    campaign_id: UUID
    toc_entry_id: UUID
    output_language: str
    claim_token: UUID
    publication_attempts: int
    publication_version: Optional[int]
    notion_page_id: Optional[str]
    abandon_requested_at: Optional[datetime]


def _publication_lineage_lock_key(toc_entry_id: UUID, output_language: str) -> int:
    """Deterministic signed int32 for one `(lesson, language)` lineage.

    Hashed in Python rather than with SQL `hashtext` so the key cannot drift
    between statements or PostgreSQL versions. A hash collision merely
    serialises two unrelated lineages against each other — the unique index
    `uq_regeneration_targets_publication_version` remains the real fence.
    """
    digest = hashlib.blake2b(
        f"{toc_entry_id}:{output_language}".encode(), digest_size=4
    ).digest()
    return int.from_bytes(digest, "big", signed=True)


def _validate_reviewed_destination(
    *,
    notion_container_policy: Optional[str],
    reviewed_notion_container_page_id: Optional[str],
    notion_parent_policy: Optional[str],
    reviewed_notion_lesson_page_id: Optional[str],
    reviewed_notion_lesson_title: Optional[str],
) -> None:
    """Refuse anything but no destination at all, or a whole coherent one.

    The Python twin of ``ck_regeneration_targets_notion_parent_decision``. The
    CHECK is the authority — this exists so a caller gets a readable
    ``ValueError`` naming the offending field instead of an ``IntegrityError``
    raised by a flush several statements later, possibly after other rows in
    the same transaction have already been built.
    """
    values = (
        notion_container_policy,
        reviewed_notion_container_page_id,
        notion_parent_policy,
        reviewed_notion_lesson_page_id,
        reviewed_notion_lesson_title,
    )
    if all(v is None for v in values):
        # No reviewed decision — the historical/internal shape. Legal.
        return
    if notion_parent_policy not in NOTION_DESTINATION_POLICIES:
        raise ValueError(
            "notion_parent_policy must be one of "
            f"{list(NOTION_DESTINATION_POLICIES)} once any reviewed destination "
            f"field is set (got {notion_parent_policy!r})"
        )
    if notion_container_policy not in NOTION_DESTINATION_POLICIES:
        raise ValueError(
            "notion_container_policy must be one of "
            f"{list(NOTION_DESTINATION_POLICIES)} once any reviewed destination "
            f"field is set (got {notion_container_policy!r})"
        )
    if reviewed_notion_lesson_title is None:
        raise ValueError(
            "reviewed_notion_lesson_title is required — the operator approved a "
            "destination by its title, so it is recorded whether the Lesson "
            "Topic is reused or created"
        )
    # `reuse` names a page that exists; `create` names one that does not yet.
    if notion_container_policy == "reuse":
        if reviewed_notion_container_page_id is None:
            raise ValueError(
                "notion_container_policy='reuse' needs "
                "reviewed_notion_container_page_id — there is nothing to reuse"
            )
    elif reviewed_notion_container_page_id is not None:
        raise ValueError(
            "notion_container_policy='create' must not carry "
            "reviewed_notion_container_page_id — the container does not exist yet"
        )
    if notion_parent_policy == "reuse":
        # A container that is about to be created has no children, so there is
        # no existing Lesson Topic inside it to reuse.
        if notion_container_policy != "reuse":
            raise ValueError(
                "notion_parent_policy='reuse' requires "
                "notion_container_policy='reuse' — a container that does not "
                "exist yet holds no Lesson Topic to reuse"
            )
        if reviewed_notion_lesson_page_id is None:
            raise ValueError(
                "notion_parent_policy='reuse' needs "
                "reviewed_notion_lesson_page_id — there is nothing to reuse"
            )
    elif reviewed_notion_lesson_page_id is not None:
        raise ValueError(
            "notion_parent_policy='create' must not carry "
            "reviewed_notion_lesson_page_id — the Lesson Topic does not exist yet"
        )


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
    notion_container_policy: Optional[str] = None,
    reviewed_notion_container_page_id: Optional[str] = None,
    notion_parent_policy: Optional[str] = None,
    reviewed_notion_lesson_page_id: Optional[str] = None,
    reviewed_notion_lesson_title: Optional[str] = None,
) -> RegenerationTarget:
    """Insert one lesson's target. The caller must be ready for an
    ``IntegrityError``: ``uq_regeneration_targets_active_lineage`` is what stops
    two campaigns owning the same (lesson, language) at once, and it fires here.

    ``phase_plan`` is the serialized object produced by
    ``app.services.regeneration_planner.RegenerationPhasePlan.to_json()`` — the
    planner is the only producer, and ``from_json`` the only reader. This
    repository deliberately does no serialization of its own, so there is
    exactly one definition of the stored shape.

    The five reviewed-destination arguments default to None together, which is
    the legal "no decision recorded" shape historical and internal callers
    produce; any PARTIAL combination is refused before the row is built. There
    is deliberately no later setter — the destination is what the operator
    approved, so it is written once here and never moved."""
    _validate_reviewed_destination(
        notion_container_policy=notion_container_policy,
        reviewed_notion_container_page_id=reviewed_notion_container_page_id,
        notion_parent_policy=notion_parent_policy,
        reviewed_notion_lesson_page_id=reviewed_notion_lesson_page_id,
        reviewed_notion_lesson_title=reviewed_notion_lesson_title,
    )
    target = RegenerationTarget(
        campaign_id=campaign_id,
        toc_entry_id=toc_entry_id,
        output_language=output_language,
        phase_plan=phase_plan,
        source_job_id=source_job_id,
        is_canary=is_canary,
        status=status,
        notion_container_policy=notion_container_policy,
        reviewed_notion_container_page_id=reviewed_notion_container_page_id,
        notion_parent_policy=notion_parent_policy,
        reviewed_notion_lesson_page_id=reviewed_notion_lesson_page_id,
        reviewed_notion_lesson_title=reviewed_notion_lesson_title,
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


async def canary_statuses_for_campaign(
    session: AsyncSession, campaign_id: UUID, *, for_update: bool = False
) -> list[str]:
    """Status of only the canary rows used by the human approval gate.

    The first approval transaction needs to fence canary state while it stamps
    the campaign, but it does not need to join/order/lock every bulk target.
    The later release transaction deliberately loads the full campaign.
    """
    stmt = (
        select(RegenerationTarget.status)
        .where(
            RegenerationTarget.campaign_id == campaign_id,
            RegenerationTarget.is_canary.is_(True),
        )
        .order_by(RegenerationTarget.id)
    )
    if for_update:
        stmt = stmt.with_for_update(of=RegenerationTarget)
    result = await session.execute(stmt)
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
    clear_publication_next_attempt: bool = False,
    clear_terminal_reason: bool = False,
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

    ``clear_publication_next_attempt=True`` is the operator-only HALF of the backoff
    clear: it drops ``publication_next_attempt_at`` while KEEPING the error text
    that explains why. Parking an exhausted (or collided) delivery needs exactly
    that shape — the publisher's claim sweep treats a due-or-past
    ``publication_next_attempt_at`` on a ``publication_failed`` row as "retry
    now", so the timestamp that made this attempt claimable has to go or the
    same failing delivery loops forever; and ``clear_publication_backoff`` would
    additionally erase the reason the operator needs to read.

    ``clear_terminal_reason=True`` is the fourth of these explicit clears, and
    it is applied AUTOMATICALLY on the one transition that always needs it: a
    re-drive out of ``generation_failed`` back into ``generating``.
    ``terminal_reason`` is the target's only free-text column, so a creation
    failure ("revision job could not be created: ...") is written there while
    the row is NOT terminal. Left in place, a retry that then SUCCEEDS carries
    that stale sentence all the way to ``published`` and the operator report
    describes a delivered revision as a failed one. Applied here rather than at
    the call sites because there are two of them — the operator retry and the
    job reconciler — and both must behave identically. Passing an explicit
    ``terminal_reason`` alongside wins (the caller is replacing the text, not
    dropping it).

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
    if clear_publication_next_attempt:
        values["publication_next_attempt_at"] = None
    redrive = (
        new_status == "generating"
        and "generation_failed" in set(expected_statuses)
    )
    if (clear_terminal_reason or redrive) and terminal_reason is None:
        values["terminal_reason"] = None

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


async def lock_owning_campaign(
    session: AsyncSession, *, campaign_id: UUID, skip_locked: bool = False
) -> Optional[RegenerationCampaign]:
    """Take the PARENT lock before touching a target, and hand back the row.

    ``FOR KEY SHARE`` deliberately, not ``FOR UPDATE``: it is exactly the lock
    ``trg_regeneration_targets_publication_gate`` takes from inside the target
    UPDATE, so holding it first means the trigger's own acquisition is already
    satisfied and the publisher never reaches for the campaign while holding
    the target. It also conflicts with the ``FOR UPDATE`` every campaign action
    takes, so an operator cancel cannot slip between this read and the write it
    guards.

    ``skip_locked=True`` returns None instead of waiting when a campaign action
    already holds the row ``FOR UPDATE``. That is what makes the CLAIM sweep
    wait-free: a publisher that never waits for a lock can never be part of a
    deadlock cycle, and a campaign that is busy is simply re-scanned next tick.
    Resolution of an ALREADY-claimed target uses ``skip_locked=False`` — by then
    the remote write has happened and the outcome has to land, and waiting is
    safe because no target lock is held yet.
    """
    return await session.scalar(
        select(RegenerationCampaign)
        .where(RegenerationCampaign.id == campaign_id)
        .with_for_update(read=True, key_share=True, skip_locked=skip_locked)
        .execution_options(populate_existing=True)
    )


def _campaign_may_publish(campaign: Optional[RegenerationCampaign]) -> bool:
    """The SAME predicate `trg_regeneration_targets_publication_gate` enforces.

    Stated here so the claim sweep never selects a row the trigger would then
    RAISE on — an exception from inside the claim aborts the whole sweep
    transaction, so this is a correctness guard, not an optimisation.
    """
    return (
        campaign is not None
        and campaign.approved_at is not None
        and campaign.status not in ("rejected", "cancelled")
    )


def _claimable(target: RegenerationTarget, *, now: datetime, lease_cutoff: datetime) -> bool:
    """Is this target releasable to a publisher right now?

    Three disjoint cases, and the middle one is the subtle one:

    * ``publication_pending`` — released work nobody holds;
    * ``publication_failed`` — retry-due ONLY. ``publication_next_attempt_at``
      must be set AND due. A NULL there means the automatic budget is spent (or
      the failure was a page collision): operator-only, never auto-claimed. A
      NULL read as "due now" would loop a permanently failing delivery forever;
    * ``publishing`` — a dead publisher's row, reclaimable once its lease ends.
      ``publishing`` has to be claimable or nothing would ever move a crashed
      delivery.
    """
    if target.status == "publication_pending":
        return True
    if target.status == "publication_failed":
        return (
            target.publication_next_attempt_at is not None
            and target.publication_next_attempt_at <= now
        )
    if target.status == "publishing":
        return (
            target.publication_claimed_at is None
            or target.publication_claimed_at <= lease_cutoff
        )
    return False


async def claim_next_publication(
    session: AsyncSession, *, now: datetime, lease_seconds: int
) -> Optional[ClaimedRegenerationTarget]:
    """Claim one releasable publication, or return None when there is none.

    The claim is durable: it lives on the row (token + timestamp + attempts),
    so a publisher that dies mid-delivery keeps its target until the lease
    expires and only then may a peer take it over.

    **Lock protocol (parent → child, wait-free).** A candidate scan reads
    without locks, then for each candidate the OWNING CAMPAIGN is locked
    ``FOR KEY SHARE ... SKIP LOCKED`` before the target is locked
    ``FOR UPDATE SKIP LOCKED``. Reaching the target first — which is what the
    older `claim_target_publication` does — inverts the order every campaign
    action takes, because the publication-gate trigger then reaches BACK to the
    campaign for its own ``FOR KEY SHARE``; a concurrent cancel holding the
    campaign and waiting for the target closes the cycle. Skipping (rather than
    waiting for) both locks means this sweep never waits on anything, and a
    transaction that never waits can never be the victim OR the cause of a
    deadlock.

    **Attempts.** Incremented on the CLAIM, not on the outcome, so a
    crash-looping publisher still exhausts its budget. The counter RESTARTS at
    1 for a ``publication_pending`` row carrying no outstanding failure — a
    freshly released target (already 0) or one an operator explicitly retried.
    Task 7's `retry_publication` deliberately preserves the cumulative count
    when it clears the backoff, so without this restart an operator retry after
    exhaustion would buy zero real attempts. The restart is scoped to
    ``publication_pending`` + ``publication_last_error IS NULL``: a retry-due
    ``publication_failed`` row always carries its error and keeps counting.
    """
    lease_cutoff = now - timedelta(seconds=lease_seconds)
    candidates = await session.execute(
        select(RegenerationTarget.id, RegenerationTarget.campaign_id)
        .join(
            RegenerationCampaign,
            RegenerationCampaign.id == RegenerationTarget.campaign_id,
        )
        .where(
            RegenerationCampaign.approved_at.is_not(None),
            RegenerationCampaign.status.not_in(("rejected", "cancelled")),
            or_(
                RegenerationTarget.status == "publication_pending",
                and_(
                    RegenerationTarget.status == "publication_failed",
                    RegenerationTarget.publication_next_attempt_at.is_not(None),
                    RegenerationTarget.publication_next_attempt_at <= now,
                ),
                and_(
                    RegenerationTarget.status == "publishing",
                    or_(
                        RegenerationTarget.publication_claimed_at.is_(None),
                        RegenerationTarget.publication_claimed_at <= lease_cutoff,
                    ),
                ),
            ),
        )
        # Oldest release first, then id: deterministic, so two publishers walk
        # the same list and `SKIP LOCKED` spreads them across it instead of
        # both retrying the same head row.
        .order_by(RegenerationTarget.publication_released_at, RegenerationTarget.id)
        .limit(_CLAIM_SCAN_LIMIT)
    )
    for target_id, campaign_id in candidates.all():
        campaign = await lock_owning_campaign(
            session, campaign_id=campaign_id, skip_locked=True
        )
        if not _campaign_may_publish(campaign):
            # Either a campaign action holds it (skip and re-scan next tick) or
            # approval was withdrawn between the scan and the lock.
            continue
        target = await session.scalar(
            select(RegenerationTarget)
            .where(RegenerationTarget.id == target_id)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
        if target is None or not _claimable(target, now=now, lease_cutoff=lease_cutoff):
            continue

        fresh_cycle = (
            target.status == "publication_pending"
            and target.publication_last_error is None
        )
        attempts = 1 if fresh_cycle else target.publication_attempts + 1
        claim_token = uuid4()
        result = await session.execute(
            update(RegenerationTarget)
            .where(RegenerationTarget.id == target_id)
            .values(
                status="publishing",
                publication_claim_token=claim_token,
                publication_claimed_at=now,
                publication_attempts=attempts,
                # Only ever FILLS a null: `ck_regeneration_targets_publication_
                # released` demands a stamp for `publishing`, and re-stamping
                # would restart the publication clock on a reserved version.
                publication_released_at=func.coalesce(
                    RegenerationTarget.publication_released_at, now
                ),
                updated_at=now,
            )
        )
        if result.rowcount != 1:  # pragma: no cover - we hold the row lock
            continue
        return ClaimedRegenerationTarget(
            target_id=target_id,
            campaign_id=campaign_id,
            toc_entry_id=target.toc_entry_id,
            output_language=target.output_language,
            claim_token=claim_token,
            publication_attempts=attempts,
            publication_version=target.publication_version,
            notion_page_id=target.notion_page_id,
            abandon_requested_at=target.abandon_requested_at,
        )
    return None


async def reserve_publication_version(
    session: AsyncSession, *, target_id: UUID, claim_token: UUID
) -> int:
    """Reserve (or re-read) this target's immutable publication version.

    Returns an already-reserved number UNCHANGED — every retry of one delivery
    publishes the same `Homework V{n}` page.

    Otherwise the number is the OWNING CAMPAIGN's declared
    ``publication_version``, exactly: the operator approved that number and
    reviewed a destination for it, so falling forward to another one would put
    reviewed content behind a title nobody signed off. Only a campaign that
    declares none — a historical draft, and every internal caller until Task 6
    makes the field mandatory — falls back to the old
    ``max(existing version for this lineage, 1) + 1``, whose first number is 2:
    logical V1 is the pre-existing `Homework` page and has no row here.

    Serialised with a transaction-scoped advisory lock on
    ``(toc_entry_id, output_language)``, taken BEFORE the target row lock and
    after the caller's campaign lock, preserving the one global order. The
    advisory lock is what makes the read-then-write safe; the partial unique
    index ``uq_regeneration_targets_publication_version`` remains the final
    fence, and a consumed number is never cleared or reused — not by a failed
    delivery, not by a cancellation, not by a later campaign.

    Raises :class:`StalePublicationClaim` when the lease was taken over: a
    publisher whose claim is gone must not reserve a number it will never use.
    Raises :class:`PublicationVersionUnavailable` when the declared number is
    already spent, or when the row somehow already holds a DIFFERENT one — both
    are immutable, so neither can be reconciled without a human.
    """
    lineage = (
        await session.execute(
            select(
                RegenerationTarget.toc_entry_id, RegenerationTarget.output_language
            ).where(RegenerationTarget.id == target_id)
        )
    ).first()
    if lineage is None:
        raise StalePublicationClaim(f"regeneration target {target_id} not found")
    toc_entry_id, output_language = lineage

    await session.execute(
        text("SELECT pg_advisory_xact_lock(:ns, :key)"),
        {
            "ns": _VERSION_LOCK_NAMESPACE,
            "key": _publication_lineage_lock_key(toc_entry_id, output_language),
        },
    )

    target = await session.scalar(
        select(RegenerationTarget)
        .where(RegenerationTarget.id == target_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if target is None or target.publication_claim_token != claim_token:
        raise StalePublicationClaim(
            f"regeneration target {target_id}: publication claim {claim_token} is "
            "no longer current — refusing to reserve a version"
        )

    # A PLAIN read, not `lock_owning_campaign`. The one global lock order is
    # campaign -> advisory(lineage) -> target row, and this point is already
    # past the first two; taking the campaign lock here would invert it for any
    # caller that does not already hold it, against a trigger
    # (`trg_regeneration_targets_publication_gate`) that takes `FOR KEY SHARE`
    # on the campaign from INSIDE a target UPDATE. No lock is needed anyway:
    # `publication_version` is written once, at campaign insert, and never
    # updated.
    requested = await session.scalar(
        select(RegenerationCampaign.publication_version).where(
            RegenerationCampaign.id == target.campaign_id
        )
    )

    if target.publication_version is not None:
        if requested is not None and target.publication_version != requested:
            raise PublicationVersionUnavailable(
                f"regeneration target {target_id}: reserved version "
                f"V{target.publication_version} differs from the campaign's "
                f"declared V{requested} — both are immutable"
            )
        return target.publication_version

    if requested is None:
        highest = await session.scalar(
            select(func.max(RegenerationTarget.publication_version)).where(
                RegenerationTarget.toc_entry_id == toc_entry_id,
                RegenerationTarget.output_language == output_language,
            )
        )
        requested = max(highest or 1, 1) + 1  # historical campaign compatibility

    # Under the advisory lock, so this read cannot be raced by another
    # reservation of the same lineage; the partial unique index remains the
    # final fence for anything that reaches the UPDATE another way.
    conflict = await session.scalar(
        select(RegenerationTarget.id).where(
            RegenerationTarget.toc_entry_id == toc_entry_id,
            RegenerationTarget.output_language == output_language,
            RegenerationTarget.publication_version == requested,
            RegenerationTarget.id != target.id,
        )
    )
    if conflict is not None:
        raise PublicationVersionUnavailable(
            f"Homework V{requested} is already consumed for this lesson and "
            f"language (regeneration target {conflict})"
        )

    version = requested
    result = await session.execute(
        update(RegenerationTarget)
        .where(
            RegenerationTarget.id == target_id,
            RegenerationTarget.publication_claim_token == claim_token,
            RegenerationTarget.publication_version.is_(None),
        )
        .values(publication_version=version, updated_at=func.now())
    )
    if result.rowcount != 1:  # pragma: no cover - the row lock makes this dead
        raise StalePublicationClaim(
            f"regeneration target {target_id}: version reservation lost a race"
        )
    return version
