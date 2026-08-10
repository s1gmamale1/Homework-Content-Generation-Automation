"""Read-only Notion page classifier for the collision repair's cleanup step.

After `scripts/repair_notion_collisions.py` clears the DB pointers (see
`tests/scripts/test_repair_notion_collisions.py`), a "mixed" homework page may
still carry leaves authored by MORE THAN ONE lesson — a non-owner appended
`practice-*` leaves the owner never produced. Before anything is rewritten or
pruned (a LATER task), the repair needs to read a page's actual leaves and
classify which phases it hosts vs. which are extras belonging to some other
lesson.

`classify_page(client, page_id, owner_phase_set)` is that classifier. It is
STRICTLY read-only — it walks `get_child_pages`/`get_block_children`, never
calls a write method, and never raises: any client error is captured as an
`unreadable` verdict.

No DB, no real Notion, no network — a hand-built `FakeNotionClient` only.

Run:
  uv run python -m pytest tests/scripts/test_repair_notion_refresh.py -q
"""
from __future__ import annotations

from scripts.repair_notion_collisions import (
    OwnerRefreshOutcome,
    PageClassification,
    RefreshReport,
    classify_page,
    format_refresh_report,
)


class FakeNotionClient:
    """Canned `get_child_pages`/`get_block_children` responses — no real API.

    `raise_on` is a set of block/page ids that raise `RuntimeError` from
    EITHER method, simulating a Notion read failure."""

    def __init__(
        self,
        child_pages: dict[str, list[dict]],
        block_children: dict[str, list[dict]],
        raise_on: set[str] | None = None,
    ) -> None:
        self._child_pages = child_pages
        self._block_children = block_children
        self._raise_on = raise_on or set()

    def get_child_pages(self, parent_id: str) -> list[dict]:
        if parent_id in self._raise_on:
            raise RuntimeError(f"fake notion error: get_child_pages({parent_id})")
        return self._child_pages.get(parent_id, [])

    def get_block_children(self, block_id: str) -> list[dict]:
        if block_id in self._raise_on:
            raise RuntimeError(f"fake notion error: get_block_children({block_id})")
        return self._block_children.get(block_id, [])


def _file_block(name: str) -> dict:
    return {"type": "file", "file": {"name": name}}


HW = "hw-page"
CBP_LEAF = "leaf-cbp"
FLASH_LEAF = "leaf-flash"
CONTAINER = "container-gamified"
GAME_RLC = "leaf-game-rlc"
BOSS_LEAF = "leaf-boss"
REFLECT_LEAF = "leaf-reflect"

_BASE_CHILD_PAGES = {
    HW: [
        {"id": CBP_LEAF, "title": "Case-Based Preview", "type": "child_page"},
        {"id": FLASH_LEAF, "title": "Flashcards", "type": "child_page"},
        {"id": CONTAINER, "title": "Gamified Practices", "type": "child_page"},
        {"id": BOSS_LEAF, "title": "Boss Arena", "type": "child_page"},
        {"id": REFLECT_LEAF, "title": "Reflection", "type": "child_page"},
    ],
    CONTAINER: [
        {"id": GAME_RLC, "title": "Real-Life Challenge", "type": "child_page"},
    ],
}

_BASE_BLOCK_CHILDREN = {
    CBP_LEAF: [_file_block("case-based-preview.md")],
    FLASH_LEAF: [_file_block("flashcards.md")],
    GAME_RLC: [_file_block("practice-rlc.md")],
    BOSS_LEAF: [_file_block("boss-arena.md")],
    REFLECT_LEAF: [_file_block("reflection.md")],
}

_OWNER_PHASES = {
    "case-based-preview", "flashcards", "memory-check",
    "practice-rlc", "practice-error-detection", "practice-tictactoe",
    "boss-arena", "reflection",
}  # 8 phases


def test_classify_clean():
    client = FakeNotionClient(_BASE_CHILD_PAGES, _BASE_BLOCK_CHILDREN)

    result = classify_page(client, HW, _OWNER_PHASES)

    assert result.verdict == "clean"
    assert result.page_phases == frozenset(
        {"case-based-preview", "flashcards", "practice-rlc", "boss-arena", "reflection"}
    )
    assert result.extra_phases == frozenset()
    assert result.extra_child_page_ids == ()
    assert result.error is None


def test_classify_mixed_extra_practice_leaves():
    jigsaw_id, memory_match_id, sentence_id = (
        "leaf-game-jigsaw", "leaf-game-memory-match", "leaf-game-sentence",
    )
    child_pages = {
        **_BASE_CHILD_PAGES,
        CONTAINER: [
            *_BASE_CHILD_PAGES[CONTAINER],
            {"id": jigsaw_id, "title": "Jigsaw Matching", "type": "child_page"},
            {"id": memory_match_id, "title": "Memory Matching", "type": "child_page"},
            {"id": sentence_id, "title": "Sentence Filling", "type": "child_page"},
        ],
    }
    block_children = {
        **_BASE_BLOCK_CHILDREN,
        jigsaw_id: [_file_block("practice-jigsaw.md")],
        memory_match_id: [_file_block("practice-memory-match.md")],
        sentence_id: [_file_block("practice-sentence.md")],
    }
    client = FakeNotionClient(child_pages, block_children)

    result = classify_page(client, HW, _OWNER_PHASES)

    assert result.verdict == "mixed"
    assert result.extra_phases == frozenset(
        {"practice-jigsaw", "practice-memory-match", "practice-sentence"}
    )
    assert set(result.extra_child_page_ids) == {jigsaw_id, memory_match_id, sentence_id}
    assert result.error is None
    # the owner's own phases are still part of page_phases, just not "extra"
    assert result.page_phases >= _OWNER_PHASES & result.page_phases


def test_classify_resolves_phase_from_attachment_name():
    """A leaf whose title doesn't match any `PHASE_TITLES` entry still
    resolves correctly from its `{phase}.md` file-block attachment."""
    odd_leaf = "leaf-odd-title"
    child_pages = {HW: [{"id": odd_leaf, "title": "Untitled 7 (copy)", "type": "child_page"}]}
    block_children = {odd_leaf: [_file_block("reflection.md")]}
    client = FakeNotionClient(child_pages, block_children)

    result = classify_page(client, HW, {"reflection"})

    assert result.verdict == "clean"
    assert result.page_phases == frozenset({"reflection"})
    assert result.extra_phases == frozenset()


def test_classify_unreadable_on_client_error():
    client = FakeNotionClient({}, {}, raise_on={HW})

    result = classify_page(client, HW, _OWNER_PHASES)

    assert result.verdict == "unreadable"
    assert result.page_phases == frozenset()
    assert result.extra_phases == frozenset()
    assert result.extra_child_page_ids == ()
    assert result.error is not None
    assert "fake notion error" in result.error


def test_page_classification_is_frozen_dataclass():
    """Sanity: the dataclass is immutable, matching the read-only contract."""
    pc = PageClassification(
        verdict="clean", page_phases=frozenset(), extra_phases=frozenset(),
        extra_child_page_ids=(), error=None,
    )
    import dataclasses

    assert dataclasses.is_dataclass(pc)
    try:
        pc.verdict = "mixed"  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        pass
    else:
        raise AssertionError("PageClassification must be frozen")


def test_format_refresh_report():
    """Pure formatter, no DB/Notion: one outcome of each kind must produce a
    labeled per-owner line AND be tallied into the summary line's counts."""
    report = RefreshReport(outcomes=(
        OwnerRefreshOutcome(
            page_id="pg-rewritten", section_id="sec-1", job_id="job-1",
            outcome="rewritten", verdict="mixed", pruned=2,
        ),
        OwnerRefreshOutcome(
            page_id="pg-drift", section_id="sec-2", job_id="job-2",
            outcome="owner_pointer_drift",
        ),
        OwnerRefreshOutcome(
            page_id="pg-no-owner-job", section_id="sec-3", job_id=None,
            outcome="no_owner_job",
        ),
        OwnerRefreshOutcome(
            page_id="pg-no-phases", section_id="sec-4", job_id="job-4",
            outcome="owner_no_phases",
        ),
        OwnerRefreshOutcome(
            page_id="pg-rewrite-failed", section_id="sec-5", job_id="job-5",
            outcome="rewrite_failed", error="boom",
        ),
        OwnerRefreshOutcome(
            page_id="pg-prune-failed", section_id="sec-6", job_id="job-6",
            outcome="prune_failed", pruned=1, error="delete boom",
        ),
    ))

    lines = format_refresh_report(report)

    assert lines[0] == (
        "refresh: rewritten=1 pruned=3 skipped_drift=1 skipped_no_phases=1 "
        "rewrite_failed=1 prune_failed=1"
    )
    body = "\n".join(lines[1:])
    assert "pg-rewritten" in body and "REWRITTEN verdict=mixed pruned=2" in body
    assert "pg-drift" in body and "SKIPPED (owner_pointer_drift)" in body
    assert "pg-no-owner-job" in body and "SKIPPED (no_owner_job)" in body
    assert "pg-no-phases" in body and "SKIPPED (owner_no_phases" in body
    assert "pg-rewrite-failed" in body and "REWRITE_FAILED error=boom" in body
    assert (
        "pg-prune-failed" in body
        and "REWRITTEN but PRUNE_FAILED pruned=1 error=delete boom" in body
    )


# ─── gesture 2: --refresh-notion --plan-file (real-DB + fake Notion) ──────
#
# After --apply clears the non-owner DB pointers (gesture 1), the collision
# query returns nothing, so this step reads the manifest --apply wrote
# instead of re-deriving a plan. For EVERY owner in the manifest — including
# ones that will classify "clean" — the page is rewritten authoritatively
# (a newer non-owner may have `auto_replace`d a shared leaf since the
# manifest was written), and only a SUCCESSFUL rewrite is followed by
# pruning the leaves `classify_page` flags as belonging to another lesson.
#
# Real scratch Postgres (RUN_DB_INTEGRATION=1 + DATABASE_URL) + a hand-built,
# simulation-based fake Notion client (unlike the canned-response
# `FakeNotionClient` above) — NEVER real Notion, NEVER production DB.
#
# Run:
#   export DATABASE_URL="postgresql+asyncpg://edu:edu@127.0.0.1:5432/edu_scratch_notionrepair"
#   RUN_DB_INTEGRATION=1 uv run python -m pytest \
#     tests/scripts/test_repair_notion_refresh.py -q

import json
import os

import pytest

_needs_db = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)


class FakeNotionArchiveClient:
    """A SIMULATION-based fake (unlike `FakeNotionClient` above, which
    replays canned dict responses): pages are actually created/removed as
    the real `_push_to_notion` and `classify_page` would see them, so a
    rewrite's *effects* are what the prune step reads. Mirrors `FakeNotion`
    in tests/services/test_notion_lesson_collision.py, extended with
    `get_block_children`/`delete_block` for the classify + prune step.

    `raise_on_push`: a set of homework-page ids whose very first
    `get_child_pages` call raises — simulates the whole push failing for
    that page (the first client call `_push_to_notion` makes once
    `homework_page_id` is already known)."""

    def __init__(self, *, raise_on_push: set[str] | None = None) -> None:
        self.pages: dict[str, dict] = {}      # id -> {"title", "parent"}
        self.content: dict[str, list] = {}    # id -> appended blocks
        self.deleted: list[str] = []
        self._raise_on_push = raise_on_push or set()
        self._n = 0

    # -- the subset of NotionClientWrapper that _push_to_notion + classify_page touch --
    def get_child_pages(self, parent_id):
        if parent_id in self._raise_on_push:
            raise RuntimeError(f"fake notion push failure: {parent_id}")
        return [{"id": pid, "title": p["title"]}
                for pid, p in self.pages.items() if p["parent"] == parent_id]

    def get_block_children(self, block_id):
        return self.content.get(block_id, [])

    def create_page(self, parent_id, title, children=None):
        self._n += 1
        pid = f"pg{self._n}"
        self.pages[pid] = {"title": title, "parent": parent_id}
        return {"id": pid}

    def page_has_content(self, page_id):
        return bool(self.content.get(page_id))

    def append_block_children(self, block_id, children):
        self.content.setdefault(block_id, []).extend(children)

    def clear_content_blocks(self, page_id):
        self.content.pop(page_id, None)

    def upload_bytes(self, data, file_name, content_type):
        return "file-upload-id"

    def delete_block(self, block_id):
        self.deleted.append(block_id)
        self.pages.pop(block_id, None)
        self.content.pop(block_id, None)

    # -- test helpers --
    def seed_extra_leaf(self, *, homework_page_id, leaf_title, phase_file):
        """Pre-seed a contaminating leaf directly under the homework page —
        simulating pre-existing Notion content from BEFORE this refresh that
        the rewrite (which only touches phases the owner actually has) will
        leave untouched."""
        self._n += 1
        leaf_id = f"pg{self._n}"
        self.pages[leaf_id] = {"title": leaf_title, "parent": homework_page_id}
        self.content[leaf_id] = [{"type": "file", "file": {"name": phase_file}}]
        return leaf_id


async def _truncate() -> None:
    from sqlalchemy import text

    from app.db import SessionLocal

    async with SessionLocal() as s:
        await s.execute(text(
            "TRUNCATE phase_outputs, homework_jobs, toc_entries, books "
            "RESTART IDENTITY CASCADE"
        ))
        await s.commit()


@pytest.fixture
async def db_clean():
    await _truncate()
    yield
    await _truncate()


async def _seed_owner(*, page_id: str, phase_md: dict[str, str], drift: bool = False):
    """Seed one toc_entries + homework_jobs + phase_outputs row representing
    a manifest owner's LIVE state. `drift=True` seeds the live
    notion_homework_page_id as something OTHER than `page_id`, simulating a
    pointer that changed since the manifest was written."""
    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.homework_job import HomeworkJob
    from app.models.phase_output import PhaseOutput
    from app.models.toc_entry import TOCEntry

    async with SessionLocal() as s:
        book = Book(
            subject="matematika", grade="5", original_filename="t.pdf",
            content_sha256="a" * 64, file_size_bytes=1, status="toc_ready",
        )
        s.add(book)
        await s.flush()

        entry = TOCEntry(
            book_id=book.id, section_title="Owner section", order_index=0,
            page_start=1,
            notion_homework_page_id="drifted-elsewhere" if drift else page_id,
        )
        s.add(entry)
        await s.flush()

        job = HomeworkJob(
            book_id=book.id, toc_entry_id=entry.id, subject=book.subject,
            status="done", provider="gemini", model="gemini-3.6-flash",
            transport="api", output_language="uz",
        )
        s.add(job)
        await s.flush()

        entry.notion_archived_job_id = job.id
        await s.flush()

        for i, (phase_name, md) in enumerate(phase_md.items()):
            s.add(PhaseOutput(
                job_id=job.id, phase_name=phase_name, phase_order=i,
                prompt_hash="h", model_name="gemini-3.6-flash",
                status="done", output_md=md,
            ))
        await s.commit()
        return entry.id, job.id


def _write_manifest(tmp_path, owners):
    """`owners`: list of (page_id, section_id, job_id). Builds real
    `GroupPlan`/`SectionRow` objects and writes them through
    `manifest_from_plans`, so the file matches exactly what `--apply` would
    have produced."""
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


async def _refresh(*, database_url, plan_file, client_factory):
    from scripts.repair_notion_collisions import run

    return await run(
        database_url=database_url, apply=False,
        refresh_notion=True, plan_file=plan_file, client_factory=client_factory,
    )


@_needs_db
async def test_refresh_rewrites_every_owner_including_clean(db_clean, tmp_path):
    """Both a 'clean' owner (nothing extra on the page) and a 'mixed' owner
    (an extra pre-existing leaf) must get a `replace=True` rewrite — no
    owner is skipped just because it would classify clean."""
    clean_page = "hw-clean"
    mixed_page = "hw-mixed"
    clean_section, clean_job = await _seed_owner(
        page_id=clean_page, phase_md={"case-based-preview": "# Preview"},
    )
    mixed_section, mixed_job = await _seed_owner(
        page_id=mixed_page, phase_md={"case-based-preview": "# Preview"},
    )
    manifest_path = _write_manifest(tmp_path, [
        (clean_page, clean_section, clean_job),
        (mixed_page, mixed_section, mixed_job),
    ])
    client = FakeNotionArchiveClient()

    rc = await _refresh(
        database_url=os.environ["DATABASE_URL"],
        plan_file=manifest_path, client_factory=lambda: client,
    )

    assert rc == 0
    # Both pages actually received a leaf write — the fake only records a
    # page as having content once append_block_children ran on it.
    clean_leaf = next(
        pid for pid, p in client.pages.items()
        if p["parent"] == clean_page and p["title"] == "Case-Based Preview"
    )
    mixed_leaf = next(
        pid for pid, p in client.pages.items()
        if p["parent"] == mixed_page and p["title"] == "Case-Based Preview"
    )
    assert client.content[clean_leaf], "clean owner's page was not rewritten"
    assert client.content[mixed_leaf], "mixed owner's page was not rewritten"


@_needs_db
async def test_refresh_prunes_extras_after_success(db_clean, tmp_path):
    """A mixed owner's pre-existing extra leaf (belonging to some OTHER
    lesson) must be pruned via `delete_block` after the rewrite succeeds —
    and ONLY that extra leaf, not the owner's own rewritten leaf."""
    page_id = "hw-mixed-2"
    section_id, job_id = await _seed_owner(
        page_id=page_id, phase_md={"case-based-preview": "# Preview"},
    )
    manifest_path = _write_manifest(tmp_path, [(page_id, section_id, job_id)])
    client = FakeNotionArchiveClient()
    extra_leaf_id = client.seed_extra_leaf(
        homework_page_id=page_id, leaf_title="Boss Arena",
        phase_file="boss-arena.md",
    )

    rc = await _refresh(
        database_url=os.environ["DATABASE_URL"],
        plan_file=manifest_path, client_factory=lambda: client,
    )

    assert rc == 0
    assert client.deleted == [extra_leaf_id]
    owner_leaf = next(
        pid for pid, p in client.pages.items()
        if p["parent"] == page_id and p["title"] == "Case-Based Preview"
    )
    assert owner_leaf not in client.deleted


@_needs_db
async def test_failed_rewrite_prunes_nothing(db_clean, tmp_path, monkeypatch):
    """A page whose rewrite raises (every retry attempt) must be recorded as
    a failure and prune NOTHING for it — but a second, healthy owner must
    still be processed."""
    import app.services.notion_archive as na
    monkeypatch.setattr(na, "_PUSH_BACKOFF_BASE_SECONDS", 0.0)

    failing_page = "hw-failing"
    healthy_page = "hw-healthy"
    failing_section, failing_job = await _seed_owner(
        page_id=failing_page, phase_md={"case-based-preview": "# Preview"},
    )
    healthy_section, healthy_job = await _seed_owner(
        page_id=healthy_page, phase_md={"case-based-preview": "# Preview"},
    )
    manifest_path = _write_manifest(tmp_path, [
        (failing_page, failing_section, failing_job),
        (healthy_page, healthy_section, healthy_job),
    ])
    client = FakeNotionArchiveClient(raise_on_push={failing_page})
    extra_leaf_id = client.seed_extra_leaf(
        homework_page_id=failing_page, leaf_title="Boss Arena",
        phase_file="boss-arena.md",
    )

    from scripts.repair_notion_collisions import refresh_owner_pages
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(os.environ["DATABASE_URL"], future=True)
    try:
        async with engine.connect() as conn:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            report = await refresh_owner_pages(client, manifest, conn)
    finally:
        await engine.dispose()

    outcomes = {o.page_id: o for o in report.outcomes}
    assert outcomes[failing_page].outcome == "rewrite_failed"
    assert extra_leaf_id not in client.deleted, "a failed rewrite must prune NOTHING"
    assert outcomes[healthy_page].outcome == "rewritten"
    healthy_leaf = next(
        pid for pid, p in client.pages.items()
        if p["parent"] == healthy_page and p["title"] == "Case-Based Preview"
    )
    assert client.content[healthy_leaf]


# These two gate checks (`run()` returns before ever creating a DB engine —
# see `scripts/repair_notion_collisions.run`) genuinely need no database at
# all, so they run unconditionally (no `_needs_db`, no scratch Postgres) —
# a bogus `database_url` proves the point: it is never dialed.
_UNUSED_DATABASE_URL = "postgresql+asyncpg://unused:unused@127.0.0.1:5432/unused_no_such_db"


async def test_refresh_requires_plan_file(tmp_path):
    """`--refresh-notion` without `--plan-file`: clear error, non-zero exit,
    zero side effects (no engine, no Notion client)."""
    calls = []

    rc = await _refresh(
        database_url=_UNUSED_DATABASE_URL,
        plan_file=None, client_factory=lambda: calls.append(1),
    )

    assert rc != 0
    assert calls == [], "the client must never be constructed"


async def test_refresh_rejects_tampered_manifest(tmp_path):
    """A hand-edited manifest (its `expected` content no longer matches the
    stored hash) must fail `manifest_load`'s internal integrity check —
    refresh exits non-zero and never touches Notion. No DB row needs to be
    real for this: `manifest_load`'s hash check is pure, over the manifest
    file's own content, and `run()` returns before ever opening a DB
    connection — arbitrary ids are enough."""
    from uuid import uuid4

    manifest_path = _write_manifest(
        tmp_path, [("hw-tampered", uuid4(), uuid4())],
    )
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw["expected"]["groups"][0]["owner_source"] = "TAMPERED"
    manifest_path.write_text(json.dumps(raw, indent=2), encoding="utf-8")
    calls = []

    rc = await _refresh(
        database_url=_UNUSED_DATABASE_URL,
        plan_file=manifest_path, client_factory=lambda: calls.append(1),
    )

    assert rc != 0
    assert calls == [], "a tampered manifest must never reach the Notion client"


@_needs_db
async def test_refresh_skips_owner_whose_pointer_drifted(db_clean, tmp_path):
    """The manifest says owner -> pageX, but the DB now has that owner's
    live `notion_homework_page_id` pointing elsewhere (drifted since the
    manifest was written) — SKIP that owner (`owner_pointer_drift`), no
    rewrite, and a healthy owner in the same manifest is still processed."""
    drifted_page = "hw-was-pagex"
    drifted_section, drifted_job = await _seed_owner(
        page_id=drifted_page, phase_md={"case-based-preview": "# Preview"},
        drift=True,
    )
    healthy_page = "hw-still-good"
    healthy_section, healthy_job = await _seed_owner(
        page_id=healthy_page, phase_md={"case-based-preview": "# Preview"},
    )
    manifest_path = _write_manifest(tmp_path, [
        (drifted_page, drifted_section, drifted_job),
        (healthy_page, healthy_section, healthy_job),
    ])
    client = FakeNotionArchiveClient()

    rc = await _refresh(
        database_url=os.environ["DATABASE_URL"],
        plan_file=manifest_path, client_factory=lambda: client,
    )

    # A drifted owner is a "didn't cleanly rewrite" outcome — run() must
    # surface that on the exit code even though a sibling owner in the same
    # manifest was rewritten fine.
    assert rc != 0
    assert not any(
        p["parent"] == drifted_page for p in client.pages.values()
    ), "the drifted owner must not have been rewritten"
    healthy_leaf = next(
        pid for pid, p in client.pages.items()
        if p["parent"] == healthy_page and p["title"] == "Case-Based Preview"
    )
    assert client.content[healthy_leaf], "the healthy owner must still be processed"


@_needs_db
async def test_owner_with_no_done_phases_prunes_nothing(db_clean, tmp_path):
    """Bug A regression guard (mass-deletion): an owner whose stamped job
    produced ZERO done non-`extract` phase_outputs must be recorded as
    `owner_no_phases` and must NOT trigger a prune. Against the buggy code,
    `owner_phase_set` collapses to the empty set, `classify_page` computes
    `extra_phases = page_phases - set() = ALL page phases`, and EVERY
    existing leaf on the page — including ones belonging to other lessons —
    gets `delete_block`'d."""
    page_id = "hw-no-phases"
    # Only an `extract` phase_output exists (or none at all) — no done,
    # non-extract phase for `load_owner_phase_md` to return.
    section_id, job_id = await _seed_owner(
        page_id=page_id, phase_md={"extract": "# raw extract text"},
    )
    manifest_path = _write_manifest(tmp_path, [(page_id, section_id, job_id)])
    client = FakeNotionArchiveClient()
    # Pre-existing leaves that must survive untouched — this is exactly the
    # "other lessons' content" a mass-delete would destroy.
    other_leaf_id = client.seed_extra_leaf(
        homework_page_id=page_id, leaf_title="Boss Arena",
        phase_file="boss-arena.md",
    )
    another_leaf_id = client.seed_extra_leaf(
        homework_page_id=page_id, leaf_title="Reflection",
        phase_file="reflection.md",
    )

    from scripts.repair_notion_collisions import refresh_owner_pages
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(os.environ["DATABASE_URL"], future=True)
    try:
        async with engine.connect() as conn:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            report = await refresh_owner_pages(client, manifest, conn)
    finally:
        await engine.dispose()

    outcomes = {o.page_id: o for o in report.outcomes}
    assert outcomes[page_id].outcome == "owner_no_phases"
    assert client.deleted == [], "an owner with zero done phases must prune NOTHING"
    assert other_leaf_id not in client.deleted
    assert another_leaf_id not in client.deleted


@_needs_db
async def test_prune_failure_is_fail_open(db_clean, tmp_path):
    """Bug B regression guard: a `delete_block` failure during the prune
    step must be caught (recorded as `prune_failed`), never propagate out of
    `refresh_owner_pages`, and must not stop a second owner in the same
    manifest from being processed. Against the buggy code the classify+prune
    block runs outside any try/except, so this exception would crash the
    whole batch."""
    failing_page = "hw-prune-fails"
    healthy_page = "hw-prune-healthy"
    failing_section, failing_job = await _seed_owner(
        page_id=failing_page, phase_md={"case-based-preview": "# Preview"},
    )
    healthy_section, healthy_job = await _seed_owner(
        page_id=healthy_page, phase_md={"case-based-preview": "# Preview"},
    )
    manifest_path = _write_manifest(tmp_path, [
        (failing_page, failing_section, failing_job),
        (healthy_page, healthy_section, healthy_job),
    ])

    class PruneFailingClient(FakeNotionArchiveClient):
        def delete_block(self, block_id):
            if self.pages.get(block_id, {}).get("parent") == failing_page:
                raise RuntimeError("fake notion delete_block failure")
            super().delete_block(block_id)

    client = PruneFailingClient()
    extra_leaf_id = client.seed_extra_leaf(
        homework_page_id=failing_page, leaf_title="Boss Arena",
        phase_file="boss-arena.md",
    )

    from scripts.repair_notion_collisions import refresh_owner_pages
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(os.environ["DATABASE_URL"], future=True)
    try:
        async with engine.connect() as conn:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            # Must not raise — fail-open per page.
            report = await refresh_owner_pages(client, manifest, conn)
    finally:
        await engine.dispose()

    outcomes = {o.page_id: o for o in report.outcomes}
    assert outcomes[failing_page].outcome == "prune_failed"
    assert extra_leaf_id not in client.deleted
    assert outcomes[healthy_page].outcome == "rewritten"
    healthy_leaf = next(
        pid for pid, p in client.pages.items()
        if p["parent"] == healthy_page and p["title"] == "Case-Based Preview"
    )
    assert client.content[healthy_leaf], "second owner must still be processed"
