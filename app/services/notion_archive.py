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

from pydantic import ValidationError

from app.config import settings
from app.db import SessionLocal
from app.repositories import jobs as jobs_repo
from app.repositories import books as books_repo
from app.repositories import toc_entries as toc_repo
from app.repositories import phase_outputs as phase_repo
from app.schemas.content_json import TeacherDeck
from app.services.notion import blocks
from app.services.notion.client import NotionClientWrapper
from app.services.notion.page_creator import _normalize, find_or_create
from app.services.teacher_deck import render_teacher_deck_markdown, render_teacher_deck_pdf

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


def _lesson_title(section_number: Optional[str], section_title: str) -> str:
    """The base lesson-page title: `"{section_number} {section_title}"`.

    Disambiguation deliberately does NOT live here — see `resolve_lesson_title`,
    which is the only thing that decides whether a suffix is needed. Keeping the
    suffix logic out of this function means a mutation to it cannot look like
    coverage of the decision.
    """
    return f"{section_number} {section_title}".strip() if section_number else section_title.strip()


def _sibling_title(row) -> str:
    """Base title of a sibling TOC row, by the SAME rule as the target row.

    Sibling and target must be computed identically or a row fails to match
    itself and the collision count silently reads 0 — suppressing the very
    suffix that prevents the collision.
    """
    section_number, section_title, chapter_title = row[0], row[1], row[2]
    return _lesson_title(section_number, section_title or chapter_title)


def resolve_lesson_title(section, siblings) -> str:
    """The Notion lesson-page title, disambiguated only as far as necessary.

    `siblings` is every TOC row sharing this lesson's Notion container —
    `(section_number, section_title, chapter_title, page_start, id)` — and
    INCLUDES this section's own row, so a count of 1 means "unique".

    Three escalating levels, because each one is demonstrably insufficient
    alone on live data:

    1. **Plain title.** Correct for the overwhelming majority, and load-bearing:
       suffixing unconditionally would rename every lesson, so the next archive
       would stop matching the existing page and duplicate it.
    2. **`· p.{page_start}`** when the title repeats. These textbooks reuse
       rubric headings as section titles (`Вспомните` ×10 in one grade) with a
       NULL section_number, so the bare title is not an identifier.
    3. **`· {short id}`** when the page number ALSO repeats. Part I and Part II
       of one textbook share a container and both restart pagination, so
       `Вспомните` sits at page 2 in both — measured, not hypothesised. Neither
       page_start nor order_index separates that pair, so the last resort is the
       TOC row's own id, which is unique by construction.
    """
    # Target and siblings MUST be titled by the same rule (incl. the
    # chapter_title fallback), or a row fails to match itself, the count reads
    # 0, and the suffix that prevents the collision is silently suppressed.
    base = _sibling_title((
        section.section_number, section.section_title,
        getattr(section, "chapter_title", "") or "", None, None,
    ))
    same_base = [r for r in siblings if _normalize(_sibling_title(r)) == _normalize(base)]
    if len(same_base) <= 1:
        return base

    page_start = getattr(section, "page_start", None)
    if page_start is not None:
        candidate = f"{base} · p.{page_start}"
        # Does the page number actually separate us from the others?
        if sum(1 for r in same_base if r[3] == page_start) <= 1:
            return candidate
    else:
        candidate = base

    return f"{candidate} · {str(section.id)[:8]}"


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
    lesson_page_id: Optional[str] = None,
    backfill_lesson_id: bool = True,
) -> tuple[Optional[str], str]:
    """Synchronous Notion I/O. Unconditionally creates the path:
    Subject → 'Generated Homeworks' → <lesson_title> → 'Homework', then the
    grouped page layout (`_HOMEWORK_LAYOUT`): Case-Based Preview, Flashcards
    (flashcards + memory-check inline), Gamified Practices (container of game
    sub-pages), Boss Arena, Reflection. Idempotent: a page that already has
    content is skipped. When `replace` is True, a populated leaf page is
    cleared (`clear_content_blocks`) and rewritten instead of skipped — used
    by the operator force-refresh path. Returns `(lesson_id, homework_id)` —
    `lesson_id` is `None` when it could not be determined (reuse branch,
    no `lesson_page_id` given, and either the `get_page_parent` backfill
    failed or `backfill_lesson_id=False` skipped it entirely — e.g. the repair
    sweep, which discards `lesson_id` and would otherwise waste a
    rate-limited Notion call for nothing)."""
    if homework_page_id:
        # Identity from the DB beats identity from the title. A section that
        # already owns a page reuses it directly — this is what stops a lesson
        # whose title IS ambiguous from being re-keyed onto a fresh suffixed
        # page and orphaning the content already filed under the old one.
        homework_id = homework_page_id
        if lesson_page_id:
            lesson_id = lesson_page_id
        elif backfill_lesson_id:
            # Backfill for the ~3,200 already-archived sections whose
            # notion_lesson_page_id is NULL: the Homework sub-page's parent
            # IS the lesson page. Best-effort — a failure here just skips the
            # stamp this run; it self-heals on the next archive.
            try:
                lesson_id = client.get_page_parent(homework_page_id)
            except Exception:  # noqa: BLE001 - best-effort backfill
                log.warning(
                    "notion: get_page_parent backfill failed for %s", homework_page_id,
                    exc_info=True,
                )
                lesson_id = None
        else:
            lesson_id = None
    else:
        container_id, _ = find_or_create(client, subject_page_id, CONTAINER_TITLE)
        lesson_id = lesson_page_id or find_or_create(client, container_id, lesson_title)[0]
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
    return lesson_id, homework_id


async def _push_with_retry(*, client, subject_page_id, lesson_title, phase_md, replace: bool = False,
                          homework_page_id: Optional[str] = None,
                          lesson_page_id: Optional[str] = None,
                          backfill_lesson_id: bool = True) -> tuple[Optional[str], str]:
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
                lesson_page_id=lesson_page_id,
                backfill_lesson_id=backfill_lesson_id,
            )
        except Exception as exc:  # noqa: BLE001 - retried, then recorded as a skip
            last_exc = exc
            log.warning("notion: push attempt %d/%d failed: %s",
                        attempt, _PUSH_MAX_ATTEMPTS, exc)
            if attempt < _PUSH_MAX_ATTEMPTS:
                await asyncio.sleep(_PUSH_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)))
    assert last_exc is not None
    raise last_exc


def _teacher_deck_blocks(client: NotionClientWrapper, deck) -> list[dict]:
    """Blocks for the Teacher Deck page: the readable page is the PRIMARY
    deliverable, with the rendered PDF attached at the top when renderable.

    Only the PDF *render* is inside the try/except — a missing native lib
    (pango/cairo/...) degrades to a page-only write. The `upload_bytes` call
    is deliberately OUTSIDE the try: a transient Notion 429/network blip must
    PROPAGATE into `_push_teacher_with_retry` (which exists to retry it), not
    silently degrade to a PDF-less page that the next archive then skips
    forever via `page_has_content`."""
    md = render_teacher_deck_markdown(deck)
    content = blocks.markdown_to_notion_blocks(md)          # readable page: PRIMARY deliverable
    try:                                                    # ONLY the render is swallowed
        pdf = render_teacher_deck_pdf(deck)                 # missing pango/native lib → page-only
    except Exception as exc:  # noqa: BLE001
        log.warning("notion: teacher-deck PDF render failed, writing page without attachment: %s", exc)
        return content
    # Upload is OUTSIDE the try: a transient Notion 429 / network blip must
    # propagate into _push_teacher_with_retry (which exists to retry it), NOT
    # silently degrade to a PDF-less page that the next archive then skips
    # forever via page_has_content. Distinct filename from the FE slide
    # export (df4ee5f, same {grade}-sinf {n}-mavzu {title}) — this is the
    # lesson-plan document.
    fname = f"{deck.meta.grade}-sinf {deck.meta.topic_number}-mavzu {deck.meta.topic_title} — dars ishlanma.pdf"
    upload = client.upload_bytes(pdf, fname, "application/pdf")
    return [blocks.make_file_upload_block(upload, fname), blocks.make_divider(), *content]


def _push_teacher_deck_to_notion(
    *,
    client: NotionClientWrapper,
    subject_page_id: str,
    lesson_title: str,
    deck,
    find_or_create: Callable = find_or_create,
    replace: bool = False,
    lesson_page_id: Optional[str] = None,
) -> tuple[str, str]:
    """Create/adopt Subject → 'Generated Homeworks' → <lesson> → 'Teacher Deck', then write the
    readable deck page (+ PDF attachment when renderable). Idempotent: a populated page is skipped
    unless `replace`. Returns `(lesson_id, deck_page_id)`."""
    container_id, _ = find_or_create(client, subject_page_id, CONTAINER_TITLE)
    lesson_id = lesson_page_id or find_or_create(client, container_id, lesson_title)[0]
    deck_id, _ = find_or_create(client, lesson_id, "Teacher Deck")
    populated = client.page_has_content(deck_id)
    if populated and not replace:
        return lesson_id, deck_id               # idempotent skip
    # Build (render + upload) BEFORE clearing, so a render/upload failure on a force re-archive can
    # never leave the page emptied — clear_content_blocks runs only once the new body is in hand.
    body = _teacher_deck_blocks(client, deck)
    if populated:                                # replace path
        client.clear_content_blocks(deck_id)
    client.append_block_children(deck_id, body)
    return lesson_id, deck_id


async def _push_teacher_with_retry(*, client, subject_page_id, lesson_title, deck,
                                   replace: bool = False,
                                   lesson_page_id: Optional[str] = None) -> tuple[str, str]:
    """Run the idempotent teacher-deck push in a worker thread, retrying transient failures with
    exponential backoff (mirrors `_push_with_retry`). Re-raises the last exception if all fail."""
    last_exc: Optional[Exception] = None
    for attempt in range(1, _PUSH_MAX_ATTEMPTS + 1):
        try:
            return await asyncio.to_thread(
                _push_teacher_deck_to_notion,
                client=client, subject_page_id=subject_page_id, lesson_title=lesson_title,
                deck=deck, replace=replace, lesson_page_id=lesson_page_id,
            )
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            log.warning("notion: teacher push attempt %d/%d failed: %s",
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


def _claim_token_ok(job, claim_token: Optional[UUID]) -> bool:
    """Fenced-lease precondition for the AUTOMATIC (pipeline) archive path,
    layered ON TOP OF the 0129 idempotency/direction guards below — it never
    replaces them. ``claim_token is None`` means a token-less caller (the
    operator/batch re-archive endpoints): always ok, behavior unchanged.
    A presented token is only honored while the job is still `done` under
    THAT exact token — an obsolete worker whose job was reclaimed (new
    claim_token minted, or status no longer `done`) must not publish or
    stamp a pointer."""
    return claim_token is None or (job.status == "done" and job.claim_token == claim_token)


async def archive_job(
    job_id: UUID, *, claim_token: Optional[UUID] = None, force: bool = False
) -> None:
    """Best-effort entry point called from the pipeline after job is `done`.
    With `force=True` (operator re-archive), an already-archived job is NOT
    short-circuited and its leaf pages are cleared and rewritten (replace mode).

    ``claim_token``: optional winning-lease fence for the automatic pipeline
    call site (threaded from the run's ``lease``). When present, publish +
    pointer-update proceed only while the job is still `done` under that exact
    token — see ``_claim_token_ok``. Token-less callers (operator/batch
    re-archive) are unaffected."""
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
            # is_teacher branches archive_job below at four points: content
            # gather (phase_md vs content_json), the skip guard, the push
            # (_push_with_retry vs _push_teacher_with_retry), and the
            # pointer-update stamps (homework columns vs
            # notion_lesson_page_id/notion_teacher_deck_job_id). Subject-page
            # resolution is kind-independent (keys on subject/grade/language,
            # which a teacher deck has too) — the real reason decks were
            # previously skipped here was the missing `output_md` deliverable
            # (they carry `content_json` instead), not a missing `_PAGES` entry.
            is_teacher = getattr(job, "kind", "homework") == "teacher_material"
            if not _claim_token_ok(job, claim_token):
                log.info(
                    "notion: job %s claim_token stale (status=%s) — obsolete worker, "
                    "skipping auto-archive", job_id, job.status,
                )
                return
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
            siblings = await toc_repo.titles_for_subject_grade(
                session, subject=job.subject, grade=book.grade,
            )
            lesson_title = resolve_lesson_title(section, siblings)
            if lesson_title != _lesson_title(section.section_number, section.section_title):
                log.info(
                    "notion: lesson title is repeated at %s|%s — filing as %r",
                    job.subject, book.grade, lesson_title,
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
            # Captured inside the session: the push runs after it closes.
            # section_page_id: homework-only (the Homework sub-page id).
            # section_lesson_page_id: SHARED by both kinds — the one Lesson
            # Topic page that is parent of both the Homework and Teacher Deck
            # sub-pages, so either kind can adopt the page the other created.
            section_page_id = section.notion_homework_page_id
            section_lesson_page_id = section.notion_lesson_page_id
            if is_teacher:
                first_archive = section.notion_teacher_deck_job_id is None
                prior_job_id = section.notion_teacher_deck_job_id
            else:
                first_archive = section.notion_homework_page_id is None
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
            phases = await phase_repo.list_for_job(session, job_id)
            phase_md: dict[str, str] = {}
            deck: Optional[TeacherDeck] = None
            if is_teacher:
                for p in phases:
                    if p.phase_name == "teacher-deck" and p.status == "done" and p.content_json is not None:
                        try:
                            deck = TeacherDeck.model_validate(p.content_json)
                        except ValidationError:
                            deck = None
                        break
            else:
                phase_md = {
                    p.phase_name: (p.output_md or "")
                    for p in phases
                    if p.status == "done" and p.phase_name != "extract" and (p.output_md or "").strip()
                }
        # session closed — do NOT hold a DB connection during the Notion push
        if is_teacher:
            if deck is None:
                log.info("notion: job %s has no teacher deck content — skipping", job_id)
                async with SessionLocal() as session:
                    await jobs_repo.set_notion_skip_reason(
                        session, job_id, "no teacher deck content")
                    await session.commit()
                return
        elif not phase_md:
            log.info("notion: job %s has no completed phase outputs — skipping", job_id)
            async with SessionLocal() as session:
                await jobs_repo.set_notion_skip_reason(
                    session, job_id, "no completed phase outputs")
                await session.commit()
            return

        # FOOTGUN: `force` clears and rewrites the leaf pages it finds. If this
        # section still carries a colliding `notion_homework_page_id` from before
        # the disambiguation fix, that page belongs to ANOTHER lesson, and force
        # would destroy its content. Repair the stamps before any forced
        # re-archive of pre-fix rows.
        do_replace = force or auto_replace

        client = NotionClientWrapper(api_key=settings.notion_api_key)
        try:
            if is_teacher:
                lesson_id, deck_id = await _push_teacher_with_retry(
                    client=client,
                    subject_page_id=subject_page_id,
                    lesson_title=lesson_title,
                    deck=deck,
                    replace=do_replace,
                    # Reuse the shared lesson page, so an ambiguous title
                    # cannot re-key the deck onto a fresh suffixed page.
                    lesson_page_id=section_lesson_page_id,
                )
            else:
                lesson_id, homework_id = await _push_with_retry(
                    client=client,
                    subject_page_id=subject_page_id,
                    lesson_title=lesson_title,
                    phase_md=phase_md,
                    replace=do_replace,
                    # Reuse the page this section already owns, so an ambiguous
                    # title cannot re-key it onto a fresh suffixed page.
                    homework_page_id=section_page_id,
                    lesson_page_id=section_lesson_page_id,
                )
        except Exception as exc:  # noqa: BLE001 - push exhausted retries; record + give up
            log.warning("notion: push failed for job %s after %d attempts (non-fatal)",
                        job_id, _PUSH_MAX_ATTEMPTS, exc_info=True)
            await _record_skip(job_id, f"push error: {type(exc).__name__}")
            return

        async with SessionLocal() as session:
            # Re-check the token fence in THIS (pointer-update) session too —
            # a check only in the first session leaves a TOCTOU window (the
            # job can be reclaimed by another worker during the Notion push,
            # which runs with no DB session held) before the pointer write.
            # Token-less callers (claim_token is None) skip the extra fetch —
            # behavior stays exactly today's.
            if claim_token is not None:
                fresh_job = await jobs_repo.get(session, job_id)
                if fresh_job is None or not _claim_token_ok(fresh_job, claim_token):
                    log.info(
                        "notion: job %s claim_token stale at pointer-update (status=%s) — "
                        "obsolete worker, discarding push result, writing no pointer",
                        job_id, fresh_job.status if fresh_job is not None else "gone",
                    )
                    return
            if is_teacher:
                if lesson_id is not None and section_lesson_page_id is None:
                    await toc_repo.set_notion_lesson_page_id(session, section_id, lesson_id)
                if first_archive or do_replace:
                    await toc_repo.set_notion_teacher_deck_job(session, section_id, job_id)
                await jobs_repo.set_notion_archived(session, job_id, _utcnow())
            else:
                await toc_repo.set_notion_homework_page_id(session, section_id, homework_id)
                if lesson_id is not None and section_lesson_page_id is None:
                    await toc_repo.set_notion_lesson_page_id(session, section_id, lesson_id)
                if first_archive or do_replace:
                    await toc_repo.set_notion_archived_job(session, section_id, job_id)
                await jobs_repo.set_notion_archived(session, job_id, _utcnow())
            await session.commit()
        if is_teacher:
            log.info("notion: archived job %s → Teacher Deck page %s", job_id, deck_id)
        else:
            log.info("notion: archived job %s → Homework page %s", job_id, homework_id)
    except Exception:
        log.warning("notion: archive failed for job %s (non-fatal)", job_id, exc_info=True)
        await _record_skip(job_id, "archive error")
