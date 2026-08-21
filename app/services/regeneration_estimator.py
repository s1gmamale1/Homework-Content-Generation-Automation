"""What a regeneration campaign will cost, before a single model call.

The estimate an operator approves. It is built from three things and nothing
else:

1. **the campaign's own stored launch contract** — a
   :class:`~app.schemas.regeneration_contract.ResolvedLaunchContract`, in which
   every provider/model is already concrete. This module NEVER reads
   ``launch_defaults`` and never resolves a role: a campaign is drafted at one
   moment and launched at another, and pricing whatever the defaults happened
   to say at estimate time would price a campaign that will not run. An
   unresolved contract is refused by ``ensure_resolved``, not repaired;
2. **the frozen phase plans** — copied phases cost zero, regenerated ones cost
   a call. A selective campaign that re-runs one phase of twelve must be priced
   as one phase of twelve;
3. **recent real spend** — successful ``transport=api`` ``agent_usages`` rows
   from the last :data:`OBSERVATION_WINDOW_DAYS` days, joined through
   ``phase_output_id`` to the phase they belong to, averaged per
   (operation, phase, provider, model) and priced with ``pricing.cost_usd``.
   Where there is no matching history — or where the only matching history
   BILLS NOTHING (see :func:`billable_token_volume`) — a documented
   conservative envelope (:data:`STATIC_TOKEN_ENVELOPE`) is priced with the
   same static table. A (provider, model) that table has no rate for is NOT
   quietly a $0 line: it keeps the $0.00 figure (no rate is ever invented) but
   is marked :data:`UNPRICED_BASIS` on the line, named in ``notes``, and
   flagged campaign-wide by
   :attr:`RegenerationEstimate.has_unpriced_lines`. Missing volume and a
   missing rate are independent: a priced pair falling back to the envelope is
   NOT unpriced, and an unpriced pair stays visibly unpriced after it.

The join is an INNER join on purpose: a usage row with no ``phase_output_id``
(a TOC extraction, a golden eval, a fidelity audit) is not evidence about a
homework phase. For judge and solver rows the operation NAMES its phase
(``judge:flashcards``), and that name must agree with the phase row the usage
actually hangs off — a row pointing at a synthetic ``__judge__`` phase is
discarded and reported in :attr:`RegenerationEstimate.notes` rather than
quietly pricing the wrong work.

Everything is a range. ``low`` is the happy path — one authoring call and one
judge call per regenerated phase, one solver call per solver-bearing phase, one
extraction if it was asked for. ``high`` adds, as SEPARATE line items, the
schema-retry budget and the configured judge/solver regeneration budgets. The
result carries ``is_estimate=True`` and a per-line explanation; it is never a
quote.

Read-only: one SELECT, no writes, no model calls.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.agent_usage import AgentUsage
from app.models.phase_output import PhaseOutput
from app.schemas.regeneration_contract import ResolvedLaunchContract, ensure_resolved
from app.services import pricing
from app.services.regeneration_planner import EXTRACT_PHASE, RegenerationPhasePlan

#: Matches spec §12: "successful API agent_usages from the previous 30 days".
OBSERVATION_WINDOW_DAYS = 30

# ─── operation kinds ──────────────────────────────────────────────────────
AUTHORING = "authoring"
JUDGE = "judge"
SOLVER = "solver"
EXTRACT = "extract"

# ─── budget buckets (why a call is counted) ───────────────────────────────
#: The calls a clean run makes. Counted in BOTH the low and the high estimate.
BASE = "base"
#: `run_phase(schema=…)` validates the response and retries ONCE on failure.
SCHEMA_RETRY = "schema_retry"
#: `settings.max_judge_regens` × (one regeneration + one re-judge) per phase.
JUDGE_REGENERATION = "judge_regeneration"
#: `settings.max_solve_regens` × (one regeneration + one re-solve) per phase.
SOLVER_REGENERATION = "solver_regeneration"

_BUDGET_ORDER = (BASE, SCHEMA_RETRY, JUDGE_REGENERATION, SOLVER_REGENERATION)

#: The key-bearing phases the solver checks. A COPY of `pipeline._SOLVER_PHASES`
#: rather than an import: importing the orchestrator into a pure pricing module
#: would drag the whole generation stack (and a future import cycle) in for four
#: strings. `test_the_solver_phase_set_matches_the_pipeline` is the drift guard.
SOLVER_PHASES = (
    "memory-check",
    "practice-error-detection",
    "practice-rlc",
    "boss-arena",
)

#: Conservative per-call token envelope, priced with the existing static
#: ``pricing.PRICE_MAP`` when the window holds no matching observation.
#:
#: These are deliberately CEILINGS, not means: the fallback fires exactly when
#: we have no evidence — for a model just switched to, or a phase never run on
#: this provider — and an estimate that under-states cost in that situation is
#: the one that hurts. Shapes, in the pipeline's own terms:
#:
#: * authoring — the phase contract plus the lesson extract plus up to three
#:   prior phase outputs (``flows.PHASE_DEPS`` tops out at three), against a
#:   full-length markdown answer;
#: * judge / solver — the same contract and context as the phase they inspect,
#:   against a short structured verdict, so the input is authoring-sized and
#:   the output is small;
#: * extract — the high-input, low-output shape: a whole lesson's text in, a
#:   flat factual summary out.
STATIC_TOKEN_ENVELOPE: dict[str, dict[str, int]] = {
    AUTHORING: {
        "prompt_tokens": 24_000,
        "output_tokens": 6_000,
        "cached_tokens": 0,
        "cache_creation_tokens": 0,
    },
    JUDGE: {
        "prompt_tokens": 24_000,
        "output_tokens": 2_000,
        "cached_tokens": 0,
        "cache_creation_tokens": 0,
    },
    SOLVER: {
        "prompt_tokens": 24_000,
        "output_tokens": 2_000,
        "cached_tokens": 0,
        "cache_creation_tokens": 0,
    },
    EXTRACT: {
        "prompt_tokens": 60_000,
        "output_tokens": 8_000,
        "cached_tokens": 0,
        "cache_creation_tokens": 0,
    },
}

STATIC_BASIS = "conservative static token envelope"

#: Stable, machine-matchable prefix for a line whose (provider, model) has no
#: rate in ``pricing.PRICE_MAP``.
#:
#: ``pricing.cost_usd`` bills such a pair $0 — correct for a cli-served call,
#: which costs no tokens, but silently WRONG as an estimate: the fallback fires
#: exactly when there is no evidence, and a model that reached the manifest
#: before the price map would otherwise render as a free line whose basis still
#: read "conservative". The dollar figure stays ``0.0`` (no rate is invented and
#: no arithmetic changes); the LINE says the price is absent, and
#: :attr:`RegenerationEstimate.has_unpriced_lines` says the TOTAL is incomplete.
UNPRICED_BASIS = (
    "UNPRICED: no rate for this provider/model in the static price table — "
    "$0.00 is an ABSENT price, not a free call"
)

#: Stable, machine-matchable prefix for the note emitted when the window's only
#: matching history bills nothing.
#:
#: A successful call whose every billable token field is 0 is MISSING VOLUME,
#: not free work: the row proves the call happened, not what it costs. Averaged
#: in as authoritative it prices a real future model call at $0.00 and the whole
#: campaign reads free — worse than having no history at all, because the line
#: also claims to be "observed". Such a group is dropped and the line falls back
#: to :data:`STATIC_TOKEN_ENVELOPE`, exactly like a phase never run on this
#: pair; the operator is told, because history that was ignored is a fact about
#: the estimate.
ZERO_VOLUME_HISTORY = "ZERO-VOLUME HISTORY"

#: The exact fields ``pricing.cost_usd`` charges for, and the only definition
#: of "real work" in this module: :func:`billable_token_volume` sums them, and
#: the unpriced detector asks ``pricing`` behaviorally on top of that sum
#: (nonzero volume that still prices at $0 means no rate exists) rather than
#: re-reading ``PRICE_MAP``, whose model resolution and per-provider fallbacks
#: belong to that module alone.
_TOKEN_KEYS = (
    "prompt_tokens",
    "output_tokens",
    "cached_tokens",
    "cache_creation_tokens",
)


def billable_token_volume(usage: Mapping) -> int:
    """Tokens in ``usage`` that ``pricing.cost_usd`` can actually charge for.

    Exactly the four fields that function reads, and nothing else. A summary
    figure such as ``agent_usages.total_tokens`` is deliberately NOT counted:
    it never reaches pricing (``observation_stmt`` does not select it and
    ``_Observation.usage`` does not emit it), and letting a non-billable total
    stand in for volume would let a row that charges nothing be read as
    evidence about what a call costs.

    Zero here means the same thing in both directions: whatever the rate, this
    shape prices at $0.00 — so it is a statement about VOLUME, never about
    whether a rate exists.
    """
    return sum(int(usage.get(key) or 0) for key in _TOKEN_KEYS)


def price_unit(
    provider: str, model: Optional[str], usage: Mapping
) -> tuple[float, bool]:
    """``(unit cost, is_unpriced)`` for one call of ``usage`` shape."""
    unit = pricing.cost_usd(provider, model, dict(usage))
    return unit, (unit == 0.0 and billable_token_volume(usage) > 0)


@dataclass(frozen=True)
class EstimateLineItem:
    """One priced row of the explanation the operator reads.

    ``calls_low == 0`` marks a budget line: it exists only in the high
    estimate, because a clean run never makes those calls.
    """

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
    #: True when no rate exists for this (provider, model): the $0.00 on this
    #: line is a missing price, not a free call.
    is_unpriced: bool = False


@dataclass(frozen=True)
class RegenerationEstimate:
    """A RANGE plus its derivation. Never a quote — see ``is_estimate``."""

    low_usd: float
    high_usd: float
    line_items: tuple[EstimateLineItem, ...]
    target_count: int
    regenerated_phase_count: int
    copied_phase_count: int
    regenerated_extract_count: int
    copied_extract_count: int
    window_start: datetime
    window_end: datetime
    notes: tuple[str, ...] = ()
    #: Always True. A field rather than a docstring so the value travels with
    #: the number into every UI and report that renders it.
    is_estimate: bool = True
    #: True when at least one line has no rate. ``low_usd``/``high_usd`` then
    #: UNDER-STATE the campaign by an unknown amount, and no caller may render
    #: them as a complete figure. A field, not just a note, so the condition
    #: survives into every screen and report machine-side. Appended LAST so the
    #: existing field order (and any positional construction) is untouched.
    has_unpriced_lines: bool = False


@dataclass
class _Observation:
    prompt_tokens: float
    output_tokens: float
    cached_tokens: float
    cache_creation_tokens: float
    samples: int

    def usage(self) -> dict:
        return {
            "prompt_tokens": int(round(self.prompt_tokens)),
            "output_tokens": int(round(self.output_tokens)),
            "cached_tokens": int(round(self.cached_tokens)),
            "cache_creation_tokens": int(round(self.cache_creation_tokens)),
        }


def classify_operation(operation: str) -> Optional[tuple[str, Optional[str]]]:
    """``(kind, the phase the operation NAMES)`` for a usage row, else None.

    The second element is ``None`` when the operation does not name a phase
    (``phase.run``, ``lesson.extract``) — those are trusted to the join. When it
    IS named, the caller must check it against the joined phase row.
    """
    if operation == "phase.run":
        return AUTHORING, None
    if operation.startswith("judge:"):
        return JUDGE, operation.split(":", 1)[1]
    if operation.startswith("solve:"):
        return SOLVER, operation.split(":", 1)[1]
    if operation == "lesson.extract":
        return EXTRACT, None
    return None


def observation_stmt(*, window_start: datetime, window_end: datetime):
    """The one SELECT this module runs: successful api calls in the window,
    joined to the phase row they produced, averaged per
    (operation, phase, provider, model).

    Aggregated in SQL because the row count over 30 fleet-days is large and the
    only thing needed downstream is the mean; the PRICING of that mean stays in
    Python, where ``pricing.cost_usd``'s per-provider cached-token semantics
    live.
    """
    return (
        select(
            AgentUsage.operation,
            PhaseOutput.phase_name,
            AgentUsage.provider,
            AgentUsage.model_name,
            func.avg(AgentUsage.prompt_tokens),
            func.avg(AgentUsage.output_tokens),
            func.avg(AgentUsage.cached_tokens),
            func.avg(AgentUsage.cache_creation_tokens),
            func.count(),
        )
        .join(PhaseOutput, PhaseOutput.id == AgentUsage.phase_output_id)
        .where(
            AgentUsage.auth_mode == "api",
            AgentUsage.success.is_(True),
            AgentUsage.started_at >= window_start,
            AgentUsage.started_at <= window_end,
        )
        .group_by(
            AgentUsage.operation,
            PhaseOutput.phase_name,
            AgentUsage.provider,
            AgentUsage.model_name,
        )
    )


def summarize_observations(
    rows: Sequence[Sequence],
) -> tuple[dict[tuple[str, str, str, Optional[str]], _Observation], list[str]]:
    """``({(kind, phase, provider, model): observation}, notes)``.

    Drops a judge/solver row whose operation names a phase the joined
    ``phase_outputs`` row does not agree with — that usage was recorded against
    a detached synthetic phase and says nothing about the phase being priced.
    """
    observed: dict[tuple[str, str, str, Optional[str]], _Observation] = {}
    detached: dict[tuple[str, str], int] = {}
    for row in rows:
        operation, phase_name, provider, model_name = row[0], row[1], row[2], row[3]
        classified = classify_operation(operation)
        if classified is None:
            continue
        kind, named_phase = classified
        if named_phase is not None and named_phase != phase_name:
            key = (operation, phase_name)
            detached[key] = detached.get(key, 0) + int(row[8])
            continue
        key = (kind, phase_name, provider, model_name)
        incoming = _Observation(
            prompt_tokens=float(row[4] or 0),
            output_tokens=float(row[5] or 0),
            cached_tokens=float(row[6] or 0),
            cache_creation_tokens=float(row[7] or 0),
            samples=int(row[8]),
        )
        previous = observed.get(key)
        if previous is None:
            observed[key] = incoming
            continue
        # Two SQL groups can only collapse into one key if two distinct
        # operations classify the same way for the same phase. Nothing does
        # that today; combine by sample-weighted mean anyway, so a future
        # operation name cannot make the answer depend on row order.
        total = previous.samples + incoming.samples
        observed[key] = _Observation(
            prompt_tokens=(
                previous.prompt_tokens * previous.samples
                + incoming.prompt_tokens * incoming.samples
            ) / total,
            output_tokens=(
                previous.output_tokens * previous.samples
                + incoming.output_tokens * incoming.samples
            ) / total,
            cached_tokens=(
                previous.cached_tokens * previous.samples
                + incoming.cached_tokens * incoming.samples
            ) / total,
            cache_creation_tokens=(
                previous.cache_creation_tokens * previous.samples
                + incoming.cache_creation_tokens * incoming.samples
            ) / total,
            samples=total,
        )
    notes = [
        f"discarded {count} api usage row(s) for operation {operation!r} linked to "
        f"phase row {phase_name!r}: judge/solver usage must hang off the phase "
        "output it inspected, not a detached synthetic phase"
        for (operation, phase_name), count in sorted(detached.items())
    ]
    return observed, notes


@dataclass
class _Counter:
    """Mutable call tally keyed by (budget, kind, phase, provider, model)."""

    calls: dict[tuple[str, str, str, str, Optional[str]], int] = field(
        default_factory=dict
    )

    def add(self, budget, kind, phase, provider, model, calls: int) -> None:
        if calls <= 0:
            return
        key = (budget, kind, phase, provider, model)
        self.calls[key] = self.calls.get(key, 0) + calls


async def estimate_regeneration(
    session: AsyncSession,
    *,
    targets: Sequence,
    plans: Mapping[UUID, RegenerationPhasePlan],
    launch_contract,
    now: datetime,
) -> RegenerationEstimate:
    """Price a campaign draft.

    ``targets`` are :class:`~app.services.regeneration_discovery.EligibleRegenerationSource`
    rows; ``plans`` maps each target's ``source_job_id`` to its frozen phase
    plan. That id — not the TOC entry — is the key, because a target row does
    not exist yet at draft time and one lesson may appear twice in a campaign
    (once per language) with a source job each.

    ``now`` is passed in rather than read from the clock so the window is a
    fixed, reproducible pair of timestamps for a given estimate.
    """
    contract: ResolvedLaunchContract = ensure_resolved(launch_contract)
    window_start = now - timedelta(days=OBSERVATION_WINDOW_DAYS)

    solver_enabled = bool(settings.solver_enabled)
    max_judge_regens = int(settings.max_judge_regens)
    max_solve_regens = int(settings.max_solve_regens)
    structured_authoring = bool(settings.structured_output_enabled)

    counter = _Counter()
    target_count = 0
    regenerated_phases = copied_phases = 0
    regenerated_extract = copied_extract = 0

    for target in targets:
        try:
            plan = plans[target.source_job_id]
        except KeyError:
            raise KeyError(
                f"no phase plan for source job {target.source_job_id} — every "
                "target must be priced against the plan it will run"
            ) from None
        target_count += 1
        # CONTENT phases only, on BOTH sides: extract is reported solely by
        # `copied_extract_count`/`regenerated_extract_count`, so that
        # `regenerated_phase_count + copied_phase_count` is the same
        # (content-phase) total whatever the extraction does.
        copied_phases += len([p for p in plan.copied_phases if p != EXTRACT_PHASE])
        if plan.refresh_extraction:
            regenerated_extract += 1
            counter.add(
                BASE, EXTRACT, EXTRACT_PHASE,
                contract.extract_provider, contract.extract_model, 1,
            )
        else:
            copied_extract += 1

        content_phases = [p for p in plan.regenerated_phases if p != EXTRACT_PHASE]
        regenerated_phases += len(content_phases)
        for phase in content_phases:
            counter.add(BASE, AUTHORING, phase, contract.provider, contract.model, 1)
            counter.add(
                BASE, JUDGE, phase, contract.judge_provider, contract.judge_model, 1
            )
            # The judge always passes a schema; authoring only does so while the
            # structured lane is on (`_generate_artifact`).
            counter.add(
                SCHEMA_RETRY, JUDGE, phase,
                contract.judge_provider, contract.judge_model, 1,
            )
            if structured_authoring:
                counter.add(
                    SCHEMA_RETRY, AUTHORING, phase, contract.provider, contract.model, 1
                )
            counter.add(
                JUDGE_REGENERATION, AUTHORING, phase,
                contract.provider, contract.model, max_judge_regens,
            )
            counter.add(
                JUDGE_REGENERATION, JUDGE, phase,
                contract.judge_provider, contract.judge_model, max_judge_regens,
            )

            if not (solver_enabled and phase in SOLVER_PHASES):
                continue
            counter.add(
                BASE, SOLVER, phase,
                contract.solver_provider, contract.solver_model, 1,
            )
            counter.add(
                SCHEMA_RETRY, SOLVER, phase,
                contract.solver_provider, contract.solver_model, 1,
            )
            counter.add(
                SOLVER_REGENERATION, AUTHORING, phase,
                contract.provider, contract.model, max_solve_regens,
            )
            counter.add(
                SOLVER_REGENERATION, SOLVER, phase,
                contract.solver_provider, contract.solver_model, max_solve_regens,
            )

    notes: list[str] = []
    if any(key[1] == SOLVER for key in counter.calls):
        notes.append(
            "solver calls are priced for every solver-bearing phase: the "
            "per-launch boss-arena solver toggle lives on the launch_defaults "
            "row, which the estimator deliberately does not read, so this is a "
            "conservative over-count while that toggle is off"
        )

    line_items: tuple[EstimateLineItem, ...] = ()
    if counter.calls:
        rows = (
            await session.execute(
                observation_stmt(window_start=window_start, window_end=now)
            )
        ).all()
        observed, observation_notes = summarize_observations(rows)
        notes.extend(observation_notes)

        priced: dict[tuple[str, str, Optional[str]], tuple[float, str, int, bool]] = {}
        unpriced_pairs: set[tuple[str, Optional[str]]] = set()
        items: list[EstimateLineItem] = []
        for (budget, kind, phase, provider, model), calls in counter.calls.items():
            cache_key = (kind, phase, provider, model)
            if cache_key not in priced:
                observation = observed.get(cache_key)
                zero_volume_samples = 0
                # Decided on the SAME mapping that would be priced (rounded
                # ints), not on the raw means, so the line can never be based
                # on volume `pricing.cost_usd` would not see.
                if observation is not None and not billable_token_volume(
                    observation.usage()
                ):
                    zero_volume_samples = observation.samples
                    observation = None
                if observation is None:
                    usage = STATIC_TOKEN_ENVELOPE[kind]
                    basis, samples = STATIC_BASIS, 0
                    if zero_volume_samples:
                        notes.append(
                            f"{ZERO_VOLUME_HISTORY}: {zero_volume_samples} "
                            f"successful api {kind} call(s) for phase {phase!r} on "
                            f"{provider}/{model} in the last "
                            f"{OBSERVATION_WINDOW_DAYS} days recorded no billable "
                            "tokens (prompt, output, cached and cache-creation "
                            "all 0) — that history says a call happened, not what "
                            "it costs, so this line is priced from the "
                            f"{STATIC_BASIS} instead"
                        )
                    else:
                        notes.append(
                            f"no successful api {kind} call for phase {phase!r} on "
                            f"{provider}/{model} in the last {OBSERVATION_WINDOW_DAYS} "
                            f"days — priced from the {STATIC_BASIS}"
                        )
                else:
                    usage = observation.usage()
                    basis = (
                        f"observed mean of {observation.samples} api call(s) in "
                        f"the last {OBSERVATION_WINDOW_DAYS} days"
                    )
                    samples = observation.samples
                unit, unpriced = price_unit(provider, model, usage)
                if unpriced:
                    # The volume provenance stays in the string — what changes
                    # is that the line no longer CLAIMS to be priced.
                    basis = f"{UNPRICED_BASIS}; volume from {basis}"
                    if (provider, model) not in unpriced_pairs:
                        unpriced_pairs.add((provider, model))
                        notes.append(
                            f"UNPRICED: {provider}/{model} has no entry in the "
                            "static price table — every line for it shows $0.00, "
                            "which means the rate is UNKNOWN, not that the calls "
                            "are free; this estimate UNDER-STATES the campaign by "
                            "an unknown amount and must not be approved as a "
                            "complete figure"
                        )
                priced[cache_key] = (unit, basis, samples, unpriced)
            unit, basis, samples, unpriced = priced[cache_key]
            calls_low = calls if budget == BASE else 0
            items.append(
                EstimateLineItem(
                    budget=budget,
                    kind=kind,
                    phase=phase,
                    provider=provider,
                    model=model,
                    calls_low=calls_low,
                    calls_high=calls,
                    unit_cost_usd=unit,
                    cost_low_usd=unit * calls_low,
                    cost_high_usd=unit * calls,
                    basis=basis,
                    observations=samples,
                    is_unpriced=unpriced,
                )
            )
        items.sort(
            key=lambda li: (
                _BUDGET_ORDER.index(li.budget),
                li.phase,
                li.kind,
                str(li.model),
            )
        )
        line_items = tuple(items)

    return RegenerationEstimate(
        low_usd=sum(li.cost_low_usd for li in line_items),
        high_usd=sum(li.cost_high_usd for li in line_items),
        line_items=line_items,
        target_count=target_count,
        regenerated_phase_count=regenerated_phases,
        copied_phase_count=copied_phases,
        regenerated_extract_count=regenerated_extract,
        copied_extract_count=copied_extract,
        window_start=window_start,
        window_end=now,
        notes=tuple(notes),
        has_unpriced_lines=any(li.is_unpriced for li in line_items),
    )
