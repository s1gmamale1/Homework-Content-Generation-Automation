"""End-to-end acceptance for the versioned regeneration workflow — happy path.

This is the implementation gate for the feature (plan Task 11). It drives the
REAL operator sequence over a real PostgreSQL:

    estimate → create → launch canary → run the revision through the real
    pipeline → approve once → publish the requested V3 → a later campaign
    publishing V4 → an independent RU V3 for the same lesson

Only two boundaries are faked, and both are faked at the outermost edge:

* **the provider.** ``agent.run_phase_prompt``/``phase_judge.judge``/
  ``solver.solve`` are replaced by deterministic stand-ins that also write the
  ``agent_usages`` row the real call would, so the campaign's actual-cost query
  is asserted against real accounting rows rather than a stub number. Every
  other spawn entry point on ``agent`` is a TRIPWIRE that fails the test.
* **Notion.** ``FakeNotion`` — the same in-memory Notion
  ``test_notion_versioned_homework.py`` and the publisher unit tests use, so a
  marker/adoption regression cannot pass here while failing there. It is
  injected as the publisher's ``client_factory``; ``NotionClientWrapper`` itself
  is a tripwire, so a real client can never be constructed.

Everything else is production code: the campaign service, the phase planner,
``create_revision_job``, the whole ``pipeline.run`` (real resume predicate, real
phase rows, real completion branch), ``regeneration_job_state``, the durable
publisher, the version allocator, the versioned Notion writer and the report
assembly.

The failure/cancellation half of the gate lives in
``test_regeneration_failure_e2e.py``.
"""
from __future__ import annotations

import asyncio
import copy
import os
import types
import uuid
from datetime import datetime, timezone

import pytest

from app.services.flows import flow_for
from app.services.regeneration_planner import build_phase_plan
from tests.services.test_notion_versioned_homework import FakeNotion

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)

SUBJECT = "math-algebra"
GRADE = "5"
CANONICAL = ("extract", *flow_for(SUBJECT))
# 2 regenerated (`boss-arena` + its dependent `reflection`), 10 copied — a real
# mixture, which is the point of item 3. A single-phase selection would not
# prove the dependency cascade reaches the snapshot.
SELECTED_PHASES = ("boss-arena",)
PLAN = build_phase_plan(subject=SUBJECT, selected_phases=list(SELECTED_PHASES))
MARKER = "pytest-regen-e2e"
SUBJECT_PAGE_UZ = "subject-page-uz-math-5"
SUBJECT_PAGE_RU = "subject-page-ru-math-5"
BASE = "/api/v1/regeneration"
_TEST_CAMPAIGN_SERVICE_FACTORY = None

_CONTRACT = {
    "provider": "gemini",
    "model": "gemini-3.6-flash",
    "transport": "api",
    "extract_provider": "gemini",
    "extract_model": "gemini-3.5-flash-lite",
    "extract_transport": "api",
    "judge_provider": "gemini",
    "judge_model": "gemini-3.5-flash",
    "judge_transport": "api",
    "solver_provider": "gemini",
    "solver_model": "gemini-3.1-pro-preview",
    "solver_transport": "api",
    "session_limit_strategy": "pause",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ═══════════════════════════════ the world ═══════════════════════════════


async def _seed_world(*, languages=("uz",)):
    """One book, one lesson, and a complete done V1 homework per language.

    The V1 jobs carry a large ``agent_usages`` row each: every "the campaign's
    money is its own" assertion below is only worth something because there is
    real historical spend sitting next to it that must not be counted.
    """
    from app.db import SessionLocal
    from app.models.agent_usage import AgentUsage
    from app.models.book import Book
    from app.models.homework_job import HomeworkJob
    from app.models.phase_output import PhaseOutput
    from app.models.toc_entry import TOCEntry

    async with SessionLocal() as session:
        book = Book(
            subject=SUBJECT, original_filename="regen_e2e_algebra5.pdf",
            content_sha256=uuid.uuid4().hex * 2, file_size_bytes=4096,
            status="toc_ready", grade=GRADE,
        )
        session.add(book)
        await session.flush()
        toc = TOCEntry(
            book_id=book.id, section_title="Kvadrat tenglamalar",
            section_number="1", chapter_title="Algebra", order_index=0,
            page_start=7,
        )
        session.add(toc)
        await session.flush()

        v1_ids: dict[str, uuid.UUID] = {}
        for language in languages:
            v1 = HomeworkJob(
                book_id=book.id, toc_entry_id=toc.id, subject=SUBJECT,
                status="done", provider="gemini", model="gemini-3.6-flash",
                transport="api", output_language=language, kind="homework",
            )
            session.add(v1)
            await session.flush()
            first_row = None
            for order, name in enumerate(CANONICAL):
                row = PhaseOutput(
                    job_id=v1.id, phase_name=name, phase_order=order,
                    prompt_hash=f"builtin:{name}:v1", provider="gemini",
                    model_name="gemini-3.6-flash",
                    output_md=f"# V1 {language} {name}\n\noriginal body",
                    status="done", judge_status="ok",
                )
                session.add(row)
                await session.flush()
                first_row = first_row or row
            # V1's own spend — never this campaign's.
            session.add(AgentUsage(
                homework_job_id=v1.id, phase_output_id=first_row.id,
                provider="gemini", model_name="gemini-3.6-flash",
                operation="phase.run", auth_mode="api",
                prompt_tokens=900_000, output_tokens=90_000,
                total_tokens=990_000, success=True,
            ))
            v1_ids[language] = v1.id
        await session.commit()
        return types.SimpleNamespace(
            book_id=book.id, toc_entry_id=toc.id, v1=v1_ids,
        )


async def _purge_world(world) -> None:
    """Every row this module created, child-first.

    A leaked NON-TERMINAL target holds ``uq_regeneration_targets_active_lineage``
    for its lesson forever and its revision job is read by any suite query that
    is not lesson-scoped, so this is correctness for the rest of the suite, not
    tidiness. The order below is the FK graph: agent_usages (SET NULL, so it
    must go by id) → copied phase rows (``copied_from_phase_output_id`` is
    RESTRICT) → remaining phase rows → revision jobs (``revision_of_job_id`` and
    ``regeneration_target_id`` are both RESTRICT) → targets → campaigns →
    source jobs → TOC → book.
    """
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
        job_ids = list((await session.execute(
            select(HomeworkJob.id).where(HomeworkJob.book_id == world.book_id)
        )).scalars().all()) or [uuid.uuid4()]
        # Scoped to the BOOK's lessons, not just the first one: a campaign may
        # legitimately span several TOC entries in the same book, and a target
        # left behind would block the campaign delete below on
        # `fk_regeneration_targets_campaign_id`.
        target_ids = list((await session.execute(
            select(RegenerationTarget.id).where(
                RegenerationTarget.toc_entry_id.in_(
                    select(TOCEntry.id).where(TOCEntry.book_id == world.book_id)
                )
            )
        )).scalars().all())
        # The campaigns that own THIS world's targets — never every campaign
        # carrying the module marker: a campaign from another test's book still
        # has its own live targets, and deleting it would fail on
        # `fk_regeneration_targets_campaign_id`.
        campaign_ids = list((await session.execute(
            select(RegenerationTarget.campaign_id)
            .where(RegenerationTarget.id.in_(target_ids or [uuid.uuid4()]))
            .distinct()
        )).scalars().all())

        await session.execute(
            delete(AgentUsage).where(AgentUsage.homework_job_id.in_(job_ids)))
        await session.execute(
            delete(PhaseOutput)
            .where(PhaseOutput.job_id.in_(job_ids))
            .where(PhaseOutput.copied_from_phase_output_id.is_not(None)))
        await session.execute(
            delete(PhaseOutput).where(PhaseOutput.job_id.in_(job_ids)))
        await session.execute(
            delete(HomeworkJob)
            .where(HomeworkJob.book_id == world.book_id)
            .where(HomeworkJob.revision_of_job_id.is_not(None)))
        if target_ids:
            await session.execute(
                delete(RegenerationTarget)
                .where(RegenerationTarget.id.in_(target_ids)))
        if campaign_ids:
            await session.execute(
                delete(RegenerationCampaign)
                .where(RegenerationCampaign.id.in_(campaign_ids)))
        # A draft that lost the lineage race has no target to be found by.
        await session.execute(
            delete(RegenerationCampaign)
            .where(RegenerationCampaign.app_git_revision == MARKER)
            .where(~RegenerationCampaign.targets.any()))
        await session.execute(
            delete(HomeworkJob).where(HomeworkJob.book_id == world.book_id))
        await session.execute(
            delete(TOCEntry).where(TOCEntry.book_id == world.book_id))
        await session.execute(delete(Book).where(Book.id == world.book_id))
        await session.commit()


def _seed_notion_v1(notion: FakeNotion, *, subject_page: str, lesson_title: str):
    """The Notion tree a previously archived lesson already has:
    subject → `Generated Homeworks` → `<lesson>` → `Homework`.

    That `Homework` page IS logical V1. It is the thing this whole feature
    exists to leave alone, so it is created with real content and snapshotted.
    """
    from app.services import notion_archive

    notion.titles[subject_page] = "Subject"
    notion.blocks[subject_page] = []
    container = notion.add_page(subject_page, notion_archive.CONTAINER_TITLE)
    lesson = notion.add_page(container, lesson_title)
    homework_v1 = notion.add_page(lesson, "Homework", [
        {"type": "heading_2", "heading_2": {
            "rich_text": [{"type": "text", "text": {"content": "V1 flashcards"}}]}},
        {"type": "paragraph", "paragraph": {
            "rich_text": [{"type": "text", "text": {"content": "original V1 body"}}]}},
    ])
    return types.SimpleNamespace(
        container=container, lesson=lesson, homework_v1=homework_v1
    )


@pytest.fixture()
def fakes(monkeypatch, tmp_path):
    """Provider + Notion boundary. $0, no network, no subprocess, no client."""
    from app.config import settings
    from app.models.base import _utcnow
    from app.api.v1 import regeneration as regen_api
    from app.services import agent as agent_mod
    from app.services import (
        notion_archive,
        phase_judge,
        pipeline,
        regeneration_destination,
        regeneration_executability,
        solver,
    )
    from app.services.regeneration_campaign import RegenerationCampaignService
    from app.services.notion import client as notion_client_mod
    from app.services.prompts import load_all

    load_all()
    ns = types.SimpleNamespace()
    ns.notion = FakeNotion()
    ns.generated: list[str] = []
    ns.judged: list[str] = []
    ns.legacy_archived: list = []
    ns.judge_outcome = phase_judge.JudgeOutcome(
        available=True, passed=True, warnings=[], feedback="")
    # Set by a failure test that needs the verdict to CHANGE between calls (the
    # unavailable→retry path); `judge_outcome` is the fixed-verdict shorthand.
    ns.judge_outcome_factory = None
    ns.solve_outcome = solver.SolveOutcome(available=True, agrees=True)
    # Per generated phase; the numbers are arbitrary but FIXED, so the money
    # assertions are exact rather than "greater than zero".
    ns.prompt_tokens = 1_000
    ns.output_tokens = 200

    pdf = tmp_path / "source.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    monkeypatch.setattr(
        pipeline.book_fetch, "ensure_book_pdf",
        lambda *a, **kw: _resolved(pdf),
    )

    async def _run_phase_prompt(*, phase_name, homework_job_id=None, **kw):
        """The provider boundary — and the accounting row it owns.

        Writing the ``agent_usages`` row here is what makes item 13 a real
        assertion: the campaign's actual cost is summed from rows in the
        database, next to V1's much larger historical row, rather than from a
        number this fake returned.
        """
        ns.generated.append(phase_name)
        if homework_job_id is not None:
            await agent_mod._record_usage(
                operation="phase.run", provider="gemini",
                model_name="gemini-3.6-flash",
                usage={
                    "prompt_tokens": ns.prompt_tokens,
                    "output_tokens": ns.output_tokens,
                    "cached_tokens": 0,
                    "total_tokens": ns.prompt_tokens + ns.output_tokens,
                    "raw": {},
                },
                duration_s=0.01, started_at=_utcnow(), success=True,
                homework_job_id=homework_job_id, auth_mode="api",
            )
        return f"# V-next {phase_name}\n\nregenerated body", 7, 11

    monkeypatch.setattr(pipeline.agent, "run_phase_prompt", _run_phase_prompt)
    # Exposed so a failure test can wrap it (and restore it) without rebuilding
    # the accounting row this fake owns.
    ns.run_phase_prompt = _run_phase_prompt

    async def _judge(**kw):
        ns.judged.append(kw.get("phase_name", "?"))
        if ns.judge_outcome_factory is not None:
            return ns.judge_outcome_factory(**kw)
        return ns.judge_outcome

    monkeypatch.setattr(phase_judge, "judge", _judge)

    async def _solve(**kw):
        return ns.solve_outcome

    monkeypatch.setattr(solver, "solve", _solve)

    # The REAL function, kept before the module attribute is replaced: patching
    # `notion_archive.archive_job` is the only way to observe the pipeline's
    # completion branch, and it would otherwise also hide the intrinsic
    # revision guard from the test that exercises it directly.
    ns.real_archive_job = notion_archive.archive_job

    async def _archive(job_id, **kw):
        ns.legacy_archived.append(job_id)

    monkeypatch.setattr(pipeline.notion_archive, "archive_job", _archive)

    # ── tripwires: nothing below may ever be reached ──────────────────────
    def _forbidden(name):
        def _boom(*_a, **_kw):
            raise AssertionError(f"the regeneration workflow must never call {name}")
        return _boom

    monkeypatch.setattr(agent_mod, "run_phase", _forbidden("agent.run_phase"))
    monkeypatch.setattr(agent_mod, "_spawn", _forbidden("agent._spawn"))
    monkeypatch.setattr(agent_mod, "_spawn_once", _forbidden("agent._spawn_once"))
    monkeypatch.setattr(
        notion_client_mod.NotionClientWrapper, "__init__",
        _forbidden("NotionClientWrapper()"))

    monkeypatch.setattr(settings, "regeneration_enabled", True)
    monkeypatch.setattr(settings, "regeneration_publisher_enabled", True)
    # A head CONFIGURED to publish: `run_once` refuses to claim anything without
    # a usable Notion destination, and these tests drive it. Nothing here reaches
    # real Notion — the pipeline's legacy `archive_job` is a recorder (above),
    # the publisher's client is `FakeNotion`, and `NotionClientWrapper()` itself
    # is a tripwire below, which the readiness check must not trip.
    monkeypatch.setattr(settings, "notion_enabled", True)
    monkeypatch.setattr(settings, "notion_api_key", "secret_pytest_not_a_real_token")
    monkeypatch.setattr(settings, "notion_subject_pages", {
        f"{SUBJECT}|{GRADE}": SUBJECT_PAGE_UZ,
        f"ru:{SUBJECT}|{GRADE}": SUBJECT_PAGE_RU,
    })
    # A one-shot, un-staggered wave: the offsets only delay a WORKER claim and
    # these tests drive `pipeline.run` directly, but a 0 offset keeps
    # `scheduled_at` readable in a failure dump.
    monkeypatch.setattr(settings, "regeneration_launch_wave_size", 0)
    monkeypatch.setattr(settings, "regeneration_launch_wave_interval_seconds", 0)
    monkeypatch.setattr(settings, "solver_enabled", False)

    async def _resolve_destinations(*, sources, requested_version, overrides):
        return await regeneration_destination.resolve_destinations(
            sources=sources,
            requested_version=requested_version,
            overrides=overrides,
            client_factory=lambda: ns.notion,
        )

    async def _workers(_session, contract):
        required = tuple(sorted({
            contract.provider,
            contract.extract_provider,
            contract.judge_provider,
            contract.solver_provider,
        }))
        return regeneration_executability.WorkerExecutability(
            ok=True,
            workers_online=1,
            compatible_worker_ids=("pytest-worker",),
            required_api_providers=required,
            fleet_api_paused=False,
            reason=None,
        )

    def _campaign_service():
        return RegenerationCampaignService(
            destination_resolver=_resolve_destinations,
            worker_checker=_workers,
        )

    global _TEST_CAMPAIGN_SERVICE_FACTORY
    _TEST_CAMPAIGN_SERVICE_FACTORY = _campaign_service
    monkeypatch.setattr(regen_api, "_service", _campaign_service)
    monkeypatch.setattr(regen_api, "_resolve_destinations", _resolve_destinations)
    monkeypatch.setattr(regen_api, "_check_active_workers", _workers)
    ns.notion_archive = notion_archive
    try:
        yield ns
    finally:
        _TEST_CAMPAIGN_SERVICE_FACTORY = None


def _resolved(value):
    fut: asyncio.Future = asyncio.get_event_loop().create_future()
    fut.set_result(value)
    return fut


@pytest.fixture()
async def world(fakes):
    """A book + lesson + UZ V1 (+ its Notion `Homework` page), purged after."""
    seeded = await _seed_world(languages=("uz",))
    seeded.notion = _seed_notion_v1(
        fakes.notion, subject_page=SUBJECT_PAGE_UZ,
        lesson_title="Kvadrat tenglamalar",
    )
    await _stamp_lesson_page(seeded.toc_entry_id, seeded.notion.lesson)
    seeded.v1_snapshot = copy.deepcopy(fakes.notion.blocks[seeded.notion.homework_v1])
    try:
        yield seeded
    finally:
        await _purge_world(seeded)


async def _stamp_lesson_page(toc_entry_id, lesson_page_id: str) -> None:
    """V1 was archived, so the TOC row already points at its Lesson Topic."""
    from app.db import SessionLocal
    from app.repositories import toc_entries as toc_repo

    async with SessionLocal() as session:
        await toc_repo.set_notion_lesson_page_id(
            session, toc_entry_id, lesson_page_id)
        await session.commit()


# ═════════════════════════════ drivers ═══════════════════════════════════


def _service():
    assert _TEST_CAMPAIGN_SERVICE_FACTORY is not None
    return _TEST_CAMPAIGN_SERVICE_FACTORY()


def _publisher(fakes):
    from app.services.regeneration_publisher import RegenerationPublisher

    return RegenerationPublisher(
        client_factory=lambda: fakes.notion,
        lease_seconds=120,
        max_attempts=3,
        backoff_base_seconds=1,
        backoff_max_seconds=2,
    )


async def _api(method: str, path: str, json=None):
    """One authenticated request over the real ASGI app and a real session.

    ``ASGITransport``, not ``TestClient``: the client's own portal loop would
    hand the engine's asyncpg connections to a second event loop.
    """
    from httpx import ASGITransport, AsyncClient

    from app.api.v1 import regeneration as regen_api
    from app.auth import get_current_user
    from app.db import SessionLocal, get_session
    from main import app

    async def _real_session():
        async with SessionLocal() as session:
            yield session

    app.dependency_overrides[get_current_user] = lambda: {"user_id": "pytest"}
    app.dependency_overrides[get_session] = _real_session
    regen_api.reset_rollup_debounce()
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as http:
            return await http.request(method, f"{BASE}{path}", json=json)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)
        regen_api.reset_rollup_debounce()


def _draft(
    world,
    *,
    languages=("uz",),
    canary_size=1,
    phases=SELECTED_PHASES,
    publication_version=3,
):
    return {
        "selection": {
            "toc_entry_ids": [str(world.toc_entry_id)],
            "output_languages": list(languages),
        },
        "contract": dict(_CONTRACT),
        "selected_phases": list(phases),
        "canary_size": canary_size,
        "publication_version": publication_version,
    }


async def _reviewed_create_body(body: dict) -> dict:
    overrides = body.get("destination_overrides", [])
    review = await _api("post", "/destinations", {
        "selection": body["selection"],
        "publication_version": body["publication_version"],
        "destination_overrides": overrides,
    })
    assert review.status_code == 200, review.text
    reviewed = review.json()
    assert reviewed["ok"] is True, reviewed
    assert reviewed["checked_target_count"] == reviewed["target_count"]
    return {
        **body,
        "destination_overrides": overrides,
        "approved_destination_digest": reviewed["destination_digest"],
    }


async def _create_campaign(world, **kw):
    body = _draft(world, **kw)
    body.update({"actor": "pytest", "app_git_revision": MARKER})
    body = await _reviewed_create_body(body)
    response = await _api("post", "/campaigns", body)
    assert response.status_code == 201, response.text
    return uuid.UUID(response.json()["id"])


async def _targets(campaign_id):
    from app.db import SessionLocal
    from app.repositories import regeneration_targets as targets_repo

    async with SessionLocal() as session:
        return await targets_repo.list_for_campaign(session, campaign_id)


async def _revision_job_id(target_id):
    from app.db import SessionLocal
    from app.repositories import regeneration_targets as targets_repo

    async with SessionLocal() as session:
        job = await targets_repo.revision_job_for_target(
            session, target_id=target_id)
        return None if job is None else job.id


async def _run_revision(target_id) -> uuid.UUID:
    """Run one target's revision job through the REAL pipeline."""
    from app.services import pipeline

    job_id = await _revision_job_id(target_id)
    assert job_id is not None, f"target {target_id} has no revision job to run"
    await pipeline.run(job_id)
    await _reconcile(job_id)
    return job_id


async def _reconcile(job_id) -> None:
    from app.db import SessionLocal
    from app.services import regeneration_job_state

    async with SessionLocal() as session:
        await regeneration_job_state.reconcile_revision_job(session, job_id)
        await session.commit()


async def _drain_publisher(fakes, *, limit: int = 12) -> int:
    """Sweep until nothing is claimable. Bounded so a wedged target fails the
    test instead of hanging it."""
    passes = 0
    publisher = _publisher(fakes)
    while passes < limit and await publisher.run_once():
        passes += 1
    assert passes < limit, "the publisher never ran out of claimable targets"
    return passes


async def _phase_rows(job_id):
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models.phase_output import PhaseOutput

    async with SessionLocal() as session:
        rows = (await session.execute(
            select(PhaseOutput).where(PhaseOutput.job_id == job_id)
        )).scalars().all()
        return {row.phase_name: row for row in rows}


async def _target(target_id):
    from app.db import SessionLocal
    from app.models.regeneration_target import RegenerationTarget

    async with SessionLocal() as session:
        return await session.get(RegenerationTarget, target_id)


async def _campaign(campaign_id):
    from app.db import SessionLocal
    from app.models.regeneration_campaign import RegenerationCampaign

    async with SessionLocal() as session:
        return await session.get(RegenerationCampaign, campaign_id)


async def _rolled_up(campaign_id):
    """Campaign status after convergence.

    A worker only reconciles its own JOB onto its TARGET; the campaign's status
    is derived from its targets by ``roll_up``, which every action and every
    report page load calls. Doing it explicitly here keeps ``_run_revision``
    faithful to what a worker actually does.
    """
    return (await _service().roll_up(campaign_id)).status


async def _report(campaign_id):
    from app.api.v1 import regeneration as regen_api
    from app.db import SessionLocal

    async with SessionLocal() as session:
        return await regen_api._campaign_detail(session, campaign_id, now=_now())


async def _publish_one_campaign(
    world,
    fakes,
    *,
    languages=("uz",),
    publication_version=3,
):
    """create → canary → run → approve → publish, for a one-lesson selection."""
    campaign_id = await _create_campaign(
        world,
        languages=languages,
        publication_version=publication_version,
    )
    await _service().launch_canary(campaign_id)
    for target in await _targets(campaign_id):
        await _run_revision(target.id)
    await _service().approve_canary(campaign_id, actor="pytest")
    await _drain_publisher(fakes)
    return campaign_id


# ═════════════ 1–2. an existing V1, and a free draft ═════════════════════


async def test_estimate_is_db_only_and_create_only_revalidates_notion(world, fakes):
    """Item 2. Pricing is DB-only; review/create make read-only Notion checks.

    The tripwires in `fakes` cover every model spawn and every real Notion
    client construction. Destination review and create revalidation may read
    the fake tree, but must not mutate it or create a revision job.
    """
    before = copy.deepcopy(fakes.notion.blocks)

    estimate = await _api("post", "/estimate", _draft(world))
    assert estimate.status_code == 200, estimate.text
    priced = estimate.json()
    assert priced["target_count"] == 1
    assert priced["preflight"]["ok"] is True
    assert priced["estimate"]["low_usd"] >= 0
    plan = priced["phase_plans"][0]
    assert sorted(plan["regenerated_phases"]) == sorted(PLAN.regenerated_phases)
    assert priced["sources"][0]["next_expected_version"] == 2, (
        "logical V1 has no version row, so the first allocated version is 2")
    assert fakes.notion.calls == [], "estimate must remain DB-only"

    campaign_id = await _create_campaign(world)

    assert fakes.generated == [], "a draft must not generate anything"
    assert fakes.notion.count("get_child_pages") > 0
    assert not any(call[0] in {
        "create_page", "append_block_children", "delete_block",
        "clear_content_blocks", "upload_bytes",
    } for call in fakes.notion.calls), "review/create must not write Notion"
    assert fakes.notion.blocks == before
    campaign = await _campaign(campaign_id)
    assert campaign.status == "draft"
    targets = await _targets(campaign_id)
    assert [t.status for t in targets] == ["planned"]
    assert await _revision_job_id(targets[0].id) is None, (
        "a draft creates no revision job — a worker could claim it before the "
        "human gate")


# ═════════════ 3, 5. a complete canary snapshot, unpublished ═════════════


async def test_the_canary_is_a_complete_snapshot_of_copies_and_regenerations(
    world, fakes
):
    """Items 3 and 5. Exactly the campaign's phases re-run; everything else is
    copied verbatim with provenance; nothing reaches Notion before approval."""
    campaign_id = await _create_campaign(world)
    await _service().launch_canary(campaign_id)
    target = (await _targets(campaign_id))[0]
    assert target.is_canary is True

    job_id = await _run_revision(target.id)

    assert sorted(fakes.generated) == sorted(PLAN.regenerated_phases), (
        "the pipeline must re-run exactly the campaign's phases — no more "
        "(re-billing a copy) and no fewer (shipping a stale phase)")
    assert len(fakes.generated) == len(PLAN.regenerated_phases)

    rows = await _phase_rows(job_id)
    assert set(rows) == set(CANONICAL), "the revision packet must be complete"
    for name in PLAN.copied_phases:
        assert rows[name].copied_from_phase_output_id is not None, (
            f"{name} must carry a link to the source row it was copied from")
        assert rows[name].output_md == f"# V1 uz {name}\n\noriginal body"
    for name in PLAN.regenerated_phases:
        assert rows[name].copied_from_phase_output_id is None, (
            f"{name} is regenerated content and must not claim copy provenance")
        assert rows[name].output_md.startswith(f"# V-next {name}")

    assert (await _target(target.id)).status == "awaiting_canary_approval"
    assert await _rolled_up(campaign_id) == "awaiting_canary_approval"
    assert fakes.legacy_archived == [], (
        "the legacy archive writes the lesson's EXISTING `Homework` page — "
        "which IS V1, the immutable thing a revision exists to preserve")
    assert not any(call[0] in {
        "create_page", "append_block_children", "delete_block",
        "clear_content_blocks", "upload_bytes",
    } for call in fakes.notion.calls), (
        "the canary may revalidate the reviewed destination but must not write")


async def test_publication_is_impossible_before_the_campaign_is_approved(
    world, fakes
):
    """Item 5, the other half: it is not merely that nobody asks the publisher
    to run — a publisher that DOES run finds nothing claimable."""
    campaign_id = await _create_campaign(world)
    await _service().launch_canary(campaign_id)
    target = (await _targets(campaign_id))[0]
    await _run_revision(target.id)

    assert await _publisher(fakes).run_once() is False
    assert not any(call[0] in {
        "create_page", "append_block_children", "delete_block",
        "clear_content_blocks", "upload_bytes",
    } for call in fakes.notion.calls)
    refreshed = await _target(target.id)
    assert refreshed.publication_version is None
    assert refreshed.publication_released_at is None


# ═════════════ 6. approve once → publish, release the rest once ══════════


async def test_approval_publishes_the_canary_and_releases_the_rest_exactly_once(
    world, fakes
):
    """Item 6. Two lessons: one canary, one bulk.

    Approval is the ONE gate: it publishes the reviewed canary, creates the
    remaining revision, and a repeated approval creates nothing twice.
    """
    from app.db import SessionLocal
    from app.models.toc_entry import TOCEntry

    async with SessionLocal() as session:
        second = TOCEntry(
            book_id=world.book_id, section_title="Ikkinchi mavzu",
            section_number="2", chapter_title="Algebra", order_index=1,
            page_start=11, notion_lesson_page_id=fakes.notion.add_page(
                world.notion.container, "Ikkinchi mavzu"),
        )
        session.add(second)
        await session.flush()
        second_id = second.id
        from app.models.homework_job import HomeworkJob
        from app.models.phase_output import PhaseOutput

        v1 = HomeworkJob(
            book_id=world.book_id, toc_entry_id=second_id, subject=SUBJECT,
            status="done", provider="gemini", model="gemini-3.6-flash",
            transport="api", output_language="uz", kind="homework",
        )
        session.add(v1)
        await session.flush()
        for order, name in enumerate(CANONICAL):
            session.add(PhaseOutput(
                job_id=v1.id, phase_name=name, phase_order=order,
                prompt_hash=f"builtin:{name}:v1", provider="gemini",
                model_name="gemini-3.6-flash",
                output_md=f"# V1 uz {name}\n\noriginal body", status="done"))
        await session.commit()

    body = _draft(world, canary_size=1)
    body["selection"]["toc_entry_ids"] = [
        str(world.toc_entry_id), str(second_id)]
    body.update({"actor": "pytest", "app_git_revision": MARKER})
    body = await _reviewed_create_body(body)
    created = await _api("post", "/campaigns", body)
    assert created.status_code == 201, created.text
    campaign_id = uuid.UUID(created.json()["id"])

    await _service().launch_canary(campaign_id)
    targets = {t.is_canary: t for t in await _targets(campaign_id)}
    canary, bulk = targets[True], targets[False]
    assert await _revision_job_id(bulk.id) is None, (
        "a bulk target must not receive a job before the gate — an ordinary "
        "worker would claim and pay for it")

    await _run_revision(canary.id)
    assert (await _target(canary.id)).status == "awaiting_canary_approval"

    await _service().approve_canary(campaign_id, actor="pytest")
    bulk_job = await _revision_job_id(bulk.id)
    assert bulk_job is not None, "approval must release the remaining targets"
    assert (await _target(canary.id)).status == "publication_pending"

    # Idempotent: the second approval must create no second job.
    await _service().approve_canary(campaign_id, actor="pytest")
    assert await _revision_job_id(bulk.id) == bulk_job

    await _drain_publisher(fakes)
    assert (await _target(canary.id)).status == "published"

    await _run_revision(bulk.id)
    await _drain_publisher(fakes)
    published = await _target(bulk.id)
    assert published.status == "published", (
        "after approval every later success publishes automatically — there is "
        "no per-lesson publication approval")
    assert published.publication_version == 3
    assert await _rolled_up(campaign_id) == "completed"


# ═════════════ 7–9. V3 → V4, an independent RU V3, and V1 intact ═════════


async def test_a_later_campaign_is_sourced_from_v3_and_publishes_v4(world, fakes):
    """Item 7. The second campaign's source is the PUBLISHED revision job, not
    V1, and the version it consumes is the next one."""
    first = await _publish_one_campaign(world, fakes)
    first_target = (await _targets(first))[0]
    first_job = await _revision_job_id(first_target.id)
    assert (await _target(first_target.id)).publication_version == 3

    eligible = await _api("get", "/eligible")
    assert eligible.status_code == 200, eligible.text
    source = next(
        row for row in eligible.json()["sources"]
        if row["toc_entry_id"] == str(world.toc_entry_id)
        and row["output_language"] == "uz"
    )
    assert source["source_job_id"] == str(first_job), (
        "V4 must be regenerated from the published V3 revision, not from V1")
    assert source["source_publication_version"] == 3
    assert source["next_expected_version"] == 4
    assert source["source_is_revision"] is True

    second = await _publish_one_campaign(
        world, fakes, publication_version=4
    )
    second_target = (await _targets(second))[0]
    assert second_target.source_job_id == first_job
    published = await _target(second_target.id)
    assert published.publication_version == 4
    assert published.notion_page_id != (
        await _target(first_target.id)).notion_page_id

    titles = fakes.notion.child_titles(world.notion.lesson)
    assert titles == ["Homework", "Homework V3", "Homework V4"], (
        "each version is a NEW sibling page; nothing is renamed or replaced")


async def test_uz_and_ru_are_independent_lineages_with_their_own_version_2(fakes):
    """Item 8. A lineage is scoped by ``(lesson, output_language)``: one campaign
    holds a UZ and an RU target for the SAME TOC row, each reserves its own
    requested version 3, and each revision carries its own source and language.

    The publisher's page-identity half of item 8 is asserted separately, in
    ``test_uz_and_ru_publish_to_separate_notion_pages`` below.
    """
    from app.db import SessionLocal
    from app.models.homework_job import HomeworkJob

    seeded = await _seed_world(languages=("uz", "ru"))
    seeded.notion = _seed_notion_v1(
        fakes.notion, subject_page=SUBJECT_PAGE_UZ,
        lesson_title="Kvadrat tenglamalar")
    _seed_notion_v1(
        fakes.notion, subject_page=SUBJECT_PAGE_RU,
        lesson_title="Kvadrat tenglamalar")
    try:
        campaign_id = await _create_campaign(
            seeded, languages=("uz", "ru"), canary_size=2)
        targets = {t.output_language: t for t in await _targets(campaign_id)}
        assert set(targets) == {"uz", "ru"}, (
            "uq_regeneration_targets_active_lineage is scoped by language, so "
            "both may be live at once for one lesson")

        await _service().launch_canary(campaign_id)
        for target in targets.values():
            await _run_revision(target.id)
        await _service().approve_canary(campaign_id, actor="pytest")
        await _drain_publisher(fakes)

        for language, target in targets.items():
            refreshed = await _target(target.id)
            assert refreshed.publication_version == 3, (
                f"{language} must reserve its OWN requested version 3 — "
                "uq_regeneration_targets_publication_version keys on "
                f"(lesson, language, version) (got {refreshed.publication_version})")

        async with SessionLocal() as session:
            for language, target in targets.items():
                job = await session.get(
                    HomeworkJob, await _revision_job_id(target.id))
                assert job.output_language == language, (
                    "a revision copies its language from its immediate source; "
                    "reading a campaign-wide one would publish an RU revision "
                    "of a UZ lesson")
                assert job.revision_of_job_id == seeded.v1[language]
    finally:
        await _purge_world(seeded)


async def test_uz_and_ru_publish_to_separate_notion_pages(fakes):
    """Item 8's page-identity promise: UZ V3 and RU V3 are valid independent
    publications, so both must reach `published` — each on a page of its own,
    beside its OWN language's V1, under its OWN language's subject page.

    Deliberately the harsh shape: the TOC row's single `notion_lesson_page_id`
    is already stamped with the UZ Lesson Topic (V1 was archived), which is
    exactly the language-blind pointer that would route the RU revision into the
    UZ tree and collide there on `Homework V3`.
    """
    seeded = await _seed_world(languages=("uz", "ru"))
    # Seeded under the title the archive really files by —
    # `"{section_number} {section_title}"` — so what is proven below is adoption
    # of each language's EXISTING Lesson Topic, not the minting of a parallel one.
    lesson_title = "1 Kvadrat tenglamalar"
    seeded.notion = _seed_notion_v1(
        fakes.notion, subject_page=SUBJECT_PAGE_UZ, lesson_title=lesson_title)
    ru = _seed_notion_v1(
        fakes.notion, subject_page=SUBJECT_PAGE_RU, lesson_title=lesson_title)
    await _stamp_lesson_page(seeded.toc_entry_id, seeded.notion.lesson)
    trees = {"uz": seeded.notion, "ru": ru}
    v1_snapshots = {
        language: copy.deepcopy(fakes.notion.blocks[node.homework_v1])
        for language, node in trees.items()
    }
    try:
        campaign_id = await _create_campaign(
            seeded, languages=("uz", "ru"), canary_size=2)
        await _service().launch_canary(campaign_id)
        targets = {t.output_language: t for t in await _targets(campaign_id)}
        for target in targets.values():
            await _run_revision(target.id)
        await _service().approve_canary(campaign_id, actor="pytest")
        await _drain_publisher(fakes)

        pages = {}
        for language, target in targets.items():
            refreshed = await _target(target.id)
            assert refreshed.status == "published", (
                f"{language} V3 did not publish: "
                f"{refreshed.publication_last_error}")
            assert refreshed.publication_version == 3, (
                f"{language} reserves its OWN version 3, independently of the "
                f"other lineage (got {refreshed.publication_version})")
            pages[language] = refreshed.notion_page_id
        assert pages["uz"] != pages["ru"], (
            "UZ V3 and RU V3 are separate publications and need separate pages")

        for language, node in trees.items():
            assert fakes.notion.child_titles(node.lesson) == [
                "Homework", "Homework V3"], (
                f"the {language} V3 belongs beside the {language} V1, and "
                f"nothing else may land under that Lesson Topic")
            assert fakes.notion.parents[pages[language]] == node.lesson
            assert fakes.notion.child_titles(node.container) == [lesson_title], (
                f"the {language} tree grew no second Lesson Topic")
            assert fakes.notion.blocks[node.homework_v1] == v1_snapshots[language], (
                f"the {language} V1 `Homework` page was modified")

        # The shared legacy pointer is a fill-once hint, not identity: it still
        # names the UZ Lesson Topic it was stamped with.
        assert await _lesson_page_id(seeded.toc_entry_id) == seeded.notion.lesson
    finally:
        await _purge_world(seeded)


async def _lesson_page_id(toc_entry_id):
    from app.db import SessionLocal
    from app.repositories import toc_entries as toc_repo

    async with SessionLocal() as session:
        return (await toc_repo.get(session, toc_entry_id)).notion_lesson_page_id


async def test_v1_and_every_earlier_version_page_are_never_touched(world, fakes):
    """Item 9. The strongest assertion in this file: V1's blocks are compared
    byte for byte against the snapshot taken before the campaign ran, and no
    destructive Notion call is ever made against V1 or V3."""
    v1_page = world.notion.homework_v1

    await _publish_one_campaign(world, fakes)
    v3_page = (await _targets(await _first_campaign_id()))[0]
    assert fakes.notion.blocks[v1_page] == world.v1_snapshot, (
        "the V1 `Homework` page was modified")

    v3_page_id = (await _target(v3_page.id)).notion_page_id
    v3_snapshot = copy.deepcopy(fakes.notion.blocks[v3_page_id])

    await _publish_one_campaign(world, fakes, publication_version=4)

    assert fakes.notion.blocks[v1_page] == world.v1_snapshot, (
        "publishing V4 rewrote V1")
    assert fakes.notion.blocks[v3_page_id] == v3_snapshot, (
        "publishing V4 rewrote V3 — earlier versions are immutable")
    assert fakes.notion.titles[v1_page] == "Homework", "V1 was renamed"
    destructive = [
        call for call in fakes.notion.calls
        if call[0] == "clear_content_blocks" and call[1] in (v1_page, v3_page_id)
    ]
    assert destructive == [], f"V1/V3 content was cleared: {destructive}"


async def _first_campaign_id():
    """The oldest campaign this module created (the V3 one)."""
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models.regeneration_campaign import RegenerationCampaign

    async with SessionLocal() as session:
        return await session.scalar(
            select(RegenerationCampaign.id)
            .where(RegenerationCampaign.app_git_revision == MARKER)
            .order_by(RegenerationCampaign.created_at.asc())
            .limit(1)
        )


# ═════════════ 13. the money is the campaign's own ═══════════════════════


async def test_actual_cost_is_exactly_the_revision_usage_and_copies_are_free(
    world, fakes
):
    """Item 13. The report's actual cost is the sum of the revision jobs' own
    ``agent_usages`` rows — V1's 990k-token row never enters it, and the copied
    phases contribute only the free ``<cache>`` extract marker."""
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models.agent_usage import AgentUsage

    campaign_id = await _publish_one_campaign(world, fakes)
    target = (await _targets(campaign_id))[0]
    job_id = await _revision_job_id(target.id)

    report = await _report(campaign_id)
    cost = report.actual_cost
    regenerated = len(PLAN.regenerated_phases)

    assert cost.revision_job_count == 1
    assert cost.paid_call_count == regenerated, (
        "one paid row per REGENERATED phase and not one more")
    assert cost.zero_cost_marker_count == 1, (
        "the copied extract's free `<cache>` marker — copies are not re-billed")
    assert cost.call_count == regenerated + 1
    assert cost.prompt_tokens == regenerated * fakes.prompt_tokens
    assert cost.output_tokens == regenerated * fakes.output_tokens
    assert cost.usd > 0
    assert cost.excluded_row_count == 0, (
        "V1's usage is not merely outnumbered — it never entered the query")

    async with SessionLocal() as session:
        rows = (await session.execute(
            select(AgentUsage).where(AgentUsage.homework_job_id == job_id)
        )).scalars().all()
    markers = [row for row in rows if row.provider == "<cache>"]
    assert len(markers) == 1
    assert markers[0].total_tokens == 0
    assert markers[0].raw_envelope["source_job_id"] == str(world.v1["uz"])
    assert sum(row.prompt_tokens for row in rows) == cost.prompt_tokens

    assert report.provenance.regenerated_phase_count == regenerated
    assert report.provenance.copied_phase_count == len(PLAN.copied_phases)


# ═════════════ 14. ordinary Fleet behaviour is untouched ═════════════════


async def test_a_live_revision_job_is_invisible_to_every_ordinary_fleet_path(
    world, fakes
):
    """Item 14. Asserted against a revision produced by the REAL campaign flow,
    not a hand-built row: dedup/adoption, relaunch resume, TOC status
    enrichment, the coverage dashboard, batch resume and never-pay-twice all
    still see V1 — and `/toc/retry`'s blocking guard still sees the revision.
    """
    from app.db import SessionLocal
    from app.repositories import cost as cost_repo
    from app.repositories import jobs as jobs_repo
    from app.repositories import subject_coverage as coverage_repo

    campaign_id = await _publish_one_campaign(world, fakes)
    target = (await _targets(campaign_id))[0]
    revision_id = await _revision_job_id(target.id)
    v1_id = world.v1["uz"]

    async with SessionLocal() as session:
        active = await jobs_repo.find_active_for_section(
            session, world.book_id, world.toc_entry_id, output_language="uz")
        assert active is None or active.id != revision_id, (
            "`/generate` and batch launch would ADOPT the revision as the "
            "lesson's current job")

        latest = await jobs_repo.latest_for_section(
            session, world.book_id, world.toc_entry_id, output_language="uz")
        assert latest is not None and latest.id == v1_id, (
            "relaunch-resume must never resume a revision")

        by_section = await jobs_repo.latest_by_section(
            session, world.book_id, output_language="uz")
        assert by_section.get(world.toc_entry_id, latest).id == v1_id, (
            "the per-row TOC badge must show V1's state, not the revision's")

        statuses = await coverage_repo.job_status_by_book(session, "uz")
        assert statuses[str(world.book_id)][str(world.toc_entry_id)] == "done"

        prior, had_done = await cost_repo.section_prior_api_cost(
            session, world.book_id, world.toc_entry_id, "api")
        assert had_done is True
        assert prior == pytest.approx(
            _expected_v1_cost(), rel=1e-6), (
            "never-pay-twice must quote V1's spend; quoting the campaign's "
            f"would rebill the lesson at the regeneration price (got {prior})")

        kept = await jobs_repo.exclude_revisions(session, [revision_id, v1_id])
        assert kept == [v1_id], (
            "batch re-archive selection must drop revisions defensively")

        for_book = await jobs_repo.list_for_book(session, world.book_id)
        assert revision_id in {row.id for row in for_book}, (
            "`list_for_book` must stay unfiltered — /toc/retry uses it to "
            "refuse replacing a TOC that regeneration history references")


def _expected_v1_cost() -> float:
    from app.services import pricing

    return pricing.cost_usd("gemini", "gemini-3.6-flash", {
        "prompt_tokens": 900_000, "output_tokens": 90_000,
        "cached_tokens": 0, "total_tokens": 990_000,
    })


async def test_the_legacy_archive_intrinsically_refuses_a_live_revision(
    world, fakes, monkeypatch
):
    """Item 14, the archival half. The guard INSIDE
    ``notion_archive.archive_job`` — not the pipeline branch — is what protects
    V1 from an operator force-rearchive, a batch sweep or any future caller.

    Notion is switched ON with a key present, so the refusal has to come from
    the guard rather than from the global switch. ``NotionClientWrapper`` stays
    a tripwire; the swallowed construction failure is exactly what tells the two
    branches apart below.
    """
    from app.config import settings
    from app.db import SessionLocal
    from app.models.homework_job import HomeworkJob
    from app.services import notion_archive

    campaign_id = await _publish_one_campaign(world, fakes)
    target = (await _targets(campaign_id))[0]
    revision_id = await _revision_job_id(target.id)
    notion_calls_before = len(fakes.notion.calls)

    monkeypatch.setattr(settings, "notion_enabled", True)
    monkeypatch.setattr(settings, "notion_api_key", "pytest-not-a-real-key")

    await fakes.real_archive_job(revision_id, force=True)

    async with SessionLocal() as session:
        job = await session.get(HomeworkJob, revision_id)
        assert job.notion_archived_at is None
        assert job.notion_skip_reason == notion_archive.REVISION_SKIP_REASON, (
            "a revision must be refused with a deterministic, greppable reason "
            "— and `force=True` must not bypass it")
    assert len(fakes.notion.calls) == notion_calls_before, (
        "the guard must refuse BEFORE any Notion read or write")
    assert fakes.notion.blocks[world.notion.homework_v1] == world.v1_snapshot, (
        "the forced archive rewrote V1 — the exact thing the guard exists for")

    # The other half of the branch: an ORDINARY job is NOT refused. It gets
    # past the guard and reaches the client, which this suite tripwires, so the
    # swallowed failure lands the generic reason instead of the revision one.
    async with SessionLocal() as session:
        ordinary_id = world.v1["uz"]
    await fakes.real_archive_job(ordinary_id, force=True)
    async with SessionLocal() as session:
        ordinary = await session.get(HomeworkJob, ordinary_id)
    assert ordinary.notion_skip_reason != notion_archive.REVISION_SKIP_REASON, (
        "an ordinary job must not be swept up by the revision guard")
