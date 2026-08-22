"""End-to-end acceptance for the versioned regeneration workflow — failures.

The other half of the plan's Task 11 gate. Same world, same fakes and the same
real production stack as ``test_regeneration_e2e.py`` (imported rather than
re-modelled, so the two files cannot drift into two different Notions), driven
into every way the workflow can go wrong:

* the judge's five soft outcomes and the solver's one hard one;
* a crash between a remote page creation and the row that records it;
* generation failure → generation retry, publication failure → publication
  retry, and the rule that separates them: a generated revision is never
  regenerated because delivery failed;
* cancellation at every non-terminal target state, and the promise that no
  terminal campaign is left hiding live work.

Everything is asserted on rows in a real PostgreSQL and on pages in the shared
in-memory Notion. No model, no network, no credential.
"""
from __future__ import annotations

import copy
import os
from contextlib import contextmanager

import pytest

from tests.integration.test_regeneration_e2e import (  # noqa: F401 — fixtures
    PLAN,
    SUBJECT_PAGE_UZ,
    _campaign,
    _create_campaign,
    _drain_publisher,
    _phase_rows,
    _publisher,
    _purge_world,
    _reconcile,
    _revision_job_id,
    _rolled_up,
    _run_revision,
    _seed_notion_v1,
    _seed_world,
    _service,
    _stamp_lesson_page,
    _target,
    _targets,
    fakes,
    world,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)


async def _job(job_id):
    from app.db import SessionLocal
    from app.models.homework_job import HomeworkJob

    async with SessionLocal() as session:
        return await session.get(HomeworkJob, job_id)


async def _set_job_status(job_id, status: str) -> None:
    """Force a job status directly.

    Used only where the REAL path cannot be reached in-process: a `running`
    revision needs a worker holding it, and there is no worker here.
    """
    from sqlalchemy import update

    from app.db import SessionLocal
    from app.models.homework_job import HomeworkJob

    async with SessionLocal() as session:
        await session.execute(
            update(HomeworkJob).where(HomeworkJob.id == job_id).values(status=status))
        await session.commit()


# ══════════════ 4. the judge's soft verdicts and the solver's hard one ═══


async def test_an_unavailable_judge_is_retried_once_and_then_shipped_soft(
    world, fakes
):
    """Item 4a. A transient judge failure is worth one free retry; a judge that
    is still unavailable is RECORDED, not a hole — the phase is `done`, the
    snapshot is complete, and the target reaches the canary gate."""
    from app.services import phase_judge

    outcomes = iter([
        phase_judge.JudgeOutcome(
            available=False, passed=False,
            warnings=["judge-unavailable: transient"], feedback=""),
    ])
    fallback = phase_judge.JudgeOutcome(
        available=False, passed=False,
        warnings=["judge-unavailable: still down"], feedback="")

    def _next(**_kw):
        return next(outcomes, fallback)

    fakes.judge_outcome_factory = _next

    campaign_id = await _create_campaign(world)
    await _service().launch_canary(campaign_id)
    target = (await _targets(campaign_id))[0]
    job_id = await _run_revision(target.id)

    judged_phases = [name for name in fakes.judged]
    assert len(judged_phases) == 2 * len(PLAN.regenerated_phases), (
        "each regenerated phase's unavailable judge must be retried EXACTLY "
        f"once (got {judged_phases})")

    rows = await _phase_rows(job_id)
    for name in PLAN.regenerated_phases:
        assert rows[name].status == "done"
        assert rows[name].judge_status == "unavailable"
    assert (await _target(target.id)).status == "awaiting_canary_approval", (
        "an unavailable judge is a WARNING, not an incomplete packet")


async def test_a_content_policy_refusal_is_recorded_and_never_retried(world, fakes):
    """Item 4b. A refusal will not self-heal, so it costs exactly one call and
    is kept distinct from a transient outage."""
    from app.services import phase_judge

    fakes.judge_outcome = phase_judge.JudgeOutcome(
        available=False, passed=False, warnings=["judge refused"], feedback="",
        refused=True)

    campaign_id = await _create_campaign(world)
    await _service().launch_canary(campaign_id)
    target = (await _targets(campaign_id))[0]
    job_id = await _run_revision(target.id)

    assert len(fakes.judged) == len(PLAN.regenerated_phases), (
        "a refusal must NOT be retried — it cannot self-heal")
    rows = await _phase_rows(job_id)
    for name in PLAN.regenerated_phases:
        assert rows[name].judge_status == "refused"
        assert rows[name].status == "done"
    assert (await _target(target.id)).status == "awaiting_canary_approval"


async def test_a_major_finding_spends_the_regen_budget_and_ships(
    world, fakes, monkeypatch
):
    """Item 4c/4d. A MAJOR verdict regenerates up to ``max_judge_regens`` and,
    if the finding survives the budget, the best artifact ships as
    ``major_shipped`` — regeneration preserves the existing pipeline rule and
    does not introduce a stricter publication gate."""
    from app.config import settings
    from app.services import phase_judge

    monkeypatch.setattr(settings, "max_judge_regens", 2)
    fakes.judge_outcome = phase_judge.JudgeOutcome(
        available=True, passed=False, warnings=["MAJOR: thin content"],
        feedback="add worked examples", has_major=True)

    campaign_id = await _create_campaign(world)
    await _service().launch_canary(campaign_id)
    target = (await _targets(campaign_id))[0]
    job_id = await _run_revision(target.id)

    regenerated = len(PLAN.regenerated_phases)
    assert len(fakes.generated) == regenerated * (1 + 2), (
        "each phase must be generated once and then re-generated once per "
        f"budget unit (max_judge_regens=2), got {fakes.generated}")
    rows = await _phase_rows(job_id)
    for name in PLAN.regenerated_phases:
        assert rows[name].judge_status == "major_shipped"
        assert rows[name].status == "done"
    assert (await _target(target.id)).status == "awaiting_canary_approval", (
        "a soft judge status must not block the canary gate")


async def test_a_failed_repair_generation_keeps_the_original_as_major_regen_failed(
    world, fakes, monkeypatch
):
    """Item 4e. The repair generation is not allowed to fail the job: the
    judge-rejected but COMPLETE original is retained and labelled."""
    from app.config import settings
    from app.services import phase_judge, pipeline

    monkeypatch.setattr(settings, "max_judge_regens", 1)
    fakes.judge_outcome = phase_judge.JudgeOutcome(
        available=True, passed=False, warnings=["MAJOR: wrong answer key"],
        feedback="fix the key", has_major=True)

    first_pass = dict.fromkeys(PLAN.regenerated_phases, False)
    original = fakes.run_phase_prompt

    async def _fail_the_repair(*, phase_name, **kw):
        if first_pass.get(phase_name):
            raise RuntimeError("repair generation is unavailable")
        first_pass[phase_name] = True
        return await original(phase_name=phase_name, **kw)

    monkeypatch.setattr(pipeline.agent, "run_phase_prompt", _fail_the_repair)
    monkeypatch.setattr(pipeline, "_failover_chain", lambda p: [p])

    campaign_id = await _create_campaign(world)
    await _service().launch_canary(campaign_id)
    target = (await _targets(campaign_id))[0]
    job_id = await _run_revision(target.id)

    assert (await _job(job_id)).status == "done", (
        "validation must NEVER fail a job — a failed repair keeps the original")
    rows = await _phase_rows(job_id)
    for name in PLAN.regenerated_phases:
        assert rows[name].judge_status == "major_regen_failed"
        assert rows[name].output_md.startswith(f"# V-next {name}"), (
            "the pre-regen artifact must be the one retained")
    assert (await _target(target.id)).status == "awaiting_canary_approval"


async def test_a_solver_mismatch_is_a_HARD_failure_that_cannot_publish(
    world, fakes, monkeypatch
):
    """Item 4f. ``solver_status='mismatch_blocked'`` is not a soft judge
    warning: the phase row is `failed`, the job fails, the target becomes
    ``generation_failed`` and nothing is publishable."""
    from app.config import settings
    from app.services import solver

    monkeypatch.setattr(settings, "solver_enabled", True)
    monkeypatch.setattr(settings, "max_solve_regens", 0)
    fakes.solve_outcome = solver.SolveOutcome(
        available=True, agrees=False, warnings=["HIGH: the key is wrong"],
        feedback="redo", has_mismatch=True)

    campaign_id = await _create_campaign(world)
    await _service().launch_canary(campaign_id)
    target = (await _targets(campaign_id))[0]
    job_id = await _run_revision(target.id)

    rows = await _phase_rows(job_id)
    blocked = [row for row in rows.values() if row.solver_status == "mismatch_blocked"]
    assert blocked, "the solver block never fired"
    assert all(row.status == "failed" for row in blocked)
    assert (await _job(job_id)).status == "failed"
    assert (await _target(target.id)).status == "generation_failed"
    assert await _publisher(fakes).run_once() is False, (
        "a packet with a knowingly wrong answer key must not be claimable")
    assert await _rolled_up(campaign_id) == "attention_required", (
        "a campaign may not report terminal completion while a target is "
        "retryable")


# ══════════════ 10. a crash between the remote write and our row ═════════


async def test_a_timeout_after_the_page_is_created_adopts_it_on_retry(
    world, fakes
):
    """Item 10. The publisher creates `Homework V4` remotely and dies before
    the page id reaches the database.

    The retry must find that page by its immutable MARKER (never by title
    alone), adopt it, and finish — ONE page and ONE reserved version, not a
    second `Homework V4` and not a second version number.
    """
    from app.services.notion_versioned_homework import decode_revision_marker

    # The guided flow starts at V3; the crash therefore lands on the next V4.
    first = await _create_campaign(world)
    await _service().launch_canary(first)
    v3_target = (await _targets(first))[0]
    await _run_revision(v3_target.id)
    await _service().approve_canary(first, actor="pytest")
    await _drain_publisher(fakes)
    assert (await _target(v3_target.id)).publication_version == 3

    second = await _create_campaign(world, publication_version=4)
    await _service().launch_canary(second)
    v4_target = (await _targets(second))[0]
    await _run_revision(v4_target.id)
    await _service().approve_canary(second, actor="pytest")

    real_create = fakes.notion.create_page
    crashed: list[str] = []

    def _create_then_die(parent_id, title, children=None):
        page = real_create(parent_id, title, children=children)
        crashed.append(page["id"])
        raise TimeoutError("connection reset after Notion created the page")

    fakes.notion.create_page = _create_then_die
    assert await _publisher(fakes).run_once() is True
    fakes.notion.create_page = real_create

    assert crashed, "the crash never happened — the test proved nothing"
    interrupted = await _target(v4_target.id)
    assert interrupted.status == "publication_failed"
    assert interrupted.publication_version == 4, (
        "the version is reserved before the remote write and is never reused")
    assert interrupted.notion_page_id is None, (
        "the page id never reached the database — that IS the crash window")

    # The retry-due backoff is in the future; an operator retry is the
    # documented way to bring it forward, and it must make no model call.
    generated_before = list(fakes.generated)
    await _service().retry_publication(v4_target.id)
    await _drain_publisher(fakes)

    published = await _target(v4_target.id)
    assert published.status == "published"
    assert published.publication_version == 4, "the reserved version was re-used"
    assert published.notion_page_id == crashed[0], (
        "the retry must ADOPT the page the crashed attempt created, not mint a "
        "second one")
    assert fakes.generated == generated_before, (
        "a publication retry re-DELIVERS; it must never re-generate")

    titles = fakes.notion.child_titles(world.notion.lesson)
    assert titles.count("Homework V4") == 1, f"duplicate V4 pages: {titles}"
    assert titles == ["Homework", "Homework V3", "Homework V4"]
    marker = decode_revision_marker(fakes.notion.blocks[published.notion_page_id])
    assert marker is not None and marker.publication_version == 4
    assert marker.toc_entry_id == world.toc_entry_id


# ══════════════ 11. two failures, two different retries ══════════════════


async def test_a_generation_failure_retries_through_the_generation_path(
    world, fakes, monkeypatch
):
    """Item 11a. A hard phase failure fails the job and the target; the
    operator's generation retry re-runs the SAME snapshot and phase plan and
    then publishes normally."""
    from app.services import pipeline

    async def _boom(**kw):
        raise RuntimeError("the provider is on fire")

    monkeypatch.setattr(pipeline.agent, "run_phase_prompt", _boom)
    monkeypatch.setattr(pipeline, "_failover_chain", lambda p: [p])

    campaign_id = await _create_campaign(world)
    await _service().launch_canary(campaign_id)
    target = (await _targets(campaign_id))[0]
    job_id = await _run_revision(target.id)

    assert (await _job(job_id)).status == "failed"
    assert (await _target(target.id)).status == "generation_failed"
    assert await _rolled_up(campaign_id) == "attention_required"

    monkeypatch.setattr(pipeline.agent, "run_phase_prompt", fakes.run_phase_prompt)
    await _service().retry_generation(target.id)
    assert (await _target(target.id)).status == "generating"
    assert await _revision_job_id(target.id) == job_id, (
        "a retry re-runs the EXISTING revision job; a new one would re-copy "
        "the snapshot and re-reserve the lineage")
    assert (await _job(job_id)).status == "pending"

    await _run_revision(target.id)
    assert (await _target(target.id)).status == "awaiting_canary_approval"
    await _service().approve_canary(campaign_id, actor="pytest")
    await _drain_publisher(fakes)
    assert (await _target(target.id)).status == "published"
    assert await _rolled_up(campaign_id) == "completed"


async def test_a_publication_failure_retries_delivery_and_never_regenerates(
    world, fakes
):
    """Item 11b. Delivery fails; the revision is NOT re-run. The retry keeps the
    same reserved version, the same revision job and makes no model call."""
    campaign_id = await _create_campaign(world)
    await _service().launch_canary(campaign_id)
    target = (await _targets(campaign_id))[0]
    job_id = await _run_revision(target.id)
    await _service().approve_canary(campaign_id, actor="pytest")

    with _notion_down(fakes, RuntimeError("notion 503")):
        assert await _publisher(fakes).run_once() is True

    failed = await _target(target.id)
    assert failed.status == "publication_failed"
    assert failed.publication_version == 3
    assert failed.publication_attempts == 1
    assert failed.publication_last_error and "notion 503" in failed.publication_last_error
    assert failed.publication_next_attempt_at is not None, (
        "a transient delivery failure keeps its automatic backoff")
    assert await _rolled_up(campaign_id) == "attention_required"

    generated_before = list(fakes.generated)
    retried = await _service().retry_publication(target.id)
    assert retried.status == "publication_pending"
    assert retried.publication_version == 3, "the reserved version must be kept"
    assert retried.publication_next_attempt_at is None
    assert retried.publication_last_error is None
    assert await _revision_job_id(target.id) == job_id

    await _drain_publisher(fakes)
    assert (await _target(target.id)).status == "published"
    assert fakes.generated == generated_before, (
        "a generated revision is never regenerated because Notion delivery "
        "failed — that is the whole point of separating the two states")
    assert await _rolled_up(campaign_id) == "completed"


@contextmanager
def _notion_down(fakes, exc):
    """Notion refuses the page enumeration for the duration of the block.

    Shadows the bound method on the FakeNotion INSTANCE and deletes the shadow
    afterwards, so the class method is restored exactly (re-assigning a bound
    method back would leave a permanent instance attribute).
    """
    def _hook(*_args, **_kwargs):
        raise exc

    fakes.notion.get_child_pages = _hook
    try:
        yield
    finally:
        del fakes.notion.get_child_pages


async def test_an_unretryable_collision_parks_for_an_operator_without_backoff(
    world, fakes
):
    """Item 11c. A same-title page we cannot prove is ours is a human decision:
    it is never cleared, and it is not handed back to the automatic sweep."""
    from app.services.notion_versioned_homework import version_page_title

    campaign_id = await _create_campaign(world)
    await _service().launch_canary(campaign_id)
    target = (await _targets(campaign_id))[0]
    await _run_revision(target.id)
    await _service().approve_canary(campaign_id, actor="pytest")

    # Somebody else's `Homework V3`, with no marker of ours.
    impostor = fakes.notion.add_page(
        world.notion.lesson, version_page_title(3),
        [{"type": "paragraph", "paragraph": {
            "rich_text": [{"type": "text", "text": {"content": "not ours"}}]}}])
    impostor_before = copy.deepcopy(fakes.notion.blocks[impostor])

    assert await _publisher(fakes).run_once() is True

    parked = await _target(target.id)
    assert parked.status == "publication_failed"
    assert "collision" in (parked.publication_last_error or "")
    assert parked.publication_next_attempt_at is None, (
        "retrying cannot change the answer, so it must not be re-swept")
    assert fakes.notion.blocks[impostor] == impostor_before, (
        "a page we cannot prove is ours is NEVER cleared or overwritten")
    assert await _publisher(fakes).run_once() is False

    # The operator's documented second exit: abandon. The reserved version is
    # permanently consumed, so the next regeneration must use V4 or higher.
    abandoned = await _service().abandon(
        target.id, actor="pytest", reason="manual Notion cleanup required")
    assert abandoned.status == "abandoned"
    assert abandoned.publication_version == 3, (
        "abandonment preserves the consumed version — it is never released")
    assert abandoned.terminal_at is not None
    assert await _rolled_up(campaign_id) == "completed_with_abandonments"


# ══════════════ 12. cancellation at every non-terminal state ═════════════


async def _world_at(state: str, fakes):
    """A fresh one-lesson world whose single target sits in ``state``.

    Its own book per state: ``uq_regeneration_targets_active_lineage`` allows
    only one non-terminal target per (lesson, language), which is exactly the
    invariant these states are testing.
    """
    seeded = await _seed_world(languages=("uz",))
    seeded.notion = _seed_notion_v1(
        fakes.notion, subject_page=SUBJECT_PAGE_UZ,
        lesson_title="Kvadrat tenglamalar")
    await _stamp_lesson_page(seeded.toc_entry_id, seeded.notion.lesson)
    campaign_id = await _create_campaign(seeded)
    seeded.campaign_id = campaign_id

    if state == "planned":
        pass
    elif state in ("generating", "generating_running"):
        await _service().launch_canary(campaign_id)
        if state == "generating_running":
            target = (await _targets(campaign_id))[0]
            await _set_job_status(await _revision_job_id(target.id), "running")
    elif state == "generation_failed":
        await _service().launch_canary(campaign_id)
        target = (await _targets(campaign_id))[0]
        job_id = await _revision_job_id(target.id)
        await _set_job_status(job_id, "failed")
        await _reconcile(job_id)
    elif state == "awaiting_canary_approval":
        await _service().launch_canary(campaign_id)
        await _run_revision((await _targets(campaign_id))[0].id)
    elif state in ("publication_pending", "publishing", "publication_failed"):
        await _service().launch_canary(campaign_id)
        await _run_revision((await _targets(campaign_id))[0].id)
        await _service().approve_canary(campaign_id, actor="pytest")
        if state == "publishing":
            await _claim_for_publication((await _targets(campaign_id))[0].id)
        elif state == "publication_failed":
            with _notion_down(fakes, RuntimeError("notion 503")):
                await _publisher(fakes).run_once()
    else:  # pragma: no cover - a typo in the parametrisation
        raise AssertionError(f"unknown state {state!r}")

    target = (await _targets(campaign_id))[0]
    expected = "generating" if state == "generating_running" else state
    assert target.status == expected, (
        f"the world could not be driven to {expected!r} (got {target.status!r})")
    return seeded, target


async def _claim_for_publication(target_id):
    """Take the durable publication claim without resolving it — the real
    `publishing` state, mid-flight."""
    from app.db import SessionLocal
    from app.models.base import _utcnow
    from app.repositories import regeneration_targets as targets_repo

    async with SessionLocal() as session:
        claim = await targets_repo.claim_next_publication(
            session, now=_utcnow(), lease_seconds=300)
        await session.commit()
    assert claim is not None and claim.target_id == target_id
    return claim


# (state, the target status a cancel must produce, is it terminal yet)
_CANCEL_TABLE = (
    ("planned", "abandoned", True),
    ("generating", "abandoned", True),
    ("generating_running", "generating", False),
    ("awaiting_canary_approval", "abandoned", True),
    ("generation_failed", "abandoned", True),
    ("publication_pending", "abandoned", True),
    ("publishing", "publishing", False),
    ("publication_failed", "abandoned", True),
)


@pytest.mark.parametrize("state,expected,terminal", _CANCEL_TABLE)
async def test_cancellation_converges_every_nonterminal_target_state(
    fakes, state, expected, terminal
):
    """Item 12. The Task-7 cancellation table, driven through the real service.

    Two states deliberately do NOT go terminal immediately, and both for the
    same reason — an outcome that is already in flight is never revoked by
    deleting state:

    * a RUNNING revision converges on the worker's next heartbeat;
    * a `publishing` target's remote request has an unknown outcome, so only
      the abandon INTENT is recorded and the publisher's own claim resolves it.
    """
    seeded, target = await _world_at(state, fakes)
    try:
        campaign_id = seeded.campaign_id
        await _service().cancel(campaign_id, actor="pytest", reason="stop now")

        refreshed = await _target(target.id)
        assert refreshed.status == expected
        assert (refreshed.terminal_at is not None) is terminal
        assert refreshed.abandon_requested_at is not None, (
            "every visited target must carry the cancellation intent, even the "
            "ones that cannot converge yet")

        campaign = await _campaign(campaign_id)
        assert campaign.cancel_requested_at is not None
        if terminal:
            assert campaign.status == "cancelled"
        else:
            assert campaign.status != "cancelled", (
                "a terminal campaign must never hide a live target — the "
                "publication gate turns that into a check violation in the "
                "middle of an irreversible delivery")
    finally:
        await _purge_world(seeded)


async def test_a_cancelled_running_revision_closes_only_once_the_job_stops(
    world, fakes
):
    """Item 12's convergence half, end to end: the campaign stays open until the
    in-flight job actually stops, and closes by itself afterwards."""
    campaign_id = await _create_campaign(world)
    await _service().launch_canary(campaign_id)
    target = (await _targets(campaign_id))[0]
    job_id = await _revision_job_id(target.id)
    await _set_job_status(job_id, "running")

    await _service().cancel(campaign_id, actor="pytest", reason="operator stop")
    assert (await _target(target.id)).status == "generating"
    assert (await _job(job_id)).status == "cancelling", (
        "a running job is stopped through the existing safe cancellation path")
    assert (await _campaign(campaign_id)).status != "cancelled"

    # The worker finishes cancelling and reconciles, exactly as `_execute_job`
    # does in its `finally`.
    await _set_job_status(job_id, "cancelled")
    await _reconcile(job_id)

    closed = await _target(target.id)
    assert closed.status == "abandoned"
    assert closed.terminal_at is not None
    assert await _rolled_up(campaign_id) == "cancelled"
    assert closed.publication_version is None, (
        "generation abandonment consumes no publication version")


async def test_a_publishing_target_cancelled_mid_flight_lands_on_the_real_outcome(
    world, fakes
):
    """Item 12's hardest case. A cancellation arriving while the Notion request
    is in flight must not decide the outcome: a write that SUCCEEDED lands
    `published` (a target reported abandoned over a live page would be a lie),
    and only a write that FAILED lands terminal `abandoned`."""
    campaign_id = await _create_campaign(world)
    await _service().launch_canary(campaign_id)
    target = (await _targets(campaign_id))[0]
    await _run_revision(target.id)
    await _service().approve_canary(campaign_id, actor="pytest")

    publisher = _publisher(fakes)
    real_deliver = publisher._deliver
    cancelled: list[bool] = []

    async def _cancel_after_the_remote_write(claim, inputs):
        """The remote write SUCCEEDS, then the cancellation lands — before the
        publisher has written a single row about it. That window is the whole
        point: our own state says nothing yet, and the page already exists."""
        page_id = await real_deliver(claim, inputs)
        cancelled.append(True)
        await _service().cancel(
            campaign_id, actor="pytest", reason="stop mid-delivery")
        return page_id

    publisher._deliver = _cancel_after_the_remote_write
    assert await publisher.run_once() is True
    assert cancelled, "the cancellation never landed mid-delivery"

    published = await _target(target.id)
    assert published.status == "published", (
        "the page EXISTS; reporting it abandoned would be a lie the operator "
        "cannot act on")
    assert published.notion_page_id is not None
    assert published.publication_version == 3
    # `roll_up_campaign` rule 1: a cancelled campaign that nevertheless
    # DELIVERED reports `completed`, not `cancelled` — the same reason the
    # target itself lands `published`. Reporting a cancellation over a live
    # Notion page would misdescribe what is actually out there.
    assert await _rolled_up(campaign_id) == "completed"
    assert (await _campaign(campaign_id)).cancel_requested_at is not None, (
        "the cancellation is still audited even though it lost the race")


# ══════════════ closure: no terminal campaign hides live work ════════════


async def test_a_terminal_campaign_status_is_refused_over_a_live_target(
    world, fakes
):
    """Item 12's closing promise, asserted at the guard itself: the repository's
    compare-and-set knows nothing about targets, so this is the only thing
    standing between an administrative override and a `check_violation` in the
    middle of an irreversible delivery."""
    from app.services.regeneration_campaign import TerminalCampaignWithLiveTargets

    campaign_id = await _create_campaign(world)
    await _service().launch_canary(campaign_id)
    target = (await _targets(campaign_id))[0]
    await _run_revision(target.id)
    await _service().approve_canary(campaign_id, actor="pytest")
    await _claim_for_publication(target.id)
    assert (await _target(target.id)).status == "publishing"

    with pytest.raises(TerminalCampaignWithLiveTargets):
        await _service().set_campaign_status(campaign_id, "completed")
    assert (await _campaign(campaign_id)).status != "completed"


async def test_rejecting_the_canary_consumes_no_version_and_writes_no_page(
    world, fakes
):
    """The pre-approval exit. Rejection abandons every canary and planned
    target, publishes nothing, and burns no publication version."""
    campaign_id = await _create_campaign(world)
    await _service().launch_canary(campaign_id)
    target = (await _targets(campaign_id))[0]
    await _run_revision(target.id)

    rejected = await _service().reject_canary(
        campaign_id, actor="pytest", reason="quality is not there yet")
    assert rejected.status == "rejected"
    assert rejected.rejected_reason == "quality is not there yet"

    closed = await _target(target.id)
    assert closed.status == "abandoned"
    assert closed.publication_version is None, "a rejected canary consumes no version"
    assert closed.notion_page_id is None
    assert not any(call[0] in {
        "create_page", "append_block_children", "delete_block",
        "clear_content_blocks", "upload_bytes",
    } for call in fakes.notion.calls)
    assert fakes.notion.child_titles(world.notion.lesson) == ["Homework"], (
        "no versioned page may exist after a rejection")
    assert await _publisher(fakes).run_once() is False

    # And the lineage is free again: the terminal target released it.
    reopened = await _create_campaign(world)
    assert reopened != campaign_id
