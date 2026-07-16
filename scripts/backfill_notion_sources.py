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
explicitly — this script refuses to start otherwise. It never falls back to
`.env`/config defaults, so an operator always points it at the intended
database on purpose (see `_require_database_url` below, checked before any
other import that could trigger `config.py`'s `load_dotenv`).

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

import os
import sys

# Checked BEFORE any other import (including our own modules below, several
# of which transitively import `app.config` — whose module-level
# `load_dotenv(override=False)` would populate `DATABASE_URL` from `.env` if
# it wasn't already in the environment, silently defeating this guard). This
# capture happens against the raw environment, so a missing DATABASE_URL is
# caught here or not at all.
_DATABASE_URL = os.environ.get("DATABASE_URL")


def _require_database_url() -> str:
    if not _DATABASE_URL:
        print(
            "ERROR: DATABASE_URL is not set. Refusing to start — this script "
            "WRITES book_notion_sources rows (with --apply), so it never "
            "falls back to .env/config defaults; point it at the intended "
            "database explicitly, e.g.\n"
            "  DATABASE_URL=postgresql+asyncpg://edu:edu@localhost:5433/edu_homework "
            "uv run python -m scripts.backfill_notion_sources --grade 9",
            file=sys.stderr,
        )
        raise SystemExit(1)
    return _DATABASE_URL


import argparse  # noqa: E402
import asyncio  # noqa: E402
import hashlib  # noqa: E402
from dataclasses import dataclass  # noqa: E402
from uuid import UUID  # noqa: E402

import httpx  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings  # noqa: E402
from app.models import Book  # noqa: E402
from app.repositories import notion_sources as notion_sources_repo  # noqa: E402
from app.services import notion_fetch  # noqa: E402
from app.services.notion.client import NotionClientWrapper  # noqa: E402
from app.services.notion_fetch import _grade_number_from_title  # noqa: E402


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


def _collect_candidates(
    client: NotionClientWrapper, lessons_root: str, grade_filter: str | None,
) -> list[Candidate]:
    """Crawl grades -> available_languages -> every part's textbook
    candidates, exactly like the live `/notion/*` routes. `grade_filter`
    (e.g. "9") restricts to one grade — recommended, see the module
    docstring's download-cost note."""
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


def _download_and_hash(client: NotionClientWrapper, candidate: Candidate) -> str:
    """Downloads the candidate's PDF via the same resolved-candidate path
    `download_textbook` uses (size-cap enforced there), returns its sha256."""
    downloaded = notion_fetch.download_textbook(
        client, candidate.part_page_id, block_id=candidate.block_id,
    )
    return hashlib.sha256(downloaded.body).hexdigest()


async def _load_existing_books(session: AsyncSession) -> list[ExistingBook]:
    rows = (await session.execute(
        select(Book.id, Book.subject, Book.content_sha256)
    )).all()
    return [ExistingBook(book_id=r.id, subject=r.subject, content_sha256=r.content_sha256)
            for r in rows]


async def run(*, grade: str | None, apply: bool) -> int:
    database_url = _require_database_url()
    if not settings.notion_api_key:
        print("ERROR: NOTION_API_KEY is not configured.", file=sys.stderr)
        return 1

    engine = create_async_engine(database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    client = NotionClientWrapper(api_key=settings.notion_api_key)

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
    await engine.dispose()
    return 0


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill book_notion_sources links by matching Notion textbook "
            "candidates against existing books (content_sha256 + subject). "
            "Dry-run by default. WARNING: even in dry-run, this DOWNLOADS "
            "every matched candidate's PDF (hundreds of MB for a full-tree "
            "run) to hash it — pass --grade to scope a run to one grade."
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
    return asyncio.run(run(grade=args.grade, apply=args.apply))


if __name__ == "__main__":
    raise SystemExit(main())
