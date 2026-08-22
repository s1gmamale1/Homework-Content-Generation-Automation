"""Which lessons may be regenerated, from which snapshot, and where they land.

Three read-only passes an operator runs BEFORE any campaign exists and before
any money is spent:

* :func:`list_eligible_sources` / :func:`list_source_candidates` — the lessons
  in a selection that have a usable snapshot behind them, and, for the ones
  that do not, WHY;
* :func:`resolve_default_source` — the single lesson-and-language question:
  which job is the immediate source of the next revision;
* :func:`preflight_notion_destinations` — can each of those lessons actually be
  published, i.e. does this lesson's OWN subject/grade/language mapping resolve
  to the Notion tree the publisher will file it under. An already-stamped
  Lesson Topic pointer is not an answer to that question: it is language-blind.

Two authorities are imported, never re-implemented:

* ``regeneration_planner.validate_complete_snapshot`` decides whether a set of
  ``phase_outputs`` rows is a usable homework snapshot, and its strings are the
  operator-facing reasons this module surfaces verbatim (including the
  flow-drift one — a source generated under an older flow is EXPLAINED, not
  silently dropped);
* ``notion_archive`` owns destination resolution: ``_resolve_subject_page_id``
  (the ``{lang}:{subject}|{grade}`` key, including the filename-keyword form)
  and ``resolve_lesson_title`` (the escalating title disambiguation). Preflight
  answers "would the archiver find a home for this?", so it must ask the
  archiver's own question — a second implementation would drift and pass a
  campaign the publisher then refuses.

Nothing here writes. Preflight in particular constructs no Notion client and
makes no model call: it reads configuration and our own rows only.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.homework_job import HomeworkJob
from app.repositories import regeneration_sources as sources_repo
from app.repositories import regeneration_targets as targets_repo
from app.repositories import toc_entries as toc_repo
from app.services import notion_archive
from app.services.regeneration_planner import validate_complete_snapshot

# ─── stable operator-facing refusal reasons ──────────────────────────────
# Fixed strings (never interpolated) so a UI, a report and a test can all key
# off them. Anything variable travels beside them, not inside them.

#: A child-first purge nulled ``regeneration_targets.source_job_id`` while the
#: reporting row survived. Deliberately its OWN reason and not the generic
#: "no completed source": the row is present and looks ordinary, so an operator
#: told only "no eligible source" would go hunting for a missing job that was
#: deleted on purpose. ``fk_regeneration_targets_source_job_id`` is SET NULL
#: while ``homework_jobs.revision_of_job_id`` is RESTRICT, so a null link also
#: implies the revision job itself is already gone.
SOURCE_JOB_ID_IS_NULL_REASON = (
    "regeneration target source_job_id IS NULL — the source snapshot was purged "
    "(child-first delete); this target row is reporting history only"
)
#: No done, kind='homework', non-revision job for this lesson AND language.
#: (Failed jobs and teacher decks are filtered out in SQL, so they arrive here
#: as "nothing at all", which is the honest thing to tell the operator.)
NO_COMPLETED_SOURCE_REASON = (
    "no completed homework job for this lesson and output language"
)
#: The lineage's newest PUBLISHED version has no revision job row any more.
#: Refused rather than demoted to the V1 job: silently regenerating from an
#: older snapshot would branch the lineage off content nobody published.
PUBLISHED_REVISION_JOB_MISSING_REASON = (
    "the latest published revision job row no longer exists — this lineage "
    "cannot be regenerated from its newest published version"
)
#: A source job carrying a subject the deployed build no longer supports.
UNKNOWN_SUBJECT_REASON = "unsupported subject on the source job: {subject}"

# ─── stable Notion-preflight reasons ─────────────────────────────────────
NO_SUBJECT_PAGE_REASON = "no Notion subject page configured for this destination"
MISSING_GRADE_REASON = (
    "the book has no grade — the Notion destination key is {subject}|{grade}"
)


class DiscoverySelectionTooLarge(RuntimeError):
    """The bounded lineage query overflowed before per-lineage discovery.

    ``count_at_least`` is intentionally a lower bound: SQL fetches only
    ``maximum + 1`` rows, which proves the refusal without loading the rest.
    """

    def __init__(self, count_at_least: int, maximum: int):
        self.count_at_least = int(count_at_least)
        self.maximum = int(maximum)
        super().__init__(
            f"selection resolves to at least {self.count_at_least} candidate "
            f"lineages; discovery supports at most {self.maximum} at once — "
            "narrow the book or lesson selection"
        )

    def __reduce__(self):
        return (type(self), (self.count_at_least, self.maximum))


class NoEligibleSource(LookupError):
    """:func:`resolve_default_source` found no usable snapshot.

    Carries the same ``reasons`` tuple :func:`list_source_candidates` reports,
    so a single-lesson call and a bulk listing never explain the same situation
    two different ways.
    """

    def __init__(
        self,
        *,
        toc_entry_id: UUID,
        output_language: str,
        reasons: Sequence[str],
        detail: str = "",
    ):
        self.toc_entry_id = toc_entry_id
        self.output_language = output_language
        self.reasons: tuple[str, ...] = tuple(reasons)
        self.detail = detail
        message = (
            f"no eligible regeneration source for lesson {toc_entry_id} "
            f"({output_language}): " + "; ".join(self.reasons)
        )
        if detail:
            message = f"{message} [{detail}]"
        super().__init__(message)


@dataclass(frozen=True)
class EligibleRegenerationSource:
    """One lineage that CAN be regenerated, plus everything the campaign
    draft, the estimate and the Notion preflight need about it.

    ``source_publication_version`` is 1 for an original homework and the
    published version of the revision otherwise; ``next_expected_version`` is
    what the next publication will consume (see
    ``regeneration_sources.next_expected_version`` — it counts RESERVED
    versions, not successful ones).

    The section fields are carried so this object can be handed straight to
    ``notion_archive.resolve_lesson_title`` as the "section"; :attr:`id` exists
    for exactly that reason (that helper reads ``section.id`` for its
    last-resort suffix).
    """

    source_job_id: UUID
    toc_entry_id: UUID
    book_id: UUID
    subject: str
    grade: Optional[str]
    output_language: str
    source_publication_version: int
    next_expected_version: int
    source_is_revision: bool
    book_filename: str
    section_number: Optional[str]
    section_title: str
    chapter_title: str
    page_start: Optional[int]
    notion_lesson_page_id: Optional[str]
    order_index: int
    # The V1 child page can recover its parent for rows archived before the
    # dedicated Lesson Topic pointer was introduced.
    notion_homework_page_id: Optional[str] = None
    notion_homework_lineage_verified: bool = False
    lineage_previously_published: bool = False

    @property
    def id(self) -> UUID:  # noqa: A003 — the name `resolve_lesson_title` reads
        """The TOC row id, under the attribute name the archiver's title
        disambiguation expects."""
        return self.toc_entry_id


@dataclass(frozen=True)
class SourceCandidate:
    """A lineage in the operator's selection, eligible or not.

    :func:`list_eligible_sources` is the filtered view of this; the unfiltered
    one exists because "why is this lesson missing from my campaign?" is the
    question an operator actually asks.
    """

    toc_entry_id: UUID
    output_language: str
    source: Optional[EligibleRegenerationSource]
    reasons: tuple[str, ...]
    detail: str = ""

    @property
    def eligible(self) -> bool:
        return self.source is not None


@dataclass(frozen=True)
class NotionPreflightFailure:
    """One lesson that cannot be published yet, and the configuration fix."""

    source_job_id: UUID
    toc_entry_id: UUID
    subject: str
    grade: Optional[str]
    output_language: str
    lesson_title: str
    reason: str
    detail: str


async def _pick_source_job(
    session: AsyncSession, *, toc_entry_id: UUID, output_language: str
) -> tuple[Optional[HomeworkJob], int, tuple[str, ...], str]:
    """``(source job, its publication version, refusal reasons, detail)``.

    Order matters. The purge check runs FIRST so a purged lineage is refused on
    the ``source_job_id IS NULL`` predicate itself rather than on whatever
    downstream symptom it happens to produce.
    """
    purged = await sources_repo.lineage_targets_missing_source(
        session, toc_entry_id=toc_entry_id, output_language=output_language
    )
    if purged:
        return (
            None,
            0,
            (SOURCE_JOB_ID_IS_NULL_REASON,),
            "purged target(s): " + ", ".join(str(t) for t in purged),
        )

    published = await sources_repo.latest_published_target(
        session, toc_entry_id=toc_entry_id, output_language=output_language
    )
    if published is not None:
        # Defensive: the lineage-wide check above already refuses a null link.
        # Keep it — this is the row the choice actually depends on.
        if published.source_job_id is None:
            return (
                None,
                0,
                (SOURCE_JOB_ID_IS_NULL_REASON,),
                f"purged target(s): {published.id}",
            )
        revision = await targets_repo.revision_job_for_target(
            session, target_id=published.id
        )
        if revision is None:
            return None, 0, (PUBLISHED_REVISION_JOB_MISSING_REASON,), ""
        return revision, int(published.publication_version), (), ""

    v1 = await sources_repo.latest_v1_source_job(
        session, toc_entry_id=toc_entry_id, output_language=output_language
    )
    if v1 is None:
        return None, 0, (NO_COMPLETED_SOURCE_REASON,), ""
    return v1, 1, (), ""


def _snapshot_reasons(job: HomeworkJob, rows: Sequence) -> tuple[str, ...]:
    """Task 2's verdict on this snapshot, or an unsupported-subject refusal.

    ``flows.flow_for`` raises ``KeyError`` for a subject this build no longer
    ships; one retired subject on one old job must not abort a whole discovery.
    """
    try:
        return validate_complete_snapshot(subject=job.subject, rows=rows).reasons
    except KeyError:
        return (UNKNOWN_SUBJECT_REASON.format(subject=job.subject),)


async def list_source_candidates(
    session: AsyncSession,
    *,
    book_ids: Optional[Collection[UUID]] = None,
    toc_entry_ids: Optional[Collection[UUID]] = None,
    output_languages: Optional[Collection[str]] = None,
) -> list[SourceCandidate]:
    """Every lineage in the selection, with its source or its refusal reasons.

    Phase rows for the whole selection are fetched in ONE query — they are by
    far the largest read here, and a per-lesson round trip would make a
    200-lesson discovery unusable. The small per-lineage lookups (published
    target, V1 job, purge check, next version) stay per-lineage on purpose:
    they are indexed point queries, and batching them would duplicate the
    resolution rule in a second, join-shaped form.
    """
    maximum = int(settings.regeneration_max_discovery_lineages)
    lineages = await sources_repo.candidate_lineages(
        session,
        book_ids=book_ids,
        toc_entry_ids=toc_entry_ids,
        output_languages=output_languages,
        limit=maximum + 1,
    )
    if len(lineages) > maximum:
        raise DiscoverySelectionTooLarge(len(lineages), maximum)

    picked: list[tuple] = []
    for lineage in lineages:
        job, version, reasons, detail = await _pick_source_job(
            session,
            toc_entry_id=lineage.toc_entry_id,
            output_language=lineage.output_language,
        )
        picked.append((lineage, job, version, reasons, detail))

    rows_by_job = await sources_repo.phase_rows_for_jobs(
        session, [job.id for _, job, _, _, _ in picked if job is not None]
    )

    candidates: list[SourceCandidate] = []
    for lineage, job, version, reasons, detail in picked:
        if job is None:
            candidates.append(
                SourceCandidate(
                    toc_entry_id=lineage.toc_entry_id,
                    output_language=lineage.output_language,
                    source=None,
                    reasons=reasons,
                    detail=detail,
                )
            )
            continue
        snapshot_reasons = _snapshot_reasons(job, rows_by_job.get(job.id, []))
        if snapshot_reasons:
            candidates.append(
                SourceCandidate(
                    toc_entry_id=lineage.toc_entry_id,
                    output_language=lineage.output_language,
                    source=None,
                    reasons=snapshot_reasons,
                    detail="",
                )
            )
            continue
        next_version = await sources_repo.next_expected_version(
            session,
            toc_entry_id=lineage.toc_entry_id,
            output_language=lineage.output_language,
        )
        candidates.append(
            SourceCandidate(
                toc_entry_id=lineage.toc_entry_id,
                output_language=lineage.output_language,
                source=EligibleRegenerationSource(
                    source_job_id=job.id,
                    toc_entry_id=lineage.toc_entry_id,
                    book_id=lineage.book_id,
                    # The JOB's subject, not the book's: the snapshot was
                    # generated under it, and it is what graded completeness.
                    subject=job.subject,
                    grade=lineage.grade,
                    output_language=lineage.output_language,
                    source_publication_version=version,
                    next_expected_version=next_version,
                    source_is_revision=job.revision_of_job_id is not None,
                    book_filename=lineage.book_filename,
                    section_number=lineage.section_number,
                    section_title=lineage.section_title,
                    chapter_title=lineage.chapter_title,
                    page_start=lineage.page_start,
                    notion_lesson_page_id=lineage.notion_lesson_page_id,
                    order_index=lineage.order_index,
                    notion_homework_page_id=lineage.notion_homework_page_id,
                    notion_homework_lineage_verified=(
                        lineage.notion_homework_lineage_verified
                    ),
                    lineage_previously_published=(
                        lineage.lineage_previously_published
                        or job.revision_of_job_id is not None
                    ),
                ),
                reasons=(),
                detail="",
            )
        )
    return candidates


async def list_eligible_sources(
    session: AsyncSession,
    *,
    book_ids: Optional[Collection[UUID]] = None,
    toc_entry_ids: Optional[Collection[UUID]] = None,
    output_languages: Optional[Collection[str]] = None,
) -> list[EligibleRegenerationSource]:
    """The regenerable lessons of a selection. See :func:`list_source_candidates`
    for the ones that were left out and why."""
    return [
        candidate.source
        for candidate in await list_source_candidates(
            session,
            book_ids=book_ids,
            toc_entry_ids=toc_entry_ids,
            output_languages=output_languages,
        )
        if candidate.source is not None
    ]


async def resolve_default_source(
    session: AsyncSession, *, toc_entry_id: UUID, output_language: str
) -> HomeworkJob:
    """The job the next revision of this lesson-and-language is built on.

    The highest SUCCESSFULLY published revision if there is one (V3 is built on
    V2, not on V1), else the latest completed ordinary job. An unpublished or
    abandoned revision is never chosen — its content was never delivered.

    Raises :class:`NoEligibleSource` (never returns ``None``) so a caller
    cannot accidentally treat "nothing usable" as "use the default".
    """
    job, _version, reasons, detail = await _pick_source_job(
        session, toc_entry_id=toc_entry_id, output_language=output_language
    )
    if job is None:
        raise NoEligibleSource(
            toc_entry_id=toc_entry_id,
            output_language=output_language,
            reasons=reasons,
            detail=detail,
        )
    rows = (await sources_repo.phase_rows_for_jobs(session, [job.id])).get(job.id, [])
    snapshot_reasons = _snapshot_reasons(job, rows)
    if snapshot_reasons:
        raise NoEligibleSource(
            toc_entry_id=toc_entry_id,
            output_language=output_language,
            reasons=snapshot_reasons,
        )
    return job


async def preflight_notion_destinations(
    session: AsyncSession, sources: Sequence[EligibleRegenerationSource]
) -> list[NotionPreflightFailure]:
    """Every lesson in ``sources`` that has nowhere to publish, in one list.

    A lesson passes when its ``{lang}:{subject}|{grade}`` destination resolves
    through ``notion_archive._resolve_subject_page_id`` — the subject tree the
    publisher will file it under, and from which the Lesson Topic is created.

    An already-stamped ``toc_entries.notion_lesson_page_id`` does NOT excuse
    that lookup. The column is a single language-blind pointer owned by
    whichever lineage archived the lesson first, and the publisher stopped
    treating it as a parent across languages: it resolves the Lesson Topic
    beneath THIS target's own subject page and honours the pointer only once
    that tree is shown to contain it. So a ``uz``-stamped pointer says nothing
    about whether the ``ru`` lineage has a home — and since a missing mapping is
    a NON-retryable publisher refusal, waving it through here would let the
    campaign spend on a revision that can only park.

    Read-only and complete: it never short-circuits on the first failure (the
    operator fixes the configuration once, before the canary spends money), it
    constructs no Notion client, and it writes nothing — not to Notion and not
    to our own rows.

    Notion being globally disabled is deliberately NOT a preflight failure:
    that is a publisher-time configuration switch, not a missing destination
    for a particular lesson.
    """
    failures: list[NotionPreflightFailure] = []
    siblings_cache: dict[tuple[str, Optional[str]], list] = {}

    for source in sources:
        key = (source.subject, source.grade)
        if key not in siblings_cache:
            siblings_cache[key] = await toc_repo.titles_for_subject_grade(
                session, subject=source.subject, grade=source.grade
            )
        lesson_title = notion_archive.resolve_lesson_title(
            source, siblings_cache[key]
        )
        destination_key = (
            f"{source.subject}|{source.grade}"
            if source.output_language == "uz"
            else f"{source.output_language}:{source.subject}|{source.grade}"
        )

        if not source.grade:
            # `_resolve_subject_page_id` returns None for a null grade, which
            # would read as "the mapping is missing" — a different fix from the
            # real one (the BOOK has no grade).
            failures.append(
                NotionPreflightFailure(
                    source_job_id=source.source_job_id,
                    toc_entry_id=source.toc_entry_id,
                    subject=source.subject,
                    grade=source.grade,
                    output_language=source.output_language,
                    lesson_title=lesson_title,
                    reason=MISSING_GRADE_REASON,
                    detail=f"destination key would be {destination_key}",
                )
            )
            continue

        page_id = notion_archive._resolve_subject_page_id(
            settings.notion_subject_pages,
            source.subject,
            source.grade,
            source.book_filename,
            language=source.output_language,
        )
        if not page_id:
            failures.append(
                NotionPreflightFailure(
                    source_job_id=source.source_job_id,
                    toc_entry_id=source.toc_entry_id,
                    subject=source.subject,
                    grade=source.grade,
                    output_language=source.output_language,
                    lesson_title=lesson_title,
                    reason=NO_SUBJECT_PAGE_REASON,
                    detail=(
                        f"NOTION_SUBJECT_PAGES has no page for {destination_key}"
                    ),
                )
            )
    return failures
