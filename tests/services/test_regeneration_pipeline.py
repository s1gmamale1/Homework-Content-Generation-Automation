"""The ordinary pipeline, running a REVISION job.

The design decision this file protects is that regeneration has no pipeline of
its own: a revision is a `kind="homework"` job with `selected_phases=NULL` whose
unchanged phases are already sitting in `phase_outputs` as copied `done` rows,
so the existing resume logic re-runs exactly the campaign's phases and nothing
else. Only two things in `pipeline.py` actually change, and both are here:

* the session-limit strategy comes from the job's own column when it has one
  (a revision has `batch_id=NULL`, so there is no batch row to read the
  approved campaign's frozen choice from) — and an ordinary job's resolution
  must be byte-for-byte what it was: batch, then the fleet-wide default;
* pipeline completion must not hand a revision to the LEGACY Notion archive.
  V1 is immutable; a revision is published as a versioned sibling by its own
  publisher, and calling `archive_job` here would overwrite the live V1 page.

The end-to-end run below fakes only the provider/judge/solver boundary — the
real `_execute_phase`, the real phase-row writes, the real resume predicate and
the real completion branch all run.
"""
from __future__ import annotations

import os
import types
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.repositories.regeneration_targets as targets_repo
from app.services import pipeline
from app.services.flows import flow_for
from app.services.regeneration_planner import build_phase_plan

_SUBJECT = "math-algebra"
_CANONICAL = ("extract", *flow_for(_SUBJECT))
_PLAN = build_phase_plan(subject=_SUBJECT, selected_phases=["flashcards"])
_PHASE_PLAN = _PLAN.to_json()
# The other extraction disposition: `refresh_extraction=True` puts `extract`
# in `regenerated_phases` (so it is NOT copied forward) and pulls every content
# phase into the closure with it.
_REFRESH_PLAN = build_phase_plan(
    subject=_SUBJECT, selected_phases=[], refresh_extraction=True)
_REFRESH_PHASE_PLAN = _REFRESH_PLAN.to_json()

db_only = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)


# ═════════════════════════════════════════════════════════════════════════
# session-limit resolution (no DB): the revision read, and the ORDINARY
# regression that must not move
# ═════════════════════════════════════════════════════════════════════════


@pytest.fixture()
def head(monkeypatch):
    """Stub `run()`'s context load so the first content phase is reached with
    nothing else touching a database, then abort there."""
    ns = types.SimpleNamespace()
    ns.captured: list[dict] = []
    ns.batch_lookups: list = []

    job = types.SimpleNamespace(
        id=uuid.uuid4(), book_id=uuid.uuid4(), toc_entry_id=uuid.uuid4(),
        provider="gemini", model="gemini-3.5-flash", transport="api",
        extract_transport="inherit", judge_transport="inherit",
        solver_transport="inherit", custom_prompts=None, selected_phases=None,
        judge_provider=None, judge_model=None, solver_provider=None,
        solver_model=None, extract_provider=None, extract_model=None,
        batch_id=None, output_language="uz", kind="homework",
        subject=_SUBJECT, session_limit_strategy=None,
        revision_of_job_id=None, regeneration_target_id=None,
    )
    book = types.SimpleNamespace(
        id=job.book_id, subject=_SUBJECT, grade="7", file_size_bytes=123)
    section = types.SimpleNamespace(
        id=job.toc_entry_id, section_title="L1", section_number="1.1",
        page_start=1, page_end=5, chapter_title="Ch1", order_index=0)
    ld = types.SimpleNamespace(
        extract_provider="gemini", extract_model="gemini-3.5-flash-lite",
        judge_provider="claude", judge_model="claude-opus-4-7",
        solver_provider="claude", solver_model="claude-opus-4-7",
        solver_boss_arena_enabled=True)
    ns.job, ns.book, ns.ld = job, book, ld

    monkeypatch.setattr(pipeline.jobs_repo, "get", AsyncMock(return_value=job))
    monkeypatch.setattr(pipeline.books_repo, "get", AsyncMock(return_value=book))
    monkeypatch.setattr(pipeline.toc_repo, "get", AsyncMock(return_value=section))
    monkeypatch.setattr(
        pipeline.toc_repo, "get_next_in_book", AsyncMock(return_value=None))
    import app.repositories.launch_defaults as ld_repo
    monkeypatch.setattr(ld_repo, "get", AsyncMock(return_value=ld))
    monkeypatch.setattr(
        pipeline.book_fetch, "ensure_book_pdf",
        AsyncMock(return_value=Path("/fake/book.pdf")))
    monkeypatch.setattr(
        pipeline.phase_repo, "list_for_job", AsyncMock(return_value=[]))
    monkeypatch.setattr(pipeline.jobs_repo, "set_status", AsyncMock(return_value=None))
    monkeypatch.setattr(pipeline.events_bus, "publish", AsyncMock())
    monkeypatch.setattr(pipeline.events_bus, "close", AsyncMock())

    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)

    async def _get(model_cls, pk, **kw):
        ns.batch_lookups.append((model_cls.__name__, pk))
        return ns.batch_row

    session.get = _get
    ns.batch_row = None
    monkeypatch.setattr(pipeline, "SessionLocal", MagicMock(return_value=session))

    async def _capture(**kw):
        ns.captured.append(kw)
        raise RuntimeError("stop after the head phase")

    monkeypatch.setattr(pipeline, "_execute_one_phase", _capture)
    return ns


async def _strategy(head) -> str:
    await pipeline.run(head.job.id)
    assert head.captured, "the head phase was never reached"
    return head.captured[0]["session_limit_strategy"]


async def test_ordinary_job_still_resolves_batch_then_global(head, monkeypatch):
    """REGRESSION: an ordinary job's column is NULL and nothing about its
    resolution moves — the batch override still wins."""
    from app.config import settings

    monkeypatch.setattr(settings, "session_limit_strategy", "pause")
    head.job.batch_id = uuid.uuid4()
    head.batch_row = types.SimpleNamespace(session_limit_strategy="switch")
    assert await _strategy(head) == "switch"
    assert [name for name, _ in head.batch_lookups] == ["Batch"], (
        "an ordinary job must still LOAD its batch to resolve the strategy")


async def test_ordinary_job_without_a_batch_still_falls_back_to_the_global(
    head, monkeypatch
):
    from app.config import settings

    monkeypatch.setattr(settings, "session_limit_strategy", "switch")
    head.job.batch_id = None
    assert await _strategy(head) == "switch"


async def test_revision_uses_its_own_column_not_the_mutable_global(head, monkeypatch):
    """A revision has `batch_id=NULL`, so without the column the operator's
    approved selection would fall through to the fleet-wide default at run time."""
    from app.config import settings

    monkeypatch.setattr(settings, "session_limit_strategy", "pause")
    # `_as_revision` (below) is what a revision actually looks like: its target
    # row is present, because `ck_homework_jobs_revision_pair` forces the id to
    # be set and the FK is RESTRICT. A run now reads that target's plan, so a
    # half-built revision would only be testing the refusal path.
    _as_revision(head, phase_plan=_PHASE_PLAN, monkeypatch=monkeypatch)
    head.job.session_limit_strategy = "switch"
    assert await _strategy(head) == "switch"
    assert head.batch_lookups == [], (
        "a revision must not need a batch row at all")


# ═════════════════════════════════════════════════════════════════════════
# refresh_extraction: the run must FORCE a fresh extract
#
# `refresh_extraction=true` is the campaign promising a genuinely re-read
# extraction — the estimator bills one extract call for it and the runbook
# says it re-reads the PDF. The pipeline's cross-job extract cache is keyed
# on (toc_entry, prompt_hash, extract provider/model) and would hand back the
# SOURCE job's extract at zero tokens, so the run has to tell `_execute_phase`
# to skip it. Non-refresh revisions never reach here (their extract is a
# copied `done` row, resumed for free) and ordinary Fleet jobs must keep the
# cache exactly as it was.
# ═════════════════════════════════════════════════════════════════════════


def _as_revision(head, *, phase_plan, monkeypatch):
    head.job.revision_of_job_id = uuid.uuid4()
    head.job.regeneration_target_id = uuid.uuid4()
    head.job.session_limit_strategy = "pause"
    lookup = AsyncMock(return_value=types.SimpleNamespace(
        id=head.job.regeneration_target_id, phase_plan=phase_plan))
    monkeypatch.setattr(targets_repo, "get_target_by_revision_job", lookup)
    return lookup


async def _head_kwargs(head) -> dict:
    await pipeline.run(head.job.id)
    assert head.captured, "the head phase was never reached"
    assert head.captured[0]["phase_name"] == "extract"
    return head.captured[0]


async def test_a_refresh_revision_forces_a_fresh_extract(head, monkeypatch):
    """The bug: the extract phase is planned to REGENERATE, then silently
    served from the cross-job cache — the campaign pays for an extraction it
    never got, and every regenerated phase is grounded in the V1 extract."""
    _as_revision(head, phase_plan=_REFRESH_PHASE_PLAN, monkeypatch=monkeypatch)
    assert (await _head_kwargs(head))["force_fresh_extract"] is True


async def test_a_copy_extract_revision_does_not_force_a_fresh_extract(
    head, monkeypatch
):
    """The default disposition stays free: nothing about a non-refresh
    revision's extract may start billing."""
    _as_revision(head, phase_plan=_PHASE_PLAN, monkeypatch=monkeypatch)
    assert (await _head_kwargs(head))["force_fresh_extract"] is False


async def test_an_ordinary_job_never_forces_a_fresh_extract(head, monkeypatch):
    """REGRESSION: ordinary Fleet generation keeps the cross-job cache, and
    pays for no extra lookup to find that out."""
    lookup = AsyncMock()
    monkeypatch.setattr(targets_repo, "get_target_by_revision_job", lookup)
    assert (await _head_kwargs(head))["force_fresh_extract"] is False
    lookup.assert_not_awaited()


# ═════════════════════════════════════════════════════════════════════════
# ... and `_execute_phase` must honour it (DB-free, $0 — the agent boundary
# is faked; the real cache branch runs)
# ═════════════════════════════════════════════════════════════════════════


class _FakePhaseRow:
    def __init__(self):
        self.id = uuid.uuid4()


class _FakeSession:
    async def commit(self):
        return None


@asynccontextmanager
async def _fake_session():
    yield _FakeSession()


def _extract_harness(monkeypatch):
    """Drive the REAL `_execute_phase` extract branch with no DB and no model.

    Returns the call counters: `cache_lookups` (the cross-job cache query),
    `summarize` (a real, billed extraction) and `cache_markers` (the $0
    `agent_usages` row the reuse path writes).
    """
    calls = {"cache_lookups": 0, "summarize": 0, "cache_markers": 0}
    cached = types.SimpleNamespace(
        id=uuid.uuid4(), job_id=uuid.uuid4(),
        output_md="# V1 extract\nthe SOURCE job's extraction")

    monkeypatch.setattr(pipeline, "SessionLocal", _fake_session)

    async def _create_or_reset(session, **kw):
        return _FakePhaseRow()

    async def _noop(*a, **kw):
        return None

    async def _find_latest_extract(session, **kw):
        calls["cache_lookups"] += 1
        return cached

    async def _summarize(**kw):
        calls["summarize"] += 1
        return ("# V2 extract\na freshly re-read extraction", 5, 7)

    async def _marker(**kw):
        calls["cache_markers"] += 1

    monkeypatch.setattr(pipeline.phase_repo, "create_or_reset", _create_or_reset)
    monkeypatch.setattr(pipeline.phase_repo, "set_status", _noop)
    monkeypatch.setattr(pipeline.jobs_repo, "set_status", _noop)
    monkeypatch.setattr(
        pipeline.phase_repo, "find_latest_extract", _find_latest_extract)
    monkeypatch.setattr(pipeline.agent, "record_cached_lesson_extract", _marker)
    monkeypatch.setattr(pipeline.agent, "summarize_lesson", _summarize)
    # Gates A/B and the density check have their own tests
    # (`test_pipeline_extract_dispatch.py`); isolate the cache branch here.
    monkeypatch.setattr(pipeline.agent, "read_whole_book_text", lambda p: "book text")
    monkeypatch.setattr(pipeline.agent, "pdf_page_count", lambda p: 2)
    monkeypatch.setattr(pipeline.agent, "extract_text_is_oversize", lambda t: False)
    monkeypatch.setattr(pipeline.agent, "extract_text_is_too_sparse", lambda t, n: False)
    monkeypatch.setattr(pipeline.agent, "validate_extract_text", lambda t: None)
    monkeypatch.setattr(pipeline.agent, "validate_extract_summary", lambda o: None)

    async def _verify(*, out, **kw):
        return out, 0, 0

    async def _coverage(**kw):
        return []

    monkeypatch.setattr(pipeline, "_verify_and_maybe_regen_extract", _verify)
    monkeypatch.setattr(pipeline, "_check_extract_coverage", _coverage)
    return calls, cached


async def _run_extract(**kw):
    return await pipeline._execute_phase(
        job_id=uuid.uuid4(),
        phase_name="extract",
        phase_order=0,
        subject=_SUBJECT,
        provider="gemini",
        model="gemini-3.5-flash",
        pdf_path=Path("/fake/book.pdf"),
        attach_file=True,
        section={"id": uuid.uuid4(), "title": "L1", "number": "1.1",
                 "page_start": 1, "page_end": 4},
        lesson_context=None,
        prior_outputs={},
        difficulty=None,
        transport="api",
        extract_transport="api",
        extract_provider="gemini",
        extract_model="gemini-3.5-flash-lite",
        **kw,
    )


async def test_execute_phase_skips_the_cross_job_cache_when_forced(monkeypatch):
    """A forced refresh may not reuse ANY prior job's extract — not even one
    that matches the cache key exactly."""
    calls, _cached = _extract_harness(monkeypatch)

    out_md, tin, tout, _hash, _parsed = await _run_extract(force_fresh_extract=True)

    assert calls["cache_lookups"] == 0, (
        "a forced refresh must not even ASK the cross-job cache")
    assert calls["summarize"] == 1, "the extraction was never actually re-run"
    assert calls["cache_markers"] == 0
    assert out_md.startswith("# V2 extract")
    assert (tin, tout) == (5, 7), "a real extraction bills real tokens"


async def test_execute_phase_still_reuses_the_cross_job_cache_by_default(monkeypatch):
    """REGRESSION: the ordinary (and non-refresh) path is byte-for-byte what
    it was — a cache hit is served at zero tokens with its $0 marker row."""
    calls, cached = _extract_harness(monkeypatch)

    out_md, tin, tout, _hash, _parsed = await _run_extract()

    assert calls["cache_lookups"] == 1
    assert calls["summarize"] == 0, "a cache hit must never call the model"
    assert calls["cache_markers"] == 1
    assert out_md == cached.output_md
    assert (tin, tout) == (0, 0)


# ═════════════════════════════════════════════════════════════════════════
# end-to-end over a real revision job
# ═════════════════════════════════════════════════════════════════════════


async def _seed(*, approved: bool = False):
    from datetime import datetime, timezone

    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.homework_job import HomeworkJob
    from app.models.phase_output import PhaseOutput
    from app.models.regeneration_campaign import RegenerationCampaign
    from app.models.regeneration_target import RegenerationTarget
    from app.models.toc_entry import TOCEntry
    from app.schemas.regeneration_contract import ResolvedLaunchContract
    from app.services import regeneration_snapshot

    contract = ResolvedLaunchContract(
        provider="gemini", model="gemini-3.5-flash", transport="api",
        extract_provider="gemini", extract_model="gemini-3.5-flash-lite",
        judge_provider="claude", judge_model="claude-opus-4-7",
        solver_provider="claude", solver_model="claude-opus-4-7",
        session_limit_strategy="pause",
    )
    async with SessionLocal() as session:
        book = Book(
            subject=_SUBJECT, original_filename="regen_pipeline.pdf",
            content_sha256=uuid.uuid4().hex * 2, file_size_bytes=1,
            status="toc_ready", grade="7")
        session.add(book)
        await session.flush()
        toc = TOCEntry(
            book_id=book.id, section_title="L1", section_number="1.1",
            order_index=0, page_start=1, page_end=4)
        session.add(toc)
        await session.flush()
        source = HomeworkJob(
            book_id=book.id, toc_entry_id=toc.id, subject=_SUBJECT,
            status="done", provider="claude", model="claude-sonnet-4-6",
            transport="cli", output_language="uz")
        session.add(source)
        await session.flush()
        for order, name in enumerate(_CANONICAL):
            session.add(PhaseOutput(
                job_id=source.id, phase_name=name, phase_order=order,
                prompt_hash=f"builtin:{name}:v1", model_name="claude-sonnet-4-6",
                provider="claude", output_md=f"# V1 {name}\ncontent",
                status="done", judge_status="ok"))
        campaign = RegenerationCampaign(
            status="approved" if approved else "canary_running",
            selection_spec={}, requested_phases=["flashcards"],
            excluded_phases=[], launch_contract=contract.model_dump(),
            approved_at=datetime.now(timezone.utc) if approved else None)
        session.add(campaign)
        await session.flush()
        target = RegenerationTarget(
            campaign_id=campaign.id, toc_entry_id=toc.id, output_language="uz",
            phase_plan=_PHASE_PLAN, source_job_id=source.id, status="generating")
        session.add(target)
        await session.commit()
        ids = {
            "book_id": book.id, "toc_id": toc.id, "source_id": source.id,
            "campaign_id": campaign.id, "target_id": target.id,
        }
    async with SessionLocal() as session:
        revision = await regeneration_snapshot.create_revision_job(
            session, target_id=target.id, launch_contract=contract)
    ids["revision_id"] = revision.id
    return ids


async def _purge(ids: dict) -> None:
    from sqlalchemy import delete, select

    from app.db import SessionLocal
    from app.models.agent_usage import AgentUsage
    from app.models.book import Book
    from app.models.homework_job import HomeworkJob
    from app.models.phase_output import PhaseOutput
    from app.models.regeneration_campaign import RegenerationCampaign
    from app.models.regeneration_target import RegenerationTarget
    from app.models.toc_entry import TOCEntry

    async with SessionLocal() as session:
        job_ids = list((await session.execute(select(HomeworkJob.id).where(
            HomeworkJob.book_id == ids["book_id"]))).scalars().all())
        await session.execute(delete(AgentUsage).where(
            AgentUsage.homework_job_id.in_(job_ids or [uuid.uuid4()])))
        # COPIES first: `fk_phase_outputs_copied_from_phase_output_id` is
        # RESTRICT and is checked immediately, so a single DELETE covering both
        # the source and the revision rows would fail on row order.
        await session.execute(delete(PhaseOutput).where(
            PhaseOutput.copied_from_phase_output_id.is_not(None),
            PhaseOutput.job_id.in_(job_ids or [uuid.uuid4()])))
        await session.execute(delete(PhaseOutput).where(
            PhaseOutput.job_id.in_(job_ids or [uuid.uuid4()])))
        await session.execute(delete(HomeworkJob).where(
            HomeworkJob.id == ids["revision_id"]))
        await session.execute(delete(RegenerationTarget).where(
            RegenerationTarget.id == ids["target_id"]))
        await session.execute(delete(RegenerationCampaign).where(
            RegenerationCampaign.id == ids["campaign_id"]))
        await session.execute(delete(HomeworkJob).where(
            HomeworkJob.book_id == ids["book_id"]))
        await session.execute(delete(TOCEntry).where(
            TOCEntry.book_id == ids["book_id"]))
        await session.execute(delete(Book).where(Book.id == ids["book_id"]))
        await session.commit()


@pytest.fixture()
def fakes(monkeypatch, tmp_path):
    """Provider / judge / solver boundary only. $0, no network, no subprocess."""
    from app.services import phase_judge, solver
    from app.services.prompts import load_all

    load_all()
    ns = types.SimpleNamespace()
    ns.generated: list[str] = []
    ns.archived: list = []
    ns.judge = phase_judge.JudgeOutcome(
        available=True, passed=True, warnings=[], feedback="")
    ns.solve = solver.SolveOutcome(available=True, agrees=True)

    pdf = tmp_path / "source.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    monkeypatch.setattr(
        pipeline.book_fetch, "ensure_book_pdf", AsyncMock(return_value=pdf))

    async def _run_phase_prompt(*, phase_name, **kw):
        ns.generated.append(phase_name)
        return f"# V2 {phase_name}\nregenerated body", 7, 11

    monkeypatch.setattr(pipeline.agent, "run_phase_prompt", _run_phase_prompt)

    async def _judge(**kw):
        return ns.judge

    monkeypatch.setattr(phase_judge, "judge", _judge)

    async def _solve(**kw):
        return ns.solve

    monkeypatch.setattr(solver, "solve", _solve)

    async def _archive(job_id, **kw):
        ns.archived.append(job_id)

    monkeypatch.setattr(pipeline.notion_archive, "archive_job", _archive)
    return ns


async def _rows(job_id):
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models.phase_output import PhaseOutput

    async with SessionLocal() as session:
        return {
            r.phase_name: r for r in (await session.execute(
                select(PhaseOutput).where(
                    PhaseOutput.job_id == job_id))).scalars()
        }


async def _job(job_id):
    from app.db import SessionLocal
    from app.models.homework_job import HomeworkJob

    async with SessionLocal() as session:
        return await session.get(HomeworkJob, job_id)


async def _target(target_id):
    from app.db import SessionLocal
    from app.models.regeneration_target import RegenerationTarget

    async with SessionLocal() as session:
        return await session.get(RegenerationTarget, target_id)


async def _reconcile(job_id):
    from app.db import SessionLocal
    from app.services import regeneration_job_state

    async with SessionLocal() as session:
        await regeneration_job_state.reconcile_revision_job(session, job_id)
        await session.commit()


@db_only
async def test_only_the_regenerated_phases_run_and_copies_survive_untouched(fakes):
    ids = await _seed()
    try:
        before = await _rows(ids["revision_id"])
        await pipeline.run(ids["revision_id"])
        # Content phases run in dependency WAVES, so completion order varies;
        # the SET (and the count, which catches a double-run) is the contract.
        assert sorted(fakes.generated) == sorted(_PLAN.regenerated_phases), (
            "the pipeline must re-run exactly the campaign's phases — no more "
            "(re-billing copies) and no fewer (shipping a stale phase)")
        assert len(fakes.generated) == len(_PLAN.regenerated_phases)
        after = await _rows(ids["revision_id"])
        assert set(after) == set(_CANONICAL), "the packet must be complete"
        for name in _PLAN.copied_phases:
            assert after[name].id == before[name].id, f"{name} was recreated"
            assert after[name].output_md == f"# V1 {name}\ncontent"
            assert after[name].copied_from_phase_output_id is not None
        regenerated = after["flashcards"]
        assert regenerated.output_md.startswith("# V2 flashcards")
        assert regenerated.copied_from_phase_output_id is None, (
            "a regenerated row is NOT a copy and must not claim provenance")
        assert (await _job(ids["revision_id"])).status == "done"
    finally:
        await _purge(ids)


@db_only
async def test_pipeline_completion_never_calls_the_legacy_archive_for_a_revision(
    fakes,
):
    ids = await _seed()
    try:
        await pipeline.run(ids["revision_id"])
        assert fakes.archived == [], (
            "the legacy archive would overwrite the IMMUTABLE V1 Notion page "
            "with the revision's content")
    finally:
        await _purge(ids)


@db_only
async def test_an_ordinary_job_is_still_archived_by_the_pipeline(fakes):
    """The other half of the same branch: ordinary completion is untouched.

    A fresh ORDINARY job on the same lesson (not the source, whose phase rows a
    revision's copies reference) so the only difference from the test above is
    `revision_of_job_id`.
    """
    from app.db import SessionLocal
    from app.models.homework_job import HomeworkJob
    from app.models.phase_output import PhaseOutput

    ids = await _seed()
    ordinary_id = None
    try:
        async with SessionLocal() as session:
            ordinary = HomeworkJob(
                book_id=ids["book_id"], toc_entry_id=ids["toc_id"],
                subject=_SUBJECT, status="pending", provider="gemini",
                model="gemini-3.5-flash", transport="api", output_language="uz")
            session.add(ordinary)
            await session.flush()
            ordinary_id = ordinary.id
            # Resume shape: every phase but one already done, so the run does
            # not need a real PDF read for `extract`. The completion branch —
            # the thing under test — is reached identically.
            for order, name in enumerate(_CANONICAL):
                if name == "flashcards":
                    continue
                session.add(PhaseOutput(
                    job_id=ordinary.id, phase_name=name, phase_order=order,
                    prompt_hash=f"builtin:{name}:v1",
                    model_name="gemini-3.5-flash", provider="gemini",
                    output_md=f"# prior {name}", status="done"))
            await session.commit()
        await pipeline.run(ordinary_id)
        assert (await _job(ordinary_id)).status == "done"
        assert fakes.archived == [ordinary_id], (
            "an ORDINARY job must still be archived by the pipeline")
    finally:
        await _purge(ids)


@db_only
async def test_a_soft_judge_verdict_still_produces_a_publishable_revision(fakes):
    """`major_shipped` is a WARNING, not a hole: the phase is `done` with real
    content, so the snapshot is complete and the target reaches the canary
    hold."""
    from app.services import phase_judge

    fakes.judge = phase_judge.JudgeOutcome(
        available=True, passed=False, warnings=["MAJOR: thin"], feedback="fix",
        has_major=True)
    ids = await _seed()
    try:
        await pipeline.run(ids["revision_id"])
        rows = await _rows(ids["revision_id"])
        assert rows["flashcards"].status == "done"
        assert rows["flashcards"].judge_status in (
            "major_shipped", "major_regen_failed", "ok")
        await _reconcile(ids["revision_id"])
        target = await _target(ids["target_id"])
        assert target.status == "awaiting_canary_approval", (
            "a soft judge verdict must not block publication — it is surfaced, "
            "not a hole in the packet")
    finally:
        await _purge(ids)


@db_only
async def test_an_approved_campaign_releases_publication_on_completion(fakes):
    ids = await _seed(approved=True)
    try:
        await pipeline.run(ids["revision_id"])
        await _reconcile(ids["revision_id"])
        target = await _target(ids["target_id"])
        assert target.status == "publication_pending"
        assert target.publication_released_at is not None
    finally:
        await _purge(ids)


@db_only
async def test_a_hard_phase_failure_fails_the_job_and_the_target(fakes, monkeypatch):
    async def _boom(**kw):
        raise RuntimeError("provider is on fire")

    monkeypatch.setattr(pipeline.agent, "run_phase_prompt", _boom)
    monkeypatch.setattr(pipeline, "_failover_chain", lambda p: [p])
    ids = await _seed()
    try:
        await pipeline.run(ids["revision_id"])
        assert (await _job(ids["revision_id"])).status == "failed"
        assert fakes.archived == []
        await _reconcile(ids["revision_id"])
        assert (await _target(ids["target_id"])).status == "generation_failed"
    finally:
        await _purge(ids)


@db_only
async def test_a_solver_blocked_phase_remains_a_HARD_job_failure(fakes, monkeypatch):
    """`solver_status='mismatch_blocked'` is unchanged by regeneration: the
    phase row is `failed`, the job fails, and the target must NOT publish a
    packet with a knowingly wrong answer key."""
    from app.config import settings
    from app.services import solver

    monkeypatch.setattr(settings, "solver_enabled", True)
    monkeypatch.setattr(settings, "max_solve_regens", 0)
    fakes.solve = solver.SolveOutcome(
        available=True, agrees=False, warnings=["HIGH: key is wrong"],
        feedback="redo", has_mismatch=True)
    ids = await _seed()
    try:
        await pipeline.run(ids["revision_id"])
        rows = await _rows(ids["revision_id"])
        blocked = [
            r for r in rows.values() if r.solver_status == "mismatch_blocked"]
        assert blocked, "the solver block never fired"
        assert all(r.status == "failed" for r in blocked)
        assert (await _job(ids["revision_id"])).status == "failed"
        await _reconcile(ids["revision_id"])
        assert (await _target(ids["target_id"])).status == "generation_failed"
        assert fakes.archived == []
    finally:
        await _purge(ids)
