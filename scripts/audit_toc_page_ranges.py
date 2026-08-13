"""Find the TOC rows and books that the ingest-time guards would now catch —
in a catalogue that was built before those guards existed.

`app/services/toc_ingest_audit.py` stops both defects at ingestion, but only
for books ingested from now on. This is the read-only sweep for what is
already in the database:

  1. **Inverted page ranges** (`page_end < page_start`). The live sweep that
     motivated the guard found 8 such rows out of 9,634, all in two books, and
     5 of them exactly off-by-one — the shape that produced
     `lesson.extract (vision): cannot scope page range 35-34 of source.pdf`.
     Off-by-one rows are reported with the exact UPDATE that repairs them
     (`page_end = page_start`, a single-page lesson); deeper inversions are
     reported for a human, never auto-fixed. See the module docstring of
     `toc_ingest_audit` for why those two cases are treated differently.

  2. **NULL page ranges** — 413 rows live. These are LEGITIMATE (chapter
     umbrellas, end-matter, and a last entry with no successor to derive an end
     from), so the report characterises them rather than flagging them: how
     many are missing both bounds vs. only the end, and how they classify
     (`toc_classifier`). Read this section to confirm the population, not to
     "fix" it.

  3. **Scanned books** (`--probe`, needs the PDFs on local disk). Probes each
     book's text layer with the same bounded sampler and the same
     `settings.extract_min_chars_per_page` floor the pipeline uses, and pairs
     it with the count of page-less LESSON rows — the exact conjunction behind
     `lesson.extract: sparse text layer (1 chars/page) — likely scanned and no
     page range`, which cost 12 lessons across 5 hosts.

**Strictly read-only.** Nothing here writes: the repair for the off-by-one rows
is printed as SQL for an operator to run deliberately.

DATABASE_URL is read directly from the environment and MUST be set explicitly —
this script refuses to start otherwise (one clear line, exit 2, no traceback).
It never falls back to `.env`/config defaults, so an operator always points it
at the intended database on purpose. To make that hold, **this module imports
NOTHING from `app.*` at module level**: `app.config`'s import-time
`load_dotenv` walks UP parent directories, so an outer `.env` would otherwise
silently supply the target (and inside a git worktree that outer `.env` is a
real fleet host — see `tests/conftest.py`'s guard).

Run:
  DATABASE_URL=postgresql+asyncpg://edu:edu@localhost:5433/edu_homework \\
  uv run python -m scripts.audit_toc_page_ranges

With the text-layer probe (reads `<VAR_DIR>/books/<id>/source.pdf` for every
book that has one; skips the rest):
  DATABASE_URL=... uv run python -m scripts.audit_toc_page_ranges --probe

If you would rather not run the script at all, the inverted-row half is one
query — `INVERTED_SQL` below is printed verbatim by `--show-sql`.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

# Make BOTH documented invocations work: `python -m scripts.audit_toc_page_ranges`
# (repo root already on sys.path) AND `python scripts/audit_toc_page_ranges.py`
# (file-path form puts scripts/ on the path, not the repo root — the deferred
# `app.*` imports inside run() would raise ModuleNotFoundError without this).
_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


class PreflightError(Exception):
    """A refuse-to-start condition. `main` prints `ERROR: <message>` to stderr
    and exits 2 — the operator never sees a traceback for these."""


DATABASE_URL_ERROR = (
    "DATABASE_URL must be set explicitly — refusing to guess the target DB. "
    "Example: DATABASE_URL=postgresql+asyncpg://edu:edu@localhost:5433/edu_homework "
    "uv run python -m scripts.audit_toc_page_ranges"
)


def preflight_database_url(environ) -> str:
    """The explicit DATABASE_URL, or `PreflightError`. Called against the RAW
    process environment BEFORE any `app.*` import (see the module docstring)."""
    url = environ.get("DATABASE_URL")
    if not url:
        raise PreflightError(DATABASE_URL_ERROR)
    return url


# ─── the documented queries (usable standalone in psql) ───

INVERTED_SQL = """
-- Every TOC row whose page range cannot be true. `page_end = page_start - 1`
-- is the repairable off-by-one; anything else needs a human.
SELECT b.id            AS book_id,
       b.subject,
       b.grade,
       b.original_filename,
       t.id            AS toc_entry_id,
       t.order_index,
       t.section_number,
       t.section_title,
       t.page_start,
       t.page_end,
       (t.page_end = t.page_start - 1) AS off_by_one
FROM   toc_entries t
JOIN   books b ON b.id = t.book_id
WHERE  t.page_start IS NOT NULL
  AND  t.page_end   IS NOT NULL
  AND  t.page_end   <  t.page_start
ORDER  BY b.original_filename, t.order_index;
"""

NULL_RANGE_SQL = """
-- Characterise the NULL-range population (legitimate: chapter umbrellas,
-- end-matter, and a last entry with no successor to derive an end from).
SELECT count(*) FILTER (WHERE page_start IS NULL AND page_end IS NULL) AS both_null,
       count(*) FILTER (WHERE page_start IS NOT NULL AND page_end IS NULL) AS end_only_null,
       count(*) FILTER (WHERE page_start IS NULL AND page_end IS NOT NULL) AS start_only_null,
       count(*) AS total_rows
FROM   toc_entries;
"""

# The repair this script will PRINT but never run — matches the ingest guard's
# rule exactly (`page_end = page_start`, i.e. a single-page lesson).
REPAIR_SQL_TEMPLATE = """
-- Repairs ONLY the exactly-off-by-one rows, to the same value the ingest guard
-- would have written. Review the listing above first; run deliberately.
UPDATE toc_entries
SET    page_end = page_start
WHERE  id IN (
{ids}
);
"""


async def run(*, database_url: str, probe: bool) -> int:
    # Heavy/app imports live HERE, after main()'s DATABASE_URL preflight — see
    # the module docstring on why `app.config` must not be imported earlier.
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.services import storage
    from app.services.toc_classifier import classify_entries
    from app.services.toc_ingest_audit import (
        OFF_BY_ONE,
        Row,
        audit_page_ranges,
        probe_text_layer,
    )

    engine = create_async_engine(database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    exit_code = 0
    try:
        async with session_factory() as session:
            # ── 1. inverted ranges ───────────────────────────────────────────
            inverted = (await session.execute(text(INVERTED_SQL))).mappings().all()
            print(f"\n=== inverted page ranges (page_end < page_start): {len(inverted)} row(s) ===")
            if not inverted:
                print("  none — nothing to repair")
            off_by_one_ids: list[str] = []
            for r in inverted:
                # Re-derive the kind through the SAME code the ingest guard uses,
                # so this report can never disagree with what ingestion would do.
                issues = audit_page_ranges(
                    [Row(0, r["section_title"] or "", r["page_start"], r["page_end"])],
                    repair=False,
                )
                kind = issues[0].kind if issues else "?"
                if kind == OFF_BY_ONE:
                    off_by_one_ids.append(str(r["toc_entry_id"]))
                verdict = (
                    f"REPAIRABLE → page_end={r['page_start']}"
                    if kind == OFF_BY_ONE
                    else "NEEDS A HUMAN"
                )
                print(
                    f"  {r['original_filename']!r} [{r['subject']} g{r['grade']}] "
                    f"#{r['order_index']} {str(r['section_title'])[:48]!r} "
                    f"pages {r['page_start']}-{r['page_end']}  {verdict}"
                )
            if off_by_one_ids:
                print(f"\n  {len(off_by_one_ids)} of {len(inverted)} are exactly off-by-one.")
                print(
                    REPAIR_SQL_TEMPLATE.format(
                        ids="\n".join(f"  '{i}'," for i in off_by_one_ids).rstrip(",")
                    )
                )
                exit_code = 1

            # ── 2. NULL ranges (characterise, do not flag) ──────────────────
            counts = (await session.execute(text(NULL_RANGE_SQL))).mappings().one()
            null_total = counts["both_null"] + counts["end_only_null"] + counts["start_only_null"]
            print(
                f"=== NULL page ranges: {null_total} of {counts['total_rows']} row(s) "
                "— legitimate, reported for understanding only ==="
            )
            print(
                f"  both bounds NULL: {counts['both_null']}   "
                f"end only: {counts['end_only_null']}   start only: {counts['start_only_null']}"
            )

            # What ARE they? Classify every page-less row so the population is
            # visible: headers/end-matter are expected; `lesson` rows are the
            # ones that matter, and only on a scanned book (see the probe below).
            page_less_rows = (
                await session.execute(
                    text(
                        "SELECT t.book_id, t.section_title, t.page_start, t.page_end "
                        "FROM toc_entries t WHERE t.page_start IS NULL OR t.page_end IS NULL"
                    )
                )
            ).mappings().all()
            by_book: dict = {}
            for r in page_less_rows:
                by_book.setdefault(r["book_id"], []).append(r)
            tally: dict[str, int] = {}
            page_less_lessons: dict = {}
            for book_id, rows in by_book.items():
                # Classify against the book's FULL row set — containment-based
                # HEADER detection needs the siblings, not just the page-less rows.
                full = (
                    await session.execute(
                        text(
                            "SELECT section_title, page_start, page_end FROM toc_entries "
                            "WHERE book_id = :b ORDER BY order_index"
                        ),
                        {"b": book_id},
                    )
                ).mappings().all()
                proxies = [
                    Row(i, r["section_title"] or "", r["page_start"], r["page_end"])
                    for i, r in enumerate(full)
                ]
                classes = classify_entries(proxies)
                for i, r in enumerate(full):
                    if r["page_start"] is None or r["page_end"] is None:
                        tally[classes[i]] = tally.get(classes[i], 0) + 1
                        if classes[i] == "lesson":
                            page_less_lessons[book_id] = page_less_lessons.get(book_id, 0) + 1
            if tally:
                print(
                    "  by class: "
                    + "  ".join(f"{k}={v}" for k, v in sorted(tally.items(), key=lambda kv: -kv[1]))
                )
                print(
                    "  (only `lesson` rows are launchable; a page-less lesson is harmless on a "
                    "text-layer book and fatal on a scanned one — see the probe below)"
                )

            # ── 3. scanned books (opt-in: needs the PDFs on local disk) ──────
            if not probe:
                print(
                    "\n=== text layer: skipped (pass --probe to read "
                    "<VAR_DIR>/books/<id>/source.pdf for every book) ==="
                )
            else:
                books = (
                    await session.execute(
                        text(
                            "SELECT id, subject, grade, original_filename, status "
                            "FROM books ORDER BY original_filename"
                        )
                    )
                ).mappings().all()
                print(f"\n=== text-layer probe over {len(books)} book(s) ===")
                scanned = missing = 0
                for b in books:
                    pdf = storage.book_pdf_path(b["id"])
                    if not pdf.exists():
                        missing += 1
                        continue
                    p = await asyncio.to_thread(probe_text_layer, pdf)
                    if not p.is_sparse:
                        continue
                    scanned += 1
                    fatal = page_less_lessons.get(b["id"], 0)
                    flag = (
                        f"BLOCKS {fatal} lesson(s) — scanned AND no page range"
                        if fatal
                        else "usable via the vision path (every lesson has a page range)"
                    )
                    print(
                        f"  SCANNED  {b['original_filename']!r} [{b['subject']} g{b['grade']}] "
                        f"{p.describe()} — {flag}"
                    )
                    if fatal:
                        exit_code = 1
                print(
                    f"  {scanned} scanned book(s); {missing} book(s) skipped "
                    "(no source.pdf on this host)"
                )
        print("")
        return exit_code
    finally:
        await engine.dispose()


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only audit of toc_entries page ranges + book text layers. "
            "Requires an explicit DATABASE_URL env var (never .env-defaults). "
            "Writes nothing; prints the repair SQL for the off-by-one rows."
        )
    )
    parser.add_argument(
        "--probe",
        action="store_true",
        help="also probe each book's on-disk PDF for a missing text layer (scanned books)",
    )
    parser.add_argument(
        "--show-sql",
        action="store_true",
        help="print the audit queries and exit — run them in psql instead of this script",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.show_sql:
        print(INVERTED_SQL)
        print(NULL_RANGE_SQL)
        return 0
    try:
        database_url = preflight_database_url(os.environ)
        return asyncio.run(run(database_url=database_url, probe=args.probe))
    except PreflightError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
