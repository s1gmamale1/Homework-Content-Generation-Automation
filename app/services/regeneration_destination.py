"""Read-only resolution of the Notion destination an operator will approve.

The service accepts scalar snapshots, performs one bounded Notion scan off the
event loop, and returns an immutable decision.  It never creates or edits a
page.  Publication later executes the recorded ids/policies instead of
silently deriving a different destination.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from typing import Callable, Literal, Optional, Sequence
from uuid import UUID

from notion_client.errors import APIErrorCode, APIResponseError

from app.config import settings
from app.services import notion_archive
from app.services.notion.client import NotionClientWrapper
from app.services.notion.page_creator import _normalize
from app.services.notion_versioned_homework import version_page_title
from app.services.regeneration_notion_readiness import publication_unavailable_reason

LineageKey = tuple[UUID, str]


class DestinationServiceUnavailable(RuntimeError):
    """The scan could not produce a complete answer.

    ``retryable`` distinguishes deployment configuration from transient Notion
    failures.  In either case callers must not treat a partial scan as an
    operator-reviewable result.
    """

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class DestinationSource:
    toc_entry_id: UUID
    output_language: str
    source_job_id: UUID
    subject: str
    grade: Optional[str]
    book_filename: str
    section_number: Optional[str]
    section_title: str
    chapter_title: str
    page_start: Optional[int]
    notion_lesson_page_id: Optional[str]
    lesson_title: str
    # Exact V1 Homework child identity.  Older rows often predate the separate
    # Lesson Topic pointer; asking Notion for this page's parent recovers that
    # identity without guessing from a title that may since be disambiguated.
    notion_homework_page_id: Optional[str] = None
    notion_homework_lineage_verified: bool = False
    lineage_previously_published: bool = False


@dataclass(frozen=True)
class DestinationOverride:
    toc_entry_id: UUID
    output_language: str
    notion_lesson_page_id: str


@dataclass(frozen=True)
class DestinationCandidate:
    page_id: str
    title: str


@dataclass(frozen=True)
class DestinationResolution:
    toc_entry_id: UUID
    output_language: str
    lesson_title: str
    status: Literal["reuse", "create", "ambiguous", "blocked"]
    container_policy: Optional[Literal["reuse", "create"]]
    container_page_id: Optional[str]
    lesson_policy: Optional[Literal["reuse", "create"]]
    lesson_page_id: Optional[str]
    candidates: tuple[DestinationCandidate, ...]
    reason: Optional[str]


@dataclass(frozen=True)
class DestinationPreflight:
    ok: bool
    resolutions: tuple[DestinationResolution, ...]
    digest: str
    checked_target_count: int


def default_client() -> NotionClientWrapper:
    return NotionClientWrapper(api_key=settings.notion_api_key)


def destination_digest(
    resolutions: Sequence[DestinationResolution], *, requested_version: int
) -> str:
    """Bind approval to the decisions, not explanatory or display-only data."""
    decisions = [
        {
            "toc_entry_id": str(item.toc_entry_id),
            "output_language": item.output_language,
            "lesson_title": item.lesson_title,
            "status": item.status,
            "container_policy": item.container_policy,
            "container_page_id": item.container_page_id,
            "lesson_policy": item.lesson_policy,
            "lesson_page_id": item.lesson_page_id,
        }
        for item in resolutions
    ]
    decisions.sort(key=lambda item: (item["toc_entry_id"], item["output_language"]))
    payload = json.dumps(
        {"requested_version": requested_version, "resolutions": decisions},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _blocked(source: DestinationSource, reason: str, *,
             container_policy: Optional[Literal["reuse", "create"]] = None,
             container_page_id: Optional[str] = None,
             candidates: tuple[DestinationCandidate, ...] = (),
             lesson_policy: Optional[Literal["reuse", "create"]] = None,
             lesson_page_id: Optional[str] = None,
             status: Literal["ambiguous", "blocked"] = "blocked",
             ) -> DestinationResolution:
    return DestinationResolution(
        toc_entry_id=source.toc_entry_id,
        output_language=source.output_language,
        lesson_title=source.lesson_title,
        status=status,
        container_policy=container_policy,
        container_page_id=container_page_id,
        lesson_policy=lesson_policy,
        lesson_page_id=lesson_page_id,
        candidates=candidates,
        reason=reason,
    )


def _scan_destinations(
    *,
    sources: Sequence[DestinationSource],
    requested_version: int,
    overrides: dict[LineageKey, str],
    client_factory: Callable[[], NotionClientWrapper],
) -> tuple[DestinationResolution, ...]:
    """Synchronous body of the single worker-thread hop."""
    client = client_factory()
    child_cache: dict[str, tuple[dict, ...]] = {}

    def children(page_id: str) -> tuple[dict, ...]:
        if page_id not in child_cache:
            child_cache[page_id] = tuple(client.get_child_pages(page_id))
        return child_cache[page_id]

    resolutions: list[DestinationResolution] = []
    expected_version_title = version_page_title(requested_version)

    for source in sources:
        lineage = (source.toc_entry_id, source.output_language)
        override = overrides.get(lineage)

        # The legacy archive stamp is the strongest identity available.  It
        # binds one concrete Homework child to the job which published this
        # exact TOC + language lineage, so its two ancestors remain authoritative
        # even when titles or NOTION_SUBJECT_PAGES have since changed.
        if override is None and source.notion_homework_lineage_verified:
            homework_id = (source.notion_homework_page_id or "").strip()
            if not homework_id:
                resolutions.append(_blocked(
                    source,
                    "the published lineage is verified but its stored V1 "
                    "Homework page id is missing",
                ))
                continue
            try:
                lesson_id = client.get_page_parent(homework_id)
            except APIResponseError as exc:
                if exc.code != APIErrorCode.ObjectNotFound:
                    raise
                lesson_id = None
            if not lesson_id:
                resolutions.append(_blocked(
                    source,
                    "the stored V1 Homework page for this published lineage "
                    "is unavailable; refusing to create a replacement Lesson "
                    "Topic without exact identity",
                ))
                continue
            try:
                container_id = client.get_page_parent(lesson_id)
            except APIResponseError as exc:
                if exc.code != APIErrorCode.ObjectNotFound:
                    raise
                container_id = None
            if not container_id:
                resolutions.append(_blocked(
                    source,
                    "the stored V1 Homework page's Lesson Topic has no readable "
                    "parent container; refusing to guess a destination",
                ))
                continue

            try:
                lesson_children = children(lesson_id)
            except APIResponseError as exc:
                if exc.code != APIErrorCode.ObjectNotFound:
                    raise
                resolutions.append(_blocked(
                    source,
                    "the lineage-proven Lesson Topic no longer exists or is "
                    "inaccessible; refusing to create a replacement",
                    container_policy="reuse",
                    container_page_id=container_id,
                    lesson_policy="reuse",
                    lesson_page_id=lesson_id,
                ))
                continue

            version_exists = any(
                _normalize(str(page.get("title", "")))
                == _normalize(expected_version_title)
                for page in lesson_children
            )
            if version_exists:
                resolutions.append(_blocked(
                    source,
                    f"{expected_version_title} already exists under the "
                    "lineage-proven Lesson Topic",
                    container_policy="reuse",
                    container_page_id=container_id,
                    lesson_policy="reuse",
                    lesson_page_id=lesson_id,
                ))
                continue

            resolutions.append(DestinationResolution(
                toc_entry_id=source.toc_entry_id,
                output_language=source.output_language,
                lesson_title=source.lesson_title,
                status="reuse",
                container_policy="reuse",
                container_page_id=container_id,
                lesson_policy="reuse",
                lesson_page_id=lesson_id,
                candidates=(),
                reason=None,
            ))
            continue

        subject_page_id = notion_archive._resolve_subject_page_id(
            settings.notion_subject_pages,
            source.subject,
            source.grade,
            source.book_filename,
            language=source.output_language,
        )
        if not subject_page_id:
            resolutions.append(_blocked(
                source,
                f"no Notion subject page for language={source.output_language} "
                f"{source.subject}|{source.grade}",
            ))
            continue

        containers = tuple(
            page for page in children(subject_page_id)
            if _normalize(str(page.get("title", "")))
            == _normalize(notion_archive.CONTAINER_TITLE)
        )
        if len(containers) > 1:
            resolutions.append(_blocked(
                source,
                f"multiple {notion_archive.CONTAINER_TITLE!r} containers exist "
                "under the configured subject page",
            ))
            continue
        if not containers:
            if override is not None:
                resolutions.append(_blocked(
                    source,
                    f"override page {override!r} is not a safe candidate because "
                    f"{notion_archive.CONTAINER_TITLE!r} does not exist",
                    candidates=(),
                ))
                continue
            if source.lineage_previously_published:
                resolutions.append(_blocked(
                    source,
                    "this lineage was previously published, but the configured "
                    f"subject page has no {notion_archive.CONTAINER_TITLE!r} "
                    "container; refusing to create a parallel tree",
                ))
                continue
            resolutions.append(DestinationResolution(
                toc_entry_id=source.toc_entry_id,
                output_language=source.output_language,
                lesson_title=source.lesson_title,
                status="create",
                container_policy="create",
                container_page_id=None,
                lesson_policy="create",
                lesson_page_id=None,
                candidates=(),
                reason=None,
            ))
            continue

        container_id = str(containers[0]["id"])
        lesson_children = children(container_id)
        title_candidates = tuple(
            DestinationCandidate(page_id=str(page["id"]),
                                 title=str(page.get("title", "")))
            for page in lesson_children
            if _normalize(str(page.get("title", "")))
            == _normalize(source.lesson_title)
        )
        hint = (source.notion_lesson_page_id or "").strip()
        child_by_id = {
            str(page.get("id")): page for page in lesson_children
        }
        candidates = title_candidates
        if (
            source.lineage_previously_published
            and hint in child_by_id
            and hint not in {candidate.page_id for candidate in candidates}
        ):
            page = child_by_id[hint]
            candidates = (*candidates, DestinationCandidate(
                page_id=hint,
                title=str(page.get("title", "")),
            ))

        chosen_id: Optional[str] = None
        if override is not None:
            if override not in {candidate.page_id for candidate in candidates}:
                resolutions.append(_blocked(
                    source,
                    f"override page {override!r} is not one of the reviewed "
                    "Lesson Topic candidates",
                    container_policy="reuse",
                    container_page_id=container_id,
                    candidates=candidates,
                ))
                continue
            chosen_id = override
        else:
            if source.lineage_previously_published:
                resolutions.append(_blocked(
                    source,
                    "this lineage was previously published, but no stored page "
                    "identity proves the current language destination; select "
                    "an existing Lesson Topic instead of creating another",
                    container_policy="reuse",
                    container_page_id=container_id,
                    candidates=candidates,
                    status="ambiguous" if candidates else "blocked",
                ))
                continue
            if hint and hint in child_by_id:
                chosen_id = hint
            if chosen_id is None and len(candidates) == 1:
                chosen_id = candidates[0].page_id
            elif chosen_id is None and len(candidates) > 1:
                resolutions.append(_blocked(
                    source,
                    "multiple Lesson Topic pages match; an operator must choose",
                    container_policy="reuse",
                    container_page_id=container_id,
                    candidates=candidates,
                    status="ambiguous",
                ))
                continue

        if chosen_id is None:
            resolutions.append(DestinationResolution(
                toc_entry_id=source.toc_entry_id,
                output_language=source.output_language,
                lesson_title=source.lesson_title,
                status="create",
                container_policy="reuse",
                container_page_id=container_id,
                lesson_policy="create",
                lesson_page_id=None,
                candidates=candidates,
                reason=None,
            ))
            continue

        version_exists = any(
            _normalize(str(page.get("title", "")))
            == _normalize(expected_version_title)
            for page in children(chosen_id)
        )
        if version_exists:
            resolutions.append(_blocked(
                source,
                f"{expected_version_title} already exists under the reviewed "
                "Lesson Topic",
                container_policy="reuse",
                container_page_id=container_id,
                candidates=candidates,
                lesson_policy="reuse",
                lesson_page_id=chosen_id,
            ))
            continue

        resolutions.append(DestinationResolution(
            toc_entry_id=source.toc_entry_id,
            output_language=source.output_language,
            lesson_title=source.lesson_title,
            status="reuse",
            container_policy="reuse",
            container_page_id=container_id,
            lesson_policy="reuse",
            lesson_page_id=chosen_id,
            candidates=candidates,
            reason=None,
        ))

    return tuple(resolutions)


async def resolve_destinations(
    *,
    sources: Sequence[DestinationSource],
    requested_version: int,
    overrides: Sequence[DestinationOverride],
    client_factory: Callable[[], NotionClientWrapper] = default_client,
    maximum_targets: int = 500,
) -> DestinationPreflight:
    """Resolve every lineage or raise when a complete scan is impossible."""
    if maximum_targets < 0:
        raise ValueError("maximum_targets must not be negative")
    if len(sources) > maximum_targets:
        raise ValueError(
            f"requested {len(sources)} targets, exceeding the bound of "
            f"{maximum_targets}"
        )

    source_keys = [(source.toc_entry_id, source.output_language) for source in sources]
    if len(source_keys) != len(set(source_keys)):
        duplicate = next(key for key in source_keys if source_keys.count(key) > 1)
        raise ValueError(f"duplicate destination lineage {duplicate[0]}:{duplicate[1]}")

    source_key_set = set(source_keys)
    source_by_key = {
        (source.toc_entry_id, source.output_language): source
        for source in sources
    }
    override_map: dict[LineageKey, str] = {}
    for override in overrides:
        key = (override.toc_entry_id, override.output_language)
        page_id = override.notion_lesson_page_id.strip()
        if not page_id:
            raise ValueError(f"blank destination override for {key[0]}:{key[1]}")
        if key not in source_key_set:
            raise ValueError(
                f"destination override {key[0]}:{key[1]} is not under review"
            )
        if source_by_key[key].notion_homework_lineage_verified:
            raise ValueError(
                f"destination override {key[0]}:{key[1]} is not allowed for "
                "a lineage-proven V1 destination"
            )
        if key in override_map:
            raise ValueError(f"duplicate destination override for {key[0]}:{key[1]}")
        override_map[key] = page_id

    unavailable = publication_unavailable_reason()
    if unavailable is not None:
        raise DestinationServiceUnavailable(unavailable, retryable=False)

    if not sources:
        resolutions: tuple[DestinationResolution, ...] = ()
    else:
        try:
            resolutions = await asyncio.to_thread(
                _scan_destinations,
                sources=sources,
                requested_version=requested_version,
                overrides=override_map,
                client_factory=client_factory,
            )
        except Exception as exc:
            raise DestinationServiceUnavailable(
                f"Notion destination scan failed: {type(exc).__name__}: {exc}",
                retryable=True,
            ) from exc

    digest = destination_digest(resolutions, requested_version=requested_version)
    return DestinationPreflight(
        ok=all(item.status in ("reuse", "create") for item in resolutions),
        resolutions=resolutions,
        digest=digest,
        checked_target_count=len(sources),
    )
