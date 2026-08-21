"""Can any worker actually RUN this campaign? — the pre-approval preflight.

A regeneration campaign freezes its provider/model/transport selection at draft
time (``ResolvedLaunchContract``) and launches later against whatever fleet is
up. Nothing in that flow checks whether the fleet HOLDS the credentials the
contract needs. When it does not, the failure is silent and expensive: every
revision job is created, stamped and left ``pending`` forever, because
``jobs_repo.claim_next_job`` refuses to hand an api job to a worker without
that provider's key. The operator sees a launched campaign making no progress
and no error anywhere.

This module answers the question BEFORE approval:

    ``required_api_providers(contract)``   which credentials the contract needs
    ``worker_can_execute(contract, w)``    can THIS worker serve it
    ``check_active_workers(session, …)``   can the LIVE fleet serve it

**The claim gate is the authority.** Every rule below is a transcription of
``app/repositories/jobs.py::claim_next_job`` — the SQL that actually decides
whether a job is claimable. A preflight that disagrees with it is worse than
none: too strict and it refuses campaigns the fleet would run, too lax and it
promises capacity that does not exist and the campaign stalls anyway.
``tests/repositories/test_regeneration_claim_parity.py`` asserts the two agree
on a real Postgres, row by row, so they cannot drift on the self-grade and
self-solve rules (the two places where the credential a job needs is NOT the
one stamped on it).

Two places where this module deliberately implements the CLAIM GATE rather than
the literal pseudocode of the task brief (``2026-08-21-guided-regeneration-ux-
implementation``, Task 3 Step 3). The brief's own Step 4 makes parity with
``claim_next_job`` an explicit correctness contract, so where the two conflict,
parity wins:

1. **The content provider's credential is CONDITIONAL on ``transport``.** The
   brief opens with ``required = {contract.provider}`` unconditionally. The
   claim gate's ``content_ok`` is ``(transport == 'cli') OR api_cap[provider]``
   — a cli job needs no credential at all. Taking the brief literally would
   refuse an all-cli campaign on every credential-less worker in the fleet,
   i.e. exactly the workers that can run it.
2. **The fleet api pause refuses only contracts that TOUCH api.** The brief's
   Step-3 prose says ``worker_can_execute`` requires ``fleet_api_paused is
   False`` unconditionally, while its own Step-1 prose says the pause "refuses
   every API contract". The claim gate's ``fleet_gate`` is ``(NOT
   job_resolved_api) OR (NOT fleet_api_paused)``: a cli-only job is never
   blocked. "Touches api" is exactly ``required_api_providers(contract)`` being
   non-empty — the two conditions are the same predicate, because content
   contributes a provider iff ``transport == 'api'`` and each role contributes
   one iff its own ``*_needs_api`` arm is true.

What is NOT parity, on purpose: the offline/stale and ``draining`` refusals.
``claim_next_job`` receives a credential dict, never the worker registry, so it
cannot see either. They are preflight-only judgements about whether a worker is
CAPACITY: a dead worker's credentials run nothing, and a draining worker — which
may still claim until it observes the drain signal — must not be counted as
capacity for a campaign that has not launched yet. Both make the preflight
STRICTER than the gate, which is the safe direction: the campaign is refused
with a reason instead of stalling.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories import budget as budget_repo
from app.repositories import workers as workers_repo
from app.schemas.regeneration_contract import ResolvedLaunchContract
from app.services.agent_models import resolve_role_transport
from app.services.model_tiers import resolve_judge, resolve_solver

# `workers.status` is a free String(32) column; this is the one value that means
# "an operator asked this worker to stop taking work". Named here rather than
# inlined so the drain semantics are greppable from both sides.
_DRAINING = "draining"

# The exact reason the fleet-wide api spend pause surfaces with. Rendered by
# later tasks; pinned by test so a reword is a visible change.
_PAUSED_REASON = "fleet API spend is paused"


@dataclass(frozen=True)
class WorkerExecutability:
    """Whether the live fleet can run a contract, and what is missing if not.

    ``workers_online`` counts every worker whose heartbeat is fresh —
    INCLUDING draining ones. That is deliberate: "3 workers online, none can
    run this" is the diagnosis an operator needs, and hiding the draining
    workers would make the same fleet look empty for one contract and populated
    for another.

    ``required_api_providers`` is sorted so the tuple is stable to render and
    to assert on.
    """

    ok: bool
    workers_online: int
    compatible_worker_ids: tuple[str, ...]
    required_api_providers: tuple[str, ...]
    fleet_api_paused: bool
    reason: Optional[str]


def required_api_providers(contract: ResolvedLaunchContract) -> frozenset[str]:
    """Every api credential a job launched from ``contract`` would need.

    One entry per arm of ``claim_next_job``'s capability routing:

    * **content** — only when ``transport == 'api'``. ``content_ok`` passes
      unconditionally on cli (see module docstring, deviation 1).
    * **extract** — when its resolved transport is api, keyed on the STAMPED
      ``extract_provider``. Extract has no self-fallback: the gate gates on the
      stamped provider directly.
    * **judge** / **solver** — when their resolved transport is api, keyed on
      the provider ``model_tiers`` will actually pick. That is NOT always the
      stamped one: a judge (or solver) that resolves to the generator's own
      model is hard-swapped to a frontier PEER by ``_self_fallback``, so an
      all-gemini contract can need an ``ANTHROPIC_API_KEY`` it never mentions
      (and a claude-opus-4-7 generator that grades itself needs a gemini one —
      the fallback is generator-aware and returns the alternate peer for
      exactly that case). Calling ``resolve_judge`` / ``resolve_solver`` is
      what keeps this in step with the runtime decision; re-deriving the rule
      here would be a second copy to drift.

    A role whose resolved transport is cli contributes nothing, whatever its
    provider — a cli spawn's credentials are scrubbed by ``agent._auth_env``.

    Returns a ``frozenset`` because the result is a set-membership question
    (the caller ANDs it against a worker's capability blob) and must not be
    mutated by one caller on behalf of the next.
    """
    required: set[str] = set()

    if contract.transport == "api":
        required.add(contract.provider)

    if resolve_role_transport(contract.extract_transport, contract.transport) == "api":
        required.add(contract.extract_provider)

    if resolve_role_transport(contract.judge_transport, contract.transport) == "api":
        judge_provider, _judge_model = resolve_judge(
            contract.provider,
            contract.model,
            contract.judge_provider,
            contract.judge_model,
        )
        required.add(judge_provider)

    if resolve_role_transport(contract.solver_transport, contract.transport) == "api":
        solver_provider, _solver_model = resolve_solver(
            contract.provider,
            contract.model,
            contract.solver_provider,
            contract.solver_model,
        )
        required.add(solver_provider)

    return frozenset(required)


def _published_api_capabilities(worker: dict[str, Any]) -> dict[str, Any]:
    """The ``api`` section of a worker's published capability blob, defensively.

    ``workers.capabilities`` is nullable (a worker that has never heartbeat a
    blob, or an older worker that published a different shape), so every lookup
    here has to survive ``None`` and a missing ``"api"`` key. Absent evidence is
    NOT a credential: the result is an empty mapping, and every membership test
    against it fails closed.
    """
    blob = worker.get("capabilities") or {}
    if not isinstance(blob, dict):
        return {}
    api = blob.get("api") or {}
    return api if isinstance(api, dict) else {}


def worker_can_execute(
    contract: ResolvedLaunchContract,
    worker: dict[str, Any],
    fleet_api_paused: bool = False,
) -> bool:
    """Would this worker claim a revision job launched from ``contract``?

    ``worker`` is one row as ``workers_repo.list_with_liveness`` returns it:
    ``{"pc_id", "last_heartbeat", "status", "notes", "capabilities", "online"}``.

    Three independent refusals:

    * **liveness / drain** — ``online`` must be true and ``status`` must not be
      ``draining``. Preflight-only (see module docstring); a stale heartbeat
      already arrives here as ``online=False``, so "stale" and "offline" are
      one rule, decided against the DB clock inside the repository.
    * **fleet api pause** — refuses iff the contract touches api at all, which
      mirrors ``claim_next_job``'s ``fleet_gate`` (deviation 2). A cli-only
      contract is unaffected: the pause is a SPEND lever and a cli campaign
      spends nothing.
    * **credentials** — every provider in ``required_api_providers`` must be
      truthy in the worker's published ``capabilities["api"]``. ANDed, exactly
      like the SQL gate ANDs its per-role arms: one missing key is enough.
    """
    if worker.get("online") is not True:
        return False
    if worker.get("status") == _DRAINING:
        return False

    required = required_api_providers(contract)
    if fleet_api_paused and required:
        return False

    api = _published_api_capabilities(worker)
    return all(bool(api.get(provider)) for provider in required)


async def check_active_workers(
    session: AsyncSession,
    contract: ResolvedLaunchContract,
    stale_after_seconds: int,
) -> WorkerExecutability:
    """Ask the LIVE fleet whether ``contract`` is launchable, with a reason.

    Reads exactly two row-sets: the ``budget_state`` singleton (the fleet-wide
    api spend pause) and the worker registry with derived liveness. Both are
    read through their repositories so this stays the only place regeneration
    reasons about fleet capacity.

    The refusals are ordered by what an operator can ACT on:

    1. the fleet api pause — a lever someone deliberately pulled, and the one
       fact that makes every other diagnosis noise. Checked first, but (see
       module docstring, deviation 2) only when the contract actually spends on
       api; a cli campaign is launchable during an api pause, exactly as
       ``claim_next_job`` would claim it;
    2. nothing online at all — start a worker;
    3. everything online is draining — stop the drain, or wait;
    4. online workers exist but none holds the credentials — the missing
       providers are named, because that is a ``.env`` fix on a specific host.
    """
    required = required_api_providers(contract)
    required_sorted = tuple(sorted(required))

    budget_state = await budget_repo.get_state(session)
    fleet_api_paused = budget_state.api_paused_at is not None

    workers = await workers_repo.list_with_liveness(
        session, stale_after_seconds=stale_after_seconds
    )
    online = [w for w in workers if w.get("online") is True]
    workers_online = len(online)

    if fleet_api_paused and required:
        return WorkerExecutability(
            ok=False,
            workers_online=workers_online,
            compatible_worker_ids=(),
            required_api_providers=required_sorted,
            fleet_api_paused=True,
            reason=_PAUSED_REASON,
        )

    # The compatibility verdict is `worker_can_execute`'s alone — including the
    # drain rule — so the preflight has exactly ONE definition of "this worker
    # can run it" and the parity test covers the definition this function uses.
    # `accepting` exists only to tell an all-draining fleet apart from an
    # under-credentialed one when composing the reason.
    compatible = tuple(
        str(w.get("pc_id"))
        for w in online
        if worker_can_execute(contract, w, fleet_api_paused)
    )
    accepting = [w for w in online if w.get("status") != _DRAINING]

    if compatible:
        reason = None
    elif workers_online == 0:
        reason = "no workers are online"
    elif not accepting:
        reason = "every online worker is draining"
    elif required_sorted:
        reason = (
            "no online worker holds api credentials for "
            + ", ".join(required_sorted)
        )
    else:
        reason = "no online worker can run this contract"

    return WorkerExecutability(
        ok=bool(compatible),
        workers_online=workers_online,
        compatible_worker_ids=compatible,
        required_api_providers=required_sorted,
        fleet_api_paused=fleet_api_paused,
        reason=reason,
    )
