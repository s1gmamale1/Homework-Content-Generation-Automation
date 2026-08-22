"""The durable publisher for regenerated homework: `Homework V{n}` in Notion.

One head-side loop turns released `regeneration_targets` into immutable
versioned Notion sibling pages. It is deliberately narrow — it publishes
regeneration targets and nothing else, and it never touches V1 or the legacy
archive's columns.

Seven rules run through the whole module.

**Nothing starts without a destination.** `publication_unavailable_reason` is
the deployment-level answer to "can anything be published here at all?", and it
is checked at the TOP of `run_once`, before the claim. A version number is spent
forever once reserved, and the credential is not needed until `_deliver` — one
step too late — so a head with the regeneration flags on but no usable Notion
destination would otherwise claim targets and burn their `Homework V{n}`
identities on deliveries that could never happen. `main.lifespan` uses the same
function to decline to start the loop, and the API to refuse approval before any
target is released; all three read this one function so they cannot disagree.

**The claim is durable, and it is the unit of work.** `claim_next_publication`
moves one target to ``publishing``, stamps a UUID lease and increments the
attempt counter. Everything after that is fenced on that token: a publisher
whose lease expired and was taken over writes nothing, so a slow delivery can
never overwrite its successor's outcome. A crash simply leaves the row
``publishing`` until the lease elapses and a peer reclaims it.

**Notion never runs on the event loop.** The DB session is CLOSED before any
remote call, every scalar the remote work needs is copied into
:class:`PublicationInputs` first, and both remote steps run inside
``asyncio.to_thread`` — including construction of the client itself, because
`NotionClientWrapper.__init__` builds an HTTP client. A detached ORM row would
re-open the door (lazy attribute loads are database I/O), which is why the
claim and the inputs are plain frozen value objects.

**A version is spent forever.** `reserve_publication_version` allocates once per
target and every retry reuses it, so a failed delivery, a cancellation, or a
process crash all leave the same `Homework V{n}` identity to resume onto. The
page is then found by MARKER, not by title, so a retry adopts its own half
written page and refuses anything it cannot prove is ours.

**The abandon intent is decided at resolution, not at claim time.** A
cancellation can land at any point, including while the Notion request is in
flight with an unknown outcome. So: a remote write that SUCCEEDED always lands
``published`` (the page exists; reporting it abandoned would be a lie), and a
remote write that FAILED under an abandon intent lands terminal ``abandoned``
with the reserved version preserved, which is what lets the campaign roll up.
The intent is re-read inside the fenced resolution transaction, never trusted
from the claim snapshot.

**A reviewed lineage id outranks mutable filing configuration.** A lineage is
`(lesson, output_language)`. Preflight may freeze an exact container + Lesson
Topic recovered from the V1 Homework page only after the database proves that
the archive stamp belongs to this same lineage. Publication then revalidates
the immutable Lesson Topic -> container edge and uses it even if titles or
`NOTION_SUBJECT_PAGES` changed after review. Unreviewed historical targets and
container-creation decisions still require the configured language subject
page. This keeps cross-language pointers fail-closed without re-filing a proven
legacy lesson merely because mutable organization drifted.

**Lock order is parent then child, always.** Campaign-level actions take
campaign ``FOR UPDATE`` then target ``FOR UPDATE``; the publication-gate trigger
takes campaign ``FOR KEY SHARE`` from inside a target UPDATE. Every transaction
here therefore locks the owning campaign FIRST (see
`regeneration_targets.lock_owning_campaign`) — wait-free while CLAIMING, and
waiting during RESOLUTION, where the outcome has to land and no target lock is
held yet.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta
from typing import Callable, Literal, Mapping, Optional, Union
from uuid import UUID

from loguru import logger
from notion_client.errors import APIErrorCode, APIResponseError
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.db import SessionLocal
from app.models.base import _utcnow
from app.repositories import books as books_repo
from app.repositories import phase_outputs as phase_repo
from app.repositories import regeneration_targets as targets_repo
from app.repositories import toc_entries as toc_repo
from app.services import notion_archive, regeneration_job_state
from app.services.notion.client import NotionClientWrapper
from app.services.notion.page_creator import _normalize
from app.services.notion_versioned_homework import (
    HomeworkRevisionMarker,
    VersionPageCollision,
    version_page_title,
    write_or_adopt_versioned_homework,
)
from app.services.regeneration_campaign import RegenerationCampaignService
from app.services.regeneration_notion_readiness import publication_unavailable_reason
from app.services.regeneration_planner import validate_complete_snapshot

__all__ = [
    "PublicationInputs",
    "RegenerationPublisher",
    "build_publisher_from_settings",
    "publication_unavailable_reason",
]

# Written to `terminal_reason` on a successful delivery. `terminal_reason` is
# the target's only free-text column, so it has to say something true for BOTH
# terminal outcomes rather than only describing failures.
_PUBLISHED_REASON = "published as {title}"
# Fallback when an abandon intent carries no reason of its own.
_ABANDONED_REASON = (
    "abandoned before delivery: cancellation was requested and the Notion write "
    "did not succeed"
)
# 2**30 seconds already dwarfs `backoff_max_seconds`; clamping the exponent
# keeps a pathological attempt count from building a giant int for nothing.
_MAX_BACKOFF_SHIFT = 30


@dataclass(frozen=True)
class PublicationInputs:
    """Every scalar the remote work needs, copied out while the session is open.

    Frozen and flat on purpose: the publisher closes its DB session before the
    first Notion call, and an ORM row carried across that boundary turns a
    remote-I/O path into surprise database access on a closed session.

    ``legacy_lesson_page_id`` is named for what it is: the shared, language-blind
    `toc_entries.notion_lesson_page_id`. It is a HINT, never the parent — see
    `_resolve_lesson_parent`. The name is the guard; a field called
    `lesson_page_id` invites the next reader to pass it straight to the writer.
    """

    target_id: UUID
    campaign_id: UUID
    toc_entry_id: UUID
    output_language: str
    claim_token: UUID
    publication_version: int
    subject_page_id: Optional[str]
    lesson_title: str
    legacy_lesson_page_id: Optional[str]
    stored_version_page_id: Optional[str]
    revision_job_id: UUID
    phase_md: Mapping[str, str]
    notion_container_policy: Optional[Literal["reuse", "create"]] = None
    reviewed_container_page_id: Optional[str] = None
    notion_parent_policy: Optional[Literal["reuse", "create"]] = None
    reviewed_lesson_page_id: Optional[str] = None
    reviewed_lesson_title: Optional[str] = None

    @property
    def marker(self) -> HomeworkRevisionMarker:
        return HomeworkRevisionMarker(
            toc_entry_id=self.toc_entry_id,
            output_language=self.output_language,
            revision_job_id=self.revision_job_id,
            campaign_id=self.campaign_id,
            publication_version=self.publication_version,
        )


@dataclass(frozen=True)
class _Refusal:
    """A reason this claim cannot be delivered, decided before any remote call.

    ``retryable`` is about whether ANOTHER automatic attempt could plausibly
    change the answer. A missing Notion mapping, an incomplete snapshot or a
    withdrawn approval cannot, so they park for an operator immediately instead
    of burning the budget.
    """

    reason: str
    retryable: bool = False


class _StaleClaim(RuntimeError):
    """This publisher no longer owns the target; it must write nothing."""


class ReviewedDestinationChanged(RuntimeError):
    """The Notion tree no longer matches the destination approved in review."""


#: The partial unique index that makes a publication version consumed forever.
_VERSION_INDEX = "uq_regeneration_targets_publication_version"

#: The DIAGNOSTIC tail of a version refusal, kept out of the sentence the
#: operator is meant to act on — an index identifier names no remedy. It stays
#: in the message rather than only in the log because
#: `publication_last_error` is also where an engineer looks to see which fence
#: actually fired.
_VERSION_INDEX_NOTE = f" (diagnostic: {_VERSION_INDEX})"


def _is_version_collision(exc: IntegrityError) -> bool:
    """Is this integrity error the version index, and only that one?

    A reported ``constraint_name`` is TRUSTED over the message: matching on the
    text alone would misfire on generated content that happens to quote an
    index name, and a constraint that names itself as something else is by
    definition not this one.

    It has to be looked for in TWO places, because the driver this runs on does
    not put it where the obvious lookup finds it. SQLAlchemy's asyncpg dialect
    (``_handle_exception``) builds a FRESH
    ``AsyncAdapt_asyncpg_dbapi.IntegrityError`` out of a message string, copies
    only ``pgcode``/``sqlstate`` onto it, and re-raises it ``from`` the original
    — so on ``transport=api``'s asyncpg engine ``exc.orig`` has NO
    ``constraint_name`` attribute at all and the real name survives only on
    ``exc.orig.__cause__``. ``exc.orig`` is still read FIRST so a psycopg-style
    driver, which does report the name there, keeps working unchanged.

    Both lookups are guarded on ``is not None`` rather than on presence,
    because asyncpg reports ``constraint_name = None`` for a NOT NULL violation
    (SQLSTATE 23502): treating that as "named, and not us" would be right by
    luck here, but it is not a name and must fall through.

    The text check is the last resort, for a driver that reports no name in
    either place.
    """
    orig = getattr(exc, "orig", None)
    constraint = getattr(orig, "constraint_name", None)
    if constraint is None:
        constraint = getattr(
            getattr(orig, "__cause__", None), "constraint_name", None
        )
    if constraint is not None:
        return constraint == _VERSION_INDEX
    return _VERSION_INDEX in str(exc)


# Both ways a takeover surfaces: detected by this module's own token compares,
# or raised by the repository's fenced version reservation. Neither is a
# delivery failure — writing an outcome would land it on the NEW owner's row —
# so both are caught together and logged as the ordinary lease handover they
# are, not as an unexpected error.
_TAKEOVER = (_StaleClaim, targets_repo.StalePublicationClaim)


def _validated_reviewed_destination(target) -> Optional[tuple[
    Literal["reuse", "create"], Optional[str],
    Literal["reuse", "create"], Optional[str], str,
]]:
    """Return one executable reviewed decision, or reject a partial/corrupt row.

    The database CHECK prevents these shapes in ordinary writes, but the
    publisher is the final safety boundary.  It must also handle historical
    rows, manual repairs, and whitespace values without consuming a version or
    making a Notion call.
    """
    container_policy = getattr(target, "notion_container_policy", None)
    container_id = getattr(target, "reviewed_notion_container_page_id", None)
    parent_policy = getattr(target, "notion_parent_policy", None)
    lesson_id = getattr(target, "reviewed_notion_lesson_page_id", None)
    lesson_title = getattr(target, "reviewed_notion_lesson_title", None)
    values = (container_policy, container_id, parent_policy, lesson_id, lesson_title)
    if all(value is None for value in values):
        return None
    if container_policy not in ("reuse", "create"):
        raise ValueError("destination container policy must be reuse or create")
    if parent_policy not in ("reuse", "create"):
        raise ValueError("destination Lesson Topic policy must be reuse or create")
    if not isinstance(lesson_title, str) or not lesson_title.strip():
        raise ValueError("destination Lesson Topic title must not be blank")

    clean_container_id = (
        container_id.strip() if isinstance(container_id, str) else None
    )
    clean_lesson_id = lesson_id.strip() if isinstance(lesson_id, str) else None
    if container_policy == "reuse":
        if not clean_container_id:
            raise ValueError("destination container reuse needs a nonblank page id")
    elif container_id is not None:
        raise ValueError("destination container create must not carry a page id")

    if parent_policy == "reuse":
        if container_policy != "reuse":
            raise ValueError(
                "destination Lesson Topic reuse requires a reused container"
            )
        if not clean_lesson_id:
            raise ValueError(
                "destination Lesson Topic reuse needs a nonblank page id"
            )
    elif lesson_id is not None:
        raise ValueError("destination Lesson Topic create must not carry a page id")

    return (
        container_policy,
        clean_container_id,
        parent_policy,
        clean_lesson_id,
        lesson_title.strip(),
    )


class RegenerationPublisher:
    """The publication loop. One instance is stateless apart from its config.

    Every collaborator is injectable — sessions, the campaign service, the
    Notion client factory — because the loop's own logic (fencing, backoff,
    abandonment, thread offloading) is what needs testing without a database or
    a network.
    """

    def __init__(
        self,
        *,
        session_factory: Optional[Callable] = None,
        campaign_service=None,
        client_factory: Optional[Callable[[], NotionClientWrapper]] = None,
        interval_seconds: Optional[float] = None,
        lease_seconds: Optional[int] = None,
        max_attempts: Optional[int] = None,
        backoff_base_seconds: Optional[int] = None,
        backoff_max_seconds: Optional[int] = None,
    ) -> None:
        self._sessions = session_factory or SessionLocal
        self._campaigns = campaign_service or RegenerationCampaignService()
        self._client_factory = client_factory or self._default_client
        self._interval = (
            settings.regeneration_publisher_interval_seconds
            if interval_seconds is None else interval_seconds
        )
        self._lease_seconds = (
            settings.regeneration_publisher_lease_seconds
            if lease_seconds is None else lease_seconds
        )
        self._max_attempts = (
            settings.regeneration_publisher_max_attempts
            if max_attempts is None else max_attempts
        )
        self._backoff_base = (
            settings.regeneration_publisher_backoff_base_seconds
            if backoff_base_seconds is None else backoff_base_seconds
        )
        self._backoff_max = (
            settings.regeneration_publisher_backoff_max_seconds
            if backoff_max_seconds is None else backoff_max_seconds
        )
        # `run_forever` would otherwise repeat the same refusal every interval
        # for as long as the process lives.
        self._warned_unavailable = False

    def _warn_unavailable(self, reason: str) -> None:
        """Say it once, loudly. A silently idle publisher reads exactly like an
        empty queue, which is the wrong thing for an operator to conclude when
        approved targets are waiting."""
        if self._warned_unavailable:
            return
        self._warned_unavailable = True
        logger.warning(
            f"regeneration publisher: refusing to publish — {reason}. No target "
            "will be claimed and no version reserved until this is fixed."
        )

    @staticmethod
    def _default_client() -> NotionClientWrapper:
        """Built inside the worker thread by every caller — the constructor
        creates an HTTP client, which is not event-loop work."""
        return NotionClientWrapper(api_key=settings.notion_api_key)

    # ─── the loop ────────────────────────────────────────────────────────

    async def run_forever(self, stop: asyncio.Event) -> None:
        """Sweep until ``stop`` is set. One pass never raises out of here.

        A pass that did work loops straight back (a backlog drains at Notion's
        pace, not at the poll interval); an idle or failed pass waits, and the
        wait is on ``stop`` so shutdown is immediate rather than up to one
        interval late.
        """
        logger.info(
            f"regeneration publisher starting | interval={self._interval}s "
            f"lease={self._lease_seconds}s max_attempts={self._max_attempts}"
        )
        while not stop.is_set():
            did_work = False
            try:
                did_work = await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "regeneration publisher: pass failed — retrying after the "
                    "interval"
                )
            if stop.is_set():
                break
            if did_work:
                await asyncio.sleep(0)
                continue
            try:
                await asyncio.wait_for(stop.wait(), timeout=self._interval)
            except (asyncio.TimeoutError, TimeoutError):
                pass
        logger.info("regeneration publisher stopped")

    async def run_once(self) -> bool:
        """Reconcile, then deliver at most one publication.

        Returns True when a claim was processed (whatever its outcome), False
        when there was nothing releasable — which is what lets ``run_forever``
        drain a backlog without sleeping between targets.
        """
        # The Notion prerequisite, BEFORE the claim — the last point at which
        # refusing is free. `main.lifespan` already declines to start this loop
        # without a destination, so reaching here means something else built the
        # publisher; it still refuses, because the cost of not doing so is a
        # version number spent on a delivery that never had a chance.
        unavailable = publication_unavailable_reason()
        if unavailable is not None:
            self._warn_unavailable(unavailable)
            return False

        # Crash repair FIRST: a revision job can commit its terminal status and
        # die before its target is updated, and with no API read involved this
        # loop is the only thing that would ever notice.
        async with self._sessions() as session:
            await regeneration_job_state.reconcile_terminal_revision_jobs(session)

        async with self._sessions() as session:
            claim = await targets_repo.claim_next_publication(
                session, now=_utcnow(), lease_seconds=self._lease_seconds
            )
            await session.commit()
        if claim is None:
            return False

        logger.info(
            f"regeneration publisher: claimed target {claim.target_id} "
            f"(campaign {claim.campaign_id}, attempt {claim.publication_attempts})"
        )
        try:
            prepared = await self._prepare(claim)
        except _TAKEOVER as exc:
            logger.info(f"regeneration publisher: {exc}")
            return True
        except Exception as exc:  # noqa: BLE001 - any load error is retryable
            logger.exception(
                f"regeneration publisher: could not prepare target "
                f"{claim.target_id}"
            )
            await self._resolve_failure(
                claim, f"preparation failed: {type(exc).__name__}: {exc}",
                retryable=True,
            )
            return True

        if isinstance(prepared, _Refusal):
            logger.warning(
                f"regeneration publisher: target {claim.target_id} refused — "
                f"{prepared.reason}"
            )
            await self._resolve_failure(
                claim, prepared.reason, retryable=prepared.retryable
            )
            return True

        try:
            page_id = await self._deliver(claim, prepared)
        except _TAKEOVER as exc:
            logger.info(f"regeneration publisher: {exc}")
            return True
        except VersionPageCollision as exc:
            # Retrying cannot change the answer: a page we cannot prove is ours
            # is a human decision, never an automatic overwrite.
            await self._resolve_failure(
                claim, f"version page collision: {exc}", retryable=False
            )
            return True
        except ReviewedDestinationChanged as exc:
            # The version was reserved before remote delivery began and remains
            # consumed.  Following a moved/duplicated page would publish to a
            # destination the operator never approved, while retrying the same
            # frozen decision cannot repair the Notion tree.
            await self._resolve_failure(
                claim,
                "reviewed Notion destination changed after "
                f"{version_page_title(prepared.publication_version)} was reserved "
                f"and remains consumed: {exc}",
                retryable=False,
            )
            return True
        except Exception as exc:  # noqa: BLE001 - transient remote failure
            logger.warning(
                f"regeneration publisher: delivery of target {claim.target_id} "
                f"failed: {type(exc).__name__}: {exc}"
            )
            await self._resolve_failure(
                claim, f"{type(exc).__name__}: {exc}", retryable=True
            )
            return True

        await self._resolve_published(claim, prepared, page_id)
        return True

    # ─── step 1: reload, validate, reserve, copy ─────────────────────────

    async def _prepare(
        self, claim: targets_repo.ClaimedRegenerationTarget
    ) -> Union[PublicationInputs, _Refusal]:
        """One transaction: revalidate everything, reserve the version, and copy
        out every scalar the remote steps need. The session is closed on return.

        The destination is revalidated here rather than trusted from the
        campaign preflight because configuration can change in between — and it
        is validated BEFORE the version is reserved, so a mis-configured lesson
        does not permanently consume a version number.
        """
        async with self._sessions() as session:
            try:
                campaign = await targets_repo.lock_owning_campaign(
                    session, campaign_id=claim.campaign_id
                )
                if (campaign is None or campaign.approved_at is None
                        or campaign.status in ("rejected", "cancelled")):
                    return _Refusal(
                        f"owning campaign {claim.campaign_id} is no longer approved "
                        "— refusing to publish"
                    )
                target = await targets_repo.get_target_for_update(
                    session, claim.target_id
                )
                if (target is None
                        or target.publication_claim_token != claim.claim_token):
                    raise _StaleClaim(
                        f"target {claim.target_id}: claim {claim.claim_token} is no "
                        "longer current — discarding this pass"
                    )
                if target.abandon_requested_at is not None:
                    # Nothing has been sent yet, so there is no unknown-outcome
                    # request to protect: converge now rather than re-claiming this
                    # row every lease and wedging the campaign's rollup forever.
                    return _Refusal(
                        "abandonment was requested before delivery began",
                        retryable=True,
                    )

                job = await targets_repo.revision_job_for_target(
                    session, target_id=claim.target_id
                )
                if job is None:
                    return _Refusal("the target has no revision job")
                if job.status != "done":
                    return _Refusal(f"the revision job is {job.status!r}, not done")
                if job.output_language != target.output_language:
                    return _Refusal(
                        f"revision job language {job.output_language!r} does not match "
                        f"the target's {target.output_language!r}"
                    )
                rows = await phase_repo.list_for_job(session, job.id)
                validation = validate_complete_snapshot(subject=job.subject, rows=rows)
                if not validation.usable:
                    return _Refusal(
                        "incomplete revision snapshot: "
                        + "; ".join(validation.reasons)
                    )

                book = await books_repo.get(session, job.book_id)
                section = await toc_repo.get(session, claim.toc_entry_id)
                if book is None or section is None:
                    return _Refusal("the book or TOC row is missing")
                try:
                    reviewed_destination = _validated_reviewed_destination(target)
                except ValueError as exc:
                    return _Refusal(
                        f"reviewed Notion destination is not executable: {exc}",
                        retryable=False,
                    )

                subject_page_id = notion_archive._resolve_subject_page_id(
                    settings.notion_subject_pages, job.subject, book.grade,
                    book.original_filename or "", language=target.output_language,
                )
                # A frozen reused container is already an exact destination;
                # mutable subject-page configuration is only needed when the
                # publisher may have to create that container (or when serving
                # a historical target with no reviewed decision at all).
                needs_subject_page = (
                    reviewed_destination is None
                    or reviewed_destination[0] == "create"
                )
                if not subject_page_id and needs_subject_page:
                    return _Refusal(
                        f"no Notion subject page for language={target.output_language} "
                        f"{job.subject}|{book.grade}"
                    )

                version = await targets_repo.reserve_publication_version(
                    session, target_id=claim.target_id, claim_token=claim.claim_token
                )

                if reviewed_destination is None:
                    # Historical targets still derive the title exactly as they
                    # did before reviewed destinations existed.
                    siblings = await toc_repo.titles_for_subject_grade(
                        session, subject=job.subject, grade=book.grade,
                    )
                    lesson_title = notion_archive.resolve_lesson_title(
                        section, siblings
                    )
                    container_policy = None
                    reviewed_container_id = None
                    parent_policy = None
                    reviewed_lesson_id = None
                    reviewed_lesson_title = None
                else:
                    (
                        container_policy,
                        reviewed_container_id,
                        parent_policy,
                        reviewed_lesson_id,
                        reviewed_lesson_title,
                    ) = reviewed_destination
                    # This title is part of what the operator approved.  Do not
                    # re-derive it from mutable sibling rows at delivery time.
                    lesson_title = reviewed_lesson_title
                phase_md = {
                    row.phase_name: (row.output_md or "")
                    for row in rows
                    if row.status == "done"
                    and row.phase_name != "extract"
                    and (row.output_md or "").strip()
                }
                if not notion_archive.layout_groups(phase_md):
                    # Unreachable while the snapshot validated above, and still
                    # checked: the writer would raise `ValueError` on a payload that
                    # renders nothing, and that would look like a transient failure.
                    return _Refusal("the revision renders no homework content")

                inputs = PublicationInputs(
                    target_id=claim.target_id,
                    campaign_id=claim.campaign_id,
                    toc_entry_id=claim.toc_entry_id,
                    output_language=target.output_language,
                    claim_token=claim.claim_token,
                    publication_version=version,
                    subject_page_id=subject_page_id,
                    lesson_title=lesson_title,
                    legacy_lesson_page_id=section.notion_lesson_page_id,
                    stored_version_page_id=target.notion_page_id,
                    revision_job_id=job.id,
                    phase_md=phase_md,
                    notion_container_policy=container_policy,
                    reviewed_container_page_id=reviewed_container_id,
                    notion_parent_policy=parent_policy,
                    reviewed_lesson_page_id=reviewed_lesson_id,
                    reviewed_lesson_title=reviewed_lesson_title,
                )
                await session.commit()
            except targets_repo.PublicationVersionUnavailable as exc:
                # Not a lease handover (`_TAKEOVER` would discard it) and not a
                # transient fault: a consumed number cannot be freed by trying
                # again, so this parks for an operator on the first attempt.
                await session.rollback()
                return _Refusal(str(exc), retryable=False)
            except IntegrityError as exc:
                # The partial unique index is the allocator's final fence and it
                # fires at the UPDATE inside the reservation, not only at the
                # commit above — which is why the whole preparation is wrapped.
                # The rollback is explicit because this session may not be
                # ours to discard: `session_factory` is injectable, and a
                # caller that hands in a SHARED session would be left holding a
                # poisoned transaction (the test harness does exactly that, and
                # pins `rollbacks == 1`). It is NOT what protects the outcome
                # write — that runs in `_resolve_failure`'s own fresh session,
                # and closing this one rolls it back anyway.
                await session.rollback()
                if not _is_version_collision(exc):
                    # Any other constraint is a database defect. Hiding it
                    # behind a version message would tell an operator to pick
                    # another number for a fault no number can fix.
                    raise
                # `campaign` is bound here: the only statements in this
                # block that can violate a constraint are the reservation's
                # UPDATE and the trailing commit, both after it is loaded.
                declared = campaign.publication_version
                if declared is not None:
                    refusal = (
                        f"Homework V{declared} is already consumed for this "
                        "lesson and language, and a campaign's declared "
                        "version is immutable — retrying this delivery "
                        "reserves the same number and parks again; publish "
                        "this lesson from a new campaign at a different "
                        "version, or abandon this target"
                    )
                else:
                    # A campaign declaring no version allocates
                    # `max(existing, 1) + 1` instead, so the number that
                    # collided is not knowable from the campaign — and the next
                    # allocation DOES see the row that took it. Hence no
                    # "retrying cannot help" here: that is true of a declared
                    # version, not of this one.
                    refusal = (
                        "the publication version reserved for this lesson and "
                        "language is already consumed; this campaign declares "
                        "no version, so a retry allocates the next free number "
                        "instead"
                    )
                return _Refusal(refusal + _VERSION_INDEX_NOTE, retryable=False)
        return inputs

    # ─── step 2: the two remote steps, both off the event loop ───────────

    async def _deliver(
        self, claim: targets_repo.ClaimedRegenerationTarget, inputs: PublicationInputs
    ) -> str:
        # One thread call for the whole parent resolution, and it runs on EVERY
        # delivery — a stored pointer cannot be taken as the parent without
        # first proving it belongs to this target's language tree. A crash
        # between this and the stamp below is safe: the same resolution is
        # recomputed next time and adopts the page it made rather than minting a
        # second one.
        lesson_page_id = await asyncio.to_thread(
            self._resolve_lesson_parent, inputs
        )
        if inputs.legacy_lesson_page_id is None:
            await self._stamp_lesson_page(claim, lesson_page_id)
        return await asyncio.to_thread(
            self._write_version_page, inputs, lesson_page_id
        )

    def _resolve_lesson_parent(self, inputs: PublicationInputs) -> str:
        """Execute the frozen destination, or derive the historical one.

        Adopts the container and Lesson Topic the legacy archive and the teacher
        deck already share — a revision is a sibling INSIDE that lesson, not a
        parallel tree.

        Reviewed reuse ids were lineage-proven before the campaign froze them;
        revalidate only their immutable Lesson Topic -> container relationship.
        Historical targets still use this language's configured subject tree,
        and its language-blind legacy pointer remains only a membership-checked
        hint on that compatibility path.
        """
        client = self._client_factory()
        if inputs.notion_container_policy is None:
            # Byte-for-byte behavioural compatibility for historical targets:
            # derive/adopt the destination exactly as the old publisher did.
            if not inputs.subject_page_id:
                raise ReviewedDestinationChanged(
                    "the historical destination has no configured subject page"
                )
            container_id, _ = notion_archive.find_or_create(
                client, inputs.subject_page_id, notion_archive.CONTAINER_TITLE
            )
            hint = inputs.legacy_lesson_page_id
            if hint is not None and any(
                page.get("id") == hint
                for page in client.get_child_pages(container_id)
            ):
                return hint
            lesson_id, _ = notion_archive.find_or_create(
                client, container_id, inputs.lesson_title
            )
            return lesson_id

        if inputs.notion_container_policy == "reuse":
            container_id = inputs.reviewed_container_page_id
            if not container_id:
                raise ReviewedDestinationChanged(
                    "the approved container id is missing"
                )
        else:
            if not inputs.subject_page_id:
                raise ReviewedDestinationChanged(
                    "the approved container creation has no configured subject page"
                )
            container_matches = [
                page for page in client.get_child_pages(inputs.subject_page_id)
                if _normalize(str(page.get("title", "")))
                == _normalize(notion_archive.CONTAINER_TITLE)
            ]
            if len(container_matches) > 1:
                raise ReviewedDestinationChanged(
                    "the Generated Homeworks container became ambiguous"
                )
            if container_matches:
                container_id = str(container_matches[0]["id"])
            else:
                container_id = str(client.create_page(
                    inputs.subject_page_id, notion_archive.CONTAINER_TITLE
                )["id"])

        try:
            lesson_children = client.get_child_pages(container_id)
        except APIResponseError as exc:
            if (
                inputs.notion_container_policy == "reuse"
                and exc.code == APIErrorCode.ObjectNotFound
            ):
                raise ReviewedDestinationChanged(
                    "the approved container no longer exists or is inaccessible"
                ) from exc
            raise
        if inputs.notion_parent_policy == "reuse":
            lesson_id = inputs.reviewed_lesson_page_id
            if not lesson_id or not any(
                str(page.get("id")) == lesson_id for page in lesson_children
            ):
                raise ReviewedDestinationChanged(
                    "the approved Lesson Topic is no longer inside the approved "
                    "container"
                )
            return lesson_id

        lesson_matches = [
            page for page in lesson_children
            if _normalize(str(page.get("title", "")))
            == _normalize(inputs.lesson_title)
        ]
        if len(lesson_matches) > 1:
            raise ReviewedDestinationChanged(
                "the approved Lesson Topic title became ambiguous"
            )
        if lesson_matches:
            return str(lesson_matches[0]["id"])
        return str(client.create_page(container_id, inputs.lesson_title)["id"])

    def _write_version_page(
        self, inputs: PublicationInputs, lesson_page_id: str
    ) -> str:
        client = self._client_factory()
        return write_or_adopt_versioned_homework(
            client=client,
            lesson_page_id=lesson_page_id,
            phase_md=inputs.phase_md,
            marker=inputs.marker,
            stored_page_id=inputs.stored_version_page_id,
        )

    async def _stamp_lesson_page(
        self, claim: targets_repo.ClaimedRegenerationTarget, lesson_page_id: str
    ) -> None:
        """Persist ONLY `toc_entries.notion_lesson_page_id`, and only when it is
        absent.

        That column is the one TOC pointer this feature may backfill: it is
        shared with the legacy archive and the teacher deck, so it is filled
        never repointed. Version-page identity lives on
        `RegenerationTarget.notion_page_id` instead.
        """
        async with self._sessions() as session:
            await targets_repo.lock_owning_campaign(
                session, campaign_id=claim.campaign_id
            )
            target = await targets_repo.get_target_for_update(
                session, claim.target_id
            )
            if target is None or target.publication_claim_token != claim.claim_token:
                raise _StaleClaim(
                    f"target {claim.target_id}: claim {claim.claim_token} is no "
                    "longer current — not stamping a lesson page"
                )
            section = await toc_repo.get(session, claim.toc_entry_id)
            if section is not None and section.notion_lesson_page_id is None:
                await toc_repo.set_notion_lesson_page_id(
                    session, claim.toc_entry_id, lesson_page_id
                )
            await session.commit()

    # ─── step 3: fenced resolution ───────────────────────────────────────

    async def _resolve_published(
        self,
        claim: targets_repo.ClaimedRegenerationTarget,
        inputs: PublicationInputs,
        page_id: str,
    ) -> None:
        """Land ``published``. Deliberately blind to the abandon intent: the page
        EXISTS, and a target reported abandoned over a live Notion page would be
        a lie the operator cannot act on. Cancellation converges through the
        campaign rollup instead, which is exactly why `cancel` never writes
        ``cancelled`` while a target is still `publishing`."""
        async with self._sessions() as session:
            await targets_repo.lock_owning_campaign(
                session, campaign_id=claim.campaign_id
            )
            moved = await targets_repo.set_target_status(
                session,
                target_id=claim.target_id,
                new_status="published",
                expected_statuses=["publishing"],
                expected_claim_token=claim.claim_token,
                notion_page_id=page_id,
                terminal_at=_utcnow(),
                terminal_reason=_PUBLISHED_REASON.format(
                    title=version_page_title(inputs.publication_version)
                ),
                clear_publication_claim=True,
                clear_publication_backoff=True,
            )
            await session.commit()
        if not moved:
            logger.warning(
                f"regeneration publisher: target {claim.target_id} moved out of "
                f"our claim before the published write — page {page_id} stands, "
                "the new owner resolves it"
            )
            return
        logger.info(
            f"regeneration publisher: target {claim.target_id} published as "
            f"{version_page_title(inputs.publication_version)} ({page_id})"
        )
        await self._roll_up(claim.campaign_id)

    async def _resolve_failure(
        self,
        claim: targets_repo.ClaimedRegenerationTarget,
        error: str,
        *,
        retryable: bool,
    ) -> None:
        """Land the non-published outcome, re-reading the abandon intent under
        the lock.

        Three shapes, in this order:

        * an abandon intent (cancellation or an explicit operator abandon) →
          terminal ``abandoned``. No page was written, so nothing is being
          disowned; the reserved version and any page id are left exactly as
          they are, because a consumed version is never reused;
        * a retryable failure with budget left → ``publication_failed`` with
          exponential backoff;
        * anything else → ``publication_failed`` parked for an operator, with
          ``publication_next_attempt_at`` explicitly CLEARED. The row still
          carries the past timestamp that made this attempt claimable, and
          leaving it would put the same failing delivery straight back in the
          sweep's hands.
        """
        async with self._sessions() as session:
            await targets_repo.lock_owning_campaign(
                session, campaign_id=claim.campaign_id
            )
            target = await targets_repo.get_target_for_update(
                session, claim.target_id
            )
            if target is None or target.publication_claim_token != claim.claim_token:
                logger.info(
                    f"regeneration publisher: target {claim.target_id} is no longer "
                    f"ours — discarding outcome ({error})"
                )
                return
            abandoning = target.abandon_requested_at is not None
            if abandoning:
                moved = await targets_repo.set_target_status(
                    session,
                    target_id=claim.target_id,
                    new_status="abandoned",
                    expected_statuses=["publishing"],
                    expected_claim_token=claim.claim_token,
                    terminal_at=_utcnow(),
                    terminal_reason=(
                        target.abandon_requested_reason or _ABANDONED_REASON
                    ),
                    publication_last_error=error,
                    clear_publication_claim=True,
                    clear_publication_next_attempt=True,
                )
            else:
                next_attempt = None
                if retryable and claim.publication_attempts < self._max_attempts:
                    next_attempt = _utcnow() + timedelta(
                        seconds=self._backoff_seconds(claim.publication_attempts)
                    )
                moved = await targets_repo.set_target_status(
                    session,
                    target_id=claim.target_id,
                    new_status="publication_failed",
                    expected_statuses=["publishing"],
                    expected_claim_token=claim.claim_token,
                    publication_last_error=error,
                    publication_next_attempt_at=next_attempt,
                    clear_publication_claim=True,
                    # Both branches clear it; the retry branch immediately writes
                    # its own value over the clear.
                    clear_publication_next_attempt=next_attempt is None,
                )
                if next_attempt is None:
                    logger.warning(
                        f"regeneration publisher: target {claim.target_id} parked "
                        f"in publication_failed for an operator after "
                        f"{claim.publication_attempts} attempt(s): {error}"
                    )
            await session.commit()
        if moved:
            await self._roll_up(claim.campaign_id)

    def _backoff_seconds(self, attempts: int) -> int:
        """Exponential from the base, capped. ``attempts`` is the 1-based number
        of the attempt that just failed, so the first wait is the base itself."""
        shift = min(max(attempts - 1, 0), _MAX_BACKOFF_SHIFT)
        return min(self._backoff_base * (2 ** shift), self._backoff_max)

    async def _roll_up(self, campaign_id: UUID) -> None:
        """Re-derive the campaign status. Guarded: the target's own outcome is
        already committed, and a rollup hiccup must not make a delivered
        publication look like a failed one."""
        try:
            await self._campaigns.roll_up(campaign_id)
        except Exception:  # noqa: BLE001 - reporting only; the target is durable
            logger.exception(
                f"regeneration publisher: rollup of campaign {campaign_id} failed "
                "— the target's own outcome is committed and stands"
            )


def build_publisher_from_settings() -> RegenerationPublisher:
    """Production wiring: real sessions, real campaign service, real client."""
    return RegenerationPublisher()
