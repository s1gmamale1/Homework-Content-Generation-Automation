"""Backfill `book_notion_sources` links for books that were ingested BEFORE
the (page,block) -> book mapping existed (worklog 0144 task 2 shipped the
mapping; anything uploaded/fetched earlier has no link row at all).

Crawls the configured Notion tree exactly like the live `/notion/*` routes
(`list_grades` -> `available_languages` per grade -> every part's textbook
candidates), downloads each candidate PDF (read-only against Notion),
sha256-hashes it, and matches UNIQUELY against existing `books` rows by
(content_sha256, subject):
  - exactly one match, not yet linked to that (page,block)  -> WOULD LINK
    (writes via `notion_sources_repo.upsert_link` only with --apply)
  - exactly one match, already linked to it                 -> already linked
    (no-op either way)
  - zero matches                                            -> no match
  - more than one match                                     -> ambiguous
    (skipped — an operator call, not this script's)

**Dry-run by default.** Pass --apply to actually write rows. Either way this
DOWNLOADS every candidate's PDF bytes to hash it — a full-tree run pulls
hundreds of MB across the whole Notion workspace. Use --grade N to bound a
run to one grade at a time (recommended for the first few runs).

DATABASE_URL is read directly from the environment and MUST be set
explicitly — this script refuses to start otherwise (one clear error line,
exit 2, no traceback). It never falls back to `.env`/config defaults, so an
operator always points it at the intended database on purpose. To make that
hold, **this module imports NOTHING from `app.*` at module level**: every
`app` import happens lazily inside `run()`, strictly AFTER the DATABASE_URL
check — `app.config`'s import-time `load_dotenv` (which walks UP parent
directories via `find_dotenv`) can otherwise either inject an outer `.env`'s
DATABASE_URL silently or crash with a raw pydantic traceback when no `.env`
is reachable at all.

The target DB must already carry migration 0048 (`book_notion_sources`).
A cheap preflight verifies the table exists BEFORE any Notion work — an
un-migrated target fails in one line up front, not with an asyncpg
UndefinedTableError after hundreds of MB of downloads.

Run (dry-run, one grade):
  DATABASE_URL=postgresql+asyncpg://edu:edu@localhost:5433/edu_homework \\
  uv run python -m scripts.backfill_notion_sources --grade 9

Apply (writes links):
  DATABASE_URL=postgresql+asyncpg://edu:edu@localhost:5433/edu_homework \\
  uv run python -m scripts.backfill_notion_sources --grade 9 --apply

Full tree (no --grade): every grade under `settings.notion_lessons_root` —
expect a long run and a large download; scope with --grade whenever possible.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

# ─── preflights (unit-tested; each failure = one clear line + exit 2) ───


class PreflightError(Exception):
    """A refuse-to-start condition. `main` prints `ERROR: <message>` to
    stderr and exits 2 — the operator never sees a traceback for these."""


DATABASE_URL_ERROR = (
    "DATABASE_URL must be set explicitly — refusing to guess the target DB. "
    "Example: DATABASE_URL=postgresql+asyncpg://edu:edu@localhost:5433/edu_homework "
    "uv run python -m scripts.backfill_notion_sources --grade 9"
)

NOTION_KEY_ERROR = (
    "NOTION_API_KEY is not configured — set it in the environment (or .env) "
    "before running the backfill."
)

MIGRATION_ERROR = (
    "book_notion_sources missing — apply migration 0048 "
    "(alembic upgrade head) on the target DB first"
)


def preflight_database_url(environ: Mapping[str, str]) -> str:
    """The explicit DATABASE_URL, or `PreflightError`. Called against the RAW
    process environment BEFORE any `app.*` import (see module docstring —
    `app.config`'s load_dotenv must never get the chance to fill this in)."""
    url = environ.get("DATABASE_URL")
    if not url:
        raise PreflightError(DATABASE_URL_ERROR)
    return url


def preflight_notion_api_key(value: str | None) -> str:
    """The configured NOTION_API_KEY, or `PreflightError`. Unlike
    DATABASE_URL this MAY come from `.env` (read-only Notion access is not
    the footgun the write-target DB is) — this check only guarantees a
    missing key is one clear line, not a traceback."""
    if not value:
        raise PreflightError(NOTION_KEY_ERROR)
    return value


async def assert_book_notion_sources_exists(session) -> None:
    """Raise `PreflightError` when the target DB was never migrated to 0048.

    `to_regclass` resolves a table name to its regclass (search_path-aware)
    or NULL — it never raises for a missing table, so this stays a trivial
    SELECT rather than a try/except around UndefinedTableError. Runs BEFORE
    any Notion crawl/download so an un-migrated target costs one query, not
    hundreds of MB."""
    from sqlalchemy import text  # deferred with the rest of the heavy imports

    exists = (
        await session.execute(text("SELECT to_regclass('book_notion_sources')"))
    ).scalar()
    if exists is None:
        raise PreflightError(MIGRATION_ERROR)


# ─── pure matching decision (TDD'd in tests/test_backfill_notion_sources.py) ───

@dataclass(frozen=True)
class ExistingBook:
    """One `books` row, trimmed to the columns the match needs."""

    book_id: UUID
    subject: str
    content_sha256: str


@dataclass(frozen=True)
class LinkDecision:
    action: str  # "would_link" | "already_linked" | "no_match" | "ambiguous"
    book_id: UUID | None
    reason: str


def decide_link(
    *,
    candidate_sha256: str,
    candidate_subject: str,
    existing_books: list[ExistingBook],
    linked_book_id: UUID | None,
) -> LinkDecision:
    """Pure decision: does a downloaded candidate resolve UNIQUELY to exactly
    one existing book by (content_sha256, subject)?

    `linked_book_id` is whatever this candidate's own (page, block) is
    CURRENTLY linked to (looked up by the caller via
    `notion_sources_repo.links_for_sources`, batched for the whole run) —
    `None` if unlinked. It only changes the verdict between `would_link` and
    `already_linked`; it plays no role in finding the match itself.
    """
    matches = [
        b for b in existing_books
        if b.subject == candidate_subject and b.content_sha256 == candidate_sha256
    ]
    if not matches:
        return LinkDecision("no_match", None, "no book matches this subject+sha256")
    if len(matches) > 1:
        ids = ", ".join(str(b.book_id) for b in matches)
        return LinkDecision(
            "ambiguous", None,
            f"{len(matches)} books share this subject+sha256 ({ids}) — resolve manually",
        )
    matched = matches[0]
    if linked_book_id == matched.book_id:
        return LinkDecision(
            "already_linked", matched.book_id, f"already linked to book {matched.book_id}",
        )
    return LinkDecision(
        "would_link", matched.book_id, f"unique match -> book {matched.book_id}",
    )


# ─── script glue (download, hash, DB I/O — not unit tested; see module docstring) ───

@dataclass
class Candidate:
    app_subject: str
    grade_title: str
    lang: str
    part_page_id: str
    page_id: str
    block_id: str
    filename: str


def _collect_candidates(client, lessons_root: str, grade_filter: str | None) -> list[Candidate]:
    """Crawl grades -> available_languages -> every part's textbook
    candidates, exactly like the live `/notion/*` routes. `grade_filter`
    (e.g. "9") restricts to one grade — recommended, see the module
    docstring's download-cost note."""
    from app.services import notion_fetch
    from app.services.notion_fetch import _grade_number_from_title

    out: list[Candidate] = []
    for grade in notion_fetch.list_grades(client, lessons_root):
        grade_number = _grade_number_from_title(grade["title"])
        if grade_filter is not None and grade_number != grade_filter:
            continue
        langs = notion_fetch.available_languages(client, grade["page_id"])
        for app_subject, lang_map in langs.items():
            for lang, avail in lang_map.items():
                for part in avail.get("parts", []):
                    for c in part.get("candidates", []):
                        out.append(Candidate(
                            app_subject=app_subject, grade_title=grade["title"], lang=lang,
                            part_page_id=part["page_id"], page_id=c["page_id"],
                            block_id=c["block_id"], filename=c["filename"],
                        ))
    return out


def _download_and_hash(client, candidate: Candidate) -> str:
    """Downloads the candidate's PDF via the same resolved-candidate path
    `download_textbook` uses (size-cap enforced there), returns its sha256."""
    from app.services import notion_fetch

    downloaded = notion_fetch.download_textbook(
        client, candidate.part_page_id, block_id=candidate.block_id,
    )
    return hashlib.sha256(downloaded.body).hexdigest()


async def _load_existing_books(session) -> list[ExistingBook]:
    from sqlalchemy import select

    from app.models import Book

    rows = (await session.execute(
        select(Book.id, Book.subject, Book.content_sha256)
    )).all()
    return [ExistingBook(book_id=r.id, subject=r.subject, content_sha256=r.content_sha256)
            for r in rows]


async def run(*, database_url: str, grade: str | None, apply: bool) -> int:
    # Heavy/app imports live HERE, after main()'s DATABASE_URL preflight:
    # importing app.config any earlier either injects an outer .env's
    # DATABASE_URL (silently defeating the explicit-target rule) or crashes
    # with a raw pydantic traceback when no .env is reachable at all.
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.config import settings
    from app.repositories import notion_sources as notion_sources_repo
    from app.services.notion.client import NotionClientWrapper

    preflight_notion_api_key(settings.notion_api_key)

    engine = create_async_engine(database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        # Migration preflight FIRST — before the Notion client is even built,
        # and long before any PDF download, so an un-migrated target DB fails
        # in one line up front.
        async with session_factory() as session:
            await assert_book_notion_sources_exists(session)

        try:
            client = NotionClientWrapper(api_key=settings.notion_api_key)
        except ValueError as exc:  # malformed key — same clean treatment as missing
            raise PreflightError(str(exc)) from exc

        print(f"crawling Notion tree{f' (grade {grade})' if grade else ' (FULL TREE)'}…")
        candidates = await asyncio.to_thread(
            _collect_candidates, client, settings.notion_lessons_root, grade,
        )
        print(f"{len(candidates)} textbook candidate(s) found; downloading + hashing each…")

        async with session_factory() as session:
            existing_books = await _load_existing_books(session)
            pairs = [(c.page_id, c.block_id) for c in candidates]
            links = await notion_sources_repo.links_for_sources(session, pairs)

            counts = {"would_link": 0, "already_linked": 0, "no_match": 0, "ambiguous": 0}
            rows: list[tuple[Candidate, LinkDecision]] = []
            for c in candidates:
                try:
                    sha = await asyncio.to_thread(_download_and_hash, client, c)
                except Exception as exc:  # noqa: BLE001 - report and keep going
                    print(f"  DOWNLOAD FAILED  {c.app_subject}/{c.lang} {c.filename!r}: {exc}")
                    continue
                key = (
                    notion_sources_repo.normalize_notion_id(c.page_id),
                    notion_sources_repo.normalize_notion_id(c.block_id),
                )
                decision = decide_link(
                    candidate_sha256=sha, candidate_subject=c.app_subject,
                    existing_books=existing_books, linked_book_id=links.get(key),
                )
                counts[decision.action] += 1
                rows.append((c, decision))
                tag = decision.action.upper().replace("_", " ")
                print(f"  {tag:<14} {c.app_subject}/{c.lang} {c.filename!r} — {decision.reason}")

            if apply:
                applied = 0
                for c, decision in rows:
                    if decision.action != "would_link":
                        continue
                    await notion_sources_repo.upsert_link(
                        session, book_id=decision.book_id,
                        notion_page_id=c.page_id, notion_block_id=c.block_id,
                    )
                    applied += 1
                await session.commit()
                print(f"\napplied {applied} link(s).")
            else:
                print(f"\nDRY RUN — would link {counts['would_link']}. Pass --apply to write.")

        print(
            f"\nsummary: would_link={counts['would_link']} already_linked={counts['already_linked']} "
            f"no_match={counts['no_match']} ambiguous={counts['ambiguous']}"
        )
    finally:
        await engine.dispose()
    return 0


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill book_notion_sources links by matching Notion textbook "
            "candidates against existing books (content_sha256 + subject). "
            "Dry-run by default. WARNING: even in dry-run, this DOWNLOADS "
            "every matched candidate's PDF (hundreds of MB for a full-tree "
            "run) to hash it — pass --grade to scope a run to one grade. "
            "Requires an explicit DATABASE_URL env var (never .env-defaults) "
            "and a target DB already migrated to 0048."
        ),
    )
    parser.add_argument(
        "--grade", default=None,
        help="Restrict the crawl to one grade (e.g. '9'). Strongly recommended — "
             "omitting it downloads every textbook PDF in the whole Notion tree.",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually write book_notion_sources rows. Without this flag, the "
             "script only reports what it WOULD link.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        database_url = preflight_database_url(os.environ)
        return asyncio.run(run(database_url=database_url, grade=args.grade, apply=args.apply))
    except PreflightError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
