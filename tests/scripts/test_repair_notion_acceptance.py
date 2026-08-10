"""End-to-end acceptance test for `scripts/repair_notion_collisions.py`,
driven through the ACTUAL `run()` CLI entrypoint — not the unit-tested
helper functions directly (`plan_hash`, `classify_page`,
`refresh_owner_pages`, ... already have their own coverage in
`test_repair_notion_collisions_planhash.py` / `test_repair_notion_refresh.py`
/ `test_repair_notion_collisions.py`).

Proves the whole two-gesture operator flow end to end:
  1. dry-run (`apply=False`) — capture `plan-hash=` from stdout.
  2. `--apply --expect-plan-hash=<hash> --manifest-out=<path>` — clears the
     non-owner DB pointers, writes a manifest.
  3. `--refresh-notion --plan-file=<manifest>` — rewrites the owner's Notion
     page authoritatively and prunes contaminating leaves.

Plus the safety negative legs (wrong hash, out-of-band drift between
dry-run and apply, missing --plan-file, partial refresh failure).

Real scratch Postgres (RUN_DB_INTEGRATION=1 + DATABASE_URL) + a FAKE (pure
in-memory) Notion client reused from `test_repair_notion_refresh.py` — NEVER
real Notion, NEVER production DB. Zero real network calls anywhere in this
file: `FakeNotionArchiveClient` does no I/O of any kind, and every
`client_factory` passed to `run()` returns that same in-memory fake (or is
never invoked at all, on the apply/dry-run and reject-before-DB paths).

Run:
  export DATABASE_URL="postgresql+asyncpg://edu:edu@127.0.0.1:5432/edu_scratch_notionrepair"
  RUN_DB_INTEGRATION=1 uv run python -m pytest \
    tests/scripts/test_repair_notion_acceptance.py -q
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from tests.scripts.test_repair_notion_refresh import FakeNotionArchiveClient

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)

T0 = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)


def _h(n: int) -> datetime:
    return T0 + timedelta(hours=n)


# ─── scratch-DB plumbing ──────────────────────────────────────────────────


async def _truncate() -> None:
    from sqlalchemy import text

    from app.db import SessionLocal

    async with SessionLocal() as s:
        await s.execute(text(
            "TRUNCATE phase_outputs, homework_jobs, toc_entries, books "
            "RESTART IDENTITY CASCADE"
        ))
        await s.commit()


@pytest.fixture(autouse=True)
async def _clean_db():
    await _truncate()
    yield
    await _truncate()


async def _snapshot() -> tuple:
    """Every column of toc_entries + homework_jobs, ordered — a failed
    apply must leave this EXACTLY as it found it."""
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


# ─── seed helpers ──────────────────────────────────────────────────────────

# The owner's 8 done, non-extract phase_outputs — matches
# test_repair_notion_refresh.py's `_OWNER_PHASES` set exactly (validated
# there against the real `_HOMEWORK_LAYOUT`/`PHASE_TITLES`).
OWNER_PHASE_MD = {
    "case-based-preview": "# Case-based preview",
    "flashcards": "# Flashcards",
    "memory-check": "# Memory check",
    "practice-rlc": "# Real-life challenge",
    "practice-error-detection": "# Error detection",
    "practice-tictactoe": "# Tic-tac-toe",
    "boss-arena": "# Boss arena",
    "reflection": "# Reflection",
}

# 3 practice-* leaves the owner never produced — belong to some OTHER lesson
# (the pre-#120 collision contamination `classify_page` must flag).
EXTRA_LEAVES = [
    ("Jigsaw Matching", "practice-jigsaw.md"),
    ("Memory Matching", "practice-memory-match.md"),
    ("Sentence Filling", "practice-sentence.md"),
]


async def _seed_book(s, *, subject: str = "matematika", grade: str = "5"):
    from app.models.book import Book

    book = Book(
        subject=subject, grade=grade, original_filename="t.pdf",
        content_sha256="a" * 64, file_size_bytes=1, status="toc_ready",
    )
    s.add(book)
    await s.flush()
    return book


async def _seed_section(
    s, book, *, title: str, page_id: str, order_index: int,
    archived_at: datetime | None, completed_at: datetime | None,
    status: str = "done", phase_md: dict[str, str] | None = None,
    stamped: bool = True,
):
    """One toc_entries row + one homework_jobs row (+ optional
    phase_outputs). Returns (entry_id, job_id)."""
    from app.models.homework_job import HomeworkJob
    from app.models.phase_output import PhaseOutput
    from app.models.toc_entry import TOCEntry

    entry = TOCEntry(
        book_id=book.id, section_title=title, order_index=order_index,
        page_start=order_index + 1, notion_homework_page_id=page_id,
    )
    s.add(entry)
    await s.flush()

    job = HomeworkJob(
        book_id=book.id, toc_entry_id=entry.id, subject=book.subject,
        status=status, provider="gemini", model="gemini-3.6-flash",
        transport="api", output_language="uz",
        notion_archived_at=archived_at, completed_at=completed_at,
    )
    s.add(job)
    await s.flush()

    if stamped:
        entry.notion_archived_job_id = job.id
        await s.flush()

    if phase_md:
        for i, (phase_name, md) in enumerate(phase_md.items()):
            s.add(PhaseOutput(
                job_id=job.id, phase_name=phase_name, phase_order=i,
                prompt_hash="h", model_name="gemini-3.6-flash",
                status="done", output_md=md,
            ))
        await s.flush()

    return entry.id, job.id


async def _seed_mixed_collision(page_id: str):
    """The realistic MIXED collision the whole acceptance test drives: one
    page id shared by an OWNER section (pushed first at +0h, stamped job
    with the 8 done non-extract phase_outputs above) and a NON-OWNER section
    (a different job, stamped later at +2h — exactly the pre-#120 bug: the
    second job still got a false stamp even though its push silently
    no-opped)."""
    from app.db import SessionLocal

    async with SessionLocal() as s:
        book = await _seed_book(s)
        owner_entry_id, owner_job_id = await _seed_section(
            s, book, title="Owner Lesson", page_id=page_id, order_index=0,
            archived_at=_h(0), completed_at=_h(0), phase_md=OWNER_PHASE_MD,
        )
        non_owner_entry_id, non_owner_job_id = await _seed_section(
            s, book, title="Non-owner Lesson", page_id=page_id, order_index=1,
            archived_at=_h(2), completed_at=_h(2),
        )
        await s.commit()
    return owner_entry_id, owner_job_id, non_owner_entry_id, non_owner_job_id


async def _seed_standalone_owner(*, page_id: str, phase_md: dict[str, str]):
    """A single toc_entries + homework_jobs + phase_outputs row NOT part of
    any DB-level collision group — used by the refresh-only negative leg,
    where the manifest is hand-built (mirrors `_write_manifest` /
    `_seed_owner` in test_repair_notion_refresh.py)."""
    from app.db import SessionLocal

    async with SessionLocal() as s:
        book = await _seed_book(s)
        entry_id, job_id = await _seed_section(
            s, book, title="Owner", page_id=page_id, order_index=0,
            archived_at=_h(0), completed_at=_h(0), phase_md=phase_md,
        )
        await s.commit()
    return entry_id, job_id


def _write_manifest(tmp_path, owners):
    """`owners`: list of (page_id, section_id, job_id). Builds real
    `GroupPlan`/`SectionRow` objects and writes them through
    `manifest_from_plans` — matches exactly what `--apply` would have
    produced (mirrors test_repair_notion_refresh.py's helper of the same
    name)."""
    from scripts.repair_notion_collisions import (
        GroupPlan, SectionRow, manifest_from_plans,
    )

    plans = []
    for page_id, section_id, job_id in owners:
        section = SectionRow(
            section_id=section_id, page_id=page_id, section_title="t",
            page_start=1, subject="matematika", grade="5",
            stamped_job_id=job_id, jobs=(),
        )
        plans.append(GroupPlan(
            page_id=page_id, sections=(section,), owner=section,
            owner_source="stamped_push", owner_push=None,
            ordering_disagreement=False,
        ))
    manifest = manifest_from_plans(plans)
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return path


class RecordingFakeNotionClient(FakeNotionArchiveClient):
    """`FakeNotionArchiveClient` plus a spy on `clear_content_blocks` /
    `append_block_children`, so the test can assert a REWRITE happened
    (clear-then-append on an already-populated leaf) rather than merely
    inferring it from final content."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.cleared: list[str] = []
        self.appended: list[str] = []

    def clear_content_blocks(self, page_id):
        self.cleared.append(page_id)
        super().clear_content_blocks(page_id)

    def append_block_children(self, block_id, children):
        self.appended.append(block_id)
        super().append_block_children(block_id, children)


def _seed_existing_notion_page(client, *, page_id: str) -> list[str]:
    """Build a REALISTIC pre-repair Notion page on `client`: pushes the
    owner's own phases through the real `_push_to_notion` layout logic (so
    nesting matches production exactly, not a hand-rolled approximation),
    then adds the 3 `EXTRA_LEAVES` directly under the homework page —
    contamination from the since-cleared non-owner collision (mirrors
    `FakeNotionArchiveClient.seed_extra_leaf`'s own convention in
    test_repair_notion_refresh.py). Returns the extra leaves' ids."""
    from app.services.notion_archive import _push_to_notion

    _push_to_notion(
        client=client, subject_page_id="", lesson_title="",
        phase_md=OWNER_PHASE_MD, replace=False, homework_page_id=page_id,
    )
    return [
        client.seed_extra_leaf(homework_page_id=page_id, leaf_title=title, phase_file=phase_file)
        for title, phase_file in EXTRA_LEAVES
    ]


# ─── the happy path: dry-run -> apply -> refresh, all through run() ───────


async def test_two_gesture_flow_end_to_end(capsys, tmp_path):
    from scripts.repair_notion_collisions import manifest_load, run

    database_url = os.environ["DATABASE_URL"]
    page_id = "hw-e2e-mixed"
    (owner_entry_id, owner_job_id,
     non_owner_entry_id, non_owner_job_id) = await _seed_mixed_collision(page_id)

    # ── 1. dry-run: capture plan-hash from stdout ──────────────────────
    rc = await run(database_url=database_url, apply=False)
    assert rc == 0
    out = capsys.readouterr().out
    hash_line = next(line for line in out.splitlines() if line.startswith("plan-hash="))
    captured_hash = hash_line.split("=", 1)[1]
    assert captured_hash  # non-empty

    # ── 2. apply ─────────────────────────────────────────────────────
    manifest_path = tmp_path / "manifest.json"
    rc = await run(
        database_url=database_url, apply=True,
        expect_plan_hash=captured_hash, manifest_out=manifest_path,
    )
    assert rc == 0
    assert manifest_path.exists()

    manifest = manifest_load(manifest_path)  # accepts it (internal hash check passes)
    assert [o["page_id"] for o in manifest["owners"]] == [page_id]
    assert manifest["owners"][0]["section_id"] == str(owner_entry_id)
    assert manifest["owners"][0]["job_id"] == str(owner_job_id)

    from sqlalchemy import text

    from app.db import SessionLocal

    async with SessionLocal() as s:
        non_owner_row = (await s.execute(text(
            "SELECT notion_homework_page_id, notion_archived_job_id "
            "FROM toc_entries WHERE id = :id"
        ), {"id": non_owner_entry_id})).one()
        non_owner_job_row = (await s.execute(text(
            "SELECT notion_archived_at FROM homework_jobs WHERE id = :id"
        ), {"id": non_owner_job_id})).one()
        owner_row = (await s.execute(text(
            "SELECT notion_homework_page_id, notion_archived_job_id "
            "FROM toc_entries WHERE id = :id"
        ), {"id": owner_entry_id})).one()
        owner_job_row = (await s.execute(text(
            "SELECT notion_archived_at FROM homework_jobs WHERE id = :id"
        ), {"id": owner_job_id})).one()

    assert non_owner_row.notion_homework_page_id is None
    assert non_owner_row.notion_archived_job_id is None
    assert non_owner_job_row.notion_archived_at is None
    # owner row + owner job left COMPLETELY untouched
    assert owner_row.notion_homework_page_id == page_id
    assert owner_row.notion_archived_job_id == owner_job_id
    assert owner_job_row.notion_archived_at == _h(0)

    # ── 3. refresh-notion ────────────────────────────────────────────
    client = RecordingFakeNotionClient()
    extra_ids = _seed_existing_notion_page(client, page_id=page_id)

    constructed: list[object] = []

    def _factory():
        constructed.append(client)
        return client

    rc = await run(
        database_url=database_url, apply=False, refresh_notion=True,
        plan_file=manifest_path, client_factory=_factory,
    )
    assert rc == 0
    # the fake was the ONLY client ever built — pure in-memory, zero real
    # network calls anywhere in this run.
    assert len(constructed) == 1
    assert constructed[0] is client

    # owner page was REWRITTEN (not skipped): already-populated leaves were
    # cleared then re-appended, not merely left alone.
    assert client.cleared, "expected clear_content_blocks — owner page must be rewritten, not skipped"
    assert client.appended
    # the 3 extra leaves the owner never produced were pruned — and ONLY
    # those: nothing else the owner itself owns was deleted.
    assert set(client.deleted) == set(extra_ids)


# ─── negative leg 4: wrong --expect-plan-hash ──────────────────────────────


async def test_apply_wrong_expect_hash_aborts(tmp_path):
    from scripts.repair_notion_collisions import run

    database_url = os.environ["DATABASE_URL"]
    page_id = "hw-wrong-hash"
    await _seed_mixed_collision(page_id)

    before = await _snapshot()
    manifest_path = tmp_path / "manifest.json"

    rc = await run(
        database_url=database_url, apply=True,
        expect_plan_hash="0" * 64,  # well-formed but certainly wrong
        manifest_out=manifest_path,
    )

    assert rc != 0
    assert not manifest_path.exists()
    after = await _snapshot()
    assert before == after


# ─── negative leg 5: out-of-band mutation between dry-run and apply ───────


async def test_apply_aborts_on_out_of_band_drift(capsys, tmp_path):
    from scripts.repair_notion_collisions import run

    database_url = os.environ["DATABASE_URL"]
    page_id = "hw-drift"
    (_owner_entry_id, _owner_job_id,
     non_owner_entry_id, _non_owner_job_id) = await _seed_mixed_collision(page_id)

    # capture a valid hash via a real dry run, through run() itself
    rc = await run(database_url=database_url, apply=False)
    assert rc == 0
    out = capsys.readouterr().out
    captured_hash = next(
        line for line in out.splitlines() if line.startswith("plan-hash=")
    ).split("=", 1)[1]

    # mutate the non-owner's pointer OUT OF BAND (a concurrent process, not
    # this repair) between the dry-run and the apply below — same page id,
    # so it's still a collision, but the plan's expected state has changed.
    from sqlalchemy import text

    from app.db import SessionLocal

    async with SessionLocal() as s:
        await s.execute(text(
            "UPDATE toc_entries SET notion_homework_page_id = :new_page "
            "WHERE id = :id"
        ), {"new_page": "hw-drifted-elsewhere", "id": non_owner_entry_id})
        await s.commit()

    before_apply = await _snapshot()
    manifest_path = tmp_path / "manifest.json"

    rc = await run(
        database_url=database_url, apply=True,
        expect_plan_hash=captured_hash, manifest_out=manifest_path,
    )

    assert rc != 0
    assert not manifest_path.exists()
    after_apply = await _snapshot()
    assert before_apply == after_apply


# ─── negative leg 6: --refresh-notion with no --plan-file ─────────────────


async def test_refresh_requires_plan_file_never_builds_client():
    from scripts.repair_notion_collisions import run

    database_url = os.environ["DATABASE_URL"]
    constructed: list[object] = []

    rc = await run(
        database_url=database_url, apply=False,
        refresh_notion=True, plan_file=None,
        client_factory=lambda: constructed.append(1),
    )

    assert rc != 0
    assert constructed == [], "the Notion client must never be constructed"


# ─── negative leg 7: one owner's rewrite raises, a sibling still processed ─


async def test_refresh_partial_failure_still_processes_second_owner(tmp_path):
    from scripts.repair_notion_collisions import run

    database_url = os.environ["DATABASE_URL"]
    failing_page = "hw-partial-failing"
    healthy_page = "hw-partial-healthy"
    failing_section_id, failing_job_id = await _seed_standalone_owner(
        page_id=failing_page, phase_md={"case-based-preview": "# Preview"},
    )
    healthy_section_id, healthy_job_id = await _seed_standalone_owner(
        page_id=healthy_page, phase_md={"case-based-preview": "# Preview"},
    )
    manifest_path = _write_manifest(tmp_path, [
        (failing_page, failing_section_id, failing_job_id),
        (healthy_page, healthy_section_id, healthy_job_id),
    ])

    client = RecordingFakeNotionClient(raise_on_push={failing_page})
    extra_leaf_id = client.seed_extra_leaf(
        homework_page_id=failing_page, leaf_title="Boss Arena",
        phase_file="boss-arena.md",
    )
    constructed: list[object] = []

    def _factory():
        constructed.append(client)
        return client

    rc = await run(
        database_url=database_url, apply=False, refresh_notion=True,
        plan_file=manifest_path, client_factory=_factory,
    )

    assert rc != 0
    assert len(constructed) == 1
    # the failing owner's pre-existing extra leaf must NOT be pruned
    assert extra_leaf_id not in client.deleted
    # the healthy owner must still have been rewritten
    healthy_leaf = next(
        pid for pid, p in client.pages.items()
        if p["parent"] == healthy_page and p["title"] == "Case-Based Preview"
    )
    assert client.content[healthy_leaf]
