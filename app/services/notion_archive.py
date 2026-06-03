"""Phase-1 Notion push. Best-effort: archive_job never raises into the pipeline.

Flow: resolve subject page from config map ({subject}|{grade}) → find-or-create
lesson page → find-or-create `Homework` sub-page → grouped page layout
(Case-Based Preview · Flashcards[+memory-check] · Gamified Practices[game
children] · Boss Arena · Reflection), each page's .md attached at the top →
stamp toc_entry + job."""

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
from app.services.notion.page_creator import find_or_create

log = logging.getLogger("notion.archive")

_warned_unconfigured = False


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
) -> Optional[str]:
    """Resolve the Notion subject-page id for a job.

    The value at ``{subject}|{grade}`` is either a plain page-id string (the
    normal case, incl. grades with a single combined history page), or a
    ``{keyword: page-id}`` object for grades that split one app-subject across
    several Notion pages (e.g. history → Jahon tarixi / O‘zbekiston tarixi). For
    the object form, ``hint`` (the book filename) is folded and matched against
    each keyword as a substring; no match returns None so the caller logs a skip
    rather than mis-filing."""
    if not grade:
        return None
    value = mapping.get(f"{subject}|{grade}")
    if value is None or isinstance(value, str):
        return value
    folded = _fold(hint)
    for keyword, page_id in value.items():
        if _fold(keyword) in folded:
            return page_id
    return None


def _lesson_title(section_number: Optional[str], section_title: str) -> str:
    return f"{section_number} {section_title}".strip() if section_number else section_title.strip()


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
# A job only ever has one of the practice games present; absent phases are skipped.
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
) -> str:
    """Synchronous Notion I/O. Creates lesson → Homework, then the grouped page
    layout (`_HOMEWORK_LAYOUT`): Case-Based Preview, Flashcards (flashcards +
    memory-check inline), Gamified Practices (container of game sub-pages), Boss
    Arena, Reflection. Idempotent: a page that already has content is skipped.
    Returns the Homework page id."""
    lesson_id, _ = find_or_create(client, subject_page_id, lesson_title)
    homework_id, _ = find_or_create(client, lesson_id, "Homework")

    def _write_leaf(parent_id: str, title: str, present: list[tuple[str, str]]) -> None:
        page_id, _ = find_or_create(client, parent_id, title)
        if client.page_has_content(page_id):
            log.info("notion: page %s (%s) already populated — skipping", page_id, title)
            return
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


async def archive_job(job_id: UUID) -> None:
    """Best-effort entry point called from the pipeline after job is `done`."""
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
            if job is None or job.notion_archived_at is not None:
                return  # gone or already archived (idempotent on retry)
            book = await books_repo.get(session, job.book_id)
            section = await toc_repo.get(session, job.toc_entry_id)
            if book is None or section is None:
                return
            subject_page_id = _resolve_subject_page_id(
                settings.notion_subject_pages, job.subject, book.grade,
                book.original_filename or "",
            )
            if not subject_page_id:
                log.warning(
                    "notion: no subject-page mapping for subject=%s grade=%s — skipping",
                    job.subject, book.grade,
                )
                return
            section_id = section.id
            lesson_title = _lesson_title(section.section_number, section.section_title)
            phase_md = {
                p.phase_name: (p.output_md or "")
                for p in await phase_repo.list_for_job(session, job_id)
                if p.status == "done" and p.phase_name != "extract" and (p.output_md or "").strip()
            }
        # session closed — do NOT hold a DB connection during the Notion push
        if not phase_md:
            log.info("notion: job %s has no completed phase outputs — skipping", job_id)
            return

        client = NotionClientWrapper(api_key=settings.notion_api_key)
        homework_id = await asyncio.to_thread(
            _push_to_notion,
            client=client,
            subject_page_id=subject_page_id,
            lesson_title=lesson_title,
            phase_md=phase_md,
        )

        async with SessionLocal() as session:
            await toc_repo.set_notion_homework_page_id(session, section_id, homework_id)
            await jobs_repo.set_notion_archived(session, job_id, _utcnow())
            await session.commit()
        log.info("notion: archived job %s → Homework page %s", job_id, homework_id)
    except Exception:
        log.warning("notion: archive failed for job %s (non-fatal)", job_id, exc_info=True)
