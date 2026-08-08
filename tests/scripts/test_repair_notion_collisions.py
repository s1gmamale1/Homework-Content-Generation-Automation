"""Real-DB tests for `scripts/repair_notion_collisions.py` — the repair that
clears the FALSE Notion stamps left behind by the pre-#120 title collision
(distinct lessons sharing a title collapsed onto ONE Notion page: the first
push populated it, every later push hit `page_has_content` and silently
returned WITHOUT writing, yet still got stamped `notion_archived_at`).

The script picks the section that actually PUSHED first (the page's real
owner) and clears the stamps on all the others so they become re-archivable.
Owner selection walks a 4-step effective-push ladder; these tests pin the
whole ladder, the dry-run/apply split, idempotency and the blast radius.

Run:
  createdb -h 127.0.0.1 -p 5432 -U macmini5 -O edu edu_scratch_repair_a
  DATABASE_URL=postgresql+asyncpg://edu:edu@127.0.0.1:5432/edu_scratch_repair_a \\
  RUN_DB_INTEGRATION=1 uv run python -m pytest \\
  tests/scripts/test_repair_notion_collisions.py -q
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)

T0 = datetime(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc)


def _h(n: int) -> datetime:
    return T0 + timedelta(hours=n)


PAGE_A = "aaaaaaaa-1111-2222-3333-444444444444"
PAGE_B = "bbbbbbbb-1111-2222-3333-444444444444"
PAGE_SOLO = "cccccccc-1111-2222-3333-444444444444"


# ─── scratch-DB plumbing ──────────────────────────────────────────────────


async def _truncate() -> None:
    from sqlalchemy import text

    from app.db import SessionLocal

    async with SessionLocal() as s:
        await s.execute(
            text("TRUNCATE homework_jobs, toc_entries, books RESTART IDENTITY CASCADE")
        )
        await s.commit()


@pytest.fixture(autouse=True)
async def _clean_db():
    """Each test owns the whole scratch DB — the script's scope is global
    (every duplicated page id), so leftovers from a sibling test would show
    up in its summary counts."""
    await _truncate()
    yield
    await _truncate()


async def _seed_book(s, subject: str = "matematika", grade: str | None = "5"):
    from app.models.book import Book

    book = Book(
        subject=subject, grade=grade, original_filename="t.pdf",
        content_sha256="a" * 64, file_size_bytes=1, status="toc_ready",
    )
    s.add(book)
    await s.flush()
    return book


async def _seed_section(
    s, book, *, title: str, page_id: str | None, order_index: int,
    jobs: list[tuple[datetime | None, datetime | None, str]],
    stamped: int | None = None, page_start: int | None = None,
    output_language: str = "uz",
):
    """One toc_entries row + its homework_jobs.

    `jobs` items are (notion_archived_at, completed_at, status); `stamped` is
    the index of the job the section's `notion_archived_job_id` points at
    (None = a pre-0129 husk with no stamped job)."""
    from app.models.homework_job import HomeworkJob
    from app.models.toc_entry import TOCEntry

    entry = TOCEntry(
        book_id=book.id, section_title=title, order_index=order_index,
        page_start=page_start, notion_homework_page_id=page_id,
    )
    s.add(entry)
    await s.flush()
    created = []
    for archived_at, completed_at, status in jobs:
        job = HomeworkJob(
            book_id=book.id, toc_entry_id=entry.id, subject=book.subject,
            status=status, provider="gemini", model="gemini-2.5-flash",
            transport="api", output_language=output_language,
            notion_archived_at=archived_at, completed_at=completed_at,
        )
        s.add(job)
        created.append(job)
    await s.flush()
    if stamped is not None:
        entry.notion_archived_job_id = created[stamped].id
    await s.flush()
    return entry, created


async def _run(*, apply: bool) -> int:
    from scripts.repair_notion_collisions import run

    return await run(database_url=os.environ["DATABASE_URL"], apply=apply)


async def _toc_state() -> dict:
    from sqlalchemy import text

    from app.db import SessionLocal

    async with SessionLocal() as s:
        rows = (await s.execute(text(
            "SELECT id, section_title, notion_homework_page_id, notion_archived_job_id "
            "FROM toc_entries ORDER BY id"
        ))).all()
    return {r.id: (r.notion_homework_page_id, r.notion_archived_job_id) for r in rows}


async def _job_stamps() -> dict:
    from sqlalchemy import text

    from app.db import SessionLocal

    async with SessionLocal() as s:
        rows = (await s.execute(text(
            "SELECT id, notion_archived_at FROM homework_jobs ORDER BY id"
        ))).all()
    return {r.id: r.notion_archived_at for r in rows}


async def _snapshot() -> tuple:
    """Every column of both tables, ordered — the dry-run must leave this
    EXACTLY as it found it."""
    from sqlalchemy import text

    from app.db import SessionLocal

    async with SessionLocal() as s:
        toc = (await s.execute(text(
            "SELECT to_jsonb(t) AS j FROM toc_entries t ORDER BY t.id"
        ))).scalars().all()
        jobs = (await s.execute(text(
            "SELECT to_jsonb(j) AS j FROM homework_jobs j ORDER BY j.id"
        ))).scalars().all()
    return (toc, jobs)


# ─── the standard collision group: 3 sections, S2 pushed FIRST ────────────


async def _seed_standard_group():
    """PAGE_A shared by three sections. All three carry a stamped job, so the
    owner comes from step 1 (`stamped_push`): S2 pushed at +1h, before S1
    (+2h) and S3 (+3h). `completed_at` is ordered the SAME way here so the
    naive rule agrees — this group must report ordering_disagreement=false
    deterministically (equal completed_at would make the naive rule fall
    through to its section-id tiebreak, i.e. random UUID order)."""
    from app.db import SessionLocal

    async with SessionLocal() as s:
        book = await _seed_book(s)
        s1, j1 = await _seed_section(
            s, book, title="Matnli masalalar", page_id=PAGE_A, order_index=0,
            page_start=10, jobs=[(_h(2), _h(1), "done")], stamped=0)
        s2, j2 = await _seed_section(
            s, book, title="Matnli masalalar", page_id=PAGE_A, order_index=1,
            page_start=20, jobs=[(_h(1), _h(0), "done")], stamped=0)
        s3, j3 = await _seed_section(
            s, book, title="Matnli masalalar", page_id=PAGE_A, order_index=2,
            page_start=30, jobs=[(_h(3), _h(2), "done")], stamped=0)
        await s.commit()
        return {
            "book_id": book.id,
            "owner": s2.id, "owner_job": j2[0].id,
            "non_owners": {s1.id, s3.id},
            "non_owner_jobs": {j1[0].id, j3[0].id},
        }


# ─── 1. the owner is preserved exactly ────────────────────────────────────


async def test_owner_row_and_its_job_are_left_completely_untouched(capsys):
    seeded = await _seed_standard_group()

    assert await _run(apply=True) == 0

    toc = await _toc_state()
    page_id, archived_job_id = toc[seeded["owner"]]
    assert page_id == PAGE_A, "owner must KEEP the page it owns"
    assert archived_job_id == seeded["owner_job"], "owner's stamped job must survive"
    stamps = await _job_stamps()
    assert stamps[seeded["owner_job"]] == _h(1), "owner's job stays stamped"

    out = capsys.readouterr().out
    assert "owner_source=stamped_push" in out
    assert "ordering_disagreement=false" in out


# ─── 2. exactly the non-owners are cleared ────────────────────────────────


async def test_exactly_the_non_owners_are_cleared(capsys):
    seeded = await _seed_standard_group()

    assert await _run(apply=True) == 0

    toc = await _toc_state()
    cleared = {sid for sid, (page, _job) in toc.items() if page is None}
    assert cleared == seeded["non_owners"], "the cleared set must be EXACTLY the non-owners"
    for sid in seeded["non_owners"]:
        assert toc[sid] == (None, None), "both stamps must be NULLed on a non-owner"

    stamps = await _job_stamps()
    unstamped = {jid for jid, at in stamps.items() if at is None}
    assert unstamped == seeded["non_owner_jobs"]

    out = capsys.readouterr().out
    assert (
        "summary: groups=1 sections=3 owners=1 non_owners=2 jobs_to_unstamp=2 "
        "unresolvable_groups=0 ordering_disagreements=0"
    ) in out


# ─── 3. dry-run writes nothing ────────────────────────────────────────────


async def test_dry_run_changes_nothing_at_all(capsys):
    await _seed_standard_group()
    before = await _snapshot()

    assert await _run(apply=False) == 0

    assert await _snapshot() == before, "dry-run must not modify a single column"
    out = capsys.readouterr().out
    assert "DRY RUN" in out
    assert "--apply" in out


# ─── 4. idempotent ────────────────────────────────────────────────────────


async def test_apply_is_idempotent(capsys):
    await _seed_standard_group()

    assert await _run(apply=True) == 0
    after_first = await _snapshot()
    capsys.readouterr()

    assert await _run(apply=True) == 0

    assert await _snapshot() == after_first, "a second apply must be a no-op"
    out = capsys.readouterr().out
    assert "groups=0 sections=0 owners=0 non_owners=0 jobs_to_unstamp=0" in out


# ─── 5. the fallback ladder (no stamped job anywhere in the group) ────────


async def test_group_with_no_stamped_job_still_resolves_an_owner(capsys):
    """Pre-0129 husks: `notion_archived_job_id IS NULL` on every member. The
    owner then comes from the row-level fallbacks — S2's own `completed_at`
    (+1h, step 4) beats S1's own `notion_archived_at` (+5h, step 3)."""
    from app.db import SessionLocal

    async with SessionLocal() as s:
        book = await _seed_book(s)
        s1, j1 = await _seed_section(
            s, book, title="Husk", page_id=PAGE_B, order_index=0,
            jobs=[(_h(5), _h(4), "done")], stamped=None)
        s2, j2 = await _seed_section(
            s, book, title="Husk", page_id=PAGE_B, order_index=1,
            jobs=[(None, _h(1), "done")], stamped=None)
        await s.commit()
        owner, owner_job = s2.id, j2[0].id
        loser, loser_job = s1.id, j1[0].id

    assert await _run(apply=True) == 0

    toc = await _toc_state()
    assert toc[owner] == (PAGE_B, None), "owner keeps its page (and its NULL husk stamp)"
    assert toc[loser] == (None, None)
    stamps = await _job_stamps()
    assert stamps[owner_job] is None, "owner's job never had a push stamp — unchanged"
    assert stamps[loser_job] is None, "non-owner's push stamp is cleared"

    out = capsys.readouterr().out
    assert "owner_source=row_completed" in out
    assert "groups=1 sections=2 owners=1 non_owners=1 jobs_to_unstamp=1" in out


# ─── 6. push time beats completed_at, and the disagreement is reported ────


async def test_push_time_wins_over_completed_at_and_is_flagged(capsys):
    """The live counter-example (group 3a499838…): the section that COMPLETED
    first was pushed LAST. Owner = earliest actual push; the group must be
    flagged `ordering_disagreement=true` so a reviewer sees the judgement."""
    from app.db import SessionLocal

    async with SessionLocal() as s:
        book = await _seed_book(s)
        early_complete, ec_jobs = await _seed_section(
            s, book, title="Matnli masalalar", page_id=PAGE_A, order_index=0,
            jobs=[(_h(5), _h(0), "done")], stamped=0)
        early_push, ep_jobs = await _seed_section(
            s, book, title="Matnli masalalar", page_id=PAGE_A, order_index=1,
            jobs=[(_h(1), _h(3), "done")], stamped=0)
        await s.commit()
        owner, owner_job = early_push.id, ep_jobs[0].id
        loser, loser_job = early_complete.id, ec_jobs[0].id

    assert await _run(apply=True) == 0

    toc = await _toc_state()
    assert toc[owner] == (PAGE_A, owner_job), "earliest PUSH owns the page"
    assert toc[loser] == (None, None), "earliest completed_at is NOT the owner"
    stamps = await _job_stamps()
    assert stamps[owner_job] == _h(1)
    assert stamps[loser_job] is None

    out = capsys.readouterr().out
    assert "ordering_disagreement=true" in out
    assert "ordering_disagreements=1" in out


# ─── 7. a page id held by exactly one row is never a group ────────────────


async def test_unique_page_id_is_never_touched(capsys):
    from app.db import SessionLocal

    async with SessionLocal() as s:
        book = await _seed_book(s)
        solo, solo_jobs = await _seed_section(
            s, book, title="Solo lesson", page_id=PAGE_SOLO, order_index=0,
            jobs=[(_h(2), _h(1), "done")], stamped=0)
        unarchived, _ = await _seed_section(
            s, book, title="Never archived", page_id=None, order_index=1,
            jobs=[(None, _h(1), "done")], stamped=None)
        await s.commit()
        solo_id, solo_job_id = solo.id, solo_jobs[0].id
        unarchived_id = unarchived.id

    before = await _snapshot()
    assert await _run(apply=True) == 0
    assert await _snapshot() == before, "no duplicated page id -> nothing to repair"

    toc = await _toc_state()
    assert toc[solo_id] == (PAGE_SOLO, solo_job_id)
    assert toc[unarchived_id] == (None, None)

    out = capsys.readouterr().out
    assert "groups=0 sections=0 owners=0 non_owners=0 jobs_to_unstamp=0" in out
    assert PAGE_SOLO not in out
