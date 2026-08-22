"""The campaign report: buckets, provenance, judge/solver counts, money.

Two layers, on purpose.

**Assembly (always runs, no database).** A whole campaign is composed from
target/job/phase/usage rows and the report is asserted end to end: every bucket
populated at once, soft judge statuses summed across targets, copied vs
regenerated counted from the real phase rows, the publication history of a
retried delivery, and a human-readable reason on every failed or abandoned row.

**Gather (real Postgres, ``RUN_DB_INTEGRATION=1``).** The SQL half — that the
actual-cost query reaches this campaign's revision jobs and NOTHING else, that
a source's publication version resolves through the revision chain, that the
report path repairs a crashed revision before rendering it, and that the route
returns all of it through the real session.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.v1 import regeneration as regen_api
from app.auth import get_current_user
from app.config import settings
from app.db import get_session
from app.schemas import regeneration as out
from app.services.flows import flow_for
from app.services.regeneration_planner import build_phase_plan
from main import app

client = TestClient(app)

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
SUBJECT = "math-algebra"
CANONICAL = ("extract", *flow_for(SUBJECT))
PLAN = build_phase_plan(subject=SUBJECT, selected_phases=["flashcards"]).to_json()
BASE = "/api/v1/regeneration"


# ═════════════════════════ assembly (no database) ════════════════════════


def _campaign(**overrides):
    base = dict(
        id=uuid4(),
        status="attention_required",
        selection_spec={"output_languages": ["uz"]},
        requested_phases=["flashcards"],
        excluded_phases=["reflection"],
        launch_contract={"provider": "gemini", "model": "gemini-3.6-flash",
                         "transport": "api"},
        refresh_extraction=False,
        exclusion_acknowledged=True,
        canary_size=1,
        estimated_cost_low_usd=0.4,
        estimated_cost_high_usd=1.2,
        app_git_revision="a4a0aa5",
        canary_launched_at=NOW - timedelta(hours=2),
        approved_at=NOW - timedelta(hours=1),
        rejected_at=None,
        cancel_requested_at=None,
        completed_at=None,
        rejected_reason=None,
        cancel_requested_reason=None,
        created_at=NOW - timedelta(hours=3),
        updated_at=NOW,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _target(campaign_id, status, **overrides):
    published_like = status in (
        "publication_pending", "publishing", "published", "publication_failed"
    )
    base = dict(
        id=uuid4(),
        campaign_id=campaign_id,
        toc_entry_id=uuid4(),
        output_language="uz",
        is_canary=False,
        source_job_id=uuid4(),
        status=status,
        phase_plan=PLAN,
        publication_released_at=NOW if published_like else None,
        publication_version=2 if published_like else None,
        notion_page_id="page-abc" if status == "published" else None,
        publication_attempts=0,
        publication_next_attempt_at=None,
        publication_last_error=None,
        terminal_at=NOW if status in ("published", "abandoned") else None,
        terminal_reason=None,
        abandon_requested_at=None,
        abandon_requested_reason=None,
        created_at=NOW,
        updated_at=NOW,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _job(status="done", **overrides):
    base = dict(
        id=uuid4(),
        status=status,
        scheduled_at=NOW,
        error_message=None,
        last_error=None,
        current_phase=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _phase(name, *, judge=None, solver=None, copied=False, status="done"):
    return SimpleNamespace(
        phase_name=name,
        judge_status=judge,
        solver_status=solver,
        copied_from_phase_output_id=uuid4() if copied else None,
        status=status,
    )


def _usage(job_id, **overrides):
    base = dict(
        homework_job_id=job_id,
        provider="gemini",
        model_name="gemini-3.6-flash",
        operation="phase.run",
        prompt_tokens=10_000,
        output_tokens=2_000,
        cached_tokens=0,
        cache_creation_tokens=0,
        total_tokens=12_000,
        success=True,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _full_campaign():
    """One campaign holding every bucket at once, with real phase rows."""
    campaign = _campaign()
    targets, jobs, rows, usage = [], {}, {}, []

    published = _target(campaign.id, "published", is_canary=True)
    pending = _target(campaign.id, "publication_pending")
    backing_off = _target(
        campaign.id, "publication_failed",
        publication_attempts=2,
        publication_next_attempt_at=NOW + timedelta(minutes=5),
        publication_last_error="notion 502 bad gateway",
    )
    parked = _target(
        campaign.id, "publication_failed",
        publication_attempts=5,
        publication_next_attempt_at=None,
        publication_last_error="VersionPageCollision: Homework V2 exists with a "
                               "different marker",
    )
    gen_failed = _target(campaign.id, "generation_failed")
    abandoned = _target(
        campaign.id, "abandoned",
        publication_released_at=NOW, publication_version=3,
        publication_attempts=5,
        publication_last_error="notion 403 forbidden",
        terminal_reason="abandoned by op: destination page was archived",
        abandon_requested_at=NOW, abandon_requested_reason="destination archived",
    )
    generating = _target(campaign.id, "generating")
    publishing = _target(campaign.id, "publishing")

    for target, job_status in (
        (published, "done"), (pending, "done"), (backing_off, "done"),
        (parked, "done"), (gen_failed, "failed"), (abandoned, "done"),
        (generating, "running"), (publishing, "done"),
    ):
        targets.append(target)
        job = _job(job_status)
        if job_status == "failed":
            job.error_message = "phase 'flashcards' failed: provider 500"
        jobs[target.id] = job
        rows[target.id] = [
            _phase("extract", copied=True),
            _phase("flashcards", judge="major_shipped"),
            _phase("boss-arena", judge="ok", solver="ok"),
        ]
        usage.append(_usage(job.id))
    # A copied extract's free provenance marker, and one source-job row that
    # must never be counted.
    usage.append(_usage(jobs[published.id].id, provider="<cache>",
                        model_name="<cache>", operation="lesson.extract",
                        prompt_tokens=0, output_tokens=0, total_tokens=0))
    usage.append(_usage(published.source_job_id, prompt_tokens=999_999))
    return campaign, targets, jobs, rows, usage


def _report():
    campaign, targets, jobs, rows, usage = _full_campaign()
    return out.CampaignDetailOut.build(
        campaign, targets, now=NOW,
        jobs_by_target=jobs, phase_rows_by_target=rows,
        source_versions={t.id: 1 for t in targets},
        usage_rows=usage,
    ), targets, jobs


def test_the_report_populates_every_bucket_and_loses_no_target():
    report, targets, _jobs = _report()
    body = report.model_dump()

    assert set(body["buckets"]) == set(out.BUCKETS)
    assert len(body["buckets"]["published"]) == 1
    assert len(body["buckets"]["publication_pending"]) == 1
    assert len(body["buckets"]["publication_failed"]) == 2
    assert len(body["buckets"]["generation_failed"]) == 1
    assert len(body["buckets"]["abandoned"]) == 1
    # `generating` + `publishing`: in-flight rows are reported, not dropped.
    assert len(body["buckets"]["in_flight"]) == 2
    assert sum(len(v) for v in body["buckets"].values()) == len(targets)
    assert body["target_count"] == len(targets)
    assert body["status_counts"]["publishing"] == 1
    assert body["status_counts"]["generating"] == 1


def test_the_report_separates_backing_off_from_operator_parked():
    report, _targets, _jobs = _report()
    by_state = {t.publication_state: t for t in report.targets}

    assert by_state["backing_off"].action_required is False
    assert "automatic retry is scheduled" in by_state["backing_off"].reason
    assert by_state["action_required"].action_required is True
    assert "NO AUTOMATIC RETRY" in by_state["action_required"].reason
    assert "VersionPageCollision" in by_state["action_required"].reason
    # Same status, two different situations.
    assert by_state["backing_off"].status == by_state["action_required"].status


def test_every_failed_or_abandoned_row_reads_as_a_sentence_not_a_code():
    report, _targets, _jobs = _report()
    for target in report.targets:
        assert target.reason and target.reason[-1] in ".…"
        if target.status == "generation_failed":
            assert "provider 500" in target.reason
            assert "Retry generation or abandon" in target.reason
        if target.status == "abandoned":
            # BOTH: why we stopped, and what broke.
            assert "destination page was archived" in target.reason
            assert "notion 403 forbidden" in target.reason
            assert target.delivery_error == "notion 403 forbidden"


def test_publication_history_survives_into_the_report():
    report, _targets, _jobs = _report()
    parked = next(t for t in report.targets
                  if t.publication_state == "action_required")
    assert parked.publication_attempts == 5
    assert parked.publication_version == 2
    assert parked.publication_released_at is not None
    assert parked.publication_last_error.startswith("VersionPageCollision")

    published = next(t for t in report.targets if t.status == "published")
    assert published.notion_page_url == "https://www.notion.so/pageabc"
    assert published.publication_version == 2


def test_soft_judge_statuses_are_counted_not_treated_as_failures():
    report, targets, _jobs = _report()
    # One `major_shipped` and one `ok` per target.
    assert report.judge_status_counts["major_shipped"] == len(targets)
    assert report.solver_status_counts["ok"] == len(targets)
    # A soft judge status does not put a target in a failure bucket.
    published = next(t for t in report.targets if t.status == "published")
    assert published.judge_status_counts == {"major_shipped": 1, "ok": 1}
    assert published.bucket == "published"


def test_copied_and_regenerated_provenance_comes_from_the_phase_rows():
    report, targets, _jobs = _report()
    assert report.provenance.copied_phase_count == len(targets)      # 1 each
    assert report.provenance.regenerated_phase_count == 2 * len(targets)
    assert report.provenance.phase_row_count == 3 * len(targets)


def test_actual_cost_excludes_source_job_usage_and_free_markers():
    report, targets, jobs = _report()
    cost = report.actual_cost

    assert cost.excluded_row_count == 1          # the source-job row
    assert cost.call_count == len(targets) + 1   # + the free <cache> marker
    assert cost.paid_call_count == len(targets)
    assert cost.zero_cost_marker_count == 1
    assert cost.prompt_tokens == 10_000 * len(targets)
    assert cost.usd > 0

    # The excluded source row is 100x the tokens of a revision row: had it been
    # counted, the total would be dominated by it.
    assert cost.prompt_tokens < 999_999


def test_the_canary_block_points_at_the_revision_job_to_review():
    report, _targets, jobs = _report()
    assert len(report.canary) == 1
    canary = report.canary[0]
    assert canary.content_path == f"/api/v1/jobs/{canary.revision_job_id}"
    assert canary.download_path == f"/api/v1/jobs/{canary.revision_job_id}/download"
    assert canary.revision_job_id in {job.id for job in jobs.values()}


class _StatementRecorder:
    """A session stand-in that captures the statement and runs no SQL.

    The projection is the point of the gather, so it is asserted on the
    STATEMENT rather than on a rendered string: a compiled-SQL substring check
    would pass the day someone re-adds ``output_md`` under an alias.
    """

    def __init__(self):
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return _EmptyResult()


class _EmptyResult:
    def all(self):
        return []

    def scalars(self):
        return self


async def test_the_phase_gather_selects_only_the_columns_the_report_reads():
    from app.models.phase_output import PhaseOutput

    session = _StatementRecorder()
    assert await regen_api._phase_rows(session, []) == {}
    assert session.statements == []          # no ids, no query

    await regen_api._phase_rows(session, [uuid4(), uuid4()])
    statement = session.statements[0]
    selected = {column.key for column in statement.selected_columns}

    # Everything the router groups/orders by and the schema counts from.
    assert selected == {
        "job_id",
        "phase_order",
        "copied_from_phase_output_id",
        "judge_status",
        "solver_status",
    }
    # The payload columns are the whole weight of a `phase_outputs` row, and a
    # campaign report reads NONE of them.
    assert "output_md" not in selected
    assert "content_json" not in selected
    # Not a whole-entity select in disguise: `select(PhaseOutput)` describes
    # itself as the ORM entity and expands to every column.
    assert all(
        description["expr"] is not PhaseOutput
        for description in statement.column_descriptions
    )


def test_the_report_keeps_the_frozen_plan_and_the_extraction_choice():
    report, _targets, _jobs = _report()
    assert report.requested_phases == ["flashcards"]
    assert report.excluded_phases == ["reflection"]
    assert report.refresh_extraction is False
    assert report.exclusion_acknowledged is True
    assert report.launch_contract["transport"] == "api"
    plan = report.targets[0].phase_plan
    assert "flashcards" in plan.regenerated_phases
    assert plan.refresh_extraction is False


def test_campaign_report_labels_exact_and_legacy_versions_explicitly():
    exact = out.CampaignSummaryOut.from_row(
        _campaign(publication_version=3),
        status_counts={"planned": 1},
    )
    legacy = out.CampaignSummaryOut.from_row(
        _campaign(publication_version=None),
        status_counts={"planned": 1},
    )

    assert exact.publication_version == 3
    assert exact.publication_version_label == "Homework V3"
    assert legacy.publication_version is None
    assert legacy.publication_version_label == "Legacy mixed/automatic version"


def test_campaign_summary_has_an_empty_display_identity_for_legacy_callers():
    """A caller without the list query's lesson join must still serialize a
    truthful, explicitly empty identity instead of reviving phase names as a
    display fallback."""
    summary = out.CampaignSummaryOut.from_row(
        _campaign(publication_version=3),
        status_counts={"planned": 1},
    ).model_dump()

    assert summary.get("subjects") == []
    assert summary.get("grades") == []
    assert summary.get("lesson_count") == 0
    assert summary.get("lesson_title") is None


@pytest.mark.asyncio
async def test_campaign_list_deduplicates_lesson_identity_across_languages():
    """One lesson regenerated in UZ and RU is one lesson name, not two.

    Removing the identity projection or deduping by target rather than TOC row
    must fail this test.
    """
    campaign = _campaign()
    lesson_id = uuid4()

    campaign_rows = MagicMock()
    campaign_rows.scalars.return_value.all.return_value = [campaign]
    count_rows = MagicMock()
    count_rows.all.return_value = [(campaign.id, "published", 2)]
    identity_rows = MagicMock()
    identity_rows.all.return_value = [
        SimpleNamespace(
            campaign_id=campaign.id,
            toc_entry_id=lesson_id,
            subject="biology",
            grade="9",
            lesson_title="Photosynthesis",
        ),
        SimpleNamespace(
            campaign_id=campaign.id,
            toc_entry_id=lesson_id,
            subject="biology",
            grade="9",
            lesson_title="Photosynthesis",
        ),
    ]
    session = SimpleNamespace(
        execute=AsyncMock(side_effect=[campaign_rows, count_rows, identity_rows]),
        scalar=AsyncMock(return_value=1),
    )

    result = await regen_api._list_campaigns(
        session, statuses=None, limit=50, offset=0
    )

    assert len(result) == 4
    _campaigns, _counts, identities, total = result
    assert total == 1
    assert identities[campaign.id] == {
        "subjects": ["biology"],
        "grades": ["9"],
        "lesson_count": 1,
        "lesson_title": "Photosynthesis",
    }


def test_target_report_separates_reviewed_lesson_from_published_version_page():
    target = _target(
        uuid4(),
        "published",
        notion_container_policy="reuse",
        reviewed_notion_container_page_id="container-page",
        notion_parent_policy="reuse",
        reviewed_notion_lesson_page_id="lesson-topic-page",
        reviewed_notion_lesson_title="1 Lesson 1",
        notion_page_id="homework-v3-page",
        publication_version=3,
    )

    report = out.TargetReportOut.from_row(target, now=NOW)

    assert report.reviewed_notion_container_page_url.endswith("containerpage")
    assert report.reviewed_notion_lesson_page_url.endswith("lessontopicpage")
    assert report.reviewed_notion_lesson_title == "1 Lesson 1"
    assert report.notion_page_url.endswith("homeworkv3page")
    assert report.notion_page_id != report.reviewed_notion_lesson_page_id


# ═════════════════════════ gather (real Postgres) ════════════════════════

_DB = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)
_MARKER = "pytest-regen-wave4-reports"


async def _seed(*, target_status="published", job_status="done", canary=True):
    """One book/lesson, a V1 source with usage, a campaign, a target and its
    revision job with its own usage."""
    from app.db import SessionLocal
    from app.models.agent_usage import AgentUsage
    from app.models.book import Book
    from app.models.homework_job import HomeworkJob
    from app.models.phase_output import PhaseOutput
    from app.models.regeneration_campaign import RegenerationCampaign
    from app.models.regeneration_target import RegenerationTarget
    from app.models.toc_entry import TOCEntry

    published_like = target_status in (
        "publication_pending", "publishing", "published", "publication_failed"
    )
    async with SessionLocal() as session:
        book = Book(
            subject=SUBJECT, original_filename="regen_reports.pdf",
            content_sha256=uuid.uuid4().hex * 2, file_size_bytes=1,
            status="toc_ready", grade="5",
        )
        session.add(book)
        await session.flush()
        toc = TOCEntry(book_id=book.id, section_title="Lesson 1",
                       section_number="1", chapter_title="Chapter 1",
                       order_index=0)
        session.add(toc)
        await session.flush()

        source = HomeworkJob(
            book_id=book.id, toc_entry_id=toc.id, subject=SUBJECT, status="done",
            provider="gemini", model="gemini-3.6-flash", transport="api",
            output_language="uz",
        )
        session.add(source)
        await session.flush()
        source_phase = PhaseOutput(
            job_id=source.id, phase_name="flashcards", phase_order=1,
            prompt_hash="builtin:flashcards:v1", provider="gemini",
            model_name="gemini-3.6-flash", output_md="# source", status="done",
        )
        session.add(source_phase)
        await session.flush()
        # The V1 run's own spend. It must NEVER reach the campaign's cost.
        source_usage = AgentUsage(
            homework_job_id=source.id, phase_output_id=source_phase.id,
            provider="gemini", model_name="gemini-3.6-flash",
            operation="phase.run", auth_mode="api",
            prompt_tokens=500_000, output_tokens=100_000, total_tokens=600_000,
            success=True,
        )
        session.add(source_usage)
        await session.flush()

        campaign = RegenerationCampaign(
            status="approved", selection_spec={"marker": _MARKER},
            requested_phases=["flashcards"], excluded_phases=[],
            launch_contract={"provider": "gemini", "model": "gemini-3.6-flash",
                             "transport": "api"},
            canary_size=1, app_git_revision=_MARKER,
            approved_at=datetime.now(timezone.utc),
        )
        session.add(campaign)
        await session.flush()
        target = RegenerationTarget(
            campaign_id=campaign.id, toc_entry_id=toc.id, output_language="uz",
            phase_plan=PLAN, source_job_id=source.id, is_canary=canary,
            status=target_status,
            publication_released_at=(
                datetime.now(timezone.utc) if published_like else None),
            publication_version=2 if published_like else None,
            notion_page_id="page-abc" if target_status == "published" else None,
            terminal_at=(datetime.now(timezone.utc)
                         if target_status in ("published", "abandoned") else None),
        )
        session.add(target)
        await session.flush()
        revision = HomeworkJob(
            book_id=book.id, toc_entry_id=toc.id, subject=SUBJECT,
            status=job_status, provider="gemini", model="gemini-3.6-flash",
            transport="api", output_language="uz",
            revision_of_job_id=source.id, regeneration_target_id=target.id,
            session_limit_strategy="pause",
        )
        session.add(revision)
        await session.flush()
        for order, name in enumerate(CANONICAL):
            session.add(PhaseOutput(
                job_id=revision.id, phase_name=name, phase_order=order,
                prompt_hash=f"builtin:{name}:v9", provider="gemini",
                model_name="gemini-3.6-flash", output_md=f"# {name}",
                status="done",
                judge_status="major_shipped" if name == "flashcards" else None,
                copied_from_phase_output_id=(
                    source_phase.id if name != "flashcards" else None),
            ))
        await session.flush()
        revision_usage = AgentUsage(
            homework_job_id=revision.id, provider="gemini",
            model_name="gemini-3.6-flash", operation="phase.run",
            auth_mode="api", prompt_tokens=1_000, output_tokens=200,
            total_tokens=1_200, success=True,
        )
        session.add(revision_usage)
        await session.flush()
        await session.commit()
        return SimpleNamespace(
            campaign_id=campaign.id, target_id=target.id,
            source_job_id=source.id, revision_job_id=revision.id,
            toc_entry_id=toc.id, book_id=book.id,
            # `agent_usages.homework_job_id` is SET NULL, so these rows OUTLIVE
            # their jobs unless they are deleted by id. `_purge` is asserted
            # against these, not against the (by then null) job link.
            usage_ids=(source_usage.id, revision_usage.id),
        )


async def _purge(seeded) -> None:
    """Delete every row `_seed` created, child-first.

    Not hygiene for its own sake. A seeded target that is left behind is a
    NON-TERMINAL regeneration target, so it holds
    `uq_regeneration_targets_active_lineage` for its lesson forever, and its
    revision job is picked up by any query in the suite that is not
    lesson-scoped. Both of those turn a later, unrelated test into a failure
    that depends only on run order.

    The order below is the FK graph, not a preference: `agent_usages` points at
    both jobs and at a phase row; `phase_outputs.copied_from_phase_output_id`
    is RESTRICT, so the revision's copies must go before the source rows they
    reference; `homework_jobs.regeneration_target_id` and
    `revision_of_job_id` are RESTRICT, so the revision job goes before both its
    target and its source.
    """
    from sqlalchemy import delete

    from app.db import SessionLocal
    from app.models.agent_usage import AgentUsage
    from app.models.book import Book
    from app.models.homework_job import HomeworkJob
    from app.models.phase_output import PhaseOutput
    from app.models.regeneration_campaign import RegenerationCampaign
    from app.models.regeneration_target import RegenerationTarget
    from app.models.toc_entry import TOCEntry

    job_ids = [seeded.source_job_id, seeded.revision_job_id]
    async with SessionLocal() as session:
        await session.execute(
            delete(AgentUsage).where(AgentUsage.homework_job_id.in_(job_ids)))
        await session.execute(
            delete(PhaseOutput)
            .where(PhaseOutput.job_id.in_(job_ids))
            .where(PhaseOutput.copied_from_phase_output_id.is_not(None)))
        await session.execute(
            delete(PhaseOutput).where(PhaseOutput.job_id.in_(job_ids)))
        await session.execute(
            delete(HomeworkJob).where(HomeworkJob.id == seeded.revision_job_id))
        await session.execute(
            delete(RegenerationTarget).where(
                RegenerationTarget.id == seeded.target_id))
        await session.execute(
            delete(RegenerationCampaign).where(
                RegenerationCampaign.id == seeded.campaign_id))
        await session.execute(
            delete(HomeworkJob).where(HomeworkJob.id == seeded.source_job_id))
        await session.execute(
            delete(TOCEntry).where(TOCEntry.id == seeded.toc_entry_id))
        await session.execute(delete(Book).where(Book.id == seeded.book_id))
        await session.commit()


@pytest.fixture()
async def seed():
    """`_seed`, with its rows removed again afterwards.

    Every real-DB test below goes through this rather than calling `_seed`
    directly, so the module cannot leave a live lineage or a stray revision job
    in the database for whatever runs next.
    """
    created = []

    async def _factory(**kwargs):
        seeded = await _seed(**kwargs)
        created.append(seeded)
        return seeded

    try:
        yield _factory
    finally:
        for seeded in reversed(created):
            await _purge(seeded)


@_DB
async def test_the_seed_helper_removes_every_row_it_created():
    """The fixture teardown above is only worth anything if `_purge` is
    complete — a forgotten table leaves exactly the residue it exists to
    prevent, and nothing else in the suite would say so."""
    from sqlalchemy import func, select

    from app.db import SessionLocal
    from app.models.agent_usage import AgentUsage
    from app.models.book import Book
    from app.models.homework_job import HomeworkJob
    from app.models.phase_output import PhaseOutput
    from app.models.regeneration_campaign import RegenerationCampaign
    from app.models.regeneration_target import RegenerationTarget
    from app.models.toc_entry import TOCEntry

    seeded = await _seed()
    await _purge(seeded)

    async with SessionLocal() as session:
        for model, pk in (
            (Book, seeded.book_id),
            (TOCEntry, seeded.toc_entry_id),
            (HomeworkJob, seeded.source_job_id),
            (HomeworkJob, seeded.revision_job_id),
            (RegenerationTarget, seeded.target_id),
            (RegenerationCampaign, seeded.campaign_id),
        ):
            assert await session.get(model, pk) is None, (
                f"{model.__name__} {pk} survived the purge")
        surviving = await session.scalar(
            select(func.count()).select_from(PhaseOutput).where(
                PhaseOutput.job_id.in_(
                    [seeded.source_job_id, seeded.revision_job_id])))
        assert surviving == 0, "phase_outputs rows survived the purge"
        # By ID: the FK is SET NULL, so a usage row that was never deleted is
        # still sitting there with a null job link and would pass a
        # link-scoped count.
        surviving = await session.scalar(
            select(func.count()).select_from(AgentUsage).where(
                AgentUsage.id.in_(list(seeded.usage_ids))))
        assert surviving == 0, "agent_usages rows survived the purge"


@_DB
async def test_actual_cost_query_reaches_revision_jobs_only(seed):
    from app.db import SessionLocal

    seeded = await seed()
    async with SessionLocal() as session:
        report = await regen_api._campaign_detail(
            session, seeded.campaign_id, now=datetime.now(timezone.utc)
        )

    assert report.actual_cost.call_count == 1
    assert report.actual_cost.prompt_tokens == 1_000
    assert report.actual_cost.revision_job_count == 1
    # The V1 job's 500k-token row is not merely outnumbered — it never entered
    # the query, so it is not even in `excluded_row_count`.
    assert report.actual_cost.excluded_row_count == 0
    assert report.actual_cost.usd > 0


@_DB
async def test_the_report_resolves_the_source_version_and_lesson(seed):
    from app.db import SessionLocal

    seeded = await seed()
    async with SessionLocal() as session:
        report = await regen_api._campaign_detail(
            session, seeded.campaign_id, now=datetime.now(timezone.utc)
        )

    target = report.targets[0]
    # The source is an ordinary completed job: logical V1, which has no row.
    assert target.source_publication_version == 1
    assert target.source_job_id == seeded.source_job_id
    assert target.revision_job_id == seeded.revision_job_id
    assert target.lesson.section_title == "Lesson 1"
    assert target.lesson.book_id == seeded.book_id


@_DB
async def test_provenance_and_judge_counts_come_from_the_stored_phase_rows(seed):
    from app.db import SessionLocal

    seeded = await seed()
    async with SessionLocal() as session:
        report = await regen_api._campaign_detail(
            session, seeded.campaign_id, now=datetime.now(timezone.utc)
        )

    assert report.provenance.phase_row_count == len(CANONICAL)
    assert report.provenance.regenerated_phase_count == 1     # flashcards
    assert report.provenance.copied_phase_count == len(CANONICAL) - 1
    assert report.judge_status_counts == {"major_shipped": 1}
    assert report.canary[0].revision_job_id == seeded.revision_job_id


@_DB
async def test_the_report_path_repairs_a_crashed_revision_before_rendering(seed):
    """The worker died between the job's terminal commit and its target update:
    the target still says `generating` over a finished job."""
    from app.db import SessionLocal
    from app.models.regeneration_target import RegenerationTarget

    seeded = await seed(target_status="generating", job_status="failed")
    async with SessionLocal() as session:
        moved = await regen_api._reconcile(session)
        assert moved >= 1
        refreshed = await session.get(RegenerationTarget, seeded.target_id)
        assert refreshed.status == "generation_failed"

        report = await regen_api._campaign_detail(
            session, seeded.campaign_id, now=datetime.now(timezone.utc)
        )
    assert report.buckets["generation_failed"] == [seeded.target_id]
    assert report.targets[0].action_required is True


@_DB
async def test_the_campaign_list_counts_targets_per_campaign(seed):
    from app.db import SessionLocal

    seeded = await seed()
    async with SessionLocal() as session:
        campaigns, counts, identities, total = await regen_api._list_campaigns(
            session, statuses=None, limit=200, offset=0
        )

    assert total >= 1
    assert seeded.campaign_id in {c.id for c in campaigns}
    assert counts[seeded.campaign_id]["published"] == 1
    assert identities[seeded.campaign_id] == {
        "subjects": [SUBJECT],
        "grades": ["5"],
        "lesson_count": 1,
        "lesson_title": "Lesson 1",
    }


@_DB
async def test_the_route_serves_the_whole_report_over_a_real_session(seed, monkeypatch):
    """Driven through ASGITransport, not ``TestClient``: the client's own
    portal loop would hand the engine's asyncpg connections to a second event
    loop and the pool would tear down across loops."""
    from httpx import ASGITransport, AsyncClient

    from app.db import SessionLocal

    seeded = await seed()
    monkeypatch.setattr(settings, "regeneration_enabled", True)
    monkeypatch.setattr(settings, "regeneration_publisher_enabled", True)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "test"}

    async def _real_session():
        async with SessionLocal() as session:
            yield session

    app.dependency_overrides[get_session] = _real_session
    regen_api.reset_rollup_debounce()
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as http:
            response = await http.get(f"{BASE}/campaigns/{seeded.campaign_id}")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)
        regen_api.reset_rollup_debounce()

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(seeded.campaign_id)
    assert body["buckets"]["published"] == [str(seeded.target_id)]
    assert body["actual_cost"]["prompt_tokens"] == 1_000
    assert body["targets"][0]["notion_page_url"] == "https://www.notion.so/pageabc"
    assert body["targets"][0]["reason"].startswith("published as Homework V2")
    assert body["rollup_error"] is None


@_DB
async def test_a_single_target_report_matches_the_campaign_report(seed, monkeypatch):
    from app.db import SessionLocal

    seeded = await seed()
    async with SessionLocal() as session:
        target = await regen_api._load_target(session, seeded.target_id)
        single = await regen_api._target_report(
            session, target, now=datetime.now(timezone.utc)
        )
        report = await regen_api._campaign_detail(
            session, seeded.campaign_id, now=datetime.now(timezone.utc)
        )

    from_campaign = report.targets[0]
    assert single.id == from_campaign.id
    assert single.revision_job_id == from_campaign.revision_job_id
    assert single.copied_phase_count == from_campaign.copied_phase_count
    assert single.judge_status_counts == from_campaign.judge_status_counts
    assert single.source_publication_version == (
        from_campaign.source_publication_version
    )


@_DB
async def test_the_gathered_phase_rows_carry_no_markdown_and_still_assemble(seed):
    """The projection must be enough for the whole provenance/judge/solver
    half of the report — and must not have brought the snapshot with it."""
    from app.db import SessionLocal

    seeded = await seed()
    async with SessionLocal() as session:
        grouped = await regen_api._phase_rows(session, [seeded.revision_job_id])
        rows = grouped[seeded.revision_job_id]
        target = await regen_api._load_target(session, seeded.target_id)
        report = out.TargetReportOut.from_row(
            target, now=datetime.now(timezone.utc), phase_rows=rows
        )

    assert [row.phase_order for row in rows] == list(range(len(CANONICAL)))
    for row in rows:
        assert not hasattr(row, "output_md")
        assert not hasattr(row, "content_json")

    assert report.copied_phase_count == len(CANONICAL) - 1
    assert report.regenerated_phase_count == 1          # flashcards
    assert report.judge_status_counts == {"major_shipped": 1}
    assert report.solver_status_counts == {}


@_DB
async def test_a_gathered_revision_job_is_read_as_it_is_now_not_from_the_map(seed):
    """The request session already holds the revision job — ``_reconcile``
    loads exactly these rows before every report and mutation — and the
    service, which owns its OWN session, has since moved it. ``SessionLocal``
    is ``expire_on_commit=False``, so a plain entity ``select`` is answered
    from the identity map and would render the job as it was BEFORE the
    retry, on the very screen an operator uses to confirm the retry took.

    The held reference is the point: SQLAlchemy's identity map is weak, so the
    stale copy survives exactly as long as something still refers to it.
    """
    from app.db import SessionLocal
    from app.models.homework_job import HomeworkJob

    seeded = await seed(target_status="generating", job_status="failed")
    async with SessionLocal() as session:
        assert await regen_api._reconcile(session) >= 1
        held = await session.get(HomeworkJob, seeded.revision_job_id)
        assert held.status == "failed"

        async with SessionLocal() as service_session:
            job = await service_session.get(HomeworkJob, seeded.revision_job_id)
            job.status = "pending"
            job.error_message = None
            await service_session.commit()

        jobs = await regen_api._revision_jobs(session, [seeded.target_id])
        target = await regen_api._load_target(session, seeded.target_id)
        report = await regen_api._target_report(
            session, target, now=datetime.now(timezone.utc)
        )

    assert jobs[seeded.target_id].status == "pending"
    assert jobs[seeded.target_id].error_message is None
    assert report.revision_job_status == "pending"


@_DB
async def test_the_campaign_list_reports_the_status_as_it_is_now(seed):
    """Same safeguard, the other whole-entity gather in this router: the list
    route reconciles first — which loads campaigns into the request session —
    and the status it renders is the one the SERVICE's rollup moves."""
    from app.db import SessionLocal
    from app.models.regeneration_campaign import RegenerationCampaign

    seeded = await seed()
    async with SessionLocal() as session:
        held = await session.get(RegenerationCampaign, seeded.campaign_id)
        assert held.status == "approved"

        async with SessionLocal() as service_session:
            campaign = await service_session.get(
                RegenerationCampaign, seeded.campaign_id
            )
            campaign.status = "attention_required"
            await service_session.commit()

        campaigns, _counts, _identities, _total = await regen_api._list_campaigns(
            session, statuses=["attention_required"], limit=200, offset=0
        )

    listed = next(c for c in campaigns if c.id == seeded.campaign_id)
    assert listed.status == "attention_required"
