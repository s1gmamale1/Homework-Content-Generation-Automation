"""The versioned-homework regeneration API.

A separate router and namespace, deliberately not folded into the Fleet
endpoints: an operator is either **Generating** or **Regenerating**, and the two
must never be reachable through the same control.

Four rules shape everything below.

**Authentication is router-level and unconditional.** ``app/api/v1/__init__.py``
mounts this router with ``dependencies=[Depends(get_current_user)]``, exactly
like books/batch/jobs, and FastAPI evaluates an ``include_router`` dependency
BEFORE the sub-router's own — so an anonymous request fails authentication
without ever learning whether the feature exists. The general operator
dependency is used, not the SA-key-strict one: this is an operator workflow,
and the SSE/query-token form belongs to it.

**The feature gate is a 404, not a 403.** With ``REGENERATION_ENABLED=false``
every route is absent, so a stale UI cannot mutate a hidden feature. The
explicit destination review, campaign-create and canary routes require a
readable Notion destination: that snapshot is part of the frozen campaign and
is revalidated immediately before paid work. The two routes that hand work to
the publication loop (``approve``, ``retry-publication``) additionally require
that delivery is actually POSSIBLE — the
``REGENERATION_PUBLISHER_ENABLED`` flag AND a usable Notion destination, which
are exactly the conditions under which `main.py` starts the loop — because
approving a campaign into a queue nobody serves is a lie, and it is an expensive
one: approval releases
every target, and each release ends in a `Homework V{n}` number reserved
forever. So it is a structured 409 instead, naming which of the two is missing.
The DB-only estimate remains available with delivery dark and makes no Notion
call. No campaign can be frozen or launched until the destinations for its
exact requested version have been reviewed.

**This router owns no state machine.** Every transition belongs to
``RegenerationCampaignService``; the routes translate its refusals into status
codes and its rows into the report shape. In particular the retired-model check,
the preflight and the active-lineage rule stay service-owned — the API only
renders them. It never claims a publication: the per-target claim
primitive on ``regeneration_targets`` inverts the campaign→target lock order
and is the shape that produced a real ``DeadlockDetectedError``, so claiming
stays where it belongs — the publisher loop's own wait-free sweep.

**A report is a read, and reads must not stop the publisher.**
``reconcile_terminal_revision_jobs`` runs on every report and every mutation —
it is the operator-facing crash-repair path and it takes no campaign lock
unless a target genuinely needs repair. ``roll_up`` is different: it holds the
campaign ``FOR UPDATE`` for its whole reconcile loop, which makes the
publisher's wait-free claim SKIP that campaign for the duration. So the
report-driven rollup is debounced per campaign to at most once per
``REGENERATION_PUBLISHER_INTERVAL_SECONDS``; a mutation's rollup is
service-owned and never debounced.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Optional, Sequence, TypedDict
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import Row, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import SessionLocal, get_session
from app.models.agent_usage import AgentUsage
from app.models.book import Book
from app.models.homework_job import HomeworkJob
from app.models.phase_output import PhaseOutput
from app.models.regeneration_campaign import RegenerationCampaign
from app.models.regeneration_target import RegenerationTarget
from app.models.toc_entry import TOCEntry
from app.repositories import launch_defaults as launch_defaults_repo
from app.repositories import regeneration_campaigns as campaigns_repo
from app.repositories import regeneration_sources as sources_repo
from app.repositories import regeneration_targets as targets_repo
from app.schemas import regeneration as out
from app.schemas.regeneration_contract import (
    LaunchDefaultsSnapshot,
    resolve_launch_contract,
)
from app.services import (
    agent_models,
    code_version,
    regeneration_destination,
    regeneration_executability,
    regeneration_job_state,
    regeneration_notion_readiness,
    regeneration_publisher,
    storage,
)
from app.services import regeneration_discovery as discovery
from app.services.regeneration_campaign import (
    ActiveLineageConflict,
    CampaignError,
    CampaignNotFound,
    CampaignSelection,
    CanaryNotReviewable,
    CreateCampaignSpec,
    DestinationResolutionBlocked,
    DestinationReviewChanged,
    IllegalCampaignAction,
    IllegalTargetAction,
    NoEligibleTargets,
    NonApiTransport,
    PartialWaveRelease,
    PreflightBlocked,
    RegenerationCampaignService,
    RequestedPublicationVersionConflict,
    RetiredModelRefusal,
    SelectionTooLarge,
    SelectionDiscoveryTooLarge,
    TargetNotFound,
    TerminalCampaignWithLiveTargets,
    UnboundedSelection,
    WorkerPreflightBlocked,
    require_api_transport,
    require_bounded_selection,
    require_live_models,
    require_selection_within_cap,
)
from app.services.regeneration_estimator import estimate_regeneration
from app.services.regeneration_states import CAMPAIGN_STATUSES
from app.services.regeneration_planner import (
    ExclusionAcknowledgementRequired,
    UnknownPhaseError,
    build_phase_plan,
)


def require_regeneration_enabled() -> None:
    """Hide the whole feature when the master flag is off.

    404, not 403: a stale UI must not be able to tell that the routes exist,
    let alone mutate through them.
    """
    if not settings.regeneration_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")


def require_publication_available() -> None:
    """Refuse the two routes that promise automatic delivery unless something
    can actually carry it out.

    Two independent reasons, and the operator has to be told WHICH — they live
    in different files. The flag is answered first because it is the switch the
    rollout turns first (runbook §3b); the Notion destination is answered second
    and is the one that used to be invisible here.

    Refusing the destination case BEFORE approval is the whole point: approval
    releases every target to the publication loop, and each release ends in a
    reserved `Homework V{n}` number that is spent forever. Discovering the
    missing credential at delivery time — where it used to surface — costs a
    version per target for deliveries that never had a chance.
    """
    if not settings.regeneration_publisher_enabled:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "error": "publisher_disabled",
                "message": (
                    "automatic publication is unavailable: "
                    "REGENERATION_PUBLISHER_ENABLED is off, so no publication "
                    "loop is running and this action would queue delivery work "
                    "nobody serves. Enable the publisher first."
                ),
            },
        )
    unavailable = regeneration_publisher.publication_unavailable_reason()
    if unavailable is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "error": "notion_unavailable",
                "message": (
                    f"Notion publication is unavailable: {unavailable}. Set "
                    "NOTION_ENABLED and a valid NOTION_API_KEY on the head and "
                    "restart it — the publication loop is not running either. "
                    "Proceeding would reserve a version number for every target "
                    "and deliver none of them."
                ),
            },
        )


router = APIRouter(
    prefix="/regeneration",
    tags=["regeneration"],
    dependencies=[Depends(require_regeneration_enabled)],
)

# ─── rollup debounce (see the module docstring) ───────────────────────────
#: campaign id → the monotonic time of its last REPORT-driven rollup.
_ROLLUP_DEBOUNCE: dict[UUID, float] = {}
#: Hard ceiling so a long-lived head cannot accumulate one entry per campaign
#: it has ever served. Expired entries are pruned first; the cap is the
#: backstop for a pathological burst inside one window.
_DEBOUNCE_MAX_ENTRIES = 1024
#: Indirection so tests can drive the window without sleeping.
_clock = time.monotonic
#: Indirection so a route test can price without a database.
_estimate_regeneration = estimate_regeneration
_check_active_workers = regeneration_executability.check_active_workers
_resolve_destinations = regeneration_destination.resolve_destinations
_publication_version_conflicts = sources_repo.publication_version_conflicts


def reset_rollup_debounce() -> None:
    """Clear the debounce map (tests; also a safe no-op in production)."""
    _ROLLUP_DEBOUNCE.clear()


def _claim_rollup_slot(campaign_id: UUID) -> bool:
    """May THIS request run a report-driven ``roll_up`` for this campaign?

    Check-and-record with no ``await`` in between, so two concurrent polls on
    the event loop cannot both win the slot. Per process by design: the head is
    where reports are served, and a second process holding its own map still
    honours the same per-campaign rate.
    """
    window = max(1, int(settings.regeneration_publisher_interval_seconds))
    now = _clock()
    for key in [k for k, seen in _ROLLUP_DEBOUNCE.items() if now - seen >= window]:
        _ROLLUP_DEBOUNCE.pop(key, None)
    last = _ROLLUP_DEBOUNCE.get(campaign_id)
    if last is not None and now - last < window:
        return False
    if len(_ROLLUP_DEBOUNCE) >= _DEBOUNCE_MAX_ENTRIES:
        _ROLLUP_DEBOUNCE.pop(min(_ROLLUP_DEBOUNCE, key=_ROLLUP_DEBOUNCE.get), None)
    _ROLLUP_DEBOUNCE[campaign_id] = now
    return True


def _service() -> RegenerationCampaignService:
    """The campaign state machine. It owns its own sessions (``create_revision_job``
    commits internally), so it is never handed the request session."""
    return RegenerationCampaignService()


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _reconcile(session: AsyncSession) -> int:
    """Carry every terminal revision job onto its target.

    The operator-facing crash-repair path: a worker that died between its job
    commit and its target update leaves a target visibly ``generating`` over a
    finished job, and a report that did not repair it would show — and let an
    operator act on — a state that is not true. It commits per target and takes
    a campaign lock only for a target it actually moves.
    """
    return await regeneration_job_state.reconcile_terminal_revision_jobs(session)


async def _reconcile_closed() -> int:
    """Run crash repair in its own short session before remote service work."""
    async with SessionLocal() as session:
        repaired = await _reconcile(session)
        await session.commit()
        return repaired


# ═══════════════════════════ error mapping ═══════════════════════════════


def _conflict(error: str, message: str, **extra) -> HTTPException:
    return HTTPException(
        status.HTTP_409_CONFLICT, {"error": error, "message": message, **extra}
    )


def _unprocessable(error: str, message: str, **extra) -> HTTPException:
    # The literal, not `status.HTTP_422_*`: Starlette renamed that constant and
    # deprecated the old spelling, and the number is the stable thing.
    return HTTPException(422, {"error": error, "message": message, **extra})


def _retired(exc: RetiredModelRefusal) -> HTTPException:
    return _conflict(
        "retired_model",
        str(exc),
        retired=[
            {"role": role, "provider": provider, "model": model}
            for role, provider, model in exc.retired
        ],
    )


def _lineage_conflict(exc: ActiveLineageConflict) -> HTTPException:
    return _conflict(
        "active_lineage_conflict",
        str(exc),
        count=len(exc.lineages),
        campaign_ids=[str(campaign_id) for campaign_id in exc.campaign_ids],
        lineages=[
            {"toc_entry_id": str(toc), "output_language": language}
            for toc, language in exc.lineages
        ],
    )


def _preflight_conflict(exc: PreflightBlocked) -> HTTPException:
    """ONE 409 listing every blocked lesson — an operator fixes the
    configuration once, and a first-failure-only response would send them
    round the loop per lesson."""
    return _conflict(
        "preflight_blocked",
        str(exc),
        count=len(exc.failures),
        failures=[
            out.PreflightFailureOut.from_failure(f).model_dump(mode="json")
            for f in exc.failures
        ],
    )


def _translate_campaign_error(exc: CampaignError, *, request_shaped: bool):
    """Map a service refusal onto its HTTP shape.

    ``request_shaped`` distinguishes a draft the caller just submitted (an
    unprocessable request) from a stored contract that has gone stale (a state
    conflict on an existing campaign).
    """
    if isinstance(exc, (CampaignNotFound, TargetNotFound)):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    if isinstance(exc, ActiveLineageConflict):
        return _lineage_conflict(exc)
    if isinstance(exc, RequestedPublicationVersionConflict):
        return _conflict(
            "publication_version_conflict",
            str(exc),
            conflicts=[
                {
                    "toc_entry_id": str(item.toc_entry_id),
                    "output_language": item.output_language,
                    "requested_version": item.requested_version,
                    "existing_version": item.existing_version,
                    "reason": item.reason,
                }
                for item in exc.conflicts
            ],
        )
    if isinstance(exc, DestinationReviewChanged):
        return _conflict("destination_review_changed", str(exc))
    if isinstance(exc, DestinationResolutionBlocked):
        return _conflict("destination_resolution_blocked", str(exc))
    if isinstance(exc, WorkerPreflightBlocked):
        result = exc.result
        return _conflict(
            "worker_not_executable",
            str(exc),
            workers_online=result.workers_online,
            compatible_worker_ids=list(result.compatible_worker_ids),
            required_api_providers=list(result.required_api_providers),
            fleet_api_paused=result.fleet_api_paused,
        )
    if isinstance(exc, PreflightBlocked):
        return _preflight_conflict(exc)
    if isinstance(exc, RetiredModelRefusal):
        return _retired(exc)
    if isinstance(exc, NoEligibleTargets):
        return _conflict(
            "no_eligible_targets",
            str(exc),
            candidates=[
                out.IneligibleLineageOut.from_candidate(c).model_dump(mode="json")
                for c in exc.candidates
            ],
        )
    if isinstance(exc, NonApiTransport):
        detail = dict(
            offenders=[
                {"field": field, "transport": value} for field, value in exc.offenders
            ]
        )
        return (
            _unprocessable("non_api_transport", str(exc), **detail)
            if request_shaped
            else _conflict("non_api_transport", str(exc), **detail)
        )
    if isinstance(exc, UnboundedSelection):
        # A property of the REQUEST on either path — an unbounded selection is
        # not a state an existing campaign can be in.
        return _unprocessable("unbounded_selection", str(exc))
    if isinstance(exc, SelectionTooLarge):
        return _unprocessable(
            "selection_too_large", str(exc), count=exc.count, maximum=exc.maximum
        )
    if isinstance(exc, SelectionDiscoveryTooLarge):
        return _unprocessable(
            "selection_discovery_too_large",
            str(exc),
            count_at_least=exc.count_at_least,
            maximum=exc.maximum,
        )
    if isinstance(exc, CanaryNotReviewable):
        # BEFORE the `IllegalCampaignAction` branch it would otherwise fall
        # into: the operator's next move is specific (retry or abandon the
        # blocked canaries, then approve), so it gets its own code and names
        # them, rather than a generic "illegal campaign state".
        return _conflict(
            "canary_not_reviewable",
            str(exc),
            blockers=list(exc.blockers),
            canary_count=exc.total,
            reason_code=exc.reason_code,
            remedy=exc.remedy,
        )
    if isinstance(exc, IllegalTargetAction):
        return _conflict("illegal_target_state", str(exc))
    if isinstance(exc, (IllegalCampaignAction, TerminalCampaignWithLiveTargets)):
        return _conflict("illegal_campaign_state", str(exc))
    return _conflict("campaign_error", str(exc))


#: The environment variable a build bakes its own commit into. The runtime
#: image declares it as an ``ARG`` and exports it as ``ENV``; CI binds it to
#: ``github.sha``. See ``Dockerfile`` and ``.github/workflows/docker-publish.yml``.
APP_GIT_REVISION_ENV = "APP_GIT_REVISION"

#: ``regeneration_campaigns.app_git_revision`` is ``String(64)`` — and 64 is
#: exactly a full SHA-256 git object name, so no real revision is truncated.
_MAX_APP_GIT_REVISION = 64


def _normalize_revision(value: Optional[str]) -> Optional[str]:
    """One normalization for all three sources: trim, treat whitespace-only as
    ABSENT, and cap at the audit column's width.

    Blank-is-absent is load-bearing, not tidiness. ``ARG APP_GIT_REVISION=""``
    with no ``--build-arg`` reaches the process as an empty string, so a
    present-but-meaningless variable would otherwise shadow a perfectly good
    git checkout — and store ``""`` in a column that can never be corrected.
    The cap is the same shape of protection at the other end: only the request
    field is bounded by the schema, so an over-long build arg would surface as
    a 500 at INSERT on a request that had already validated.
    """
    text = (value or "").strip()
    return text[:_MAX_APP_GIT_REVISION] if text else None


def _resolve_app_git_revision(requested: Optional[str]) -> str:
    """Which application revision is this campaign being created under?

    Design §6: every campaign records it, because "which code produced this
    packet?" is the first question asked of a regenerated lesson, and the row
    is immutable once written — there is no later chance to fill it in.

    Three sources, in descending order of authority.

    1. An EXPLICIT request value. The field is exposed on purpose, so an
       operator who knows the deployed revision better than the artifact does
       — a mis-baked image, a hotfix — can say so and not be overruled.
    2. The BUILD's own statement, ``APP_GIT_REVISION`` in the environment.
       This is the production source: the deployed head is a container, and
       that container has no git — the build context excludes ``.git`` and the
       runtime image installs no git binary — so the build stamping the commit
       it built is the only thing that still knows it.
    3. The process's own checkout, ``code_version.GIT_SHA``. Last, because a
       checkout merely being present is not evidence of what was deployed: a
       mounted source tree drifts from the code already imported. It remains
       the right answer for a bare-metal head run straight out of git.

    The SPA posts ``app_git_revision: null`` by design, so on a container
    everything rests on (2) — without it every containerised create would be
    the refusal below, which is what made this chain necessary.

    None of the three is a refusal, not a fallback to NULL. Storing "unknown"
    in an audit column is worse than not creating the campaign: it is
    indistinguishable from a campaign whose provenance was never asked for.
    """
    explicit = _normalize_revision(requested)
    if explicit:
        return explicit
    baked = _normalize_revision(os.environ.get(APP_GIT_REVISION_ENV))
    if baked:
        return baked
    detected = _normalize_revision(code_version.GIT_SHA)
    if detected:
        return detected
    raise _conflict(
        "app_git_revision_unavailable",
        "cannot record which application revision this campaign would be "
        "created under: no revision was sent, the APP_GIT_REVISION "
        "environment variable is unset or blank, and this process reports no "
        "git revision (it is running from a build or container without a .git "
        "directory). A campaign is an audit record and is never created with "
        "unknown provenance. Fix it at whichever level owns the deployment: "
        "rebuild the image with --build-arg APP_GIT_REVISION=<sha> (CI passes "
        "the built commit automatically), or set APP_GIT_REVISION=<sha> in the "
        "running container's environment, or run the API from a git checkout, "
        "or send app_git_revision in this request with the revision that was "
        "deployed.",
    )


def _translate_plan_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ExclusionAcknowledgementRequired):
        return _unprocessable("exclusion_acknowledgement_required", str(exc))
    if isinstance(exc, UnknownPhaseError):
        return _unprocessable("unknown_phase", str(exc))
    if isinstance(exc, KeyError):
        return _unprocessable("unknown_subject", f"unsupported subject: {exc}")
    return _unprocessable("invalid_phase_selection", str(exc))


# ═══════════════════════════ gather helpers ══════════════════════════════
#
# Two rules hold for every helper below.
#
# A gather that returns ORM ENTITIES reads with ``populate_existing=True``,
# exactly as the repositories do. ``SessionLocal`` is ``expire_on_commit=False``
# and ``_reconcile`` runs on the REQUEST session before every report and every
# mutation, so an entity the request already holds would otherwise be handed
# back as it was BEFORE the service — which owns its own session — moved it.
#
# A gather that only feeds counts reads COLUMNS, not entities: a report is
# polled, and it must never drag a phase's generated markdown across the wire
# to throw it away.


async def _load_campaign(
    session: AsyncSession, campaign_id: UUID
) -> RegenerationCampaign:
    campaign = await campaigns_repo.get_campaign(session, campaign_id)
    if campaign is None:
        raise CampaignNotFound(f"regeneration campaign {campaign_id} not found")
    return campaign


async def _load_target(session: AsyncSession, target_id: UUID) -> RegenerationTarget:
    target = await session.scalar(
        select(RegenerationTarget)
        .where(RegenerationTarget.id == target_id)
        .execution_options(populate_existing=True)
    )
    if target is None:
        raise TargetNotFound(f"regeneration target {target_id} not found")
    return target


async def _current_target_statuses(
    session: AsyncSession, target_ids: Sequence[UUID]
) -> dict[UUID, str]:
    """The targets' ACTUAL statuses right now.

    ``PartialWaveRelease``'s message asserts every failed release is
    ``generation_failed``; a terminal race can leave one ``abandoned`` (or even
    published), so the report reads the rows instead of repeating the claim.
    """
    if not target_ids:
        return {}
    rows = await session.execute(
        select(RegenerationTarget.id, RegenerationTarget.status).where(
            RegenerationTarget.id.in_(list(target_ids))
        )
    )
    return {row[0]: row[1] for row in rows.all()}


async def _revision_jobs(
    session: AsyncSession, target_ids: Sequence[UUID]
) -> dict[UUID, HomeworkJob]:
    """The revision job behind each target, as the rows are NOW.

    ``populate_existing`` for the same reason ``campaigns_repo.get_campaign``
    has it, and it is load-bearing here: ``_reconcile`` loads exactly these
    jobs into the REQUEST session before every report and every mutation,
    the transition that follows is the SERVICE's — a different session — and
    ``SessionLocal`` is ``expire_on_commit=False``. Without the refresh the
    identity map answers the gather with the job as it was before the action,
    so a retry would report the failure it just replaced.
    """
    if not target_ids:
        return {}
    rows = await session.execute(
        select(HomeworkJob)
        .where(HomeworkJob.regeneration_target_id.in_(list(target_ids)))
        .execution_options(populate_existing=True)
    )
    return {job.regeneration_target_id: job for job in rows.scalars().all()}


async def _phase_rows(
    session: AsyncSession, job_ids: Sequence[UUID]
) -> dict[UUID, list[Row]]:
    """Provenance and judge/solver verdicts per job — and NOTHING else.

    A projection, not the entities: a report counts copied-vs-regenerated and
    tallies the two verdict columns, and every campaign poll would otherwise
    drag ``output_md`` and ``content_json`` — the whole generated snapshot,
    for every phase of every lesson in the campaign — across the wire to be
    thrown away. The schema reads these rows through ``getattr``, so a
    ``Row`` serves it exactly as an ORM object did.
    """
    if not job_ids:
        return {}
    rows = await session.execute(
        select(
            PhaseOutput.job_id,
            PhaseOutput.phase_order,
            PhaseOutput.copied_from_phase_output_id,
            PhaseOutput.judge_status,
            PhaseOutput.solver_status,
        )
        .where(PhaseOutput.job_id.in_(list(job_ids)))
        .order_by(PhaseOutput.job_id, PhaseOutput.phase_order)
    )
    grouped: dict[UUID, list[Row]] = {}
    for row in rows.all():
        grouped.setdefault(row.job_id, []).append(row)
    return grouped


async def _usage_rows(
    session: AsyncSession, job_ids: Sequence[UUID]
) -> list[AgentUsage]:
    """Usage attached to this campaign's REVISION jobs and nothing else.

    Copied phases carry no paid row (only the free ``<cache>`` extract marker),
    and the source/V1 job's usage belongs to the run that produced it — either
    one leaking in would double historical spend and destroy the
    estimate-vs-actual comparison the canary gate depends on.
    """
    if not job_ids:
        return []
    rows = await session.execute(
        select(AgentUsage)
        .where(AgentUsage.homework_job_id.in_(list(job_ids)))
        .execution_options(populate_existing=True)
    )
    return list(rows.scalars().all())


async def _source_versions(
    session: AsyncSession, source_job_ids: Sequence[UUID]
) -> dict[UUID, int]:
    """``source job id → the publication version that job represents``.

    A source that is itself a revision carries its own target's version; an
    ordinary completed job is logical V1, which has no row anywhere.
    """
    ids = [job_id for job_id in source_job_ids if job_id is not None]
    if not ids:
        return {}
    rows = await session.execute(
        select(HomeworkJob.id, RegenerationTarget.publication_version)
        .join(
            RegenerationTarget,
            RegenerationTarget.id == HomeworkJob.regeneration_target_id,
        )
        .where(HomeworkJob.id.in_(ids))
    )
    known = {row[0]: row[1] for row in rows.all() if row[1] is not None}
    return {job_id: known.get(job_id, 1) for job_id in ids}


async def _lessons(
    session: AsyncSession, toc_entry_ids: Sequence[UUID]
) -> dict[UUID, out.LessonOut]:
    if not toc_entry_ids:
        return {}
    rows = await session.execute(
        select(TOCEntry)
        .where(TOCEntry.id.in_(list(toc_entry_ids)))
        .execution_options(populate_existing=True)
    )
    return {
        entry.id: out.LessonOut(
            book_id=entry.book_id,
            order_index=entry.order_index,
            section_number=entry.section_number,
            section_title=entry.section_title,
            chapter_title=entry.chapter_title,
        )
        for entry in rows.scalars().all()
    }


async def _campaign_detail(
    session: AsyncSession,
    campaign_id: UUID,
    *,
    now: datetime,
) -> out.CampaignDetailOut:
    """The whole report, in a fixed number of queries.

    A pure gather: the per-request extras (a rollup that refused, a partial
    release's failures) are attached afterwards by :func:`_with_extras`, so
    this stays one shape whether it is serving a report or a mutation.
    """
    campaign = await _load_campaign(session, campaign_id)
    targets = await targets_repo.list_for_campaign(session, campaign_id)
    target_ids = [t.id for t in targets]

    jobs = await _revision_jobs(session, target_ids)
    job_ids = [job.id for job in jobs.values()]
    rows_by_job = await _phase_rows(session, job_ids)
    versions = await _source_versions(session, [t.source_job_id for t in targets])
    lessons = await _lessons(session, [t.toc_entry_id for t in targets])
    usage = await _usage_rows(session, job_ids)

    phase_rows_by_target = {
        target_id: rows_by_job.get(job.id, [])
        for target_id, job in jobs.items()
    }
    source_versions = {
        t.id: versions.get(t.source_job_id)
        for t in targets
        if t.source_job_id is not None
    }
    return out.CampaignDetailOut.build(
        campaign,
        targets,
        now=now,
        jobs_by_target=jobs,
        phase_rows_by_target=phase_rows_by_target,
        source_versions=source_versions,
        lessons_by_target={t.id: lessons.get(t.toc_entry_id) for t in targets
                           if lessons.get(t.toc_entry_id) is not None},
        usage_rows=usage,
        solver_enabled_observed=bool(settings.solver_enabled),
    )


def _with_extras(
    detail: out.CampaignDetailOut,
    *,
    rollup_error: Optional[str] = None,
    released_failures: Sequence[out.WaveFailureOut] = (),
) -> out.CampaignDetailOut:
    """Attach this request's own findings to a gathered report."""
    return detail.model_copy(
        update={
            "rollup_error": rollup_error,
            "released_failures": list(released_failures),
        }
    )


async def _target_report(
    session: AsyncSession, target: RegenerationTarget, *, now: datetime
) -> out.TargetReportOut:
    jobs = await _revision_jobs(session, [target.id])
    job = jobs.get(target.id)
    rows = (await _phase_rows(session, [job.id])).get(job.id, []) if job else []
    versions = await _source_versions(session, [target.source_job_id])
    lessons = await _lessons(session, [target.toc_entry_id])
    return out.TargetReportOut.from_row(
        target,
        now=now,
        revision_job=job,
        source_publication_version=versions.get(target.source_job_id),
        lesson=lessons.get(target.toc_entry_id),
        phase_rows=rows,
    )


class _CampaignIdentity(TypedDict):
    subjects: list[str]
    grades: list[str]
    lesson_count: int
    lesson_title: Optional[str]


async def _list_campaigns(
    session: AsyncSession,
    *,
    statuses: Optional[list[str]],
    limit: int,
    offset: int,
) -> tuple[
    list[RegenerationCampaign],
    dict[UUID, dict[str, int]],
    dict[UUID, _CampaignIdentity],
    int,
]:
    stmt = select(RegenerationCampaign)
    count_stmt = select(func.count()).select_from(RegenerationCampaign)
    if statuses:
        stmt = stmt.where(RegenerationCampaign.status.in_(statuses))
        count_stmt = count_stmt.where(RegenerationCampaign.status.in_(statuses))
    rows = await session.execute(
        stmt.order_by(RegenerationCampaign.created_at.desc())
        .limit(limit)
        .offset(offset)
        .execution_options(populate_existing=True)
    )
    campaigns = list(rows.scalars().all())
    total = int(await session.scalar(count_stmt) or 0)

    counts: dict[UUID, dict[str, int]] = {c.id: {} for c in campaigns}
    identities: dict[UUID, _CampaignIdentity] = {
        c.id: {
            "subjects": [],
            "grades": [],
            "lesson_count": 0,
            "lesson_title": None,
        }
        for c in campaigns
    }
    if campaigns:
        campaign_ids = list(counts)
        grouped = await session.execute(
            select(
                RegenerationTarget.campaign_id,
                RegenerationTarget.status,
                func.count(),
            )
            .where(RegenerationTarget.campaign_id.in_(campaign_ids))
            .group_by(RegenerationTarget.campaign_id, RegenerationTarget.status)
        )
        for campaign_id, target_status, count in grouped.all():
            counts.setdefault(campaign_id, {})[target_status] = int(count)

        identity_rows = await session.execute(
            select(
                RegenerationTarget.campaign_id,
                RegenerationTarget.toc_entry_id,
                Book.subject.label("subject"),
                Book.grade.label("grade"),
                TOCEntry.section_title.label("lesson_title"),
            )
            .join(TOCEntry, TOCEntry.id == RegenerationTarget.toc_entry_id)
            .join(Book, Book.id == TOCEntry.book_id)
            .where(RegenerationTarget.campaign_id.in_(campaign_ids))
            .order_by(
                RegenerationTarget.campaign_id,
                Book.subject,
                Book.grade,
                TOCEntry.order_index,
                RegenerationTarget.output_language,
            )
        )
        seen_lessons: dict[UUID, set[UUID]] = {c.id: set() for c in campaigns}
        for row in identity_rows.all():
            identity = identities[row.campaign_id]
            subject = (row.subject or "").strip()
            grade = (row.grade or "").strip()
            lesson_title = (row.lesson_title or "").strip()
            if subject and subject not in identity["subjects"]:
                identity["subjects"].append(subject)
            if grade and grade not in identity["grades"]:
                identity["grades"].append(grade)
            if row.toc_entry_id not in seen_lessons[row.campaign_id]:
                seen_lessons[row.campaign_id].add(row.toc_entry_id)
                identity["lesson_count"] = len(seen_lessons[row.campaign_id])
                identity["lesson_title"] = (
                    lesson_title if identity["lesson_count"] == 1 else None
                )
    return campaigns, counts, identities, total


async def _roll_up_for_report(campaign_id: UUID) -> Optional[str]:
    """Converge the campaign for a report, at most once per publisher interval.

    Returns the rollup's own error text instead of raising: a campaign whose
    stored state the pure rollup refuses to derive is exactly the campaign an
    operator most needs to look at, so a 500 here would hide the only screen
    that could explain it.
    """
    if not _claim_rollup_slot(campaign_id):
        return None
    try:
        await _service().roll_up(campaign_id)
    except CampaignNotFound:
        raise
    except (CampaignError, ValueError) as exc:
        return str(exc)
    return None


def _plan_for_subject(request, subject: str):
    """``(plan, needs acknowledgement)`` for one subject's flow.

    A preview never refuses for a missing acknowledgement: this is precisely
    the screen on which an operator is shown the edges they would be breaking.
    It REPORTS the requirement and lets campaign creation — where the decision
    becomes permanent — be the thing that insists.
    """
    try:
        return build_phase_plan(
            subject=subject,
            selected_phases=request.selected_phases,
            excluded_affected_phases=request.excluded_affected_phases,
            refresh_extraction=request.refresh_extraction,
            exclusion_acknowledged=request.exclusion_acknowledged,
        ), False
    except ExclusionAcknowledgementRequired:
        return build_phase_plan(
            subject=subject,
            selected_phases=request.selected_phases,
            excluded_affected_phases=request.excluded_affected_phases,
            refresh_extraction=request.refresh_extraction,
            exclusion_acknowledged=True,
        ), True


def _plans_for_sources(request: out.EstimateRequest, sources: Sequence):
    """One phase plan per SUBJECT in the selection.

    Per subject, not campaign-wide: a campaign may legitimately span subjects,
    the plan is built from the SOURCE JOB's flow, and an acknowledgement flag
    copied across subjects would tell an operator a flow breaks an edge that it
    does not.
    """
    plans: dict[str, tuple] = {}
    for subject in sorted({s.subject for s in sources}):
        plans[subject] = _plan_for_subject(request, subject)
    return plans


def _source_availability_warnings(
    request: out.EstimateRequest,
    sources: Sequence,
) -> list[str]:
    """Describe head-local refresh inputs without treating them as fleet truth."""
    if not request.refresh_extraction:
        return []
    warnings: list[str] = []
    seen_books: set[UUID] = set()
    for source in sources:
        if source.book_id in seen_books:
            continue
        seen_books.add(source.book_id)
        if storage.book_pdf_path(source.book_id).is_file():
            continue
        warnings.append(
            f"{source.book_filename} ({source.book_id}) is not present on the "
            "head machine. Extraction refresh may still run if an active "
            "worker already has or can pull the source PDF."
        )
    return warnings


# ═══════════════════════════ discovery routes ════════════════════════════


@router.get("/eligible", response_model=out.EligibleSourcesOut)
async def list_eligible(
    book_id: list[UUID] = Query(default=[]),
    toc_entry_id: list[UUID] = Query(default=[]),
    output_language: list[str] = Query(default=[]),
    session: AsyncSession = Depends(get_session),
) -> out.EligibleSourcesOut:
    """Regenerable lessons, with the current and next version per language —
    and why every other selected lineage was left out."""
    for language in output_language:
        if language not in out.OUTPUT_LANGUAGES:
            raise _unprocessable(
                "unknown_output_language",
                f"unknown output language {language!r}; expected any of "
                f"{list(out.OUTPUT_LANGUAGES)}",
            )
    try:
        candidates = await discovery.list_source_candidates(
            session,
            book_ids=list(book_id) or None,
            toc_entry_ids=list(toc_entry_id) or None,
            output_languages=list(output_language) or None,
        )
    except discovery.DiscoverySelectionTooLarge as exc:
        raise _unprocessable(
            "selection_discovery_too_large", str(exc),
            count_at_least=exc.count_at_least, maximum=exc.maximum,
        ) from exc
    return out.EligibleSourcesOut.from_candidates(candidates)


@router.post("/phase-plan", response_model=out.PhasePlanOut)
async def preview_phase_plan(body: out.PhasePlanRequest) -> out.PhasePlanOut:
    """The real dependency closure for one subject, with the broken edges an
    exclusion would create. Pure: no database, no spend."""
    acknowledgement_required = False
    try:
        plan = build_phase_plan(
            subject=body.subject,
            selected_phases=body.selected_phases,
            excluded_affected_phases=body.excluded_affected_phases,
            refresh_extraction=body.refresh_extraction,
            exclusion_acknowledged=body.exclusion_acknowledged,
        )
    except ExclusionAcknowledgementRequired:
        acknowledgement_required = True
        plan = build_phase_plan(
            subject=body.subject,
            selected_phases=body.selected_phases,
            excluded_affected_phases=body.excluded_affected_phases,
            refresh_extraction=body.refresh_extraction,
            exclusion_acknowledged=True,
        )
    except (UnknownPhaseError, KeyError, ValueError) as exc:
        raise _translate_plan_error(exc) from exc
    return out.PhasePlanOut.from_plan(
        plan, subject=body.subject, acknowledgement_required=acknowledgement_required
    )


def _pinned_selection(draft, defaults: LaunchDefaultsSnapshot) -> SimpleNamespace:
    """The four ``(provider, model)`` pairs this draft would actually run with.

    Job-shaped on purpose: ``require_live_models`` takes anything carrying the
    role attributes, so the fleet's single retirement predicate
    (``job_reactivation.retired_models_in_job``) applies unchanged.

    The per-role fallback is not restated here — it is
    ``agent_models.resolve_role_selection``, the same production helper
    ``resolve_launch_contract`` calls — so this cannot drift from what the
    campaign would freeze. Only the *pins* are needed, so the completion step
    (`model or default_model(provider)`) is deliberately left out: a NULL role
    model is not a retired one, and `retired_models_in_job` skips it.
    """
    fields: dict[str, object] = {"provider": draft.provider, "model": draft.model}
    for role in ("extract", "judge", "solver"):
        provider, model = agent_models.resolve_role_selection(
            getattr(draft, f"{role}_provider"),
            getattr(draft, f"{role}_model"),
            getattr(defaults, f"{role}_provider"),
            getattr(defaults, f"{role}_model"),
        )
        fields[f"{role}_provider"] = provider
        fields[f"{role}_model"] = model
    return SimpleNamespace(**fields)


@router.post("/estimate", response_model=out.EstimateOut)
async def estimate(
    body: out.EstimateRequest,
    session: AsyncSession = Depends(get_session),
) -> out.EstimateOut:
    """Price a draft and preflight its destinations. Creates nothing, spends
    nothing, and makes no Notion call."""
    # The same two guards creation applies, in the same order and through the
    # same helpers — an estimate an operator can act on must be refused for
    # exactly the reasons the campaign behind it would be. The scope check
    # precedes discovery: the unfiltered scan IS part of what it prevents.
    try:
        require_bounded_selection(body.selection)
    except UnboundedSelection as exc:
        raise _translate_campaign_error(exc, request_shaped=True) from exc

    try:
        candidates = await discovery.list_source_candidates(
            session,
            book_ids=body.selection.book_ids or None,
            toc_entry_ids=body.selection.toc_entry_ids or None,
            output_languages=body.selection.output_languages or None,
        )
    except discovery.DiscoverySelectionTooLarge as exc:
        raise _unprocessable(
            "selection_discovery_too_large", str(exc),
            count_at_least=exc.count_at_least, maximum=exc.maximum,
        ) from exc
    sources = [c.source for c in candidates if c.source is not None]
    try:
        require_selection_within_cap(
            len(sources), what="estimate a regeneration campaign"
        )
    except SelectionTooLarge as exc:
        raise _translate_campaign_error(exc, request_shaped=True) from exc
    listing = out.EligibleSourcesOut.from_candidates(candidates)
    if not sources:
        return out.EstimateOut(
            target_count=0,
            canary_size=body.canary_size,
            publication_version=body.publication_version,
            acknowledgement_required=False,
            sources=listing.sources,
            ineligible=listing.ineligible,
            phase_plans=[],
            estimate=None,
            preflight=out.PreflightOut.from_failures([]),
            worker_executability=out.WorkerExecutabilityOut(
                ok=False,
                workers_online=0,
                compatible_worker_ids=[],
                required_api_providers=[],
                fleet_api_paused=False,
                reason="no eligible targets",
            ),
            source_availability_warnings=[],
        )

    try:
        plans = _plans_for_sources(body, sources)
    except (UnknownPhaseError, KeyError, ValueError) as exc:
        raise _translate_plan_error(exc) from exc
    acknowledgement_required = any(required for _plan, required in plans.values())

    # A PREVIEW resolution: the same pure helper `create_campaign` uses, run
    # here only to price the draft. Nothing is persisted, and creation resolves
    # again — once — as the campaign's single authoritative resolution.
    defaults = LaunchDefaultsSnapshot.model_validate(
        await launch_defaults_repo.get(session)
    )
    try:
        # Retirement is checked on the RAW pins, BEFORE resolution — the same
        # order `_stored_contract` uses, and for the same reason: a retired
        # model has been removed from MODEL_MANIFEST, so
        # `resolve_launch_contract` would refuse it with an "unknown (provider,
        # model)" validation error that never says the word retired and gives
        # an operator nothing to act on. Estimate is where an operator finds
        # out; without this they would price a dead model here and only learn
        # the truth one request later, at create.
        require_live_models(
            _pinned_selection(body.contract, defaults),
            what="estimate a regeneration campaign",
        )
        contract = resolve_launch_contract(
            body.contract,
            defaults=defaults,
            session_limit_strategy=agent_models.resolve_session_limit_strategy(
                body.contract.session_limit_strategy
            ),
        )
        require_api_transport(contract)
    except (NonApiTransport, RetiredModelRefusal) as exc:
        raise _translate_campaign_error(exc, request_shaped=True) from exc
    except ValueError as exc:
        raise _unprocessable("unresolvable_contract", str(exc)) from exc

    version_conflicts = await _publication_version_conflicts(
        session,
        sources=sources,
        requested_version=body.publication_version,
    )
    if version_conflicts:
        raise _translate_campaign_error(
            RequestedPublicationVersionConflict(version_conflicts),
            request_shaped=True,
        )

    worker_executability = await _check_active_workers(
        session,
        contract,
        stale_after_seconds=settings.worker_registry_stale_seconds,
    )

    priced = await _estimate_regeneration(
        session,
        targets=sources,
        plans={s.source_job_id: plans[s.subject][0] for s in sources},
        launch_contract=contract,
        now=_now(),
    )
    failures = await discovery.preflight_notion_destinations(session, sources)
    return out.EstimateOut(
        target_count=len(sources),
        canary_size=body.canary_size,
        publication_version=body.publication_version,
        acknowledgement_required=acknowledgement_required,
        sources=listing.sources,
        ineligible=listing.ineligible,
        phase_plans=[
            out.PhasePlanOut.from_plan(
                plan, subject=subject, acknowledgement_required=required
            )
            for subject, (plan, required) in sorted(plans.items())
        ],
        estimate=out.RegenerationEstimateOut.from_estimate(priced),
        preflight=out.PreflightOut.from_failures(failures),
        worker_executability=out.WorkerExecutabilityOut.from_result(
            worker_executability
        ),
        source_availability_warnings=_source_availability_warnings(body, sources),
    )


@router.post("/destinations", response_model=out.DestinationCheckOut)
async def check_destinations(
    body: out.DestinationCheckRequest,
) -> out.DestinationCheckOut:
    """Explicit read-only Notion review; no request DB session stays open."""
    unavailable = regeneration_notion_readiness.publication_unavailable_reason()
    if unavailable is not None:
        raise _conflict(
            "notion_destination_unavailable",
            unavailable,
            retryable=False,
        )
    selection = CampaignSelection(
        book_ids=tuple(body.selection.book_ids),
        toc_entry_ids=tuple(body.selection.toc_entry_ids),
        output_languages=tuple(body.selection.output_languages),
    )
    service = _service()
    try:
        sources = await service.load_destination_sources(
            selection, publication_version=body.publication_version
        )
        result = await _resolve_destinations(
            sources=sources,
            requested_version=body.publication_version,
            overrides=tuple(
                regeneration_destination.DestinationOverride(
                    toc_entry_id=item.toc_entry_id,
                    output_language=item.output_language,
                    notion_lesson_page_id=item.notion_lesson_page_id,
                )
                for item in body.destination_overrides
            ),
        )
    except regeneration_destination.DestinationServiceUnavailable as exc:
        raise _conflict(
            "notion_destination_unavailable",
            str(exc),
            retryable=exc.retryable,
        ) from exc
    except CampaignError as exc:
        raise _translate_campaign_error(exc, request_shaped=True) from exc

    return out.DestinationCheckOut(
        ok=result.ok,
        target_count=len(sources),
        checked_target_count=result.checked_target_count,
        destination_digest=result.digest,
        destinations=[
            out.DestinationResolutionOut.from_resolution(item)
            for item in result.resolutions
        ],
    )


# ═══════════════════════════ campaign routes ═════════════════════════════


@router.post(
    "/campaigns", response_model=out.CampaignDetailOut, status_code=201
)
async def create_campaign(
    body: out.CreateCampaignRequest,
    session: AsyncSession = Depends(get_session),
) -> out.CampaignDetailOut:
    """Freeze an immutable campaign and its targets. No job, no model call."""
    # Reject an unbounded request before provenance resolution or the
    # crash-repair write sweep. The service repeats this as its authority.
    try:
        require_bounded_selection(body.selection)
    except UnboundedSelection as exc:
        raise _translate_campaign_error(exc, request_shaped=True) from exc

    unavailable = regeneration_notion_readiness.publication_unavailable_reason()
    if unavailable is not None:
        raise _conflict(
            "notion_destination_unavailable", unavailable, retryable=False
        )

    # Provenance is resolved before the crash-repair sweep and before
    # the service is asked for anything. It is a pure local read with no I/O,
    # and creation is the audit boundary — a request that cannot say which code
    # it ran under must leave no trace at all. Both router-level gates still
    # precede it (auth from `include_router`, then the feature 404) and so does
    # request validation, so neither an anonymous nor a flag-off nor a
    # malformed caller ever learns this deployment's git state.
    app_git_revision = _resolve_app_git_revision(body.app_git_revision)
    await _reconcile_closed()
    spec = CreateCampaignSpec(
        selection=CampaignSelection(
            book_ids=tuple(body.selection.book_ids),
            toc_entry_ids=tuple(body.selection.toc_entry_ids),
            output_languages=tuple(body.selection.output_languages),
        ),
        contract=body.contract,
        selected_phases=tuple(body.selected_phases),
        publication_version=body.publication_version,
        destination_overrides=tuple(
            regeneration_destination.DestinationOverride(
                toc_entry_id=item.toc_entry_id,
                output_language=item.output_language,
                notion_lesson_page_id=item.notion_lesson_page_id,
            )
            for item in body.destination_overrides
        ),
        approved_destination_digest=body.approved_destination_digest,
        excluded_affected_phases=tuple(body.excluded_affected_phases),
        refresh_extraction=body.refresh_extraction,
        exclusion_acknowledged=body.exclusion_acknowledged,
        canary_size=body.canary_size,
        estimated_cost_low_usd=body.estimated_cost_low_usd,
        estimated_cost_high_usd=body.estimated_cost_high_usd,
        app_git_revision=app_git_revision,
        actor=body.actor,
        notes=body.notes,
    )
    try:
        campaign = await _service().create_campaign(spec)
    except CampaignError as exc:
        raise _translate_campaign_error(exc, request_shaped=True) from exc
    except (ExclusionAcknowledgementRequired, UnknownPhaseError) as exc:
        raise _translate_plan_error(exc) from exc
    except ValueError as exc:
        raise _unprocessable("invalid_campaign_draft", str(exc)) from exc
    return await _campaign_detail(session, campaign.id, now=_now())


@router.get("/campaigns", response_model=out.CampaignListOut)
async def list_campaigns(
    status_filter: list[str] = Query(default=[], alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> out.CampaignListOut:
    """Campaign rollups. Deliberately does NOT roll up per campaign: a list
    poll must never take a campaign write lock."""
    unknown = [s for s in status_filter if s not in CAMPAIGN_STATUSES]
    if unknown:
        # Silently returning an empty page for a typo reads as "no campaigns
        # are in that state", which is a different and wrong answer.
        raise _unprocessable(
            "unknown_campaign_status",
            f"unknown campaign status {unknown}; expected any of "
            f"{sorted(CAMPAIGN_STATUSES)}",
        )
    await _reconcile(session)
    campaigns, counts, identities, total = await _list_campaigns(
        session, statuses=list(status_filter) or None, limit=limit, offset=offset
    )
    return out.CampaignListOut(
        campaigns=[
            out.CampaignSummaryOut.from_row(
                c,
                status_counts=counts.get(c.id, {}),
                identity=identities.get(c.id),
            )
            for c in campaigns
        ],
        count=total,
        limit=limit,
        offset=offset,
    )


@router.get("/campaigns/{campaign_id}", response_model=out.CampaignDetailOut)
async def get_campaign(
    campaign_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> out.CampaignDetailOut:
    """The campaign report: every bucket, every reason, every dollar."""
    await _reconcile(session)
    try:
        await _load_campaign(session, campaign_id)
        rollup_error = await _roll_up_for_report(campaign_id)
        return _with_extras(
            await _campaign_detail(session, campaign_id, now=_now()),
            rollup_error=rollup_error,
        )
    except CampaignError as exc:
        raise _translate_campaign_error(exc, request_shaped=False) from exc


async def _campaign_action(
    session: AsyncSession,
    campaign_id: UUID,
    action,
    *,
    already_reconciled: bool = False,
    **kwargs,
) -> out.CampaignDetailOut:
    """Run one campaign transition and return the refreshed report.

    ``PartialWaveRelease`` is COMMITTED partial success — every healthy target
    already has its revision job and the campaign has already been rolled up —
    so it becomes a 200 carrying the per-target failures, never a generic
    409/500.
    """
    if not already_reconciled:
        await _reconcile(session)
    failures: list[out.WaveFailureOut] = []
    try:
        await action(campaign_id, **kwargs)
    except PartialWaveRelease as exc:
        statuses = await _current_target_statuses(
            session, [f.target_id for f in exc.failures]
        )
        failures = [
            out.WaveFailureOut.from_failure(f, current_status=statuses.get(f.target_id))
            for f in exc.failures
        ]
    except CampaignError as exc:
        raise _translate_campaign_error(exc, request_shaped=False) from exc
    except regeneration_destination.DestinationServiceUnavailable as exc:
        raise _conflict(
            "notion_destination_unavailable",
            str(exc),
            retryable=exc.retryable,
        ) from exc
    return _with_extras(
        await _campaign_detail(session, campaign_id, now=_now()),
        released_failures=failures,
    )


@router.post("/campaigns/{campaign_id}/canary", response_model=out.CampaignDetailOut)
async def launch_canary(
    campaign_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> out.CampaignDetailOut:
    """Preflight every destination, then create ONLY the canary jobs.
    Idempotent: a target that already has a revision job is left alone."""
    unavailable = regeneration_notion_readiness.publication_unavailable_reason()
    if unavailable is not None:
        raise _conflict(
            "notion_destination_unavailable", unavailable, retryable=False
        )
    await _reconcile_closed()
    return await _campaign_action(
        session,
        campaign_id,
        _service().launch_canary,
        already_reconciled=True,
    )


@router.post(
    "/campaigns/{campaign_id}/approve",
    response_model=out.CampaignDetailOut,
    dependencies=[Depends(require_publication_available)],
)
async def approve_canary(
    campaign_id: UUID,
    body: out.CampaignApproveRequest,
    session: AsyncSession = Depends(get_session),
) -> out.CampaignDetailOut:
    """The one human gate. Releases the canaries for publication and creates
    every remaining revision exactly once. There is no per-lesson publication
    approval, and a repeated approval creates nothing twice."""
    return await _campaign_action(
        session, campaign_id, _service().approve_canary, actor=body.actor
    )


@router.post("/campaigns/{campaign_id}/reject", response_model=out.CampaignDetailOut)
async def reject_canary(
    campaign_id: UUID,
    body: out.CampaignRejectRequest,
    session: AsyncSession = Depends(get_session),
) -> out.CampaignDetailOut:
    """Decline the canary: nothing publishes and no version is consumed."""
    return await _campaign_action(
        session, campaign_id, _service().reject_canary,
        actor=body.actor, reason=body.reason,
    )


@router.post("/campaigns/{campaign_id}/cancel", response_model=out.CampaignDetailOut)
async def cancel_campaign(
    campaign_id: UUID,
    body: out.CampaignCancelRequest,
    session: AsyncSession = Depends(get_session),
) -> out.CampaignDetailOut:
    """Stop a campaign. Published pages and reserved versions stand."""
    return await _campaign_action(
        session, campaign_id, _service().cancel,
        actor=body.actor, reason=body.reason,
    )


# ═══════════════════════════ target routes ═══════════════════════════════


async def _target_action(
    session: AsyncSession,
    target_id: UUID,
    action,
    *,
    previous: Optional[RegenerationTarget] = None,
    **kwargs,
) -> out.TargetActionOut:
    await _reconcile(session)
    failures: list[out.WaveFailureOut] = []
    try:
        target = await action(target_id, **kwargs)
    except PartialWaveRelease as exc:
        statuses = await _current_target_statuses(
            session, [f.target_id for f in exc.failures]
        )
        failures = [
            out.WaveFailureOut.from_failure(f, current_status=statuses.get(f.target_id))
            for f in exc.failures
        ]
        target = await _load_target(session, target_id)
    except CampaignError as exc:
        raise _translate_campaign_error(exc, request_shaped=False) from exc
    report = await _target_report(session, target, now=_now())
    return out.TargetActionOut(
        target=report,
        campaign_id=report.campaign_id,
        campaign_status=await _campaign_status(session, report.campaign_id),
        released_failures=failures,
        previous_publication_error=(
            previous.publication_last_error if previous is not None else None
        ),
        previous_publication_attempts=(
            int(previous.publication_attempts or 0) if previous is not None else None
        ),
        previous_publication_next_attempt_at=(
            previous.publication_next_attempt_at if previous is not None else None
        ),
    )


async def _campaign_status(session: AsyncSession, campaign_id: UUID) -> str:
    campaign = await campaigns_repo.get_campaign(session, campaign_id)
    return campaign.status if campaign is not None else "unknown"


@router.post(
    "/targets/{target_id}/retry-generation", response_model=out.TargetActionOut
)
async def retry_generation(
    target_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> out.TargetActionOut:
    """Re-run a failed revision on its EXISTING snapshot and phase plan.

    No publisher flag: this only re-drives generation. The retired-model check
    that guards it is the service's, and it surfaces as a visible 409.
    """
    return await _target_action(session, target_id, _service().retry_generation)


@router.post(
    "/targets/{target_id}/retry-publication",
    response_model=out.TargetActionOut,
    dependencies=[Depends(require_publication_available)],
)
async def retry_publication(
    target_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> out.TargetActionOut:
    """Re-queue delivery. Never calls a model, never allocates a new version.

    The service CLEARS ``publication_last_error`` — that is what buys the
    operator a fresh automatic attempt budget — so the failure that prompted
    the retry is captured here, before the call, or it is gone.
    """
    try:
        previous = await _load_target(session, target_id)
    except CampaignError as exc:
        raise _translate_campaign_error(exc, request_shaped=False) from exc
    return await _target_action(
        session, target_id, _service().retry_publication, previous=previous
    )


@router.post("/targets/{target_id}/abandon", response_model=out.TargetActionOut)
async def abandon_target(
    target_id: UUID,
    body: out.TargetAbandonRequest,
    session: AsyncSession = Depends(get_session),
) -> out.TargetActionOut:
    """Give up on one target. Audited, never deletes a Notion page, never
    reuses a version that was already reserved."""
    return await _target_action(
        session, target_id, _service().abandon, actor=body.actor, reason=body.reason
    )
