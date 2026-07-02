"""CQ-A acceptance smoke — boundary + reflection + L2 bridge, over transport='api'
(Vertex SDK), in-process, no server.

Three checks against a real lesson (book 860e86aa, §17 "Pifagor teoremasi..."):
  1. boundary   — boss-arena must not (a) echo the injected curriculum-boundary
                  note verbatim, or (b) reach into §18's converse/"verify a right
                  angle" material (heuristic WARN, human eyeballs the excerpt).
  2. reflection — same lesson; must not echo the boundary note, and should not
                  pre-assert an attempt outcome ("Needs Retry" etc — WARN).
  3. L2 bridge  — pure prompt render (no model call): english/flashcards at
                  output_language="ru" must scaffold in Russian, not Uzbek.

DB: uses the ``.env`` ``DATABASE_URL`` (production edu_copy, where book 860e86aa
lives) via the normal ``app.config.settings`` / ``app.db.SessionLocal`` — never
hardcode edu_homework. Checks 1-2 degrade to a clear SKIP if that book/section
isn't present in whichever DB is connected, so the script stays runnable and
informative off edu_copy too. Check 3 always runs (no DB, no model call).

Checks 1-2 make REAL BILLED model calls (transport=api). Do not run this
outside the CQ-A acceptance gate without the operator's go-ahead (see
CLAUDE.md's no-homework-spam-money rule — two single-phase calls here, not a
mass generation, but still real spend).

Run: uv run python -m scripts.cqa_prompt_smoke
"""
from __future__ import annotations

import asyncio
import sys
from typing import Optional
from uuid import UUID

from sqlalchemy import String, cast, select

from app.config import settings  # noqa: F401 — triggers load_dotenv; DATABASE_URL source
from app.db import SessionLocal
from app.models import Book, HomeworkJob, PhaseOutput, TOCEntry
from app.repositories import launch_defaults as launch_defaults_repo
from app.repositories import toc_entries as toc_repo
from app.services import agent, book_fetch
from app.services.pipeline import _inject_grade, _inject_lesson_boundary
from app.services.prompts import get_prompt

BOOK_ID_PREFIX = "860e86aa"
# transport=api requires an explicit model. gemini-2.5-flash = the campaign
# content-tier model; the boundary/reflection fixes are model-agnostic prompt edits.
GEN_MODEL = "gemini-2.5-flash"
# The audit's packet #5 lesson: §17 "Pifagor teoremasi va uning turli isbotlari"
# (pp 41-43). Its successor is §18 "…teskari teorema" (the converse) — exactly the
# next-lesson material boss-arena must NOT reach for. NB: this book stores
# section_number as "17-mavzu", and several rows contain "Pifagor" (§4/§17/§18/§19),
# so we pin the §17 row by its distinctive title ("turli isbot") to avoid selecting
# the wrong lesson (a wasted billed call).
SECTION_NUMBER = "17-mavzu"
SECTION_TITLE_HINT = "turli isbot"

# markers the boundary note injects — must NEVER appear in student-facing output
_NOTE_MARKERS = ("CURRICULUM BOUNDARY", "The NEXT lesson in this textbook")

# §18-leakage heuristic signals (converse / "verify a right angle from side
# lengths") — WARN only, never hard-fail: a string match is not a semantic proof,
# the human eyeballs the printed excerpt.
_CONVERSE_LEAKAGE_SIGNALS = (
    "teskari",
    "converse",
    "verify a right angle",
    "to'g'ri burchak ekanini",
)

# reflection pre-asserted-outcome leakage signals (R21.5 regression watch) —
# WARN only, same reasoning as above.
_REFLECTION_OUTCOME_SIGNALS = ("Needs Retry", "not passed", "ikkilanish")


def _scan(text: str, markers: tuple[str, ...]) -> list[str]:
    return [m for m in markers if m in text]


# ─────────────────────────────────────────────────────────────────────
# DB lookups
# ─────────────────────────────────────────────────────────────────────


async def _load_lesson() -> Optional[tuple[Book, TOCEntry, Optional[str]]]:
    """Resolve book 860e86aa's §17 TOC entry + its successor's title against
    whichever DB DATABASE_URL points at. Returns None if not found (caller
    degrades to SKIP)."""
    async with SessionLocal() as session:
        book_stmt = select(Book).where(cast(Book.id, String).like(f"{BOOK_ID_PREFIX}%"))
        book = (await session.execute(book_stmt)).scalars().first()
        if book is None:
            return None
        toc_stmt = (
            select(TOCEntry)
            .where(
                TOCEntry.book_id == book.id,
                (TOCEntry.section_number == SECTION_NUMBER)
                | (TOCEntry.section_title.ilike(f"%{SECTION_TITLE_HINT}%")),
            )
            .order_by(TOCEntry.order_index)
        )
        section = (await session.execute(toc_stmt)).scalars().first()
        if section is None:
            return None
        nxt = await toc_repo.get_next_in_book(session, book.id, section.order_index)
        next_title = nxt.section_title if nxt else None
        return book, section, next_title


async def _reused_extract(section_id: UUID) -> Optional[str]:
    """Prefer reuse: the most recent `done` extract phase_output for ANY job on
    this section (any producing provider/model — unlike the pipeline's strict
    cross-job cache key, we just want real lesson text without spending a fresh
    extract call). Returns output_md or None."""
    async with SessionLocal() as session:
        stmt = (
            select(PhaseOutput)
            .join(HomeworkJob, HomeworkJob.id == PhaseOutput.job_id)
            .where(
                HomeworkJob.toc_entry_id == section_id,
                PhaseOutput.phase_name == "extract",
                PhaseOutput.status == "done",
                PhaseOutput.output_md.is_not(None),
            )
            .order_by(PhaseOutput.completed_at.desc())
            .limit(1)
        )
        row = (await session.execute(stmt)).scalars().first()
        return row.output_md if row else None


async def _live_extract(book: Book, section: TOCEntry) -> str:
    """Fallback: run a real (billed) extract over transport=api when no done
    extract phase_output exists yet to reuse. Mirrors the pipeline's normal
    (non-scanned) extract branch (app/services/pipeline.py _execute_phase) —
    role provider/model come from the launch_defaults DB row, same as the
    real pipeline (NOT hardcoded, NOT read from settings — see
    app/repositories/launch_defaults.py)."""
    async with SessionLocal() as session:
        ld = await launch_defaults_repo.get(session)
    pdf_path = await asyncio.to_thread(
        book_fetch.ensure_book_pdf_sync, book.id, book.file_size_bytes
    )
    book_text = await asyncio.to_thread(agent.read_whole_book_text, pdf_path)
    text, _tin, _tout = await agent.summarize_lesson(
        provider=ld.extract_provider,
        model=ld.extract_model,
        book_text=book_text,
        section_title=section.section_title,
        section_number=section.section_number or "",
        page_start=section.page_start,
        page_end=section.page_end,
        homework_job_id=None,  # smoke has no real job row; FK is nullable + best-effort
        phase_output_id=None,
        transport="api",
    )
    return text


async def _build_lesson_context(book: Book, section: TOCEntry, next_title: Optional[str]) -> str:
    reused = await _reused_extract(section.id)
    if reused is not None:
        print(f"  extract: REUSED existing done phase_output ({len(reused)} chars)")
        raw_context = reused
    else:
        print("  extract: no existing done extract found for this section — running a LIVE extract (billed)")
        raw_context = await _live_extract(book, section)
    ctx = _inject_grade(raw_context, book.grade)
    ctx = _inject_lesson_boundary(ctx, next_title)
    return ctx


# ─────────────────────────────────────────────────────────────────────
# Checks
# ─────────────────────────────────────────────────────────────────────


async def check_boundary(book: Book, section: TOCEntry, next_title: Optional[str]) -> bool:
    print("\n" + "=" * 72)
    print("CHECK 1 — boundary (boss-arena, §17 Pifagor teoremasi)")
    print("=" * 72)
    lesson_context = await _build_lesson_context(book, section, next_title)
    prompt = get_prompt(book.subject, "boss-arena", output_language="uz")
    text, tin, tout = await agent.run_phase_prompt(
        provider="gemini", model=GEN_MODEL,
        phase_prompt=prompt, lesson_context=lesson_context, prior_outputs={},
        difficulty=None, phase_name="boss-arena", transport="api",
    )
    print(f"\n--- boss-arena output ({len(text)} chars, tokens in={tin} out={tout}) ---\n")
    print(text[:3000])

    note_hits = _scan(text, _NOTE_MARKERS)
    assert not note_hits, f"boundary note echoed verbatim into boss-arena output: {note_hits}"
    print("\n  PASS: no _NOTE_MARKERS echoed.")

    leak_hits = _scan(text.lower(), tuple(s.lower() for s in _CONVERSE_LEAKAGE_SIGNALS))
    if leak_hits:
        print(f"  WARN: possible §18 (converse) leakage signal(s) found: {leak_hits} — eyeball the excerpt above.")
    else:
        print("  OK: no converse/§18-leakage signal strings found (heuristic).")
    return True


async def check_reflection(book: Book, section: TOCEntry, next_title: Optional[str]) -> bool:
    print("\n" + "=" * 72)
    print("CHECK 2 — reflection (same lesson)")
    print("=" * 72)
    lesson_context = await _build_lesson_context(book, section, next_title)
    prompt = get_prompt(book.subject, "reflection", output_language="uz")
    text, tin, tout = await agent.run_phase_prompt(
        provider="gemini", model=GEN_MODEL,
        phase_prompt=prompt, lesson_context=lesson_context, prior_outputs={},
        difficulty=None, phase_name="reflection", transport="api",
    )
    print(f"\n--- reflection output ({len(text)} chars, tokens in={tin} out={tout}) ---\n")
    print(text[:3000])

    note_hits = _scan(text, _NOTE_MARKERS)
    assert not note_hits, f"boundary note echoed verbatim into reflection output: {note_hits}"
    print("\n  PASS: no _NOTE_MARKERS echoed.")

    outcome_hits = _scan(text, _REFLECTION_OUTCOME_SIGNALS)
    if outcome_hits:
        print(f"  WARN: possible pre-asserted attempt-outcome signal(s): {outcome_hits} — eyeball the excerpt above.")
    else:
        print("  OK: no pre-asserted-outcome signal strings found (heuristic).")
    return True


def check_l2_bridge() -> bool:
    print("\n" + "=" * 72)
    print("CHECK 3 — L2 scaffolding bridge (pure prompt render, no model call)")
    print("=" * 72)
    prompt = get_prompt("english", "flashcards", output_language="ru")
    bridge_lines = [ln for ln in prompt.splitlines() if "Russian" in ln or "Uzbek" in ln]
    print("\n".join(f"  {ln}" for ln in bridge_lines[:12]))

    assert "formal Russian" in prompt, (
        '"formal Russian" not found in english/flashcards prompt at output_language=ru'
    )
    assert 'formal Uzbek ("Siz")' not in prompt, (
        'stale formal Uzbek ("Siz") bridge still present at output_language=ru'
    )
    print('\n  PASS: scaffolding bridge is Russian; no stale Uzbek ("Siz") bridge.')
    return True


# ─────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────


async def _main() -> int:
    results: dict[str, bool] = {}

    lesson = await _load_lesson()
    if lesson is None:
        db_host = settings.database_url.split("@")[-1] if "@" in settings.database_url else "<unparsed>"
        print(
            f"SKIP checks 1-2: book with id prefix {BOOK_ID_PREFIX!r} / section "
            f"matching §{SECTION_NUMBER} or {SECTION_TITLE_HINT!r} not found against "
            f"the connected DB ({db_host}). Point DATABASE_URL at edu_copy (where "
            f"860e86aa lives) to run the real-model checks."
        )
    else:
        book, section, next_title = lesson
        print(f"Loaded book {book.id} (subject={book.subject}, grade={book.grade})")
        print(
            f"Section: §{section.section_number} {section.section_title!r} "
            f"(order_index={section.order_index})"
        )
        print(f"Successor lesson (curriculum-boundary target): {next_title!r}")

        for name, coro in (
            ("boundary", check_boundary(book, section, next_title)),
            ("reflection", check_reflection(book, section, next_title)),
        ):
            try:
                results[name] = await coro
            except Exception as exc:  # noqa: BLE001 — smoke script: report, keep going
                print(f"\n  FAIL ({name}): {exc!r}")
                results[name] = False

    try:
        results["l2_bridge"] = check_l2_bridge()
    except Exception as exc:  # noqa: BLE001
        print(f"\n  FAIL (l2_bridge): {exc!r}")
        results["l2_bridge"] = False

    print("\n" + "=" * 72)
    print("SUMMARY")
    print("=" * 72)
    if not results:
        print("  (no checks ran)")
        return 1
    for name, ok in results.items():
        print(f"  {name}: {'PASS' if ok else 'FAIL'}")

    if all(results.values()):
        print("\nSMOKE PASS")
        return 0
    print("\nSMOKE FAIL")
    return 1


def main() -> None:
    sys.exit(asyncio.run(_main()))


if __name__ == "__main__":
    main()
