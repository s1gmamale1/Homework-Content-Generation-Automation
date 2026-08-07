"""Clear the FALSE Notion archive stamps left behind by the pre-#120 lesson
title collision.

Before commit 3945b83, `notion_archive` resolved a lesson's Notion page by
TITLE alone. Distinct lessons that happened to share a title therefore
collapsed onto ONE page: the first job to push populated it; every later job
hit `page_has_content` and silently returned WITHOUT writing — yet still got
stamped `notion_archived_at` (and had `toc_entries.notion_homework_page_id`
pointed at that other lesson's page). Nothing in Notion was overwritten; the
content was simply never written. It is a SKIP, not a clobber.

The code fix is deployed, so NEW archives no longer collide. But the damaged
rows still carry those false stamps and will re-skip forever until they are
cleared. This script clears them.

Scope is DERIVED FROM THE DB at run time — every
`toc_entries.notion_homework_page_id` held by more than one row
(`GROUP BY … HAVING count(*) > 1`), unfiltered by date, subject or language.
Nothing about the live damage is hardcoded.

Per group it picks the OWNER — the section whose content is actually on the
page — and clears the other members:
  - non-owner `toc_entries`: notion_homework_page_id -> NULL,
    notion_archived_job_id -> NULL
  - every job of a non-owner section that carries notion_archived_at:
    notion_archived_at -> NULL (so the job becomes re-archivable)
  - the OWNER row and the owner's job are left COMPLETELY untouched.

Owner = the section that PUSHED FIRST (everyone after it hit
`page_has_content` and skipped). The *effective push timestamp* per section
is the first non-NULL of:
  1. `stamped_push`      the stamped job's (toc_entries.notion_archived_job_id)
                         homework_jobs.notion_archived_at
  2. `stamped_completed` that same stamped job's completed_at
  3. `row_push`          min(notion_archived_at) over the row's own jobs
  4. `row_completed`     min(completed_at) over the row's own `done` jobs
Ties break on section id, so the plan is fully deterministic. Steps 3-4 exist
for pre-0129 husks (`notion_archived_job_id IS NULL`), including whole groups
with no stamped member at all.

The naive rule "earliest completed_at of the stamped job" is WRONG and was
disproved against live data (a section that completed first was pushed last);
where the two rules disagree the group is flagged
`ordering_disagreement=true` so a reviewer can see the judgement call.

**Dry-run by default** — it prints a full, reviewable plan and opens no write
transaction whatsoever. Pass --apply to write (one transaction, loud banner).
A second --apply run finds nothing to do: each page id then has exactly one
row, so no group remains.

There is deliberately NO --force / Notion-writing path here. This script only
touches Postgres; it never talks to Notion.

DATABASE_URL is read directly from the environment and MUST be set
explicitly — this script refuses to start otherwise (one clear error line,
exit 2, no traceback). It never falls back to `.env`/config defaults, so an
operator always points it at the intended database on purpose. To make that
hold, **this module imports NOTHING from `app.*` at module level**: every
`app`-adjacent import happens lazily inside `run()`, strictly AFTER the
DATABASE_URL check — `app.config`'s import-time `load_dotenv` (which walks UP
parent directories via `find_dotenv`) would otherwise silently inject an
outer `.env`'s DATABASE_URL. That matters enormously here: an accidental
default could point at production.

Run (dry-run — prints the plan, writes nothing):
  DATABASE_URL=postgresql+asyncpg://edu:edu@localhost:5433/edu_homework \\
  uv run python -m scripts.repair_notion_collisions

Apply (clears the false stamps):
  DATABASE_URL=postgresql+asyncpg://edu:edu@localhost:5433/edu_homework \\
  uv run python -m scripts.repair_notion_collisions --apply
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import UUID

# Make BOTH documented invocations work: `python -m scripts.repair_notion_collisions`
# (repo root already on sys.path) AND `python scripts/repair_notion_collisions.py`
# (file-path form puts scripts/ on the path, not the repo root — the deferred
# imports inside run() would raise ModuleNotFoundError without this).
_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


# ─── preflight (one clear line + exit 2, never a traceback) ───────────────


class PreflightError(Exception):
    """A refuse-to-start condition. `main` prints `ERROR: <message>` to
    stderr and exits 2 — the operator never sees a traceback for these."""


DATABASE_URL_ERROR = (
    "DATABASE_URL must be set explicitly — refusing to guess the target DB. "
    "Example: DATABASE_URL=postgresql+asyncpg://edu:edu@localhost:5433/edu_homework "
    "uv run python -m scripts.repair_notion_collisions"
)


def preflight_database_url(environ: Mapping[str, str]) -> str:
    """The explicit DATABASE_URL, or `PreflightError`. Called against the RAW
    process environment BEFORE any app-adjacent import (see module docstring —
    `app.config`'s load_dotenv must never get the chance to fill this in)."""
    url = environ.get("DATABASE_URL")
    if not url:
        raise PreflightError(DATABASE_URL_ERROR)
    return url


# ─── pure planning model ─────────────────────────────────────────────────

@dataclass(frozen=True)
class JobRow:
    job_id: UUID
    notion_archived_at: datetime | None
    completed_at: datetime | None
    status: str
    output_language: str


@dataclass(frozen=True)
class SectionRow:
    section_id: UUID
    page_id: str
    section_title: str
    page_start: int | None
    subject: str
    grade: str | None
    stamped_job_id: UUID | None
    jobs: tuple[JobRow, ...]

    @property
    def stamped_job(self) -> JobRow | None:
        if self.stamped_job_id is None:
            return None
        for job in self.jobs:
            if job.job_id == self.stamped_job_id:
                return job
        return None  # stamp points at a job that no longer exists

    @property
    def jobs_to_unstamp(self) -> tuple[JobRow, ...]:
        """Every job of this section that carries a (false) push stamp."""
        return tuple(j for j in self.jobs if j.notion_archived_at is not None)

    @property
    def container(self) -> str:
        """`<output_language>:<subject>|<grade>` — the Notion container the
        page lives in. Language comes from the stamped job when there is one,
        else the section's first job; `?` when the row has no jobs at all."""
        job = self.stamped_job or (self.jobs[0] if self.jobs else None)
        lang = job.output_language if job is not None else "?"
        return f"{lang}:{self.subject}|{self.grade if self.grade is not None else '-'}"


def effective_push(section: SectionRow) -> tuple[datetime | None, str | None]:
    """(timestamp, ladder step) for a section's ACTUAL push time — the first
    non-NULL rung of the 4-step ladder in the module docstring, or
    (None, None) when the row carries no usable timestamp at all."""
    stamped = section.stamped_job
    if stamped is not None:
        if stamped.notion_archived_at is not None:
            return stamped.notion_archived_at, "stamped_push"
        if stamped.completed_at is not None:
            return stamped.completed_at, "stamped_completed"
    pushes = [j.notion_archived_at for j in section.jobs if j.notion_archived_at is not None]
    if pushes:
        return min(pushes), "row_push"
    completions = [
        j.completed_at for j in section.jobs
        if j.status == "done" and j.completed_at is not None
    ]
    if completions:
        return min(completions), "row_completed"
    return None, None


def naive_completed(section: SectionRow) -> datetime | None:
    """The DISPROVED rule kept only to detect disagreement: `completed_at` of
    the stamped job (NULL when the row has no stamped job)."""
    stamped = section.stamped_job
    return None if stamped is None else stamped.completed_at


def _earliest(
    sections: Sequence[SectionRow], key,
) -> SectionRow | None:
    """The section with the earliest non-NULL `key(section)`, ties broken by
    section id. None when no member has a timestamp at all."""
    dated = [s for s in sections if key(s) is not None]
    if not dated:
        return None
    return min(dated, key=lambda s: (key(s), str(s.section_id)))


@dataclass(frozen=True)
class GroupPlan:
    page_id: str
    sections: tuple[SectionRow, ...]
    owner: SectionRow | None  # None = unresolvable (no member has any timestamp)
    owner_source: str | None
    owner_push: datetime | None
    ordering_disagreement: bool

    @property
    def non_owners(self) -> tuple[SectionRow, ...]:
        if self.owner is None:
            return ()  # unresolvable -> touch NOTHING
        return tuple(s for s in self.sections if s.section_id != self.owner.section_id)

    @property
    def container(self) -> str:
        return (self.owner or self.sections[0]).container


def plan_group(page_id: str, sections: Sequence[SectionRow]) -> GroupPlan:
    """Pick the owner of one shared page and classify the decision."""
    ordered = sorted(sections, key=lambda s: str(s.section_id))
    pushes = {s.section_id: effective_push(s) for s in ordered}
    owner = _earliest(ordered, lambda s: pushes[s.section_id][0])
    naive_owner = _earliest(ordered, naive_completed)
    # "Different owner", not "undefined owner": a group the naive rule could
    # not resolve at all (no stamped member) is not a disagreement.
    disagreement = (
        owner is not None
        and naive_owner is not None
        and naive_owner.section_id != owner.section_id
    )
    push, source = pushes[owner.section_id] if owner is not None else (None, None)
    return GroupPlan(
        page_id=page_id,
        sections=tuple(ordered),
        owner=owner,
        owner_source=source,
        owner_push=push,
        ordering_disagreement=disagreement,
    )


def build_plan(sections: Sequence[SectionRow]) -> list[GroupPlan]:
    """One GroupPlan per shared page id, ordered by page id (stable output —
    a later agent diffs these reports)."""
    by_page: dict[str, list[SectionRow]] = {}
    for section in sections:
        by_page.setdefault(section.page_id, []).append(section)
    return [plan_group(page, rows) for page, rows in sorted(by_page.items())]


# ─── report formatting (pure — stable + greppable) ───────────────────────


def _ts(value: datetime | None) -> str:
    return "NULL" if value is None else value.isoformat()


def summary_counts(plans: Sequence[GroupPlan]) -> dict[str, int]:
    resolved = [p for p in plans if p.owner is not None]
    return {
        "groups": len(plans),
        "sections": sum(len(p.sections) for p in plans),
        "owners": len(resolved),
        "non_owners": sum(len(p.non_owners) for p in plans),
        "jobs_to_unstamp": sum(
            len(s.jobs_to_unstamp) for p in plans for s in p.non_owners
        ),
        "unresolvable_groups": len(plans) - len(resolved),
        "ordering_disagreements": sum(1 for p in plans if p.ordering_disagreement),
    }


SUMMARY_KEYS = (
    "groups", "sections", "owners", "non_owners", "jobs_to_unstamp",
    "unresolvable_groups", "ordering_disagreements",
)


def format_summary(counts: Mapping[str, int]) -> str:
    return "summary: " + " ".join(f"{k}={counts[k]}" for k in SUMMARY_KEYS)


def format_group(plan: GroupPlan) -> list[str]:
    """The per-group report block a human authorizes execution from."""
    lines = [
        f"GROUP page={plan.page_id} container={plan.container} "
        f"sections={len(plan.sections)} "
        f"owner_source={plan.owner_source or 'NONE'} "
        f"ordering_disagreement={'true' if plan.ordering_disagreement else 'false'}"
    ]
    if plan.owner is None:
        lines.append(
            "  UNRESOLVABLE no member carries any push/completion timestamp — "
            "SKIPPED, nothing changes for this group"
        )
        for s in plan.sections:
            lines.append(
                f"  MEMBER     section={s.section_id} title={s.section_title!r} "
                f"page_start={s.page_start if s.page_start is not None else '-'}"
            )
        return lines
    owner = plan.owner
    lines.append(
        f"  OWNER      section={owner.section_id} push={_ts(plan.owner_push)} "
        f"title={owner.section_title!r} "
        f"page_start={owner.page_start if owner.page_start is not None else '-'} "
        f"(untouched)"
    )
    for s in plan.non_owners:
        push, source = effective_push(s)
        lines.append(
            f"  NON-OWNER  section={s.section_id} push={_ts(push)} "
            f"push_source={source or 'NONE'} title={s.section_title!r} "
            f"page_start={s.page_start if s.page_start is not None else '-'}"
        )
        lines.append(
            f"    toc_entries.notion_homework_page_id: {s.page_id} -> NULL"
        )
        lines.append(
            "    toc_entries.notion_archived_job_id:  "
            f"{s.stamped_job_id if s.stamped_job_id is not None else 'NULL'} -> NULL"
        )
        for job in s.jobs_to_unstamp:
            lines.append(
                f"    homework_jobs[{job.job_id}].notion_archived_at: "
                f"{_ts(job.notion_archived_at)} -> NULL"
            )
    return lines


APPLY_BANNER = (
    "!!! APPLYING — this run WRITES to the database named by DATABASE_URL !!!"
)

DRY_RUN_FOOTER = (
    "DRY RUN — nothing was written. Re-run with --apply to clear the stamps above."
)


def format_applied(sections_cleared: int, jobs_unstamped: int) -> str:
    return (
        f"applied: cleared {sections_cleared} non-owner section(s), "
        f"un-stamped {jobs_unstamped} job(s)."
    )


# ─── DB I/O ──────────────────────────────────────────────────────────────

# Every toc row whose notion_homework_page_id is shared with at least one
# OTHER row. Scope derived entirely from the data — no date/subject/language
# filter, no hardcoded ids.
_COLLIDING_SECTIONS_SQL = """
SELECT t.id            AS section_id,
       t.notion_homework_page_id AS page_id,
       t.section_title AS section_title,
       t.page_start    AS page_start,
       t.notion_archived_job_id AS stamped_job_id,
       b.subject       AS subject,
       b.grade         AS grade
FROM toc_entries t
JOIN books b ON b.id = t.book_id
WHERE t.notion_homework_page_id IS NOT NULL
  AND t.notion_homework_page_id IN (
      SELECT notion_homework_page_id
      FROM toc_entries
      WHERE notion_homework_page_id IS NOT NULL
      GROUP BY notion_homework_page_id
      HAVING count(*) > 1
  )
ORDER BY t.notion_homework_page_id, t.id
"""

_JOBS_SQL = """
SELECT id, toc_entry_id, notion_archived_at, completed_at, status, output_language
FROM homework_jobs
WHERE toc_entry_id = ANY(:section_ids)
ORDER BY toc_entry_id, created_at, id
"""


async def load_colliding_sections(conn) -> list[SectionRow]:
    """Read-only: the colliding sections + all of their jobs."""
    from sqlalchemy import bindparam, text
    from sqlalchemy.dialects.postgresql import UUID as PgUUID
    from sqlalchemy.types import ARRAY

    rows = (await conn.execute(text(_COLLIDING_SECTIONS_SQL))).all()
    if not rows:
        return []
    section_ids = [r.section_id for r in rows]
    jobs_stmt = text(_JOBS_SQL).bindparams(
        bindparam("section_ids", type_=ARRAY(PgUUID(as_uuid=True)))
    )
    job_rows = (await conn.execute(jobs_stmt, {"section_ids": section_ids})).all()
    by_section: dict[UUID, list[JobRow]] = {}
    for j in job_rows:
        by_section.setdefault(j.toc_entry_id, []).append(JobRow(
            job_id=j.id,
            notion_archived_at=j.notion_archived_at,
            completed_at=j.completed_at,
            status=j.status,
            output_language=j.output_language,
        ))
    return [
        SectionRow(
            section_id=r.section_id,
            page_id=r.page_id,
            section_title=r.section_title,
            page_start=r.page_start,
            subject=r.subject,
            grade=r.grade,
            stamped_job_id=r.stamped_job_id,
            jobs=tuple(by_section.get(r.section_id, ())),
        )
        for r in rows
    ]


async def apply_plan(conn, plans: Sequence[GroupPlan]) -> tuple[int, int]:
    """Clear the false stamps. Owner rows and owner jobs are never in either
    id list, so they cannot be touched. Returns (sections, jobs) written."""
    from sqlalchemy import bindparam, text
    from sqlalchemy.dialects.postgresql import UUID as PgUUID
    from sqlalchemy.types import ARRAY

    section_ids = [s.section_id for p in plans for s in p.non_owners]
    job_ids = [j.job_id for p in plans for s in p.non_owners for j in s.jobs_to_unstamp]
    if section_ids:
        await conn.execute(
            text(
                "UPDATE toc_entries SET notion_homework_page_id = NULL, "
                "notion_archived_job_id = NULL WHERE id = ANY(:ids)"
            ).bindparams(bindparam("ids", type_=ARRAY(PgUUID(as_uuid=True)))),
            {"ids": section_ids},
        )
    if job_ids:
        await conn.execute(
            text(
                "UPDATE homework_jobs SET notion_archived_at = NULL "
                "WHERE id = ANY(:ids)"
            ).bindparams(bindparam("ids", type_=ARRAY(PgUUID(as_uuid=True)))),
            {"ids": job_ids},
        )
    return len(section_ids), len(job_ids)


async def run(*, database_url: str, apply: bool) -> int:
    # Deferred import: see the module docstring — nothing app-adjacent may be
    # imported before main()'s DATABASE_URL preflight.
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(database_url, future=True)
    try:
        if apply:
            print(APPLY_BANNER)
        # Dry-run stays on a plain (read-only) connection — `engine.begin()`
        # (the write transaction) is entered ONLY on the --apply path.
        async with engine.connect() as conn:
            sections = await load_colliding_sections(conn)
        plans = build_plan(sections)
        counts = summary_counts(plans)

        for plan in plans:
            for line in format_group(plan):
                print(line)
            print("")

        if apply:
            async with engine.begin() as conn:
                cleared, unstamped = await apply_plan(conn, plans)
            print(format_applied(cleared, unstamped))
        else:
            print(DRY_RUN_FOOTER)

        print(format_summary(counts))
    finally:
        await engine.dispose()
    return 0


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Clear the false Notion archive stamps left by the pre-#120 lesson "
            "title collision: for every notion_homework_page_id shared by more "
            "than one toc_entries row, keep the section that pushed FIRST (the "
            "page's real owner) and NULL the page-id/archived-job stamps on the "
            "others so they become re-archivable. Dry-run by default; --apply "
            "writes. Touches Postgres only — never Notion. Requires an explicit "
            "DATABASE_URL env var (never .env defaults)."
        ),
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually clear the stamps. Without this flag the script only "
             "prints the plan and writes nothing.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        database_url = preflight_database_url(os.environ)
        return asyncio.run(run(database_url=database_url, apply=args.apply))
    except PreflightError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
