"""Phase-1 Notion push. Best-effort: archive_job never raises into the pipeline.

Flow: resolve subject page from config map ({subject}|{grade}) → unconditionally
find-or-create a 'Generated Homeworks' container under the subject page →
find-or-create the lesson page under that container → find-or-create a 'Homework'
sub-page → grouped page layout (Case-Based Preview · Flashcards[+memory-check] ·
Gamified Practices[game children] · Boss Arena · Reflection), each page's .md
attached at the top → stamp toc_entry + job.

Every homework is filed under 'Generated Homeworks'; human-page matching/adoption
is not performed."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Callable, Optional
from uuid import UUID

from app.config import settings
from app.db import SessionLocal
from app.repositories import jobs as jobs_repo
from app.repositories import books as books_repo
from app.repositories import toc_entries as toc_repo
from app.repositories import phase_outputs as phase_repo
from app.services.notion import blocks
from app.services.notion.client import NotionClientWrapper
from app.services.notion.page_creator import _normalize, find_or_create

# All generated homeworks are filed under this container, created on demand
# under the subject page. Human-page matching/adoption is not performed.
CONTAINER_TITLE = "Generated Homeworks"

log = logging.getLogger("notion.archive")

_warned_unconfigured = False

# Bounded retry for the (idempotent) Notion push. A transient network/5xx must
# not leave the job invisibly un-archived (notion_archived_at + skip_reason both
# NULL); retry, then record a skip reason on final failure.
_PUSH_MAX_ATTEMPTS = 3
_PUSH_BACKOFF_BASE_SECONDS = 1.0


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _fold(s: str) -> str:
    """Lowercase + strip apostrophe/diacritic variants so a bare keyword like
    ``ozbekiston`` matches filenames spelled ``Ozbekiston`` or ``O‘zbekiston``."""
    return s.lower().translate({ord(c): None for c in "'‘’ʻ`"})


def _resolve_subject_page_id(
    mapping: dict[str, str | dict[str, str]],
    subject: str,
    grade: Optional[str],
    hint: str = "",
    language: str = "uz",
) -> Optional[str]:
    """Resolve the Notion subject-page id for a job.

    The lookup key is ``{subject}|{grade}`` for Uzbek (the default) and
    ``{language}:{subject}|{grade}`` for a non-Uzbek medium (``en``/``ru``), so
    English/Russian content files under its OWN Notion root page. A non-uz job
    whose language page is unconfigured resolves to None — the caller skips and
    records a reason rather than mis-filing non-Uzbek content into the uz page.

    The value at that key is either a plain page-id string (the normal case,
    incl. grades with a single combined history page), or a ``{keyword: page-id}``
    object for grades that split one app-subject across several Notion pages
    (e.g. history → Jahon tarixi / O‘zbekiston tarixi). For the object form,
    ``hint`` (the book filename) is folded and matched against each keyword as a
    substring; no match returns None so the caller logs a skip rather than
    mis-filing."""
    if not grade:
        return None
    prefix = "" if language == "uz" else f"{language}:"
    value = mapping.get(f"{prefix}{subject}|{grade}")
    if value is None or isinstance(value, str):
        return value
    folded = _fold(hint)
    for keyword, page_id in value.items():
        if _fold(keyword) in folded:
            return page_id
    return None


def _lesson_title(
    section_number: Optional[str],
    section_title: str,
    *,
    page_start: Optional[int] = None,
    ambiguous: bool = False,
    order_index: Optional[int] = None,
) -> str:
    """The Notion lesson-page title.

    `ambiguous` means another TOC entry under the same Generated-Homeworks
    container normalizes to this same title. That is not rare: these textbooks
    reuse rubric headings as section titles (`Вспомните` ×10 in one grade,
    `Подумайте. Проблемное задание` ×13) and `section_number` is NULL for
    exactly those rows. Since `find_or_create` matches on the normalized title,
    an undisambiguated collision sends every one of them to the same page, where
    all but the first are silently skipped by `page_has_content`.

    The suffix is applied ONLY when ambiguous. Adding it unconditionally would
    rename every lesson, so the next archive would no longer match the existing
    pages and would create a duplicate beside each one.

    `page_start` is the disambiguator because it is both sufficient (it
    separates all 56 known colliding rows, where the chapter number still leaves
    4 collisions) and meaningful to a human browsing Notion. `order_index` is
    the fallback for the theoretical row with no page number.
    """
    base = f"{section_number} {section_title}".strip() if section_number else section_title.strip()
    if not ambiguous:
        return base
    if page_start is not None:
        return f"{base} · p.{page_start}"
    if order_index is not None:
        return f"{base} · #{order_index}"
    return base


PHASE_TITLES: dict[str, str] = {
    "case-based-preview": "Case-Based Preview",
    "flashcards": "Flashcards",
    "memory-check": "Memory Check",
    "practice-rlc": "Real-Life Challenge",
    "practice-error-detection": "Error Detection",
    "practice-memory-match": "Memory Matching",
    "practice-tictactoe": "TicTacToe",
    "practice-jigsaw": "Jigsaw Matching",
    "practice-sentence": "Sentence Filling",
    "boss-arena": "Boss Arena",
    "reflection": "Reflection",
}

# Homework page layout under the `Homework` sub-page. Ordered top-level entries:
#  - "leaf": one page rendering its listed phases inline (in order), each phase's
#    .md attached at the very top, then the rendered content sections.
#  - "container": a parent page holding one child leaf page per present phase
#    (child title via PHASE_TITLES). The container itself carries no body.
# After all-games (worklog 0067), all four practice games are present every job;
# any absent phase is simply skipped.
_LEAF, _CONTAINER = "leaf", "container"
_HOMEWORK_LAYOUT: list[dict] = [
    {"kind": _LEAF, "title": "Case-Based Preview", "phases": ["case-based-preview"]},
    {"kind": _LEAF, "title": "Flashcards", "phases": ["flashcards", "memory-check"]},
    {"kind": _CONTAINER, "title": "Gamified Practices", "phases": [
        "practice-rlc", "practice-error-detection",
        "practice-memory-match", "practice-tictactoe",
        "practice-jigsaw", "practice-sentence",
    ]},
    {"kind": _LEAF, "title": "Boss Arena", "phases": ["boss-arena"]},
    {"kind": _LEAF, "title": "Reflection", "phases": ["reflection"]},
]


def _leaf_blocks(client: NotionClientWrapper, present: list[tuple[str, str]]) -> list[dict]:
    """Blocks for a leaf page: every phase's .md attached at the very top, then
    each phase's rendered markdown, separated by dividers."""
    body: list[dict] = []
    for phase_name, md in present:  # attachments first — top of the page
        upload = client.upload_bytes(md.encode("utf-8"), f"{phase_name}.md", "text/markdown")
        body.append(blocks.make_file_upload_block(upload, f"{phase_name}.md"))
    for phase_name, md in present:  # then content sections
        body.append(blocks.make_divider())
        body.extend(blocks.markdown_to_notion_blocks(md))
    return body


def _push_to_notion(
    *,
    client: NotionClientWrapper,
    subject_page_id: str,
    lesson_title: str,
    phase_md: dict[str, str],  # phase_name -> markdown (only present/done phases)
    find_or_create: Callable = find_or_create,  # injectable for tests
    replace: bool = False,
    homework_page_id: Optional[str] = None,
) -> str:
    """Synchronous Notion I/O. Unconditionally creates the path:
    Subject → 'Generated Homeworks' → <lesson_title> → 'Homework', then the
    grouped page layout (`_HOMEWORK_LAYOUT`): Case-Based Preview, Flashcards
    (flashcards + memory-check inline), Gamified Practices (container of game
    sub-pages), Boss Arena, Reflection. Idempotent: a page that already has
    content is skipped. When `replace` is True, a populated leaf page is
    cleared (`clear_content_blocks`) and rewritten instead of skipped — used
    by the operator force-refresh path. Returns the Homework page id."""
    if homework_page_id:
        # Identity from the DB beats identity from the title. A section that
        # already owns a page reuses it directly — this is what stops a lesson
        # whose title IS ambiguous from being re-keyed onto a fresh suffixed
        # page and orphaning the content already filed under the old one.
        homework_id = homework_page_id
    else:
        container_id, _ = find_or_create(client, subject_page_id, CONTAINER_TITLE)
        lesson_id, _ = find_or_create(client, container_id, lesson_title)
        homework_id, _ = find_or_create(client, lesson_id, "Homework")

    def _write_leaf(parent_id: str, title: str, present: list[tuple[str, str]]) -> None:
        page_id, _ = find_or_create(client, parent_id, title)
        if client.page_has_content(page_id):
            if not replace:
                log.info("notion: page %s (%s) already populated — skipping", page_id, title)
                return
            log.info("notion: page %s (%s) already populated — clearing to rewrite (force)", page_id, title)
            client.clear_content_blocks(page_id)
        client.append_block_children(page_id, _leaf_blocks(client, present))

    for entry in _HOMEWORK_LAYOUT:
        present = [(pn, phase_md[pn]) for pn in entry["phases"] if pn in phase_md]
        if not present:
            continue  # nothing generated for this group
        if entry["kind"] == _LEAF:
            _write_leaf(homework_id, entry["title"], present)
        else:  # container: one child leaf page per present phase
            container_id, _ = find_or_create(client, homework_id, entry["title"])
            for phase_name, md in present:
                _write_leaf(container_id, PHASE_TITLES.get(phase_name, phase_name), [(phase_name, md)])
    return homework_id


async def _push_with_retry(*, client, subject_page_id, lesson_title, phase_md, replace: bool = False,
                          homework_page_id: Optional[str] = None) -> str:
    """Run the idempotent Notion push in a worker thread, retrying transient
    failures with exponential backoff. Re-raises the last exception if every
    attempt fails, so the caller can record a skip reason."""
    last_exc: Optional[Exception] = None
    for attempt in range(1, _PUSH_MAX_ATTEMPTS + 1):
        try:
            return await asyncio.to_thread(
                _push_to_notion,
                client=client,
                subject_page_id=subject_page_id,
                lesson_title=lesson_title,
                phase_md=phase_md,
                replace=replace,
                homework_page_id=homework_page_id,
            )
        except Exception as exc:  # noqa: BLE001 - retried, then recorded as a skip
            last_exc = exc
            log.warning("notion: push attempt %d/%d failed: %s",
                        attempt, _PUSH_MAX_ATTEMPTS, exc)
            if attempt < _PUSH_MAX_ATTEMPTS:
                await asyncio.sleep(_PUSH_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)))
    assert last_exc is not None
    raise last_exc


async def _record_skip(job_id: UUID, reason: str) -> None:
    """Best-effort persist of a skip reason in a fresh session; never raises."""
    try:
        async with SessionLocal() as session:
            await jobs_repo.set_notion_skip_reason(session, job_id, reason)
            await session.commit()
    except Exception:  # noqa: BLE001 - the skip marker is itself best-effort
        log.warning("notion: could not record skip reason for job %s", job_id, exc_info=True)


async def archive_job(job_id: UUID, *, force: bool = False) -> None:
    """Best-effort entry point called from the pipeline after job is `done`.
    With `force=True` (operator re-archive), an already-archived job is NOT
    short-circuited and its leaf pages are cleared and rewritten (replace mode)."""
    global _warned_unconfigured
    if not settings.notion_enabled:
        return
    if not settings.notion_api_key:
        if not _warned_unconfigured:
            log.warning("notion_enabled but notion_api_key missing — skipping archive")
            _warned_unconfigured = True
        return

    try:
        async with SessionLocal() as session:
            job = await jobs_repo.get(session, job_id)
            if job is None:
                return  # gone
            if job.notion_archived_at is not None and not force:
                return  # already archived (idempotent on retry) unless forced
            book = await books_repo.get(session, job.book_id)
            section = await toc_repo.get(session, job.toc_entry_id)
            if book is None or section is None:
                await jobs_repo.set_notion_skip_reason(
                    session, job_id, "book/section row missing")
                await session.commit()
                return
            subject_page_id = _resolve_subject_page_id(
                settings.notion_subject_pages, job.subject, book.grade,
                book.original_filename or "", language=job.output_language,
            )
            if not subject_page_id:
                log.warning(
                    "notion: no subject-page mapping for language=%s subject=%s grade=%s — skipping",
                    job.output_language, job.subject, book.grade,
                )
                await jobs_repo.set_notion_skip_reason(
                    session, job_id,
                    f"no Notion page for language={job.output_language} {job.subject}|{book.grade}")
                await session.commit()
                return
            section_id = section.id
            # Is this lesson's title repeated anywhere in the same Notion
            # container? If so it MUST be disambiguated, or `find_or_create`
            # sends it to another lesson's page where `page_has_content` will
            # silently drop the write (the 2026-08-05 loss: 184 jobs stamped
            # archived, 135 pages, 49 homeworks never written).
            base_title = _lesson_title(section.section_number, section.section_title)
            siblings = await toc_repo.titles_for_subject_grade(
                session, subject=job.subject, grade=book.grade,
            )
            same = sum(
                1 for sn, st, ct in siblings
                if _normalize(_lesson_title(sn, st or ct)) == _normalize(base_title)
            )
            lesson_title = _lesson_title(
                section.section_number, section.section_title,
                page_start=section.page_start,
                order_index=section.order_index,
                ambiguous=same > 1,
            )
            if same > 1:
                log.info(
                    "notion: lesson title %r is repeated %dx at %s|%s — filing as %r",
                    base_title, same, job.subject, book.grade, lesson_title,
                )
            # A leaf page under 'Generated Homeworks' is always our own output
            # (no human-page adoption — see module docstring), so a regen may
            # safely clear+rewrite it. first_archive: never filed this lesson
            # (we set the page id only when we archive). auto_replace fires only
            # when the page holds a DIFFERENT job's content AND this job is
            # strictly NEWER than it — an older job re-archiving (e.g. operator
            # retry on a pre-regen job whose push failed) must never clobber a
            # newer page with stale content. force (operator override) is the
            # only direction-blind path.
            first_archive = section.notion_homework_page_id is None
            # Captured inside the session: the push runs after it closes.
            section_page_id = section.notion_homework_page_id
            prior_job_id = section.notion_archived_job_id
            auto_replace = False
            if prior_job_id is not None and prior_job_id != job_id:
                prior_job = await jobs_repo.get(session, prior_job_id)
                if prior_job is not None and job.created_at > prior_job.created_at:
                    auto_replace = True
                else:
                    log.warning(
                        "notion: job %s is not newer than stamped job %s on section %s "
                        "— keeping skip (no auto-replace)",
                        job_id, prior_job_id, section_id)
            phase_md = {
                p.phase_name: (p.output_md or "")
                for p in await phase_repo.list_for_job(session, job_id)
                if p.status == "done" and p.phase_name != "extract" and (p.output_md or "").strip()
            }
        # session closed — do NOT hold a DB connection during the Notion push
        if not phase_md:
            log.info("notion: job %s has no completed phase outputs — skipping", job_id)
            async with SessionLocal() as session:
                await jobs_repo.set_notion_skip_reason(
                    session, job_id, "no completed phase outputs")
                await session.commit()
            return

        do_replace = force or auto_replace

        client = NotionClientWrapper(api_key=settings.notion_api_key)
        try:
            homework_id = await _push_with_retry(
                client=client,
                subject_page_id=subject_page_id,
                lesson_title=lesson_title,
                phase_md=phase_md,
                replace=do_replace,
                # Reuse the page this section already owns, so an ambiguous
                # title cannot re-key it onto a fresh suffixed page.
                homework_page_id=section_page_id,
            )
        except Exception as exc:  # noqa: BLE001 - push exhausted retries; record + give up
            log.warning("notion: push failed for job %s after %d attempts (non-fatal)",
                        job_id, _PUSH_MAX_ATTEMPTS, exc_info=True)
            await _record_skip(job_id, f"push error: {type(exc).__name__}")
            return

        async with SessionLocal() as session:
            await toc_repo.set_notion_homework_page_id(session, section_id, homework_id)
            if first_archive or do_replace:
                await toc_repo.set_notion_archived_job(session, section_id, job_id)
            await jobs_repo.set_notion_archived(session, job_id, _utcnow())
            await session.commit()
        log.info("notion: archived job %s → Homework page %s", job_id, homework_id)
    except Exception:
        log.warning("notion: archive failed for job %s (non-fatal)", job_id, exc_info=True)
        await _record_skip(job_id, "archive error")
