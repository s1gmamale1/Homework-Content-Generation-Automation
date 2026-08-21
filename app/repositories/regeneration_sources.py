"""Read queries behind regeneration source discovery and version numbering.

Module-level async functions taking the session first, like every other
repository here. Three jobs, all read-only:

* find the CANDIDATE lineages an operator may regenerate — a lineage is one
  ``(toc_entry_id, output_language)`` pair, never a bare lesson: one campaign
  may legitimately regenerate the UZ and the RU homework of the same lesson,
  and they carry independent version numbers;
* answer the two AUTHORITATIVE version questions — which published revision is
  the newest source (:func:`latest_published_target`), and which number the
  next publication will consume (:func:`next_expected_version`);
* hand the raw ``phase_outputs`` rows to
  ``regeneration_planner.validate_complete_snapshot``, which is the only
  authority on whether a snapshot is usable. Nothing here grades a snapshot.

It deliberately defines no writes: a campaign's rows are created through
``regeneration_campaigns`` / ``regeneration_targets``, and duplicating an
insert here would give the lane two ways to make the same row.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Optional
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.book import Book
from app.models.homework_job import HomeworkJob
from app.models.phase_output import PhaseOutput
from app.models.regeneration_target import RegenerationTarget
from app.models.toc_entry import TOCEntry

if TYPE_CHECKING:  # pragma: no cover - annotations only
    # `EligibleRegenerationSource` lives in the SERVICE
    # `app.services.regeneration_discovery`, which already imports this module
    # at module scope. Importing it back at runtime would be a circular import
    # and a layering inversion (a repository reaching up into a service), so the
    # import is guarded and `from __future__ import annotations` above keeps the
    # annotation a string that is never evaluated. Nothing below touches
    # anything but the three structural attributes named in the docstrings.
    from app.services.regeneration_discovery import EligibleRegenerationSource

# The deliverable a revision reproduces. `teacher_material` jobs share the
# `homework_jobs` table but run a different flow and have no publication
# lineage, so they are never a regeneration source.
_HOMEWORK_KIND = "homework"


@dataclass(frozen=True)
class LineageCandidate:
    """One ``(toc_entry_id, output_language)`` lineage with everything the
    discovery + Notion-preflight passes need, gathered in ONE query.

    The Notion fields (``grade``, ``book_filename``, the section columns,
    ``notion_lesson_page_id``) are here rather than fetched later because
    preflight must answer "is there a destination for this lesson" for a whole
    selection at once; a per-lesson round trip would turn a 200-lesson campaign
    preflight into 600 queries.

    There is deliberately NO ``subject`` here. Every field above is functionally
    determined by ``toc_entry_id`` (the book and the TOC row are fixed per
    lesson), which is what keeps the DISTINCT below at one row per lineage.
    ``homework_jobs.subject`` is not: it is stamped from the book at launch and
    the book's subject is user-editable, so two ``done`` jobs of one lineage may
    disagree on it and would split the lineage in two. The subject that matters
    is the PICKED source job's — discovery reads it from there.
    """

    toc_entry_id: UUID
    output_language: str
    book_id: UUID
    grade: Optional[str]
    book_filename: str
    section_number: Optional[str]
    section_title: str
    chapter_title: str
    page_start: Optional[int]
    notion_lesson_page_id: Optional[str]
    order_index: int


async def latest_v1_source_job(
    session: AsyncSession, *, toc_entry_id: UUID, output_language: str
) -> Optional[HomeworkJob]:
    """The newest ORDINARY completed homework for this lineage — the V1 source.

    ``revision_of_job_id IS NULL`` is what makes it ordinary: a revision is
    reached through its published target (:func:`latest_published_target`), so
    letting one in here would pick an UNPUBLISHED or abandoned revision as a
    source. ``kind='homework'`` keeps teacher decks out.

    Newest by ``created_at`` (``id`` breaks a tie deterministically), matching
    ``cost.section_prior_api_cost``'s "latest done job for this section" rule.
    """
    return await session.scalar(
        select(HomeworkJob)
        .where(
            HomeworkJob.toc_entry_id == toc_entry_id,
            HomeworkJob.output_language == output_language,
            HomeworkJob.status == "done",
            HomeworkJob.kind == _HOMEWORK_KIND,
            HomeworkJob.revision_of_job_id.is_(None),
        )
        .order_by(HomeworkJob.created_at.desc(), HomeworkJob.id.desc())
        .limit(1)
    )


async def latest_published_target(
    session: AsyncSession,
    *,
    toc_entry_id: UUID,
    output_language: str,
    for_update: bool = False,
) -> Optional[RegenerationTarget]:
    """The highest SUCCESSFULLY published target of this lineage (V2, V3, …).

    ``status='published'`` is the whole point: a target that reserved a version
    and then failed delivery, or was abandoned, must never become the next
    campaign's source — its content was never published, so regenerating from
    it would branch the lineage off a page nobody ever saw.

    ``publication_version IS NOT NULL`` is redundant against the database
    (``ck_regeneration_targets_published_complete`` already requires it on a
    published row) and kept anyway, so the ORDER BY can never sort on NULLs if
    that check is ever relaxed.

    ``for_update=True`` takes a row lock on the row it returns. NO caller
    passes it today — discovery reads this without a lock — and version
    allocation no longer depends on it: a publication version is serialised by
    the advisory lock inside
    :func:`regeneration_targets.reserve_publication_version`, with the partial
    unique index ``uq_regeneration_targets_publication_version`` as the final
    fence.
    """
    stmt = (
        select(RegenerationTarget)
        .where(
            RegenerationTarget.toc_entry_id == toc_entry_id,
            RegenerationTarget.output_language == output_language,
            RegenerationTarget.status == "published",
            RegenerationTarget.publication_version.is_not(None),
        )
        .order_by(RegenerationTarget.publication_version.desc())
        .limit(1)
    )
    if for_update:
        stmt = stmt.with_for_update()
    return await session.scalar(stmt)


async def next_expected_version(
    session: AsyncSession, *, toc_entry_id: UUID, output_language: str
) -> int:
    """The publication number this lineage's next revision will consume.

    ``max(publication_version) + 1`` over every target that has one — NOT over
    published ones. A version is consumed when publication is RELEASED and is
    never reused even if delivery then failed
    (``uq_regeneration_targets_publication_version`` is partial on
    ``publication_version IS NOT NULL``), so counting only successes would
    promise a number the unique index refuses.

    Starts at 2: logical V1 is the existing ``Homework`` page and owns no
    target row.
    """
    highest = await session.scalar(
        select(func.max(RegenerationTarget.publication_version)).where(
            RegenerationTarget.toc_entry_id == toc_entry_id,
            RegenerationTarget.output_language == output_language,
            RegenerationTarget.publication_version.is_not(None),
        )
    )
    return 2 if highest is None else int(highest) + 1


@dataclass(frozen=True)
class VersionConflict:
    """One lesson+language that cannot publish the version the operator asked
    for, in the shape the API renders directly.

    Two reasons, and :attr:`existing_version` means something different in each
    — deliberately, so a UI never has to guess:

    * ``source_not_older`` — the lineage's IMMEDIATE source is already at
      :attr:`existing_version`, which is ``>= requested_version``. A revision
      generated from that source would overwrite the page it was made from.
      :attr:`existing_version` is the source's own
      ``source_publication_version``.
    * ``already_consumed`` — some target of this lineage already holds
      :attr:`requested_version`. A consumed number is never reusable, so here
      :attr:`existing_version` EQUALS :attr:`requested_version`. That is the
      answer, not a bug: the field says which number is taken.
    """

    toc_entry_id: UUID
    output_language: str
    requested_version: int
    reason: Literal["source_not_older", "already_consumed"]
    existing_version: int


async def publication_version_conflicts(
    session: AsyncSession,
    *,
    sources: "Sequence[EligibleRegenerationSource]",
    requested_version: int,
) -> tuple[VersionConflict, ...]:
    """Every reason this exact version cannot be published for these lineages.

    ALL of them, in the selection's own order — the operator sees one list of
    affected lessons instead of discovering them one refusal at a time.

    The consumed read covers targets in EVERY state, terminal and abandoned
    included: a number is spent when it is RESERVED, and
    ``uq_regeneration_targets_publication_version`` is partial on
    ``publication_version IS NOT NULL`` alone. Filtering on ``status`` here
    would promise a number the index then refuses, deep inside publication and
    after the generation spend.

    At most ONE conflict per lineage, ``source_not_older`` first. A source
    already AT the requested number implies that number was consumed too, so
    emitting both would make the blocked-lesson list longer than the selection
    it came from.
    """
    if not sources:
        # A PERFORMANCE guard, not a correctness one: the loop below iterates
        # the same empty `sources` and would return `()` either way. It is here
        # because an empty `or_()` compiles away and leaves only
        # `publication_version == requested_version` — a scan of every lineage
        # sitting at that version, asked on behalf of no lesson at all.
        return ()

    result = await session.execute(
        select(
            RegenerationTarget.toc_entry_id, RegenerationTarget.output_language
        ).where(
            RegenerationTarget.publication_version == requested_version,
            or_(*[
                (RegenerationTarget.toc_entry_id == source.toc_entry_id)
                & (RegenerationTarget.output_language == source.output_language)
                for source in sources
            ]),
        )
    )
    consumed = {(row[0], row[1]) for row in result.all()}

    conflicts: list[VersionConflict] = []
    for source in sources:
        existing = int(source.source_publication_version)
        if existing >= requested_version:
            reason: Literal["source_not_older", "already_consumed"] = (
                "source_not_older"
            )
        elif (source.toc_entry_id, source.output_language) in consumed:
            reason, existing = "already_consumed", requested_version
        else:
            continue
        conflicts.append(
            VersionConflict(
                toc_entry_id=source.toc_entry_id,
                output_language=source.output_language,
                requested_version=requested_version,
                reason=reason,
                existing_version=existing,
            )
        )
    return tuple(conflicts)


async def lock_lineage(
    session: AsyncSession, *, toc_entry_id: UUID, output_language: str
) -> list[RegenerationTarget]:
    """``SELECT … FOR UPDATE`` over every target row of one lineage.

    Blocks any other transaction that touches a target of this
    ``(toc_entry_id, output_language)`` until this one ends, and returns the
    locked rows.

    NO PRODUCTION CALLER TODAY — it is exercised by tests only. This once
    serialised the read-then-write version allocation; lineage version
    allocation no longer goes through it, or through any lineage row lock. A
    publication version is now serialised by the transaction-scoped advisory
    lock inside :func:`regeneration_targets.reserve_publication_version`, with
    the partial unique index ``uq_regeneration_targets_publication_version`` as
    the final fence. Kept as the row-lock primitive for a future caller that
    has to freeze a whole lineage.

    Terminal rows are deliberately INCLUDED — a published target is exactly the
    row that pins the consumed version, and skipping it would leave the number
    unlocked.

    On a lineage with no rows yet this locks nothing (there is nothing to
    lock); the partial unique indexes remain the backstop for that first race.
    """
    result = await session.execute(
        select(RegenerationTarget)
        .where(
            RegenerationTarget.toc_entry_id == toc_entry_id,
            RegenerationTarget.output_language == output_language,
        )
        .with_for_update()
    )
    return list(result.scalars().all())


async def candidate_lineages(
    session: AsyncSession,
    *,
    book_ids: Optional[Collection[UUID]] = None,
    toc_entry_ids: Optional[Collection[UUID]] = None,
    output_languages: Optional[Collection[str]] = None,
    limit: Optional[int] = None,
) -> list[LineageCandidate]:
    """Every lineage that has at least one completed homework, filtered by the
    operator's selection. ``None`` for a filter means "do not filter"; an EMPTY
    collection means "nothing matches" and is honoured as such.

    One row per ``(toc_entry_id, output_language)`` — ``DISTINCT``, because a
    lesson generated twice is still one lineage. Which of those jobs is the
    SOURCE is decided by discovery, not here.

    That guarantee only holds while every OTHER selected column is functionally
    determined by ``toc_entry_id``, so nothing job-varying may join the DISTINCT
    key. ``HomeworkJob.subject`` used to, and split a lineage whose book had
    been re-classified between two runs into two candidates — which prices and
    preflights the lesson twice and then collides on
    ``uq_regeneration_targets_campaign_toc_language``.
    """
    stmt = (
        select(
            HomeworkJob.toc_entry_id,
            HomeworkJob.output_language,
            HomeworkJob.book_id,
            Book.grade,
            Book.original_filename,
            TOCEntry.section_number,
            TOCEntry.section_title,
            TOCEntry.chapter_title,
            TOCEntry.page_start,
            TOCEntry.notion_lesson_page_id,
            TOCEntry.order_index,
        )
        .join(TOCEntry, TOCEntry.id == HomeworkJob.toc_entry_id)
        .join(Book, Book.id == HomeworkJob.book_id)
        .where(
            HomeworkJob.status == "done",
            HomeworkJob.kind == _HOMEWORK_KIND,
        )
        .distinct()
        # order_index is in the select list because Postgres requires every
        # ORDER BY expression of a SELECT DISTINCT to be selected. It is
        # functionally dependent on toc_entry_id, so it cannot split a lineage
        # into two rows.
        .order_by(TOCEntry.order_index, HomeworkJob.output_language)
    )
    if book_ids is not None:
        stmt = stmt.where(HomeworkJob.book_id.in_(list(book_ids)))
    if toc_entry_ids is not None:
        stmt = stmt.where(HomeworkJob.toc_entry_id.in_(list(toc_entry_ids)))
    if output_languages is not None:
        stmt = stmt.where(HomeworkJob.output_language.in_(list(output_languages)))
    if limit is not None:
        stmt = stmt.limit(limit)

    rows = (await session.execute(stmt)).all()
    return [
        LineageCandidate(
            toc_entry_id=row[0],
            output_language=row[1],
            book_id=row[2],
            grade=row[3],
            book_filename=row[4] or "",
            section_number=row[5],
            section_title=row[6] or "",
            chapter_title=row[7] or "",
            page_start=row[8],
            notion_lesson_page_id=row[9],
            order_index=row[10],
        )
        for row in rows
    ]


async def phase_rows_for_jobs(
    session: AsyncSession, job_ids: Sequence[UUID]
) -> dict[UUID, list[PhaseOutput]]:
    """Every ``phase_outputs`` row of the given jobs, grouped by job id.

    The batched form of ``phase_outputs.list_for_job``: discovery grades a
    whole selection at once, and one query per lesson would dominate the call.
    Rows are returned RAW — completeness is
    ``regeneration_planner.validate_complete_snapshot``'s call, and a filter
    here (say, ``status='done'``) would quietly redefine it.
    """
    if not job_ids:
        return {}
    result = await session.execute(
        select(PhaseOutput)
        .where(PhaseOutput.job_id.in_(list(job_ids)))
        .order_by(PhaseOutput.phase_order)
    )
    grouped: dict[UUID, list[PhaseOutput]] = {job_id: [] for job_id in job_ids}
    for row in result.scalars().all():
        grouped.setdefault(row.job_id, []).append(row)
    return grouped


async def lineage_targets_missing_source(
    session: AsyncSession, *, toc_entry_id: UUID, output_language: str
) -> list[UUID]:
    """Target ids of this lineage whose ``source_job_id IS NULL``.

    That exact predicate is the signature of a completed child-first purge:
    ``fk_regeneration_targets_source_job_id`` is ``SET NULL`` while
    ``homework_jobs.revision_of_job_id`` is ``RESTRICT``, so a source can only
    be deleted once its revision job already is — a null link therefore means
    the row kept its version and its Notion page id but has NO snapshot behind
    it, and no job to regenerate from.

    Returned as ids (not rows) because the caller only refuses on it; it never
    reads the rest of the row.
    """
    result = await session.execute(
        select(RegenerationTarget.id).where(
            RegenerationTarget.toc_entry_id == toc_entry_id,
            RegenerationTarget.output_language == output_language,
            RegenerationTarget.source_job_id.is_(None),
        )
    )
    return list(result.scalars().all())
