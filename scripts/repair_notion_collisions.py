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
`page_has_content` and skipped) — decided by GROUP-level evidence tiers, not
a per-section ladder. A section that never archived at all can NEVER
outrank one that did, no matter how much earlier it merely completed
generation:
  1. `row_push`/`stamped_push`  the earliest REAL `notion_archived_at` across
                                 EVERY member's jobs (stamped or not) — proven
                                 push. Only reached if at least one member of
                                 the group has ANY archive timestamp.
  2. `stamped_completed`        else the earliest stamped job's completed_at,
                                 across the whole group.
  3. `row_completed`            else the earliest row-level `done`
                                 completed_at, across the whole group.
  4. unresolvable (owner=None)  else no member has any usable timestamp.
Ties break on section id, so the plan is fully deterministic. Tiers 2-3 exist
for pre-0129 husks (`notion_archived_job_id IS NULL`), including whole groups
with no stamped member at all — and for groups where NO member ever pushed.

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

Apply (clears the false stamps — requires the hash printed by a prior dry
run AND a manifest output path; the write transaction re-reads the plan and
refuses to write if either the plan or a targeted row has drifted since):
  DATABASE_URL=postgresql+asyncpg://edu:edu@localhost:5433/edu_homework \\
  uv run python -m scripts.repair_notion_collisions --apply \\
    --expect-plan-hash=<hash printed by the dry run> \\
    --manifest-out=/path/to/manifest.json
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
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


def _section_push(section: SectionRow) -> tuple[datetime, str] | None:
    """(timestamp, source) for the earliest REAL `notion_archived_at` across
    this section's own jobs — stamped or not — or None when this section has
    no archive evidence at all. `source` is "stamped_push" when that earliest
    timestamp is the stamped job's, else "row_push"."""
    pushes = [j.notion_archived_at for j in section.jobs if j.notion_archived_at is not None]
    if not pushes:
        return None
    ts = min(pushes)
    stamped = section.stamped_job
    source = "stamped_push" if stamped is not None and stamped.notion_archived_at == ts else "row_push"
    return ts, source


def _group_owner(
    sections: Sequence[SectionRow],
) -> tuple[SectionRow | None, str | None, datetime | None]:
    """(owner, owner_source, owner_push) via GROUP-level evidence tiers (see
    module docstring): a section that never archived can never outrank one
    that did, so tier 1 is evaluated across EVERY member before any
    completion-based fallback is considered."""
    pushes = {s.section_id: _section_push(s) for s in sections}
    if any(v is not None for v in pushes.values()):
        candidates = [s for s in sections if pushes[s.section_id] is not None]
        winner = min(candidates, key=lambda s: (pushes[s.section_id][0], str(s.section_id)))
        ts, source = pushes[winner.section_id]
        return winner, source, ts

    winner = _earliest(sections, naive_completed)
    if winner is not None:
        return winner, "stamped_completed", naive_completed(winner)

    def _row_completed(s: SectionRow) -> datetime | None:
        completions = [
            j.completed_at for j in s.jobs
            if j.status == "done" and j.completed_at is not None
        ]
        return min(completions) if completions else None

    winner = _earliest(sections, _row_completed)
    if winner is not None:
        return winner, "row_completed", _row_completed(winner)

    return None, None, None


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
    owner, source, push = _group_owner(ordered)
    naive_owner = _earliest(ordered, naive_completed)
    # "Different owner", not "undefined owner": a group the naive rule could
    # not resolve at all (no stamped member) is not a disagreement.
    disagreement = (
        owner is not None
        and naive_owner is not None
        and naive_owner.section_id != owner.section_id
    )
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
    "!!! APPLYING — this run WRITES to the database named by DATABASE_URL !!!\n"
    "!!! Drain any running Notion-archive workers first — a concurrent "
    "archive push during --apply can race the guarded UPDATEs and trip the "
    "state-drift abort !!!"
)

DRY_RUN_FOOTER = (
    "DRY RUN — nothing was written. Re-run with --apply to clear the stamps above."
)


def format_applied(sections_cleared: int, jobs_unstamped: int) -> str:
    return (
        f"applied: cleared {sections_cleared} non-owner section(s), "
        f"un-stamped {jobs_unstamped} job(s)."
    )


# ─── plan hash + manifest (pure — no DB, no Notion) ───────────────────────
#
# `--apply` must only be allowed to run against the EXACT plan an operator
# reviewed in dry-run, and a later `--refresh-notion` step needs enough of
# that plan's expected state to act on it AFTER `--apply` has already
# changed the DB (at which point the collision query returns nothing, so the
# plan can no longer be re-derived). `plan_hash` covers the full expected
# state the guarded UPDATEs depend on — not just ids — so a plan that
# resolves to the same ids but different expected values (a different
# `notion_archived_at` to clear, a different page id to null out) hashes
# differently.


def _expected_state(plans: Sequence[GroupPlan]) -> dict:
    """The full expected-state structure `plan_hash` covers, and the same
    structure persisted verbatim as the manifest's `expected` field. Every
    collection is sorted by its own id so the result — and therefore the
    hash — is independent of the order `plans` (or a section's/job's own
    tuple) happens to be in."""
    groups = sorted(
        (
            {
                "page_id": plan.page_id,
                "owner_section_id": (
                    str(plan.owner.section_id) if plan.owner is not None else None
                ),
                "owner_page_id": plan.page_id if plan.owner is not None else None,
                "owner_source": plan.owner_source,
            }
            for plan in plans
        ),
        key=lambda g: g["page_id"],
    )
    sections = sorted(
        (
            {
                "section_id": str(section.section_id),
                # Expected post-apply state: every non-owner's page pointer
                # collapses onto the group's own page_id -> NULL.
                "notion_homework_page_id": plan.page_id,
                "notion_archived_job_id": (
                    str(section.stamped_job_id)
                    if section.stamped_job_id is not None
                    else None
                ),
            }
            for plan in plans
            for section in plan.non_owners
        ),
        key=lambda d: d["section_id"],
    )
    jobs = sorted(
        (
            {
                "job_id": str(job.job_id),
                "notion_archived_at": _ts(job.notion_archived_at),
            }
            for plan in plans
            for section in plan.non_owners
            for job in section.jobs_to_unstamp
        ),
        key=lambda d: d["job_id"],
    )
    return {"groups": groups, "sections": sections, "jobs": jobs}


def _hash_expected(expected: Mapping) -> str:
    canonical = json.dumps(expected, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def plan_hash(plans: Sequence[GroupPlan]) -> str:
    """Stable, order-independent digest over the FULL expected state the
    guarded UPDATEs will depend on (ids AND expected values) — see the
    section docstring above."""
    return _hash_expected(_expected_state(plans))


def dry_run_footer_lines(plans: Sequence[GroupPlan]) -> list[str]:
    """The dry-run footer, plus the plan hash the operator must pass back
    via `--expect-plan-hash` for a later `--apply` to be honored."""
    return [DRY_RUN_FOOTER, f"plan-hash={plan_hash(plans)}"]


def manifest_from_plans(plans: Sequence[GroupPlan]) -> dict:
    """The persisted manifest a later `--refresh-notion` step reads once the
    collision query can no longer re-derive the plan (`--apply` has already
    cleared the rows it was querying). Deliberately does NOT store phase
    content — a later task loads owner phases fresh from `job_id`."""
    expected = _expected_state(plans)
    owners = sorted(
        (
            {
                "page_id": plan.page_id,
                "section_id": str(plan.owner.section_id),
                "job_id": (
                    str(plan.owner.stamped_job_id)
                    if plan.owner.stamped_job_id is not None
                    else None
                ),
            }
            for plan in plans
            if plan.owner is not None
        ),
        key=lambda o: o["page_id"],
    )
    return {
        "version": 1,
        "hash": _hash_expected(expected),
        "owners": owners,
        "expected": expected,
    }


def manifest_load(path: str | Path) -> dict:
    """Read + parse a persisted manifest, then RECOMPUTE the hash over its
    own `expected` content and assert it matches the stored `hash`. This is
    the manifest's INTERNAL integrity check (detects a hand-edited or
    corrupted file) — it does NOT compare against the DB or the live plan;
    that verification happens wherever the manifest is later consulted."""
    manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    recomputed = _hash_expected(manifest.get("expected"))
    stored = manifest.get("hash")
    if recomputed != stored:
        raise ValueError(
            f"manifest integrity check failed: stored hash {stored!r} does "
            f"not match the hash recomputed over its own 'expected' content "
            f"({recomputed!r}) — the manifest file may be corrupted or "
            "hand-edited"
        )
    return manifest


# ─── apply-time safety errors ─────────────────────────────────────────────
#
# Both are raised INSIDE the `--apply` write transaction (`engine.begin()`),
# so the transaction rolls back before either propagates out of `run()`.


class PlanChangedError(Exception):
    """Raised when the plan re-read at the START of the write transaction no
    longer hashes to the caller's `--expect-plan-hash` — the DB changed
    between the reviewed dry run and this `--apply`."""


class ApplyStateDriftError(Exception):
    """Raised by `apply_plan` itself when a guarded UPDATE's rowcount doesn't
    match the number of rows it expected to touch — a row changed AFTER the
    in-transaction re-read but before the UPDATE ran. Defense in depth,
    independent of the `PlanChangedError` re-read check in `run()`."""


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
    """Clear the false stamps with per-row EXPECTED-STATE guards: each UPDATE
    only touches a row that still holds the exact value the plan expects
    (`notion_homework_page_id` for a section, `notion_archived_at` for a
    job) — a plain `id = ANY(:ids)` would happily overwrite a row that
    changed after planning. Owner rows and owner jobs are never in either
    list, so they cannot be touched.

    Raises `ApplyStateDriftError` (the caller's transaction rolls back) if
    any UPDATE's rowcount comes up short — defense in depth independent of
    `run()`'s in-transaction plan-hash re-read. Returns (sections, jobs)
    actually written."""
    from sqlalchemy import text

    section_updates = [
        {"sid": s.section_id, "expected_page_id": s.page_id}
        for p in plans for s in p.non_owners
    ]
    job_updates = [
        {"jid": j.job_id, "expected_archived_at": j.notion_archived_at}
        for p in plans for s in p.non_owners for j in s.jobs_to_unstamp
    ]

    sections_updated = 0
    if section_updates:
        stmt = text(
            "UPDATE toc_entries SET notion_homework_page_id = NULL, "
            "notion_archived_job_id = NULL "
            "WHERE id = :sid AND notion_homework_page_id = :expected_page_id"
        )
        for params in section_updates:
            result = await conn.execute(stmt, params)
            sections_updated += result.rowcount
        if sections_updated != len(section_updates):
            raise ApplyStateDriftError(
                f"apply_plan: expected to clear {len(section_updates)} "
                f"toc_entries row(s) but only {sections_updated} still held "
                "the expected notion_homework_page_id — a row drifted since "
                "planning; aborting"
            )

    jobs_updated = 0
    if job_updates:
        stmt = text(
            "UPDATE homework_jobs SET notion_archived_at = NULL "
            "WHERE id = :jid AND notion_archived_at = :expected_archived_at"
        )
        for params in job_updates:
            result = await conn.execute(stmt, params)
            jobs_updated += result.rowcount
        if jobs_updated != len(job_updates):
            raise ApplyStateDriftError(
                f"apply_plan: expected to un-stamp {len(job_updates)} "
                f"homework_jobs row(s) but only {jobs_updated} still held "
                "the expected notion_archived_at — a row drifted since "
                "planning; aborting"
            )

    return sections_updated, jobs_updated


async def run(
    *,
    database_url: str,
    apply: bool,
    expect_plan_hash: str | None = None,
    manifest_out: str | Path | None = None,
) -> int:
    if apply:
        if not expect_plan_hash:
            print(
                "ERROR: --apply requires --expect-plan-hash (the hash "
                "printed by a prior dry run) — refusing to write",
                file=sys.stderr,
            )
            return 2
        if not manifest_out:
            print(
                "ERROR: --apply requires --manifest-out (a path to persist "
                "the plan manifest) — refusing to write",
                file=sys.stderr,
            )
            return 2

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
            try:
                async with engine.begin() as conn:
                    # Re-read + rebuild the plan from INSIDE the write
                    # transaction, immediately before the UPDATEs, and
                    # compare its hash to the one the operator reviewed in
                    # dry-run — closes the TOCTOU window between the dry
                    # run and this --apply. Use the FRESH plan for the
                    # writes, not the one read above.
                    fresh_sections = await load_colliding_sections(conn)
                    fresh_plans = build_plan(fresh_sections)
                    fresh_hash = plan_hash(fresh_plans)
                    if fresh_hash != expect_plan_hash:
                        raise PlanChangedError(
                            "plan changed since dry-run: "
                            f"--expect-plan-hash={expect_plan_hash} but the "
                            f"current plan hashes to {fresh_hash} — re-run "
                            "without --apply to review the new plan before "
                            "trying again"
                        )
                    cleared, unstamped = await apply_plan(conn, fresh_plans)
            except (PlanChangedError, ApplyStateDriftError) as exc:
                print(f"ERROR: {exc}", file=sys.stderr)
                return 3
            Path(manifest_out).write_text(
                json.dumps(manifest_from_plans(fresh_plans), indent=2),
                encoding="utf-8",
            )
            print(format_applied(cleared, unstamped))
        else:
            for line in dry_run_footer_lines(plans):
                print(line)

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
    parser.add_argument(
        "--expect-plan-hash", type=str, default=None,
        help="The plan-hash printed by a prior dry run. Required by --apply "
             "— the write transaction re-reads the plan and refuses to "
             "write if it no longer matches this hash.",
    )
    parser.add_argument(
        "--manifest-out", type=Path, default=None,
        help="Path to write the persisted plan manifest to. Required by "
             "--apply — written only after a successful apply.",
    )
    parser.add_argument(
        "--plan-file", type=Path, default=None,
        help="Path to a previously persisted plan manifest. Not yet wired "
             "— wiring lands in a later task.",
    )
    parser.add_argument(
        "--refresh-notion", action="store_true",
        help="Rewrite the owner's Notion page for a manifest's groups after "
             "--apply has already run. Not yet wired — wiring lands in a "
             "later task.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        database_url = preflight_database_url(os.environ)
        return asyncio.run(run(
            database_url=database_url,
            apply=args.apply,
            expect_plan_hash=args.expect_plan_hash,
            manifest_out=args.manifest_out,
        ))
    except PreflightError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
