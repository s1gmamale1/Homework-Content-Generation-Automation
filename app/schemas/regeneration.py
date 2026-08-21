"""Request/response shapes for the versioned-regeneration API.

Everything in this module is **pure**: no session, no settings read, no clock
of its own (``now`` is always passed in). The router is a thin adapter that
fetches rows and hands them to the ``build``/``from_row`` constructors here, so
the operator-facing shape can be tested without a database and cannot drift
between the report and the mutation responses.

Three rules run through it.

**A failure is never rendered as a status code.** Every failed, parked or
abandoned target carries a ``reason`` sentence an operator can act on, and an
abandoned target carries **both** the abandon reason and the delivery error —
the publisher writes both, and showing only one hides either why we stopped or
what broke.

**The three ``publication_failed`` shapes are three different situations.**
``publication_next_attempt_at`` is the discriminator: a future timestamp means
the publisher will retry by itself, a past one means the next sweep takes it,
and ``NULL`` means the automatic budget is gone and **only** an operator can
move it. Rendering them identically hides every row that needs a human.

**An incomplete estimate says so.** ``has_unpriced_lines`` (a missing RATE) and
a zero-observation static line (missing VOLUME evidence) are independent
markers; neither is derived from the other, and while a rate is missing the
low/high range is not a complete figure.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Optional, Sequence
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.regeneration_contract import LaunchContract
from app.services import pricing
from app.services.regeneration_estimator import (
    ZERO_VOLUME_HISTORY,
    EstimateLineItem,
    RegenerationEstimate,
)
from app.services.regeneration_planner import (
    PhasePlanSerializationError,
    RegenerationPhasePlan,
)
from app.services.regeneration_states import (
    ATTENTION_TARGET_STATUSES,
    TERMINAL_CAMPAIGN_STATUSES,
    TERMINAL_TARGET_STATUSES,
)

# The five buckets the design names, plus `in_flight` for the states that are
# neither finished nor waiting on a human. In-flight rows are REPORTED, never
# omitted: a report that silently drops `planned`/`generating`/`publishing`
# tells an operator the campaign is smaller than it is.
BUCKETS = (
    "published",
    "publication_pending",
    "publication_failed",
    "generation_failed",
    "abandoned",
    "in_flight",
)

_IN_FLIGHT_STATUSES = frozenset(
    {"planned", "generating", "awaiting_canary_approval", "publishing"}
)

OUTPUT_LANGUAGES = ("uz", "ru", "en")

_NOTION_BASE_URL = "https://www.notion.so/"


def notion_page_url(page_id: Optional[str]) -> Optional[str]:
    """The operator-clickable page link for a stored Notion page id."""
    if not page_id:
        return None
    return f"{_NOTION_BASE_URL}{page_id.replace('-', '')}"


def target_bucket(status: str) -> str:
    """Which report bucket a target status belongs to."""
    if status in _IN_FLIGHT_STATUSES:
        return "in_flight"
    return status


def publication_state(
    status: str,
    *,
    publication_next_attempt_at: Optional[datetime],
    now: datetime,
) -> str:
    """The delivery situation behind a target status.

    ``publication_failed`` splits three ways on ``publication_next_attempt_at``
    — this is the whole point of the field, and the one distinction that tells
    an operator whether they have to do anything.
    """
    if status == "published":
        return "published"
    if status == "abandoned":
        return "abandoned"
    if status == "publishing":
        return "publishing"
    if status == "publication_pending":
        return "queued"
    if status == "publication_failed":
        if publication_next_attempt_at is None:
            return "action_required"
        return "backing_off" if publication_next_attempt_at > now else "retry_due"
    return "not_started"


# ═══════════════════════════ requests ════════════════════════════════════


class _Strict(BaseModel):
    """Reject an unknown field rather than silently ignoring it.

    A typo'd launch option that drops out of the payload changes what a
    campaign regenerates without telling anybody.
    """

    model_config = ConfigDict(extra="forbid")


class CampaignSelectionIn(_Strict):
    """What the operator picked. Empty list = "do not filter on this axis",
    matching ``regeneration_discovery``'s own ``None`` semantics."""

    book_ids: list[UUID] = Field(default_factory=list)
    toc_entry_ids: list[UUID] = Field(default_factory=list)
    output_languages: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate(self) -> "CampaignSelectionIn":
        unknown = [
            language
            for language in self.output_languages
            if language not in OUTPUT_LANGUAGES
        ]
        if unknown:
            raise ValueError(
                f"unknown output language(s) {unknown}; expected any of "
                f"{list(OUTPUT_LANGUAGES)}"
            )
        return self


class _PhaseSelectionIn(_Strict):
    """The phase half of a plan/estimate/create request.

    Shape only. Whether an exclusion actually breaks a dependency edge is the
    planner's judgement (it depends on the subject's flow), so the
    acknowledgement rule is enforced there and surfaced as a 422 — not
    pre-judged here, which would refuse a harmless no-op exclusion.
    """

    selected_phases: list[str] = Field(default_factory=list)
    excluded_affected_phases: list[str] = Field(default_factory=list)
    refresh_extraction: bool = False
    exclusion_acknowledged: bool = False

    @model_validator(mode="after")
    def _validate_phases(self) -> "_PhaseSelectionIn":
        for field in ("selected_phases", "excluded_affected_phases"):
            names = getattr(self, field)
            if any(not isinstance(name, str) or not name.strip() for name in names):
                raise ValueError(f"{field} contains a blank phase name")
            if len(set(names)) != len(names):
                raise ValueError(f"{field} contains duplicate phase names")
        both = sorted(set(self.selected_phases) & set(self.excluded_affected_phases))
        if both:
            raise ValueError(f"phase(s) {both} are both selected and excluded")
        if not self.selected_phases and not self.refresh_extraction:
            raise ValueError(
                "phase selection is empty — pick at least one phase, or set "
                "refresh_extraction=true"
            )
        return self


class PhasePlanRequest(_PhaseSelectionIn):
    """Preview the dependency closure for ONE subject's flow."""

    subject: str


class EstimateRequest(_PhaseSelectionIn):
    """Price a draft. Read-only: no campaign row, no job, no model call."""

    selection: CampaignSelectionIn = Field(default_factory=CampaignSelectionIn)
    contract: LaunchContract
    canary_size: int = Field(default=1, ge=1)


class CreateCampaignRequest(EstimateRequest):
    """Freeze a campaign. The estimate is passed back in because the operator
    approves the number they were SHOWN, not one recomputed at insert time."""

    estimated_cost_low_usd: Optional[float] = Field(default=None, ge=0)
    estimated_cost_high_usd: Optional[float] = Field(default=None, ge=0)
    app_git_revision: Optional[str] = Field(default=None, max_length=64)
    actor: str = ""
    notes: dict = Field(default_factory=dict)


class _ActorRequest(_Strict):
    actor: str = ""


class CampaignApproveRequest(_ActorRequest):
    """The one human gate. No per-target publication approval exists."""


class _ReasonRequest(_ActorRequest):
    reason: str

    @model_validator(mode="after")
    def _reason_is_meaningful(self) -> "_ReasonRequest":
        if not self.reason.strip():
            raise ValueError("reason must not be blank — it is stored as audit")
        return self


class CampaignRejectRequest(_ReasonRequest):
    """Decline the canary before approval."""


class CampaignCancelRequest(_ReasonRequest):
    """Stop an approved campaign."""


class TargetAbandonRequest(_ReasonRequest):
    """Give up on one target. Never deletes a page, never reuses a version."""


# ═══════════════════════════ phase plan ══════════════════════════════════


class DependencyEdgeOut(BaseModel):
    upstream: str
    downstream: str


_ACKNOWLEDGEMENT_MESSAGE = (
    "excluding these phases leaves them authored against an older upstream "
    "output — the resulting homework may be internally inconsistent; re-submit "
    "with exclusion_acknowledged=true to confirm"
)


class PhasePlanOut(BaseModel):
    """The expansion the operator must see before launching.

    ``acknowledgement_required`` is reported, never enforced here: the preview
    exists precisely so an operator can look at the broken edges BEFORE
    acknowledging them, so it must not refuse the request that asks about them.
    """

    subject: str
    canonical_phases: list[str]
    selected_phases: list[str]
    auto_included_phases: list[str]
    regenerated_phases: list[str]
    copied_phases: list[str]
    excluded_affected_phases: list[str]
    broken_dependency_edges: list[DependencyEdgeOut]
    refresh_extraction: bool
    regenerated_phase_count: int
    copied_phase_count: int
    acknowledgement_required: bool
    acknowledgement_message: Optional[str] = None

    @classmethod
    def from_plan(
        cls,
        plan: RegenerationPhasePlan,
        *,
        subject: str,
        acknowledgement_required: bool,
    ) -> "PhasePlanOut":
        return cls(
            subject=subject,
            canonical_phases=list(plan.canonical_phases),
            selected_phases=list(plan.selected_phases),
            auto_included_phases=list(plan.auto_included_phases),
            regenerated_phases=list(plan.regenerated_phases),
            copied_phases=list(plan.copied_phases),
            excluded_affected_phases=list(plan.excluded_affected_phases),
            broken_dependency_edges=[
                DependencyEdgeOut(upstream=e.upstream, downstream=e.downstream)
                for e in plan.broken_dependency_edges
            ],
            refresh_extraction=plan.refresh_extraction,
            regenerated_phase_count=len(plan.regenerated_phases),
            copied_phase_count=len(plan.copied_phases),
            acknowledgement_required=acknowledgement_required,
            acknowledgement_message=(
                _ACKNOWLEDGEMENT_MESSAGE if acknowledgement_required else None
            ),
        )


class TargetPhasePlanOut(BaseModel):
    """The per-target frozen plan, read back through the planner's serializer."""

    selected_phases: list[str]
    auto_included_phases: list[str]
    regenerated_phases: list[str]
    copied_phases: list[str]
    excluded_affected_phases: list[str]
    broken_dependency_edges: list[DependencyEdgeOut]
    refresh_extraction: bool

    @classmethod
    def from_plan(cls, plan: RegenerationPhasePlan) -> "TargetPhasePlanOut":
        return cls(
            selected_phases=list(plan.selected_phases),
            auto_included_phases=list(plan.auto_included_phases),
            regenerated_phases=list(plan.regenerated_phases),
            copied_phases=list(plan.copied_phases),
            excluded_affected_phases=list(plan.excluded_affected_phases),
            broken_dependency_edges=[
                DependencyEdgeOut(upstream=e.upstream, downstream=e.downstream)
                for e in plan.broken_dependency_edges
            ],
            refresh_extraction=plan.refresh_extraction,
        )


# ═══════════════════════════ discovery ═══════════════════════════════════


class EligibleSourceOut(BaseModel):
    """One lineage that can be regenerated, with its current and next version."""

    toc_entry_id: UUID
    output_language: str
    source_job_id: UUID
    book_id: UUID
    subject: str
    grade: Optional[str]
    source_publication_version: int
    next_expected_version: int
    source_is_revision: bool
    section_number: Optional[str]
    section_title: str
    chapter_title: str
    order_index: int
    has_notion_lesson_page: bool

    @classmethod
    def from_source(cls, source) -> "EligibleSourceOut":
        return cls(
            toc_entry_id=source.toc_entry_id,
            output_language=source.output_language,
            source_job_id=source.source_job_id,
            book_id=source.book_id,
            subject=source.subject,
            grade=source.grade,
            source_publication_version=source.source_publication_version,
            next_expected_version=source.next_expected_version,
            source_is_revision=source.source_is_revision,
            section_number=source.section_number,
            section_title=source.section_title,
            chapter_title=source.chapter_title,
            order_index=source.order_index,
            has_notion_lesson_page=bool(source.notion_lesson_page_id),
        )


class IneligibleLineageOut(BaseModel):
    """A lineage the operator selected that cannot be regenerated, and why."""

    toc_entry_id: UUID
    output_language: str
    reasons: list[str]
    detail: str = ""

    @classmethod
    def from_candidate(cls, candidate) -> "IneligibleLineageOut":
        return cls(
            toc_entry_id=candidate.toc_entry_id,
            output_language=candidate.output_language,
            reasons=list(candidate.reasons),
            detail=candidate.detail or "",
        )


class EligibleSourcesOut(BaseModel):
    sources: list[EligibleSourceOut]
    ineligible: list[IneligibleLineageOut]
    eligible_count: int
    ineligible_count: int

    @classmethod
    def from_candidates(cls, candidates: Sequence) -> "EligibleSourcesOut":
        sources = [
            EligibleSourceOut.from_source(c.source)
            for c in candidates
            if c.source is not None
        ]
        ineligible = [
            IneligibleLineageOut.from_candidate(c)
            for c in candidates
            if c.source is None
        ]
        return cls(
            sources=sources,
            ineligible=ineligible,
            eligible_count=len(sources),
            ineligible_count=len(ineligible),
        )


class PreflightFailureOut(BaseModel):
    """One lesson with nowhere to publish, and the configuration fix."""

    toc_entry_id: UUID
    source_job_id: Optional[UUID]
    output_language: str
    subject: str
    grade: Optional[str]
    lesson_title: str
    reason: str
    detail: str

    @classmethod
    def from_failure(cls, failure) -> "PreflightFailureOut":
        return cls(
            toc_entry_id=failure.toc_entry_id,
            source_job_id=failure.source_job_id,
            output_language=failure.output_language,
            subject=failure.subject,
            grade=failure.grade,
            lesson_title=failure.lesson_title,
            reason=failure.reason,
            detail=failure.detail,
        )


class PreflightOut(BaseModel):
    ok: bool
    failure_count: int
    failures: list[PreflightFailureOut]

    @classmethod
    def from_failures(cls, failures: Sequence) -> "PreflightOut":
        rendered = [PreflightFailureOut.from_failure(f) for f in failures]
        return cls(
            ok=not rendered, failure_count=len(rendered), failures=rendered
        )


# ═══════════════════════════ estimate ════════════════════════════════════


class EstimateLineOut(BaseModel):
    """One priced row. ``is_unpriced`` and ``is_static_envelope`` are
    INDEPENDENT: a line priced from the static envelope is missing volume
    evidence, not a rate, and must not be re-labelled as unpriced."""

    budget: str
    kind: str
    phase: str
    provider: str
    model: Optional[str]
    calls_low: int
    calls_high: int
    unit_cost_usd: float
    cost_low_usd: float
    cost_high_usd: float
    basis: str
    observations: int
    is_unpriced: bool
    is_observed: bool
    is_static_envelope: bool

    @classmethod
    def from_line(cls, line: EstimateLineItem) -> "EstimateLineOut":
        return cls(
            budget=line.budget,
            kind=line.kind,
            phase=line.phase,
            provider=line.provider,
            model=line.model,
            calls_low=line.calls_low,
            calls_high=line.calls_high,
            unit_cost_usd=line.unit_cost_usd,
            cost_low_usd=line.cost_low_usd,
            cost_high_usd=line.cost_high_usd,
            basis=line.basis,
            observations=line.observations,
            is_unpriced=line.is_unpriced,
            # The estimator sets `observations=0` exactly when it fell back to
            # the static envelope, so this is the same fact, not a re-guess at
            # the prose.
            is_observed=line.observations > 0,
            is_static_envelope=line.observations == 0,
        )


_INCOMPLETE_ESTIMATE_REASON = (
    "at least one line has no rate in the static price table: the range "
    "UNDER-STATES this campaign by an unknown amount and must not be presented "
    "as a complete or approvable figure"
)


class RegenerationEstimateOut(BaseModel):
    low_usd: float
    high_usd: float
    is_estimate: bool
    has_unpriced_lines: bool
    unpriced_line_count: int
    is_complete: bool
    incomplete_reason: Optional[str]
    target_count: int
    regenerated_phase_count: int
    copied_phase_count: int
    regenerated_extract_count: int
    copied_extract_count: int
    window_start: datetime
    window_end: datetime
    notes: list[str]
    zero_volume_history_notes: list[str]
    line_items: list[EstimateLineOut]

    @classmethod
    def from_estimate(cls, estimate: RegenerationEstimate) -> "RegenerationEstimateOut":
        lines = [EstimateLineOut.from_line(line) for line in estimate.line_items]
        unpriced = sum(1 for line in lines if line.is_unpriced)
        # Prefix match, not the prose: the estimator publishes
        # `ZERO_VOLUME_HISTORY` as the stable machine-readable marker.
        zero_volume = [
            note for note in estimate.notes if note.startswith(ZERO_VOLUME_HISTORY)
        ]
        return cls(
            low_usd=estimate.low_usd,
            high_usd=estimate.high_usd,
            is_estimate=estimate.is_estimate,
            has_unpriced_lines=estimate.has_unpriced_lines,
            unpriced_line_count=unpriced,
            is_complete=not estimate.has_unpriced_lines,
            incomplete_reason=(
                _INCOMPLETE_ESTIMATE_REASON if estimate.has_unpriced_lines else None
            ),
            target_count=estimate.target_count,
            regenerated_phase_count=estimate.regenerated_phase_count,
            copied_phase_count=estimate.copied_phase_count,
            regenerated_extract_count=estimate.regenerated_extract_count,
            copied_extract_count=estimate.copied_extract_count,
            window_start=estimate.window_start,
            window_end=estimate.window_end,
            notes=list(estimate.notes),
            zero_volume_history_notes=zero_volume,
            line_items=lines,
        )


class EstimateOut(BaseModel):
    """The whole read-only preview: what would be targeted, what it would cost,
    and whether it could be published at all."""

    target_count: int
    canary_size: int
    acknowledgement_required: bool
    sources: list[EligibleSourceOut]
    ineligible: list[IneligibleLineageOut]
    phase_plans: list[PhasePlanOut]
    estimate: Optional[RegenerationEstimateOut]
    preflight: PreflightOut


# ═══════════════════════════ cost ════════════════════════════════════════


_ZERO_COST_PROVIDER = "<cache>"


class ActualCostOut(BaseModel):
    """Money this campaign actually spent.

    Built ONLY from ``agent_usages`` rows whose ``homework_job_id`` is one of
    this campaign's REVISION jobs. Copied phases have no paid usage row (their
    only marker is the free ``<cache>`` extract row), and the source/V1 job's
    usage belongs to the run that produced it — counting either would double
    historical spend and make the estimate-vs-actual comparison meaningless.
    ``excluded_row_count`` is the proof: rows outside the set are dropped here
    as well as in SQL.
    """

    usd: float
    call_count: int
    paid_call_count: int
    zero_cost_marker_count: int
    failed_call_count: int
    excluded_row_count: int
    revision_job_count: int
    prompt_tokens: int
    output_tokens: int
    cached_tokens: int
    cache_creation_tokens: int
    total_tokens: int

    @classmethod
    def from_usage_rows(
        cls, rows: Sequence, *, revision_job_ids: set[UUID]
    ) -> "ActualCostOut":
        usd = 0.0
        counted = 0
        markers = 0
        failed = 0
        excluded = 0
        tokens = {
            "prompt_tokens": 0,
            "output_tokens": 0,
            "cached_tokens": 0,
            "cache_creation_tokens": 0,
            "total_tokens": 0,
        }
        seen_jobs: set[UUID] = set()
        for row in rows:
            job_id = getattr(row, "homework_job_id", None)
            if job_id not in revision_job_ids:
                excluded += 1
                continue
            counted += 1
            seen_jobs.add(job_id)
            usage = {key: int(getattr(row, key, 0) or 0) for key in tokens}
            for key in tokens:
                tokens[key] += usage[key]
            if not getattr(row, "success", True):
                failed += 1
            if getattr(row, "provider", "") == _ZERO_COST_PROVIDER:
                markers += 1
                continue
            usd += pricing.cost_usd(row.provider, row.model_name, usage)
        return cls(
            usd=round(usd, 6),
            call_count=counted,
            paid_call_count=counted - markers,
            zero_cost_marker_count=markers,
            failed_call_count=failed,
            excluded_row_count=excluded,
            revision_job_count=len(seen_jobs),
            **tokens,
        )


class ProvenanceOut(BaseModel):
    """Copied vs regenerated, counted from the REAL phase rows."""

    copied_phase_count: int = 0
    regenerated_phase_count: int = 0
    phase_row_count: int = 0


# ═══════════════════════════ targets ═════════════════════════════════════


class LessonOut(BaseModel):
    book_id: Optional[UUID] = None
    order_index: Optional[int] = None
    section_number: Optional[str] = None
    section_title: Optional[str] = None
    chapter_title: Optional[str] = None


_PURGED_SOURCE_NOTE = (
    "the source snapshot was purged (child-first delete); this target row is "
    "reporting history only"
)


def _job_error(job) -> Optional[str]:
    for field in ("error_message", "last_error"):
        value = getattr(job, field, None)
        if value:
            return str(value)
    return None


_SENTENCE_END = ".!?…"


def _sentence(text: str) -> str:
    """Terminate the reason so a rendered row always reads as a sentence.

    The last clause is usually an interpolated provider error, which carries no
    punctuation of its own; without this, half the report ends mid-word.
    """
    text = text.strip()
    return text if not text or text[-1] in _SENTENCE_END else f"{text}."


def _describe(
    *,
    status: str,
    state: str,
    version: Optional[int],
    attempts: int,
    next_attempt_at: Optional[datetime],
    last_error: Optional[str],
    terminal_reason: Optional[str],
    abandon_reason: Optional[str],
    job,
) -> str:
    """One human-readable sentence per target. Never a bare status code."""
    return _sentence(_describe_body(
        status=status, state=state, version=version, attempts=attempts,
        next_attempt_at=next_attempt_at, last_error=last_error,
        terminal_reason=terminal_reason, abandon_reason=abandon_reason, job=job,
    ))


def _describe_body(
    *,
    status: str,
    state: str,
    version: Optional[int],
    attempts: int,
    next_attempt_at: Optional[datetime],
    last_error: Optional[str],
    terminal_reason: Optional[str],
    abandon_reason: Optional[str],
    job,
) -> str:
    if status == "published":
        label = f"V{version}" if version else "a new version"
        return f"published as Homework {label}."
    if status == "abandoned":
        reason = terminal_reason or abandon_reason or "abandoned by an operator"
        text = f"abandoned: {reason} No Notion page was deleted"
        text += (
            f" and version V{version} stays consumed."
            if version
            else " and no publication version was consumed."
        )
        if last_error:
            text += f" Last delivery error: {last_error}"
        return text
    if status == "generation_failed":
        detail = terminal_reason or _job_error(job) or (
            "the revision job did not produce a complete snapshot"
        )
        return (
            f"generation failed: {detail} Retry generation or abandon this "
            "target — it holds the lesson's active lineage until then."
        )
    if status == "publication_failed":
        detail = last_error or "the Notion write failed"
        if state == "backing_off":
            return (
                f"delivery failed after {attempts} attempt(s); an automatic "
                f"retry is scheduled for {next_attempt_at.isoformat()}. "
                f"Last error: {detail}"
            )
        if state == "retry_due":
            return (
                f"delivery failed after {attempts} attempt(s); the retry is "
                f"due now and the next publisher sweep will take it. "
                f"Last error: {detail}"
            )
        return (
            f"delivery failed after {attempts} attempt(s) and there is NO "
            "AUTOMATIC RETRY left — an operator must retry publication or "
            f"abandon this target. Last error: {detail}"
        )
    if status == "publishing":
        return "delivery to Notion is in flight."
    if status == "publication_pending":
        return "generated and queued for automatic publication."
    if status == "awaiting_canary_approval":
        return "canary generated; waiting for the campaign approval gate."
    if status == "generating":
        return "the revision job is generating."
    return "planned; no revision job has been created yet."


class TargetReportOut(BaseModel):
    """One lesson of a campaign, with every state an operator must act on."""

    id: UUID
    campaign_id: UUID
    toc_entry_id: UUID
    output_language: str
    is_canary: bool
    status: str
    bucket: str
    publication_state: str
    is_terminal: bool
    action_required: bool
    reason: str

    source_job_id: Optional[UUID]
    source_publication_version: Optional[int]
    source_note: Optional[str]

    revision_job_id: Optional[UUID]
    revision_job_status: Optional[str]
    revision_job_scheduled_at: Optional[datetime]
    content_path: Optional[str]
    download_path: Optional[str]

    publication_version: Optional[int]
    notion_page_id: Optional[str]
    notion_page_url: Optional[str]
    publication_released_at: Optional[datetime]
    publication_attempts: int
    publication_next_attempt_at: Optional[datetime]
    publication_last_error: Optional[str]
    delivery_error: Optional[str]

    terminal_at: Optional[datetime]
    terminal_reason: Optional[str]
    abandon_requested_at: Optional[datetime]
    abandon_requested_reason: Optional[str]

    lesson: LessonOut
    phase_plan: Optional[TargetPhasePlanOut]
    phase_plan_error: Optional[str]
    judge_status_counts: dict[str, int]
    solver_status_counts: dict[str, int]
    copied_phase_count: int
    regenerated_phase_count: int

    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @classmethod
    def from_row(
        cls,
        target,
        *,
        now: datetime,
        revision_job=None,
        source_publication_version: Optional[int] = None,
        lesson: Optional[LessonOut] = None,
        phase_rows: Sequence = (),
    ) -> "TargetReportOut":
        state = publication_state(
            target.status,
            publication_next_attempt_at=target.publication_next_attempt_at,
            now=now,
        )
        plan_out: Optional[TargetPhasePlanOut] = None
        plan_error: Optional[str] = None
        try:
            plan_out = TargetPhasePlanOut.from_plan(
                RegenerationPhasePlan.from_json(target.phase_plan)
            )
        except PhasePlanSerializationError as exc:
            # A stored plan this build cannot read is a real problem, but it
            # must not take the whole report down — every other target still
            # needs an operator decision.
            plan_error = str(exc)

        judge: dict[str, int] = {}
        solver: dict[str, int] = {}
        copied = regenerated = 0
        for row in phase_rows:
            if getattr(row, "copied_from_phase_output_id", None) is not None:
                copied += 1
            else:
                regenerated += 1
            for value, counts in (
                (getattr(row, "judge_status", None), judge),
                (getattr(row, "solver_status", None), solver),
            ):
                if value:
                    counts[value] = counts.get(value, 0) + 1

        job_id = getattr(revision_job, "id", None)
        return cls(
            id=target.id,
            campaign_id=target.campaign_id,
            toc_entry_id=target.toc_entry_id,
            output_language=target.output_language,
            is_canary=bool(target.is_canary),
            status=target.status,
            bucket=target_bucket(target.status),
            publication_state=state,
            is_terminal=target.status in TERMINAL_TARGET_STATUSES,
            # OPERATOR-actionable, which is narrower than the backend's
            # campaign-level `attention_required`: a `publication_failed`
            # target the publisher will retry by itself needs no human, and
            # flagging it as if it did buries the rows that genuinely do.
            action_required=(
                target.status == "generation_failed"
                or state == "action_required"
            ),
            reason=_describe(
                status=target.status,
                state=state,
                version=target.publication_version,
                attempts=int(target.publication_attempts or 0),
                next_attempt_at=target.publication_next_attempt_at,
                last_error=target.publication_last_error,
                terminal_reason=target.terminal_reason,
                abandon_reason=target.abandon_requested_reason,
                job=revision_job,
            ),
            source_job_id=target.source_job_id,
            source_publication_version=source_publication_version,
            source_note=None if target.source_job_id else _PURGED_SOURCE_NOTE,
            revision_job_id=job_id,
            revision_job_status=getattr(revision_job, "status", None),
            revision_job_scheduled_at=getattr(revision_job, "scheduled_at", None),
            content_path=f"/api/v1/jobs/{job_id}" if job_id else None,
            download_path=f"/api/v1/jobs/{job_id}/download" if job_id else None,
            publication_version=target.publication_version,
            notion_page_id=target.notion_page_id,
            notion_page_url=notion_page_url(target.notion_page_id),
            publication_released_at=target.publication_released_at,
            publication_attempts=int(target.publication_attempts or 0),
            publication_next_attempt_at=target.publication_next_attempt_at,
            publication_last_error=target.publication_last_error,
            # The same value under the name a reader of an ABANDONED row looks
            # for: `terminal_reason` says why we stopped, this says what broke.
            delivery_error=target.publication_last_error,
            terminal_at=target.terminal_at,
            terminal_reason=target.terminal_reason,
            abandon_requested_at=target.abandon_requested_at,
            abandon_requested_reason=target.abandon_requested_reason,
            lesson=lesson or LessonOut(),
            phase_plan=plan_out,
            phase_plan_error=plan_error,
            judge_status_counts=judge,
            solver_status_counts=solver,
            copied_phase_count=copied,
            regenerated_phase_count=regenerated,
            created_at=getattr(target, "created_at", None),
            updated_at=getattr(target, "updated_at", None),
        )


class CanaryOut(BaseModel):
    """Where to read one canary revision before approving the campaign."""

    target_id: UUID
    toc_entry_id: UUID
    output_language: str
    status: str
    revision_job_id: Optional[UUID]
    revision_job_status: Optional[str]
    content_path: Optional[str]
    download_path: Optional[str]
    copied_phase_count: int
    regenerated_phase_count: int
    judge_status_counts: dict[str, int]
    solver_status_counts: dict[str, int]


class ReleaseScheduleOut(BaseModel):
    """The launch ramp as it was actually PERSISTED.

    Read from ``homework_jobs.scheduled_at`` rather than re-derived from the
    stagger settings: the settings are mutable and a resumed wave staggers only
    the jobs it created, so a recomputed ramp would describe a launch that
    never happened.
    """

    job_count: int
    wave_count: int
    final_offset_seconds: int
    first_scheduled_at: Optional[datetime] = None
    last_scheduled_at: Optional[datetime] = None
    source: str = "persisted homework_jobs.scheduled_at"


class WaveFailureOut(BaseModel):
    """One target a release could not give a revision job.

    ``current_status`` is READ BACK from the row, never taken from
    ``PartialWaveRelease``'s message: a terminal race can leave the target
    ``abandoned`` (or even published) rather than the ``generation_failed`` the
    message asserts.
    """

    target_id: UUID
    source_job_id: Optional[UUID]
    reason: str
    current_status: Optional[str]

    @classmethod
    def from_failure(cls, failure, *, current_status: Optional[str]) -> "WaveFailureOut":
        return cls(
            target_id=failure.target_id,
            source_job_id=failure.source_job_id,
            reason=failure.reason,
            current_status=current_status,
        )


# ═══════════════════════════ campaign ════════════════════════════════════


def _bucket_counts(status_counts: Mapping[str, int]) -> dict[str, int]:
    counts = {bucket: 0 for bucket in BUCKETS}
    for status, count in status_counts.items():
        counts[target_bucket(status)] = counts.get(target_bucket(status), 0) + count
    return counts


_APPROVED_NOTHING_RELEASED = (
    "this campaign is approved but {count} target(s) are still 'planned' with "
    "no revision job — they were never released. Approval and the bulk release "
    "are two transactions, so an approval can be recorded with nothing "
    "released. Re-run approve: it is idempotent and creates nothing twice."
)


class CampaignSummaryOut(BaseModel):
    """List-row shape. Same vocabulary as the detail, no target rows."""

    id: UUID
    status: str
    is_terminal: bool
    attention_required: bool
    target_count: int
    status_counts: dict[str, int]
    bucket_counts: dict[str, int]
    canary_size: int
    refresh_extraction: bool
    exclusion_acknowledged: bool
    requested_phases: list[str]
    excluded_phases: list[str]
    app_git_revision: Optional[str]
    estimated_cost_low_usd: Optional[float]
    estimated_cost_high_usd: Optional[float]
    canary_launched_at: Optional[datetime]
    approved_at: Optional[datetime]
    rejected_at: Optional[datetime]
    cancel_requested_at: Optional[datetime]
    completed_at: Optional[datetime]
    rejected_reason: Optional[str]
    cancel_requested_reason: Optional[str]
    created_at: Optional[datetime]
    updated_at: Optional[datetime]

    @classmethod
    def from_row(
        cls, campaign, *, status_counts: Mapping[str, int]
    ) -> "CampaignSummaryOut":
        counts = dict(status_counts)
        return cls(
            id=campaign.id,
            status=campaign.status,
            is_terminal=campaign.status in TERMINAL_CAMPAIGN_STATUSES,
            attention_required=any(
                counts.get(status) for status in ATTENTION_TARGET_STATUSES
            ),
            target_count=sum(counts.values()),
            status_counts=counts,
            bucket_counts=_bucket_counts(counts),
            canary_size=campaign.canary_size,
            refresh_extraction=bool(campaign.refresh_extraction),
            exclusion_acknowledged=bool(campaign.exclusion_acknowledged),
            requested_phases=list(campaign.requested_phases or []),
            excluded_phases=list(campaign.excluded_phases or []),
            app_git_revision=campaign.app_git_revision,
            estimated_cost_low_usd=campaign.estimated_cost_low_usd,
            estimated_cost_high_usd=campaign.estimated_cost_high_usd,
            canary_launched_at=campaign.canary_launched_at,
            approved_at=campaign.approved_at,
            rejected_at=campaign.rejected_at,
            cancel_requested_at=campaign.cancel_requested_at,
            completed_at=campaign.completed_at,
            rejected_reason=campaign.rejected_reason,
            cancel_requested_reason=campaign.cancel_requested_reason,
            created_at=getattr(campaign, "created_at", None),
            updated_at=getattr(campaign, "updated_at", None),
        )


class CampaignListOut(BaseModel):
    campaigns: list[CampaignSummaryOut]
    count: int
    limit: int
    offset: int


class CampaignDetailOut(CampaignSummaryOut):
    """The full report. Every bucket, every reason, every dollar."""

    selection_spec: dict
    launch_contract: dict
    solver_enabled_observed: Optional[bool] = None
    buckets: dict[str, list[UUID]]
    targets: list[TargetReportOut]
    canary: list[CanaryOut]
    actual_cost: ActualCostOut
    judge_status_counts: dict[str, int]
    solver_status_counts: dict[str, int]
    provenance: ProvenanceOut
    release_schedule: ReleaseScheduleOut
    warnings: list[str] = Field(default_factory=list)
    rollup_error: Optional[str] = None
    released_failures: list[WaveFailureOut] = Field(default_factory=list)

    @classmethod
    def build(
        cls,
        campaign,
        targets: Sequence,
        *,
        now: datetime,
        jobs_by_target: Optional[Mapping[UUID, Any]] = None,
        phase_rows_by_target: Optional[Mapping[UUID, Sequence]] = None,
        source_versions: Optional[Mapping[UUID, int]] = None,
        lessons_by_target: Optional[Mapping[UUID, LessonOut]] = None,
        usage_rows: Sequence = (),
        solver_enabled_observed: Optional[bool] = None,
        warnings: Optional[Sequence[str]] = None,
        rollup_error: Optional[str] = None,
        released_failures: Sequence["WaveFailureOut"] = (),
    ) -> "CampaignDetailOut":
        jobs_by_target = dict(jobs_by_target or {})
        phase_rows_by_target = dict(phase_rows_by_target or {})
        source_versions = dict(source_versions or {})
        lessons_by_target = dict(lessons_by_target or {})

        rendered: list[TargetReportOut] = []
        status_counts: dict[str, int] = {}
        buckets: dict[str, list[UUID]] = {bucket: [] for bucket in BUCKETS}
        judge: dict[str, int] = {}
        solver: dict[str, int] = {}
        provenance = ProvenanceOut()
        for target in targets:
            job = jobs_by_target.get(target.id)
            rows = phase_rows_by_target.get(target.id, ())
            out = TargetReportOut.from_row(
                target,
                now=now,
                revision_job=job,
                source_publication_version=source_versions.get(target.id),
                lesson=lessons_by_target.get(target.id),
                phase_rows=rows,
            )
            rendered.append(out)
            status_counts[out.status] = status_counts.get(out.status, 0) + 1
            buckets[out.bucket].append(out.id)
            for source, sink in (
                (out.judge_status_counts, judge),
                (out.solver_status_counts, solver),
            ):
                for key, count in source.items():
                    sink[key] = sink.get(key, 0) + count
            provenance = ProvenanceOut(
                copied_phase_count=(
                    provenance.copied_phase_count + out.copied_phase_count
                ),
                regenerated_phase_count=(
                    provenance.regenerated_phase_count + out.regenerated_phase_count
                ),
                phase_row_count=provenance.phase_row_count + len(rows),
            )

        summary = CampaignSummaryOut.from_row(campaign, status_counts=status_counts)

        offsets = sorted(
            job.scheduled_at
            for job in jobs_by_target.values()
            if getattr(job, "scheduled_at", None) is not None
        )
        schedule = ReleaseScheduleOut(
            job_count=len(jobs_by_target),
            wave_count=len({o for o in offsets}),
            final_offset_seconds=(
                int((offsets[-1] - offsets[0]).total_seconds()) if offsets else 0
            ),
            first_scheduled_at=offsets[0] if offsets else None,
            last_scheduled_at=offsets[-1] if offsets else None,
        )

        canary = [
            CanaryOut(
                target_id=out.id,
                toc_entry_id=out.toc_entry_id,
                output_language=out.output_language,
                status=out.status,
                revision_job_id=out.revision_job_id,
                revision_job_status=out.revision_job_status,
                content_path=out.content_path,
                download_path=out.download_path,
                copied_phase_count=out.copied_phase_count,
                regenerated_phase_count=out.regenerated_phase_count,
                judge_status_counts=out.judge_status_counts,
                solver_status_counts=out.solver_status_counts,
            )
            for out, target in zip(rendered, targets)
            if target.is_canary
        ]

        all_warnings = list(warnings or [])
        if campaign.approved_at is not None:
            stranded = [
                out
                for out in rendered
                if out.status == "planned" and out.revision_job_id is None
            ]
            if stranded:
                all_warnings.append(
                    _APPROVED_NOTHING_RELEASED.format(count=len(stranded))
                )

        revision_job_ids = {
            job.id for job in jobs_by_target.values() if getattr(job, "id", None)
        }
        return cls(
            **summary.model_dump(),
            selection_spec=dict(campaign.selection_spec or {}),
            launch_contract=dict(campaign.launch_contract or {}),
            solver_enabled_observed=solver_enabled_observed,
            buckets=buckets,
            targets=rendered,
            canary=canary,
            actual_cost=ActualCostOut.from_usage_rows(
                usage_rows, revision_job_ids=revision_job_ids
            ),
            judge_status_counts=judge,
            solver_status_counts=solver,
            provenance=provenance,
            release_schedule=schedule,
            warnings=all_warnings,
            rollup_error=rollup_error,
            released_failures=list(released_failures),
        )


class TargetActionOut(BaseModel):
    """The response to a per-target mutation: the refreshed target plus the
    context an operator needs to decide what happened."""

    target: TargetReportOut
    campaign_id: UUID
    campaign_status: str
    released_failures: list[WaveFailureOut] = Field(default_factory=list)
    #: Captured BEFORE `retry_publication` clears it — the service NULLs
    #: `publication_last_error` to give the operator a fresh attempt budget, so
    #: this is the only place the failure that prompted the retry survives.
    previous_publication_error: Optional[str] = None
    previous_publication_attempts: Optional[int] = None
    previous_publication_next_attempt_at: Optional[datetime] = None
