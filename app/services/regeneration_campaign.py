"""Campaign orchestration: creation, the one canary gate, retry, cancellation.

This module owns every transition a regeneration campaign or target makes
outside the pipeline. The pipeline drives JOBS; this service drives the
CAMPAIGN, and :mod:`app.services.regeneration_job_state` is the one bridge
between them.

Five rules run through all of it.

**One resolution, at creation.** ``create_campaign`` reads the
``launch_defaults`` row once and the fleet-wide session-limit default once,
calls ``resolve_launch_contract`` once, and stores the result. Every later wave
— the canary, the bulk release, a retried revision — *copies* that stored
snapshot. A campaign is launched in two waves separated by a human gate, so a
second resolution would give one immutable campaign two meanings and the
canary's approval evidence would stop describing the bulk.

**Nothing is spent before the gate.** Creation makes no job and no external
call. ``launch_canary`` preflights EVERY destination (not just the canary's)
before the first job exists, and creates canary jobs only: a non-canary target
has no ``homework_jobs`` row at all, so no worker can claim bulk work before a
human approved it.

**The commit boundary is respected.** ``regeneration_snapshot.create_revision_job``
COMMITS internally (its zero-cost extract marker is written through its own
session and would hit a foreign key against uncommitted rows). Every call
therefore gets its OWN session with no pending campaign writes, and each wave is
idempotently resumable: the campaign's own transitions commit first, job
creation is idempotent per target, and a crash mid-wave is finished by simply
running the same action again. A crash in the narrow window after that internal
commit can leave the copied extract's ``$0`` provenance marker unwritten — an
accounting-only degradation, deliberately accepted rather than redesigning
``agent.py``; nothing here may promise that every copied extract has a marker.

**Terminality is derived, never asserted.** ``cancel`` stamps
``cancel_requested_at`` and lets :func:`derive_campaign_status` decide. It never
writes ``cancelled`` while a target is still in flight — in particular not while
one is ``publishing``, because ``claim_target_publication``'s approval trigger
RAISES (it does not return False) once the owning campaign is no longer
approved, so a naive terminal write turns an in-flight Notion delivery into a
``check_violation`` mid-request. ``attention_required`` is the intermediate
state, and it is deliberately outside the trigger's reject set so an
already-claimed publication can still finish.

**Every decision is locked and fenced.** Read-then-write goes through the
row-locked repository reads (campaign first, then target — the same parent→child
order ``regeneration_job_state`` takes) and writes through the compare-and-set
repository updates, so two operators, a retried request and a worker cannot
interleave into a state nobody chose.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Optional, Sequence
from uuid import UUID

from loguru import logger
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import SessionLocal
from app.models.base import _utcnow
from app.models.regeneration_campaign import RegenerationCampaign
from app.models.regeneration_target import RegenerationTarget
from app.repositories import jobs as jobs_repo
from app.repositories import launch_defaults as launch_defaults_repo
from app.repositories import phase_outputs as phase_repo
from app.repositories import regeneration_campaigns as campaigns_repo
from app.repositories import regeneration_targets as targets_repo
from app.schemas.regeneration_contract import (
    LaunchContract,
    LaunchDefaultsSnapshot,
    ResolvedLaunchContract,
    ensure_resolved,
    resolve_launch_contract,
)
from app.services import agent_models, regeneration_job_state, regeneration_snapshot
from app.services import regeneration_discovery as discovery
from app.services.job_reactivation import retired_models_in_job
from app.services.launch_stagger import stagger_offset
from app.services.regeneration_planner import (
    build_phase_plan,
    validate_complete_snapshot,
)
from app.services.regeneration_states import (
    TERMINAL_CAMPAIGN_STATUSES,
    TERMINAL_TARGET_STATUSES,
    can_transition_campaign,
    roll_up_campaign,
)

__all__ = [
    "ActiveLineageConflict",
    "CampaignError",
    "CampaignNotFound",
    "CampaignSelection",
    "CreateCampaignSpec",
    "IllegalCampaignAction",
    "IllegalTargetAction",
    "LaunchStaggerPlan",
    "NoEligibleTargets",
    "NonApiTransport",
    "PartialWaveRelease",
    "PreflightBlocked",
    "RegenerationCampaignService",
    "RetiredModelRefusal",
    "TargetNotFound",
    "TerminalCampaignWithLiveTargets",
    "WaveFailure",
    "assert_not_hiding_live_targets",
    "derive_campaign_status",
    "plan_launch_stagger",
    "require_api_transport",
    "require_live_models",
    "retired_models_in_job",
    "roll_up_campaign",
    "target_sort_key",
]

_ROLES = ("extract", "judge", "solver")

# Target states a wave may still create a revision job for. `generating` is in
# the set on purpose: `create_revision_job` commits, so a crash between the
# campaign's own commit and the job's leaves exactly this shape, and resuming
# the action must finish it rather than skip it.
_CREATABLE_TARGET_STATUSES = ("planned", "generating")

# Target states `roll_up` may still RECONCILE against their job. Read from
# `regeneration_job_state` rather than copied, because the two must not drift:
# a target that has reached a publication state belongs to the publisher and to
# `retry_publication`, and reconciling one anyway is LEGAL in the transition
# table (`publication_failed -> publication_pending` is the edge the operator
# retry itself uses) — so a wider set here silently re-queues a failed delivery
# without clearing its backoff, erases the attention signal from the report,
# and turns the operator's own retry into a no-op.
_RECONCILABLE_TARGET_STATUSES = regeneration_job_state._REPAIRABLE_TARGET_STATUSES


# ═══════════════════════════ errors ══════════════════════════════════════


class CampaignError(RuntimeError):
    """Base for every refusal in this module — callers may catch this one."""


class CampaignNotFound(CampaignError):
    """No such campaign row."""


class TargetNotFound(CampaignError):
    """No such target row."""


class NoEligibleTargets(CampaignError):
    """The selection contains no lesson with a usable snapshot behind it.

    Carries the per-lineage refusal reasons discovery produced, so an operator
    is told WHY each lesson was left out rather than "nothing matched".
    """

    def __init__(self, candidates):
        self.candidates = list(candidates)
        detail = "; ".join(
            f"{c.toc_entry_id}/{c.output_language}: {', '.join(c.reasons)}"
            for c in self.candidates[:5]
        )
        super().__init__(
            "no eligible regeneration source in this selection" +
            (f" — {detail}" if detail else "")
        )


class ActiveLineageConflict(CampaignError):
    """Another campaign still owns one of these ``(lesson, language)`` lineages.

    The database is the authority (``uq_regeneration_targets_active_lineage``);
    this exception is the readable form of it, raised both by the pre-check and
    by the losing side of a real race.
    """

    def __init__(self, lineages):
        self.lineages = [(toc, lang) for toc, lang in lineages]
        listed = ", ".join(f"{toc}/{lang}" for toc, lang in self.lineages[:5])
        super().__init__(
            "a non-terminal regeneration target already owns "
            f"{len(self.lineages)} of these lessons: {listed} — retry, or "
            "abandon the existing target first"
        )


class NonApiTransport(CampaignError):
    """A resolved contract carrying a non-``api`` effective transport.

    Regeneration is an API-only workflow: it is priced against api rates, it is
    metered by the fleet credential limiter, and the cli path is retired from
    operational use. A cli leg would be silently unpriced by the estimator (its
    rows cost $0 by construction) and would bypass the limiter entirely, so it
    is refused at creation instead of labelled afterwards.
    """

    def __init__(self, offenders: Sequence[tuple[str, str]]):
        self.offenders = list(offenders)
        listed = ", ".join(f"{field}={value!r}" for field, value in self.offenders)
        super().__init__(
            "regeneration campaigns are API-only; refusing effective "
            f"transports: {listed}"
        )


class RetiredModelRefusal(CampaignError):
    """A pinned model has been retired since it was stamped. Fail closed.

    Checked against the RAW stored values, before ``ensure_resolved``: a retired
    model has been removed from ``MODEL_MANIFEST``, so pydantic would refuse the
    contract with a validation error that says nothing about retirement and
    gives an operator nothing to act on.
    """

    def __init__(self, retired: Sequence[tuple[str, str, str]], *, what: str):
        self.retired = [(role, provider, model) for role, provider, model in retired]
        listed = ", ".join(
            f"{role}={provider}/{model}" for role, provider, model in self.retired
        )
        super().__init__(
            f"cannot {what}: pinned to retired model(s) {listed}. The campaign "
            "contract was resolved while they were live; create a new campaign "
            "against current models rather than re-firing a dead one."
        )


class PreflightBlocked(CampaignError):
    """At least one target has nowhere to publish. Raised BEFORE any spend."""

    def __init__(self, failures):
        self.failures = list(failures)
        super().__init__(
            f"{len(self.failures)} lesson(s) have no resolvable Notion "
            "destination — fix the configuration and relaunch; no revision job "
            "was created"
        )


class PartialWaveRelease(CampaignError):
    """Some targets of a release could never be given a revision job.

    Raised AFTER every healthy target has its job committed and the campaign
    has been rolled up — never instead of that work. A revision job is created
    per target in its own committed session, so isolating a failure costs the
    wave nothing; NOT isolating it aborts the loop, strands every later
    (healthy) target in ``generating`` with no job, and — because the wave order
    is deterministic — re-running the action hits the same target first and
    aborts identically, forever. Those stranded targets hold
    ``uq_regeneration_targets_active_lineage``, so no future campaign could
    regenerate those lessons either.

    ``failures`` is in wave order, so two runs of the same broken selection
    report the same thing in the same sequence.
    """

    def __init__(self, failures: Sequence["WaveFailure"]):
        self.failures = list(failures)
        listed = "; ".join(str(f) for f in self.failures[:5])
        super().__init__(
            f"{len(self.failures)} target(s) could not be released and are "
            f"now generation_failed (retry or abandon them): {listed}"
        )


class IllegalCampaignAction(CampaignError):
    """The campaign is not in a state where this action is meaningful."""


class IllegalTargetAction(CampaignError):
    """The target is not in a state where this action is meaningful."""


class TerminalCampaignWithLiveTargets(CampaignError):
    """A terminal campaign status was attempted over a non-terminal target.

    The one rule that cannot be delegated to the pure state module: the
    repository's ``set_campaign_status`` is a deliberately dumb compare-and-set
    and would happily hide a ``publishing`` target behind ``cancelled`` — which
    the publication trigger then turns into a ``check_violation`` in the middle
    of an irreversible delivery.
    """


# ═══════════════════════════ inputs ══════════════════════════════════════


@dataclass(frozen=True)
class CampaignSelection:
    """What the operator picked. Empty means "do not filter on this axis",
    matching ``regeneration_discovery``'s own ``None`` semantics."""

    book_ids: tuple[UUID, ...] = ()
    toc_entry_ids: tuple[UUID, ...] = ()
    output_languages: tuple[str, ...] = ()

    def to_json(self) -> dict:
        return {
            "book_ids": [str(b) for b in self.book_ids],
            "toc_entry_ids": [str(t) for t in self.toc_entry_ids],
            "output_languages": list(self.output_languages),
        }


@dataclass(frozen=True)
class CreateCampaignSpec:
    """One campaign draft.

    ``contract`` is the operator's DRAFT contract ("auto" allowed); it is
    resolved exactly once inside ``create_campaign``. The estimate is passed in
    rather than computed here: pricing is ``regeneration_estimator``'s job and
    the operator approves the number they were SHOWN, not one recomputed at
    insert time.
    """

    selection: CampaignSelection
    contract: LaunchContract
    selected_phases: tuple[str, ...]
    excluded_affected_phases: tuple[str, ...] = ()
    refresh_extraction: bool = False
    exclusion_acknowledged: bool = False
    canary_size: int = 1
    estimated_cost_low_usd: Optional[float] = None
    estimated_cost_high_usd: Optional[float] = None
    app_git_revision: Optional[str] = None
    actor: str = ""
    notes: dict = field(default_factory=dict)


@dataclass(frozen=True)
class WaveFailure:
    """One target a release could not give a revision job, and why.

    Carries the SOURCE job id as well as the target id because the refusals are
    all about the source (a purged link, a snapshot that stopped validating),
    and an operator reading only ``IncompleteSnapshot``'s text is told about a
    job they cannot map back to a lesson.
    """

    target_id: UUID
    source_job_id: Optional[UUID]
    reason: str

    def __str__(self) -> str:
        return f"target {self.target_id} (source {self.source_job_id}): {self.reason}"


@dataclass(frozen=True)
class LaunchStaggerPlan:
    """The per-job start offsets for one release, plus what to report.

    Only the jobs a release ACTUALLY creates are ordered here — a resumed wave
    that creates two of twenty jobs is two jobs of load, not the tail of a
    twenty-job ramp.
    """

    offsets: tuple[int, ...]
    wave_size: int
    interval_seconds: int

    @property
    def job_count(self) -> int:
        return len(self.offsets)

    @property
    def wave_count(self) -> int:
        return len(set(self.offsets))

    @property
    def final_offset_seconds(self) -> int:
        return max(self.offsets, default=0)


# ═══════════════════════════ pure helpers ════════════════════════════════


def require_api_transport(contract) -> None:
    """Refuse a contract whose content or role EFFECTIVE transport is not api.

    Effective, not literal: a role left at ``'inherit'`` follows the contract's
    own transport, so ``inherit`` over a cli job is a cli call.
    """
    offenders: list[tuple[str, str]] = []
    if contract.transport != "api":
        offenders.append(("transport", contract.transport))
    for role in _ROLES:
        effective = agent_models.resolve_role_transport(
            getattr(contract, f"{role}_transport"), contract.transport
        )
        if effective != "api":
            offenders.append((f"{role}_transport", effective))
    if offenders:
        raise NonApiTransport(offenders)


def require_live_models(pinned, *, what: str) -> None:
    """Refuse anything pinned to a retired model, on any of the four roles.

    ``pinned`` is any object carrying the job-shaped role attributes: a
    ``HomeworkJob``, a ``ResolvedLaunchContract``, or a ``SimpleNamespace`` over
    the raw stored contract dict. The predicate itself is the fleet's single
    definition (``job_reactivation.retired_models_in_job``), shared with
    ``jobs.py::retry_job`` — which is unreachable for revisions, so this call is
    the only thing keeping the invariant alive for the regeneration class.
    """
    retired = retired_models_in_job(pinned)
    if retired:
        raise RetiredModelRefusal(retired, what=what)


def plan_launch_stagger(
    job_count: int,
    *,
    wave_size: Optional[int] = None,
    interval_seconds: Optional[int] = None,
) -> LaunchStaggerPlan:
    """Start offsets for ``job_count`` newly released revision jobs.

    Reuses ``launch_stagger.stagger_offset`` — the offset rule has exactly one
    definition — and reads the REGENERATION knobs, not the Fleet batch pair: a
    regeneration wave re-runs whole snapshots on top of whatever normal
    generation the fleet is already doing, so its default ramp is more
    conservative. Either knob at 0 is the explicit kill switch (every offset 0).
    """
    if wave_size is None:
        wave_size = int(settings.regeneration_launch_wave_size)
    if interval_seconds is None:
        interval_seconds = int(settings.regeneration_launch_wave_interval_seconds)
    offsets = tuple(
        stagger_offset(index, wave_size=wave_size, interval_seconds=interval_seconds)
        for index in range(max(0, job_count))
    )
    return LaunchStaggerPlan(
        offsets=offsets, wave_size=wave_size, interval_seconds=interval_seconds
    )


def target_sort_key(book_id, order_index, output_language: str, target_id):
    """The campaign's canonical target order: ``(book, TOC order, language,
    target id)``.

    Canary membership and the launch ramp are both defined against it, so the
    same selection always produces the same canary and the same wave layout.
    """
    return (
        str(book_id),
        -1 if order_index is None else int(order_index),
        output_language,
        str(target_id),
    )


def derive_campaign_status(
    *,
    target_statuses: Sequence[str],
    approved: bool,
    rejected: bool = False,
    cancelled: bool = False,
) -> str:
    """The campaign status implied by its targets.

    Delegates to the pure ``regeneration_states.roll_up_campaign`` for
    everything except rejection, which that function deliberately never returns
    (it is an operator decision, not a derived one). Rejection still has to
    CONVERGE like cancellation does: a rejected campaign whose canary is still
    cancelling parks in ``attention_required`` until every target is terminal,
    so a terminal campaign can never hide live work.
    """
    statuses = list(target_statuses)
    if rejected:
        if statuses and set(statuses) <= TERMINAL_TARGET_STATUSES:
            return "rejected"
        return "attention_required"
    return roll_up_campaign(statuses, approved, cancelled)


def assert_not_hiding_live_targets(
    campaign_status: str, target_statuses: Sequence[str]
) -> None:
    """Refuse a terminal campaign status while any target is non-terminal."""
    if campaign_status not in TERMINAL_CAMPAIGN_STATUSES:
        return
    live = [s for s in target_statuses if s not in TERMINAL_TARGET_STATUSES]
    if live:
        raise TerminalCampaignWithLiveTargets(
            f"refusing campaign status {campaign_status!r} while "
            f"{len(live)} target(s) are still non-terminal ({sorted(set(live))}) "
            "— cancellation converges through the rollup; a terminal campaign "
            "must never hide live work"
        )


# ═══════════════════════════ the service ═════════════════════════════════


class RegenerationCampaignService:
    """The campaign state machine. One instance is stateless and cheap.

    It owns its own sessions rather than taking one, because
    ``create_revision_job`` commits internally: a caller-supplied session
    holding half a campaign transition would have that half committed by it.
    ``session_factory`` exists for tests and for a caller that needs a different
    engine; production passes nothing.
    """

    def __init__(self, session_factory=None):
        self._sessions = session_factory or SessionLocal

    # ─── public API ──────────────────────────────────────────────────────

    async def create_campaign(self, spec: CreateCampaignSpec) -> RegenerationCampaign:
        """Draft a campaign: resolve the contract ONCE, freeze every target's
        source and phase plan, pick the canaries. No job, no external call.

        A draft holds the active-lineage lock until it is launched or
        cancelled. That is deliberate — there is no automatic expiry in v1 —
        and ``cancel`` is the documented way out.
        """
        if spec.canary_size < 1:
            raise ValueError(
                "canary_size must be at least 1 — the canary IS the human gate; "
                "a campaign with no canary would publish unreviewed content"
            )
        async with self._sessions() as session:
            contract = await self._resolve_contract_once(session, spec.contract)

            selection = spec.selection
            candidates = await discovery.list_source_candidates(
                session,
                book_ids=selection.book_ids or None,
                toc_entry_ids=selection.toc_entry_ids or None,
                output_languages=selection.output_languages or None,
            )
            eligible = [c for c in candidates if c.source is not None]
            if not eligible:
                raise NoEligibleTargets(candidates)

            lineages = [(c.toc_entry_id, c.output_language) for c in eligible]
            conflicts = await targets_repo.active_targets_for_lineages(
                session, lineages
            )
            if conflicts:
                raise ActiveLineageConflict(
                    [(t.toc_entry_id, t.output_language) for t in conflicts]
                )

            ordered = sorted(
                eligible,
                key=lambda c: target_sort_key(
                    c.source.book_id, c.source.order_index, c.output_language,
                    c.source.source_job_id,
                ),
            )
            canary_size = min(spec.canary_size, len(ordered))
            campaign = await campaigns_repo.create_campaign(
                session,
                selection_spec={
                    **selection.to_json(),
                    "actor": spec.actor,
                    "notes": spec.notes,
                    # Why a lesson the operator selected is NOT in the campaign.
                    # "why is this missing?" is the question they actually ask.
                    "ineligible": [
                        {
                            "toc_entry_id": str(c.toc_entry_id),
                            "output_language": c.output_language,
                            "reasons": list(c.reasons),
                        }
                        for c in candidates if c.source is None
                    ],
                },
                requested_phases=list(spec.selected_phases),
                excluded_phases=list(spec.excluded_affected_phases),
                launch_contract=contract.model_dump(),
                refresh_extraction=spec.refresh_extraction,
                exclusion_acknowledged=spec.exclusion_acknowledged,
                canary_size=canary_size,
                estimated_cost_low_usd=spec.estimated_cost_low_usd,
                estimated_cost_high_usd=spec.estimated_cost_high_usd,
                app_git_revision=spec.app_git_revision,
            )

            created: list[tuple[object, RegenerationTarget]] = []
            try:
                await self._insert_targets(session, campaign, ordered, spec, created)
                for _candidate, target in sorted(
                    created,
                    key=lambda pair: target_sort_key(
                        pair[0].source.book_id, pair[0].source.order_index,
                        pair[0].output_language, pair[1].id,
                    ),
                )[:canary_size]:
                    # Canaries are chosen on the FULL key, which needs the ids
                    # the inserts above just produced.
                    target.is_canary = True
                await session.commit()
            except IntegrityError as exc:
                # A concurrent creator won the lineage between our pre-check and
                # our insert. `uq_regeneration_targets_active_lineage` fires on
                # the FLUSH inside the loop, not only at commit, so the whole
                # block is guarded. The index is the authority; this is only its
                # readable form.
                await session.rollback()
                raise ActiveLineageConflict(lineages) from exc
            logger.info(
                f"regeneration campaign {campaign.id}: drafted "
                f"{len(created)} target(s), {canary_size} canary"
            )
            return campaign

    async def _insert_targets(
        self,
        session: AsyncSession,
        campaign: RegenerationCampaign,
        ordered,
        spec: CreateCampaignSpec,
        created: list,
    ) -> None:
        """Insert one target per eligible lineage, each with its own frozen
        phase plan."""
        for candidate in ordered:
            source = candidate.source
            # The SOURCE JOB's subject decides the canonical phase set — the
            # snapshot was generated under it. A campaign may legitimately span
            # subjects, so the plan is per target, never campaign-wide.
            plan = build_phase_plan(
                subject=source.subject,
                selected_phases=spec.selected_phases,
                excluded_affected_phases=spec.excluded_affected_phases,
                refresh_extraction=spec.refresh_extraction,
                exclusion_acknowledged=spec.exclusion_acknowledged,
            )
            target = await targets_repo.create_target(
                session,
                campaign_id=campaign.id,
                toc_entry_id=candidate.toc_entry_id,
                output_language=candidate.output_language,
                phase_plan=plan.to_json(),
                source_job_id=source.source_job_id,
                is_canary=False,
                status="planned",
            )
            created.append((candidate, target))

    async def launch_canary(self, campaign_id: UUID) -> RegenerationCampaign:
        """Preflight every destination, then create ONLY the canary jobs.

        Idempotent and resumable: a target that already owns a revision job is
        left alone (in particular its ``scheduled_at`` is not re-staggered), and
        a crash mid-wave is finished by calling this again.
        """
        async with self._sessions() as session:
            campaign = await self._locked_campaign(session, campaign_id)
            if campaign.status in TERMINAL_CAMPAIGN_STATUSES:
                raise IllegalCampaignAction(
                    f"campaign {campaign_id} is {campaign.status!r} — terminal"
                )
            if campaign.cancel_requested_at is not None:
                raise IllegalCampaignAction(
                    f"campaign {campaign_id} is cancelling — cannot launch"
                )
            if campaign.approved_at is not None:
                raise IllegalCampaignAction(
                    f"campaign {campaign_id} is already approved — the bulk "
                    "wave is released by approve_canary, not by a relaunch"
                )
            contract = self._stored_contract(campaign, what="launch the canary")

            targets = await targets_repo.list_for_campaign(
                session, campaign_id, for_update=True
            )
            if not targets:
                raise IllegalCampaignAction(
                    f"campaign {campaign_id} has no targets to launch"
                )

            # EVERY destination, canary or not, before any money is spent: a
            # bulk lesson with no home would only surface after the canary had
            # already been paid for and approved.
            failures = await self._preflight(session, targets)
            if failures:
                raise PreflightBlocked(failures)

            wave = await self._prepare_wave(
                session, campaign_id, [t for t in targets if t.is_canary]
            )
            if campaign.status == "draft":
                await campaigns_repo.set_campaign_status(
                    session,
                    campaign_id=campaign_id,
                    new_status="canary_running",
                    expected_statuses=["draft"],
                    canary_launched_at=_utcnow(),
                )
            await session.commit()

        failures = await self._create_wave(wave, contract)
        campaign = await self.roll_up(campaign_id)
        if failures:
            # AFTER the healthy canaries are committed and the campaign has been
            # re-derived: a partial release is reported, never rolled back.
            raise PartialWaveRelease(failures)
        return campaign

    async def approve_canary(
        self, campaign_id: UUID, *, actor: str
    ) -> RegenerationCampaign:
        """The one human gate. Stamps ``approved_at`` once, releases the
        successful canaries for publication, and creates every remaining
        revision exactly once.

        Idempotent: a repeated approval re-derives the campaign status and
        returns it, creating no duplicate job and re-stamping nothing. A
        one-target campaign uses this same path — its single canary is released
        and there is no empty bulk step.
        """
        # Converge first: a canary that finished while nobody was looking is
        # still `canary_running` at the campaign level, and `canary_running ->
        # approved` is not a legal edge.
        await self.roll_up(campaign_id)

        async with self._sessions() as session:
            campaign = await self._locked_campaign(session, campaign_id)
            if (
                campaign.status in TERMINAL_CAMPAIGN_STATUSES
                or campaign.rejected_at is not None
                or campaign.cancel_requested_at is not None
            ):
                raise IllegalCampaignAction(
                    f"campaign {campaign_id} is {campaign.status!r} — it can no "
                    "longer be approved"
                )
            # The stored contract may have gone stale since it was resolved:
            # models retire. Re-check BEFORE creating a wave against it.
            contract = self._stored_contract(campaign, what="approve the canary")

            if campaign.approved_at is None:
                moved = await campaigns_repo.set_campaign_status(
                    session,
                    campaign_id=campaign_id,
                    new_status="approved",
                    expected_statuses=["awaiting_canary_approval",
                                       "attention_required"],
                    approved_at=_utcnow(),
                )
                if not moved:
                    raise IllegalCampaignAction(
                        f"campaign {campaign_id} is {campaign.status!r} — only a "
                        "campaign whose canary has finished can be approved"
                    )
                logger.info(f"regeneration campaign {campaign_id}: approved by {actor}")
            await session.commit()

        async with self._sessions() as session:
            await self._locked_campaign(session, campaign_id)
            targets = await targets_repo.list_for_campaign(
                session, campaign_id, for_update=True
            )
            # Approval — not the repair sweep — owns
            # `awaiting_canary_approval -> publication_pending`.
            for target in targets:
                if (
                    target.status == "awaiting_canary_approval"
                    and target.abandon_requested_at is None
                ):
                    await targets_repo.set_target_status(
                        session,
                        target_id=target.id,
                        new_status="publication_pending",
                        expected_statuses=["awaiting_canary_approval"],
                        publication_released_at=(
                            target.publication_released_at or _utcnow()
                        ),
                    )
            wave = await self._prepare_wave(session, campaign_id, targets)
            await session.commit()

        failures = await self._create_wave(wave, contract)
        campaign = await self.roll_up(campaign_id)
        if failures:
            # The approval itself STANDS (`approved_at` is stamped, every
            # healthy revision exists); only the targets that could not be
            # released are reported. Re-approving is safe and creates nothing
            # twice — a `generation_failed` target is no longer creatable, so
            # the same broken lesson cannot block the action a second time.
            raise PartialWaveRelease(failures)
        return campaign

    async def reject_canary(
        self, campaign_id: UUID, *, actor: str, reason: str
    ) -> RegenerationCampaign:
        """Decline the canary: every canary and every planned target becomes
        terminal ``abandoned``. No version is consumed and nothing is published.

        Only reachable BEFORE approval — an approved campaign is stopped with
        :meth:`cancel`, which has different obligations (it must not disturb an
        in-flight publication).
        """
        async with self._sessions() as session:
            campaign = await self._locked_campaign(session, campaign_id)
            if campaign.approved_at is not None:
                raise IllegalCampaignAction(
                    f"campaign {campaign_id} is already approved — cancel it "
                    "instead; rejection is the pre-approval decision"
                )
            already = campaign.rejected_at is not None
            if not already and campaign.status in TERMINAL_CAMPAIGN_STATUSES:
                raise IllegalCampaignAction(
                    f"campaign {campaign_id} is {campaign.status!r} — terminal"
                )
            if not already and campaign.canary_launched_at is None:
                # `draft -> rejected` is not a legal edge, and rightly so: there
                # is no canary to decline. Abandoning the targets anyway would
                # leave a `draft` campaign whose every target is terminal.
                raise IllegalCampaignAction(
                    f"campaign {campaign_id} has no canary to reject (never "
                    "launched) — cancel the draft instead"
                )
            terminal_reason = f"canary rejected: {reason}"
            targets = await targets_repo.list_for_campaign(
                session, campaign_id, for_update=True
            )
            for target in targets:
                await self._converge_target(session, target, reason=terminal_reason)

            await self._apply_derived_status(
                session,
                campaign_id,
                rejected=True,
                approved=campaign.approved_at is not None,
                cancelled=campaign.cancel_requested_at is not None,
                rejected_at=None if already else _utcnow(),
                rejected_reason=None if already else reason,
            )
            await session.commit()
        if not already:
            logger.info(
                f"regeneration campaign {campaign_id}: canary rejected by "
                f"{actor} ({reason})"
            )
        return await self._read_campaign(campaign_id)

    async def cancel(
        self, campaign_id: UUID, *, actor: str, reason: str
    ) -> RegenerationCampaign:
        """Stop a campaign. The authoritative bulk stop control for revisions —
        they have no Fleet batch and therefore no batch pause.

        Visits EVERY non-terminal target: planned work is abandoned outright,
        running generation is stopped through the existing safe job-cancellation
        path, and an in-flight publication is only marked with the abandon
        INTENT — its remote request has an unknown outcome and is never revoked
        by deleting state. The campaign becomes terminal only once every target
        has converged.
        """
        async with self._sessions() as session:
            campaign = await self._locked_campaign(session, campaign_id)
            already = campaign.cancel_requested_at is not None
            if not already and campaign.status in TERMINAL_CAMPAIGN_STATUSES:
                raise IllegalCampaignAction(
                    f"campaign {campaign_id} is {campaign.status!r} — terminal"
                )
            terminal_reason = f"campaign cancelled: {reason}"
            targets = await targets_repo.list_for_campaign(
                session, campaign_id, for_update=True
            )
            for target in targets:
                await self._converge_target(session, target, reason=terminal_reason)

            await self._apply_derived_status(
                session,
                campaign_id,
                approved=campaign.approved_at is not None,
                rejected=campaign.rejected_at is not None,
                cancelled=True,
                cancel_requested_at=None if already else _utcnow(),
                cancel_requested_reason=None if already else reason,
            )
            await session.commit()
        if not already:
            logger.info(
                f"regeneration campaign {campaign_id}: cancelled by {actor} "
                f"({reason})"
            )
        return await self._read_campaign(campaign_id)

    async def retry_generation(self, target_id: UUID) -> RegenerationTarget:
        """Re-run a failed revision on its EXISTING snapshot and phase plan.

        The target moves to ``generating`` BEFORE the job is requeued, in its
        own committed transaction. That order is load-bearing: the requeued job
        becomes claimable the instant it commits, and a worker that finishes
        first would reconcile a target still sitting in ``generation_failed`` —
        an illegal edge to ``publication_pending``, which wedges it forever.
        Crashing between the two leaves a ``generating`` target over a terminal
        job, which the ordinary repair sweep converges back to
        ``generation_failed``: recoverable, and never an undriven illegal state.
        """
        async with self._sessions() as session:
            campaign, target = await self._locked_target(session, target_id)
            if target.terminal_at is not None:
                raise IllegalTargetAction(
                    f"target {target_id} is {target.status!r} — terminal"
                )
            if target.abandon_requested_at is not None:
                raise IllegalTargetAction(
                    f"target {target_id} is being abandoned — retry is refused"
                )
            if (
                campaign.status in TERMINAL_CAMPAIGN_STATUSES
                or campaign.cancel_requested_at is not None
                or campaign.rejected_at is not None
            ):
                raise IllegalCampaignAction(
                    f"campaign {campaign.id} is {campaign.status!r} — its "
                    "targets may not be retried"
                )
            if target.status not in ("generation_failed", "generating"):
                raise IllegalTargetAction(
                    f"target {target_id} is {target.status!r} — only a failed "
                    "(or already re-driven) generation can be retried"
                )
            contract = self._stored_contract(campaign, what="retry this revision")
            job = await targets_repo.revision_job_for_target(
                session, target_id=target_id
            )
            requeue = False
            if job is not None:
                # A retry reuses the job's PINNED provider/model verbatim on
                # every role. The generic `/jobs/{id}/retry` guard is
                # unreachable for a revision, so this is the only thing standing
                # between an operator retry and a 404-ing dead model.
                require_live_models(job, what="retry this revision")
                requeue = job.status in ("failed", "cancelled")
                if job.status == "done":
                    # A `done` job WITHOUT a usable snapshot is a DESIGNED
                    # `generation_failed` (`desired_target_status`: publishing a
                    # packet with a missing or empty phase is the one outcome
                    # regeneration exists to prevent) — and the design calls
                    # that state retryable. `reset_for_retry` is safe on a
                    # `done` row: it only rewrites the queue columns, the copied
                    # phase rows stay, and the pipeline resumes over them
                    # (`_done_phase_md` skips exactly the rows this predicate
                    # accepts), so the retry re-runs the phase that is missing
                    # and nothing else. Decided HERE, before the CAS below, so
                    # the target is never driven to `generating` with no work
                    # behind it.
                    rows = await phase_repo.list_for_job(session, job.id)
                    requeue = not validate_complete_snapshot(
                        subject=job.subject, rows=rows
                    ).usable
            if target.status == "generation_failed":
                await targets_repo.set_target_status(
                    session,
                    target_id=target_id,
                    new_status="generating",
                    expected_statuses=["generation_failed"],
                )
            await session.commit()
            job_id = job.id if job is not None else None

        failures: list[WaveFailure] = []
        if job_id is None:
            # The failure happened before the job existed (a crash mid-wave, or
            # a snapshot refusal): finish the creation instead of requeueing.
            failures = await self._create_wave([target_id], contract)
        elif requeue:
            async with self._sessions() as session:
                fresh = await jobs_repo.get(session, job_id)
                if (fresh is not None
                        and fresh.status
                        in regeneration_job_state.TERMINAL_JOB_STATUSES):
                    # No batch_id: `ck_homework_jobs_revision_no_batch` forbids
                    # one, and a revision is never a Fleet batch member. No
                    # offset either — an operator retry is a single job.
                    await jobs_repo.reset_for_retry(session, job_id)
                    await session.commit()
        # A job still `pending`/`running` needs nothing (the retry is a no-op on
        # work already in flight), and a `done` job WITH a usable snapshot needs
        # no regeneration — the rollup below carries it forward to publication
        # rather than paying to re-run a revision that succeeded.
        await self.roll_up((await self._read_target(target_id)).campaign_id)
        if failures:
            raise PartialWaveRelease(failures)
        return await self._read_target(target_id)

    async def retry_publication(self, target_id: UUID) -> RegenerationTarget:
        """Re-queue delivery for a target whose Notion write failed.

        Clears the backoff, the last error and any stale claim, and keeps the
        SAME reserved version, page identity and revision job. It never calls a
        model: a generated revision is not regenerated because delivery failed.
        """
        async with self._sessions() as session:
            campaign, target = await self._locked_target(session, target_id)
            if target.status == "publication_pending":
                return target  # idempotent
            if target.status != "publication_failed":
                raise IllegalTargetAction(
                    f"target {target_id} is {target.status!r} — only a failed "
                    "publication can be re-queued"
                )
            if target.abandon_requested_at is not None:
                raise IllegalTargetAction(
                    f"target {target_id} is being abandoned — publication retry "
                    "is refused"
                )
            if campaign.cancel_requested_at is not None:
                raise IllegalCampaignAction(
                    f"campaign {campaign.id} is cancelling — publication retry "
                    "is refused"
                )
            moved = await targets_repo.set_target_status(
                session,
                target_id=target_id,
                new_status="publication_pending",
                expected_statuses=["publication_failed"],
                clear_publication_backoff=True,
                clear_publication_claim=True,
            )
            if not moved:
                raise IllegalTargetAction(
                    f"target {target_id} moved out of publication_failed while "
                    "the retry was being applied"
                )
            await session.commit()
        # A publication failure parks the campaign in `attention_required`; a
        # real retry has to bring it back, or the report keeps demanding an
        # operator decision that was already made. Only on the path that
        # actually moved a target — the idempotent already-pending return above
        # changes nothing and must stay side-effect-free.
        await self.roll_up(campaign.id)
        return await self._read_target(target_id)

    async def abandon(
        self, target_id: UUID, *, actor: str, reason: str
    ) -> RegenerationTarget:
        """Explicitly give up on one target. Audited, never deletes a Notion
        page, and never reuses a version that was already reserved.

        Idempotent on an already-abandoned target and illegal on a published
        one — a delivered page is history, not work in progress.
        """
        async with self._sessions() as session:
            campaign, target = await self._locked_target(session, target_id)
            if target.status == "published":
                raise IllegalTargetAction(
                    f"target {target_id} is published — a delivered version "
                    "cannot be abandoned; its page and version stand"
                )
            if target.status == "abandoned":
                return target  # idempotent
            await self._converge_target(
                session, target, reason=f"abandoned by {actor}: {reason}"
            )
            await self._apply_derived_status(
                session,
                campaign.id,
                approved=campaign.approved_at is not None,
                rejected=campaign.rejected_at is not None,
                cancelled=campaign.cancel_requested_at is not None,
            )
            await session.commit()
        logger.info(f"regeneration target {target_id}: abandoned by {actor} ({reason})")
        return await self._read_target(target_id)

    async def roll_up(self, campaign_id: UUID) -> RegenerationCampaign:
        """Reconcile every target the GENERATION repair owns against its job,
        then re-derive the campaign status. Idempotent, and safe to call from
        any action or report.

        The reconcile is bounded to ``_RECONCILABLE_TARGET_STATUSES`` — exactly
        the set the crash-repair sweep uses. This method is documented as safe
        to call from any action or report, so it runs on every report page
        load; reconciling a target that has already reached a publication state
        would hand the publisher's and the operator's decisions back to a
        derived rule. In particular ``publication_failed`` is an
        attention-required state whose retry is explicitly operator-gated: the
        transition table ALLOWS ``publication_failed -> publication_pending``
        (it is the edge ``retry_publication`` uses), so an unbounded reconcile
        silently re-queues the delivery while leaving
        ``publication_next_attempt_at``/``publication_last_error`` set — the
        campaign stops reporting ``attention_required`` and the operator's own
        retry then hits its idempotent branch and does nothing.
        """
        async with self._sessions() as session:
            campaign = await self._locked_campaign(session, campaign_id)
            targets = await targets_repo.list_for_campaign(
                session, campaign_id, for_update=True
            )
            for target in targets:
                if target.terminal_at is not None:
                    continue
                if target.status not in _RECONCILABLE_TARGET_STATUSES:
                    continue
                job = await targets_repo.revision_job_for_target(
                    session, target_id=target.id
                )
                if job is not None:
                    await regeneration_job_state.reconcile_revision_job(
                        session, job.id
                    )
            if targets:
                await self._apply_derived_status(
                    session,
                    campaign_id,
                    approved=campaign.approved_at is not None,
                    rejected=campaign.rejected_at is not None,
                    cancelled=campaign.cancel_requested_at is not None,
                )
            await session.commit()
        return await self._read_campaign(campaign_id)

    async def set_campaign_status(
        self,
        campaign_id: UUID,
        new_status: str,
        *,
        reason: Optional[str] = None,
    ) -> RegenerationCampaign:
        """Guarded status write for an operator/administrative override.

        The guard is the point: the repository's compare-and-set knows nothing
        about targets, so this is where a terminal status over live work is
        refused.
        """
        async with self._sessions() as session:
            campaign = await self._locked_campaign(session, campaign_id)
            targets = await targets_repo.list_for_campaign(session, campaign_id)
            assert_not_hiding_live_targets(
                new_status, [t.status for t in targets]
            )
            if not can_transition_campaign(campaign.status, new_status):
                raise IllegalCampaignAction(
                    f"illegal campaign transition: {campaign.status} -> "
                    f"{new_status}"
                )
            await campaigns_repo.set_campaign_status(
                session,
                campaign_id=campaign_id,
                new_status=new_status,
                expected_statuses=[campaign.status],
                completed_at=(
                    _utcnow() if new_status in TERMINAL_CAMPAIGN_STATUSES else None
                ),
                cancel_requested_reason=reason,
            )
            await session.commit()
        return await self._read_campaign(campaign_id)

    # ─── internals ───────────────────────────────────────────────────────

    async def _resolve_contract_once(
        self, session: AsyncSession, draft: LaunchContract
    ) -> ResolvedLaunchContract:
        """The ONLY resolution in the feature: one read of ``launch_defaults``,
        one read of the fleet-wide session-limit default, one call."""
        defaults = LaunchDefaultsSnapshot.model_validate(
            await launch_defaults_repo.get(session)
        )
        strategy = agent_models.resolve_session_limit_strategy(
            draft.session_limit_strategy
        )
        contract = resolve_launch_contract(
            draft, defaults=defaults, session_limit_strategy=strategy
        )
        require_api_transport(contract)
        require_live_models(contract, what="create a regeneration campaign")
        return contract

    def _stored_contract(
        self, campaign: RegenerationCampaign, *, what: str
    ) -> ResolvedLaunchContract:
        """Read the campaign's frozen contract back. Verifies; never resolves.

        The retired check runs on the RAW dict FIRST: a model retired since the
        campaign was stored is no longer in ``MODEL_MANIFEST``, so
        ``ensure_resolved`` would refuse it with a validation error that tells
        an operator nothing about retirement.
        """
        stored = dict(campaign.launch_contract or {})
        require_live_models(SimpleNamespace(**stored), what=what)
        contract = ensure_resolved(stored)
        require_api_transport(contract)
        return contract

    async def _locked_campaign(
        self, session: AsyncSession, campaign_id: UUID
    ) -> RegenerationCampaign:
        campaign = await campaigns_repo.get_campaign_for_update(session, campaign_id)
        if campaign is None:
            raise CampaignNotFound(f"regeneration campaign {campaign_id} not found")
        return campaign

    async def _locked_target(
        self, session: AsyncSession, target_id: UUID
    ) -> tuple[RegenerationCampaign, RegenerationTarget]:
        """Lock parent then child — the same order ``regeneration_job_state``
        takes, so a bulk wave finishing while an operator acts cannot deadlock.
        """
        campaign_id = await session.scalar(
            select(RegenerationTarget.campaign_id).where(
                RegenerationTarget.id == target_id
            )
        )
        if campaign_id is None:
            raise TargetNotFound(f"regeneration target {target_id} not found")
        campaign = await self._locked_campaign(session, campaign_id)
        target = await targets_repo.get_target_for_update(session, target_id)
        if target is None:
            raise TargetNotFound(f"regeneration target {target_id} not found")
        return campaign, target

    async def _read_campaign(self, campaign_id: UUID) -> RegenerationCampaign:
        async with self._sessions() as session:
            campaign = await campaigns_repo.get_campaign(session, campaign_id)
            if campaign is None:
                raise CampaignNotFound(
                    f"regeneration campaign {campaign_id} not found"
                )
            return campaign

    async def _read_target(self, target_id: UUID) -> RegenerationTarget:
        async with self._sessions() as session:
            target = await session.scalar(
                select(RegenerationTarget)
                .where(RegenerationTarget.id == target_id)
                .execution_options(populate_existing=True)
            )
            if target is None:
                raise TargetNotFound(f"regeneration target {target_id} not found")
            return target

    async def _preflight(
        self, session: AsyncSession, targets: Sequence[RegenerationTarget]
    ) -> list:
        """Every live target that cannot be published, in ONE list.

        Read-only: it constructs no Notion client and makes no model call. A
        lineage whose source became unusable since creation is reported here
        too — at launch time that is the same operator-visible blocker.
        """
        live = [t for t in targets if t.terminal_at is None]
        if not live:
            return []
        candidates = await discovery.list_source_candidates(
            session,
            toc_entry_ids=[t.toc_entry_id for t in live],
            output_languages=sorted({t.output_language for t in live}),
        )
        by_lineage = {(c.toc_entry_id, c.output_language): c for c in candidates}

        failures: list = []
        sources = []
        for target in live:
            candidate = by_lineage.get((target.toc_entry_id, target.output_language))
            if candidate is None or candidate.source is None:
                failures.append(
                    discovery.NotionPreflightFailure(
                        source_job_id=target.source_job_id,
                        toc_entry_id=target.toc_entry_id,
                        subject="",
                        grade=None,
                        output_language=target.output_language,
                        lesson_title="",
                        reason=discovery.NO_COMPLETED_SOURCE_REASON,
                        detail="; ".join(candidate.reasons) if candidate else "",
                    )
                )
                continue
            sources.append(candidate.source)
        failures.extend(
            await discovery.preflight_notion_destinations(session, sources)
        )
        return failures

    async def _prepare_wave(
        self,
        session: AsyncSession,
        campaign_id: UUID,
        candidates: Sequence[RegenerationTarget],
    ) -> list[UUID]:
        """Move the targets that still need a revision job into ``generating``
        and return their ids, in canonical order.

        ``generating`` is written BEFORE the job exists for the same reason the
        retry path does it: the job is claimable the instant it commits, and a
        worker finishing before the target moved would reconcile a ``planned``
        target — an illegal edge into publication, which wedges it.
        """
        with_job = await targets_repo.target_ids_with_revision_job(
            session, campaign_id
        )
        wave: list[UUID] = []
        for target in candidates:
            if (
                target.terminal_at is not None
                or target.abandon_requested_at is not None
                or target.id in with_job
                or target.status not in _CREATABLE_TARGET_STATUSES
            ):
                continue
            if target.status == "planned":
                moved = await targets_repo.set_target_status(
                    session,
                    target_id=target.id,
                    new_status="generating",
                    expected_statuses=["planned"],
                )
                if not moved:
                    continue  # someone else converged it; leave it to them
            wave.append(target.id)
        return wave

    async def _create_wave(
        self, target_ids: Sequence[UUID], contract: ResolvedLaunchContract
    ) -> list[WaveFailure]:
        """Create one revision job per target, each in its OWN session.

        ``create_revision_job`` COMMITS. Handing it a session that still held
        campaign writes would commit half a transition; a dedicated session with
        nothing pending makes the boundary meaningless to the campaign's own
        atomicity, and makes the wave resumable: whatever was created stays,
        and re-running the action creates only the rest.

        Offsets are computed over the jobs this call actually creates, and are
        passed to ``create_revision_job`` as ``start_offset_seconds`` — never
        applied afterwards, which would re-stagger an already-queued revision.

        ISOLATED per target. ``create_revision_job``'s refusals
        (``IncompleteSnapshot``, ``MissingRevisionSource``,
        ``TargetNotEligible``) are PERMANENT properties of ONE target's source,
        not of the wave: the source job's phase rows were reset since the
        campaign was created, or the documented child-first purge nulled the
        link. Letting one abort the loop strands every later healthy target in
        ``generating`` with no job and no repair path — see
        :class:`PartialWaveRelease`. So each one is caught, recorded, and the
        target is driven to ``generation_failed``; the rest of the wave is
        created, and the caller raises the aggregate afterwards.

        The catch is deliberately NARROW. Only ``RevisionSnapshotError`` is a
        per-target creation failure. Anything else — a dropped connection, a
        bug — is not target-scoped, and is left to propagate with the jobs
        created so far already committed (their sessions are separate, so
        nothing rolls back) and the wave resumable by re-running the action.
        ``asyncio.CancelledError``, ``KeyboardInterrupt`` and ``SystemExit``
        are ``BaseException``s and are untouched by this ``except`` clause for
        the same reason: shutdown is not a target's fault.
        """
        if not target_ids:
            return []
        plan = plan_launch_stagger(len(target_ids))
        logger.info(
            f"regeneration wave: {plan.job_count} job(s), {plan.wave_count} wave(s), "
            f"final offset {plan.final_offset_seconds}s "
            f"(size={plan.wave_size}, interval={plan.interval_seconds}s)"
        )
        failures: list[WaveFailure] = []
        for index, target_id in enumerate(target_ids):
            try:
                async with self._sessions() as session:
                    await regeneration_snapshot.create_revision_job(
                        session,
                        target_id=target_id,
                        launch_contract=contract,
                        start_offset_seconds=plan.offsets[index],
                    )
            except regeneration_snapshot.RevisionSnapshotError as exc:
                failures.append(await self._fail_target_creation(target_id, exc))
        return failures

    async def _fail_target_creation(
        self, target_id: UUID, exc: Exception
    ) -> WaveFailure:
        """Land ONE target that cannot be given a revision job, and describe it.

        ``generation_failed`` — not ``generating`` and not terminal — because
        that is the state the design calls attention-required, retryable and
        abandonable: the operator sees it in the report, ``retry_generation``
        can re-attempt it once the source is repaired, and ``abandon`` releases
        its lineage. The reason is written to ``terminal_reason``, the target's
        only free-text explanation column, so the report has something to show;
        a later abandon overwrites it with its own.

        A cancellation or abandon intent WINS: such a target is converged by
        ``cancel``/``abandon``/the reconciler, and re-driving it here would
        fight them. Nothing is invented if the row moved on: the write is a
        compare-and-set from ``generating``.
        """
        reason = f"revision job could not be created: {exc}"
        source_job_id: Optional[UUID] = None
        async with self._sessions() as session:
            # Parent → child, the same order every other action takes.
            campaign_id = await session.scalar(
                select(RegenerationTarget.campaign_id).where(
                    RegenerationTarget.id == target_id
                )
            )
            target = None
            if campaign_id is not None:
                await campaigns_repo.get_campaign_for_update(session, campaign_id)
                target = await targets_repo.get_target_for_update(session, target_id)
            if target is not None:
                source_job_id = target.source_job_id
                if (
                    target.terminal_at is None
                    and target.abandon_requested_at is None
                    and target.status == "generating"
                ):
                    await targets_repo.set_target_status(
                        session,
                        target_id=target_id,
                        new_status="generation_failed",
                        expected_statuses=["generating"],
                        terminal_reason=reason,
                    )
            await session.commit()
        logger.warning(
            f"regeneration target {target_id} (source {source_job_id}): {reason} "
            "— left generation_failed; retry it once the source is repaired, or "
            "abandon it to release the lineage"
        )
        return WaveFailure(
            target_id=target_id, source_job_id=source_job_id, reason=reason
        )

    async def _converge_target(
        self, session: AsyncSession, target: RegenerationTarget, *, reason: str
    ) -> None:
        """Apply the cancel/reject/abandon rule for ONE target's current state.

        The table (plan §Task 7) in code, once — the three callers differ only
        in the reason they record:

        * ``published`` / ``abandoned`` — untouched;
        * ``publishing`` — record the abandon INTENT only. The remote request's
          outcome is unknown and is never revoked by deleting state; the
          publisher's claim resolves it to ``published`` or ``abandoned``;
        * ``generating`` — record the intent, stop the job through the existing
          safe cancellation path, then reconcile. A queued job cancels
          instantly; a running one converges on the worker's next heartbeat and
          the target stays non-terminal until it does;
        * everything else — terminal ``abandoned`` now, preserving any reserved
          publication version (a consumed version is never reused, even by the
          same lesson).
        """
        status = target.status
        if status in TERMINAL_TARGET_STATUSES:
            return
        now = _utcnow()
        if status == "publishing":
            await targets_repo.set_target_status(
                session,
                target_id=target.id,
                new_status="publishing",
                expected_statuses=["publishing"],
                abandon_requested_at=target.abandon_requested_at or now,
                abandon_requested_reason=target.abandon_requested_reason or reason,
            )
            return
        if status == "generating":
            await targets_repo.set_target_status(
                session,
                target_id=target.id,
                new_status="generating",
                expected_statuses=["generating"],
                abandon_requested_at=target.abandon_requested_at or now,
                abandon_requested_reason=target.abandon_requested_reason or reason,
            )
            job = await targets_repo.revision_job_for_target(
                session, target_id=target.id
            )
            if job is None:
                # A wave that never got to create the job: nothing to cancel.
                await targets_repo.set_target_status(
                    session,
                    target_id=target.id,
                    new_status="abandoned",
                    expected_statuses=["generating"],
                    terminal_at=now,
                    terminal_reason=reason,
                )
                return
            if not await jobs_repo.cancel_if_pending(session, job.id):
                await jobs_repo.request_cancel(session, job.id)
            # Terminal now (queued job cancelled, or already finished) → the
            # target completes as `abandoned`; still running → it stays
            # `generating` and the ordinary reconciler finishes the job later.
            await regeneration_job_state.reconcile_revision_job(session, job.id)
            return
        await targets_repo.set_target_status(
            session,
            target_id=target.id,
            new_status="abandoned",
            expected_statuses=[status],
            terminal_at=now,
            terminal_reason=reason,
            abandon_requested_at=target.abandon_requested_at or now,
            abandon_requested_reason=target.abandon_requested_reason or reason,
        )

    async def _apply_derived_status(
        self,
        session: AsyncSession,
        campaign_id: UUID,
        *,
        approved: bool,
        rejected: bool,
        cancelled: bool,
        **audit,
    ) -> str:
        """Re-derive the campaign status from its targets and compare-and-set it.

        Audit fields (``rejected_at``, ``cancel_requested_at``, …) ride along on
        the same UPDATE so an intent and the status it implies can never end up
        in two different transactions.
        """
        targets = await targets_repo.list_for_campaign(session, campaign_id)
        statuses = [t.status for t in targets]
        campaign = await campaigns_repo.get_campaign_for_update(session, campaign_id)
        if campaign is None:
            raise CampaignNotFound(f"regeneration campaign {campaign_id} not found")
        audit_only = {k: v for k, v in audit.items() if v is not None}
        if not statuses:
            # A campaign with no target row has nothing to derive from, but the
            # operator's INTENT still has to be recorded — dropping it here
            # would lose a cancellation.
            if audit_only:
                await campaigns_repo.set_campaign_status(
                    session,
                    campaign_id=campaign_id,
                    new_status=campaign.status,
                    expected_statuses=[campaign.status],
                    **audit_only,
                )
            return campaign.status
        derived = derive_campaign_status(
            target_statuses=statuses,
            approved=approved,
            rejected=rejected,
            cancelled=cancelled,
        )
        assert_not_hiding_live_targets(derived, statuses)
        audit = {k: v for k, v in audit.items() if v is not None}
        if derived == campaign.status:
            if audit:
                await campaigns_repo.set_campaign_status(
                    session,
                    campaign_id=campaign_id,
                    new_status=campaign.status,
                    expected_statuses=[campaign.status],
                    **audit,
                )
            return campaign.status
        if not can_transition_campaign(campaign.status, derived):
            # e.g. every target of a never-approved campaign was abandoned:
            # the targets imply `completed_with_abandonments`, which is not
            # reachable from `canary_running`. Park in `attention_required` —
            # the campaign genuinely needs an operator decision, and leaving it
            # `canary_running` would report progress that will never happen.
            fallback = "attention_required"
            if derived == fallback or not can_transition_campaign(
                campaign.status, fallback
            ):
                logger.debug(
                    f"regeneration campaign {campaign_id}: {campaign.status!r} -> "
                    f"{derived!r} is not a legal transition — leaving it"
                )
                if audit:
                    await campaigns_repo.set_campaign_status(
                        session,
                        campaign_id=campaign_id,
                        new_status=campaign.status,
                        expected_statuses=[campaign.status],
                        **audit,
                    )
                return campaign.status
            logger.info(
                f"regeneration campaign {campaign_id}: {campaign.status!r} -> "
                f"{derived!r} is not a legal transition — parking in "
                f"{fallback!r} for an operator decision"
            )
            derived = fallback
        if derived in TERMINAL_CAMPAIGN_STATUSES:
            audit.setdefault("completed_at", _utcnow())
        await campaigns_repo.set_campaign_status(
            session,
            campaign_id=campaign_id,
            new_status=derived,
            expected_statuses=[campaign.status],
            **audit,
        )
        return derived
