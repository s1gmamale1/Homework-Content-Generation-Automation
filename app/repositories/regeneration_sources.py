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
from typing import Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.book import Book
from app.models.homework_job import HomeworkJob
from app.models.phase_output import PhaseOutput
from app.models.regeneration_target import RegenerationTarget
from app.models.toc_entry import TOCEntry

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
    """

    toc_entry_id: UUID
    output_language: str
    book_id: UUID
    subject: str
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

    ``for_update=True`` takes the row lock a read-then-write version allocation
    needs; see :func:`lock_lineage` for the case where there is no row yet.
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


async def lock_lineage(
    session: AsyncSession, *, toc_entry_id: UUID, output_language: str
) -> list[RegenerationTarget]:
    """``SELECT … FOR UPDATE`` over every target row of one lineage.

    Serialises two campaigns that read :func:`next_expected_version` and then
    write: without it both read the same max and both try to reserve the same
    number, and the loser hits an ``IntegrityError`` deep inside publication
    instead of waiting its turn here.

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
) -> list[LineageCandidate]:
    """Every lineage that has at least one completed homework, filtered by the
    operator's selection. ``None`` for a filter means "do not filter"; an EMPTY
    collection means "nothing matches" and is honoured as such.

    One row per ``(toc_entry_id, output_language)`` — ``DISTINCT``, because a
    lesson generated twice is still one lineage. Which of those jobs is the
    SOURCE is decided by discovery, not here.
    """
    stmt = (
        select(
            HomeworkJob.toc_entry_id,
            HomeworkJob.output_language,
            HomeworkJob.book_id,
            HomeworkJob.subject,
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

    rows = (await session.execute(stmt)).all()
    return [
        LineageCandidate(
            toc_entry_id=row[0],
            output_language=row[1],
            book_id=row[2],
            subject=row[3],
            grade=row[4],
            book_filename=row[5] or "",
            section_number=row[6],
            section_title=row[7] or "",
            chapter_title=row[8] or "",
            page_start=row[9],
            notion_lesson_page_id=row[10],
            order_index=row[11],
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
