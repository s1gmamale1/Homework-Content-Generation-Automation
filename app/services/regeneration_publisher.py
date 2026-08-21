"""The durable publisher for regenerated homework: `Homework V{n}` in Notion.

One head-side loop turns released `regeneration_targets` into immutable
versioned Notion sibling pages. It is deliberately narrow — it publishes
regeneration targets and nothing else, and it never touches V1 or the legacy
archive's columns.

Five rules run through the whole module.

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
from typing import Callable, Mapping, Optional, Union
from uuid import UUID

from loguru import logger

from app.config import settings
from app.db import SessionLocal
from app.models.base import _utcnow
from app.repositories import books as books_repo
from app.repositories import phase_outputs as phase_repo
from app.repositories import regeneration_targets as targets_repo
from app.repositories import toc_entries as toc_repo
from app.services import notion_archive, regeneration_job_state
from app.services.notion.client import NotionClientWrapper
from app.services.notion_versioned_homework import (
    HomeworkRevisionMarker,
    VersionPageCollision,
    version_page_title,
    write_or_adopt_versioned_homework,
)
from app.services.regeneration_campaign import RegenerationCampaignService
from app.services.regeneration_planner import validate_complete_snapshot

__all__ = [
    "PublicationInputs",
    "RegenerationPublisher",
    "build_publisher_from_settings",
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
    """

    target_id: UUID
    campaign_id: UUID
    toc_entry_id: UUID
    output_language: str
    claim_token: UUID
    publication_version: int
    subject_page_id: str
    lesson_title: str
    lesson_page_id: Optional[str]
    stored_version_page_id: Optional[str]
    revision_job_id: UUID
    phase_md: Mapping[str, str]

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


# Both ways a takeover surfaces: detected by this module's own token compares,
# or raised by the repository's fenced version reservation. Neither is a
# delivery failure — writing an outcome would land it on the NEW owner's row —
# so both are caught together and logged as the ordinary lease handover they
# are, not as an unexpected error.
_TAKEOVER = (_StaleClaim, targets_repo.StalePublicationClaim)


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
            if target is None or target.publication_claim_token != claim.claim_token:
                raise _StaleClaim(
                    f"target {claim.target_id}: claim {claim.claim_token} is no "
                    "longer current — discarding this pass"
                )
            if target.abandon_requested_at is not None:
                # Nothing has been sent yet, so there is no unknown-outcome
                # request to protect: converge now rather than re-claiming this
                # row every lease and wedging the campaign's rollup forever.
                return _Refusal(
                    "abandonment was requested before delivery began", retryable=True
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
            subject_page_id = notion_archive._resolve_subject_page_id(
                settings.notion_subject_pages, job.subject, book.grade,
                book.original_filename or "", language=target.output_language,
            )
            if not subject_page_id:
                return _Refusal(
                    f"no Notion subject page for language={target.output_language} "
                    f"{job.subject}|{book.grade}"
                )

            version = await targets_repo.reserve_publication_version(
                session, target_id=claim.target_id, claim_token=claim.claim_token
            )

            # Sibling titles decide whether this lesson's page needs a
            # disambiguating suffix — a read that MUST happen here, because
            # `find_or_create` would otherwise file the revision under another
            # lesson's page.
            siblings = await toc_repo.titles_for_subject_grade(
                session, subject=job.subject, grade=book.grade,
            )
            lesson_title = notion_archive.resolve_lesson_title(section, siblings)
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
                lesson_page_id=section.notion_lesson_page_id,
                stored_version_page_id=target.notion_page_id,
                revision_job_id=job.id,
                phase_md=phase_md,
            )
            await session.commit()
        return inputs

    # ─── step 2: the two remote steps, both off the event loop ───────────

    async def _deliver(
        self, claim: targets_repo.ClaimedRegenerationTarget, inputs: PublicationInputs
    ) -> str:
        lesson_page_id = inputs.lesson_page_id
        if lesson_page_id is None:
            # One thread call for the whole parent resolution. A crash between
            # this and the stamp below is safe: the SAME collision-aware title
            # is recomputed next time and `find_or_create` adopts the page it
            # made rather than minting a second one.
            lesson_page_id = await asyncio.to_thread(
                self._resolve_lesson_parent, inputs
            )
            await self._stamp_lesson_page(claim, lesson_page_id)
        return await asyncio.to_thread(
            self._write_version_page, inputs, lesson_page_id
        )

    def _resolve_lesson_parent(self, inputs: PublicationInputs) -> str:
        """Subject → `Generated Homeworks` → `<lesson title>`, synchronously.

        Adopts the container and Lesson Topic the legacy archive and the teacher
        deck already share — a revision is a sibling INSIDE that lesson, not a
        parallel tree.
        """
        client = self._client_factory()
        container_id, _ = notion_archive.find_or_create(
            client, inputs.subject_page_id, notion_archive.CONTAINER_TITLE
        )
        lesson_id, _ = notion_archive.find_or_create(
            client, container_id, inputs.lesson_title
        )
        return lesson_id

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
