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

What is NOT parity, on purpose: the registry refusals. ``claim_next_job``
receives a credential dict, never the worker registry, so it cannot see
liveness or ``workers.status`` at all. They are preflight-only judgements about
whether a worker is CAPACITY, and they are an ALLOWLIST — a worker counts only
when it is live AND ``status == 'online'``:

* a dead worker's credentials run nothing;
* a ``draining`` worker may still claim until it observes the drain signal, but
  must not be PROMISED as capacity for a campaign that has not launched yet;
* every other status must fail closed. ``workers.status`` is a free
  ``String(32)`` with no CHECK constraint (``app/models/worker.py``), and
  ``workers_repo.mark_stale_offline`` stamps ``'offline'`` on its OWN window
  while liveness here is derived from the CALLER's ``stale_after_seconds`` — so
  a caller with a wider window legitimately sees ``status='offline'`` together
  with ``online=True``. A denylist that refused only ``draining`` would count
  that row as capacity, approve the campaign, and let it stall forever: exactly
  the failure this module exists to prevent. Any status added later
  (``paused``, ``quarantined``) is refused for free.

All of these make the preflight STRICTER than the gate, which is the safe
direction: the campaign is refused with a reason instead of stalling.
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

# The ONLY `workers.status` value that means "this worker is taking work".
# An allowlist, not a denylist — see the module docstring: the column has no
# CHECK constraint, so anything else (`draining`, `offline`, a status added
# next year) must fail closed rather than be counted as fleet capacity.
_STATUS_ONLINE = "online"

# The exact reason the fleet-wide api spend pause surfaces with. Rendered by
# later tasks; pinned by test so a reword is a visible change.
_PAUSED_REASON = "fleet API spend is paused"

# How many DISTINCT `workers.status` values the status rung will name.
# `workers.status` is an unbounded String(32) written by the worker process
# itself, and the rendered set is otherwise bounded only by the live-worker
# count — a 40-worker fleet with distinct statuses would put ~1.3 KB of
# worker-supplied text into one operator-facing sentence. Three is enough to
# tell "they are all draining" from "some are draining, some went offline",
# which is the whole decision this rung supports.
_MAX_STATUSES_IN_REASON = 3


@dataclass(frozen=True)
class WorkerExecutability:
    """Whether the live fleet can run a contract, and what is missing if not.

    ``workers_online`` is pure LIVENESS: every worker whose heartbeat is fresh,
    whatever its registry ``status`` (draining, offline, anything). That is
    deliberate — "3 workers online, none can run this" is the diagnosis an
    operator needs, and filtering by status here would make the same fleet look
    empty for one contract and populated for another. ``compatible_worker_ids``
    is the strict subset that can actually serve THIS contract.

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


def _pause_blocks(required: frozenset[str], fleet_api_paused: bool) -> bool:
    """Does the fleet-wide api spend pause block a contract needing ``required``?

    ONE definition, two readers (``worker_can_execute`` decides with it,
    ``check_active_workers`` words its reason with it) — the rule must not be
    written twice, because a divergence between the verdict and the message it
    carries is invisible to a test that only reads one of them.

    Mirrors ``claim_next_job``'s ``fleet_gate`` (module docstring, deviation 2):
    the pause blocks a contract iff the contract actually spends on api, which
    is exactly ``required_api_providers`` being non-empty.
    """
    return fleet_api_paused and bool(required)


def _published_api_credentials(worker: dict[str, Any]) -> frozenset[str]:
    """The api credentials a worker CLAIMS to hold, read fail-closed.

    ``workers.capabilities`` is a nullable JSONB blob published by the worker
    itself, so this function's whole job is to survive whatever is in it:
    ``None`` (a worker that never heartbeat a blob), a non-mapping, a missing
    or non-mapping ``"api"`` section, and values that are not booleans.

    Only a literal ``True`` counts. ``worker._capability_blob`` publishes real
    Python bools, so nothing legitimate is lost — but a blob from an older or
    hand-edited worker carrying the STRING ``"false"`` is truthy in Python, and
    ``bool("false")`` would hand a credential to a worker that just told us it
    has none. Absent, malformed and non-``True`` evidence are all the same
    answer here: not a credential.
    """
    blob = worker.get("capabilities")
    if not isinstance(blob, dict):
        return frozenset()
    api = blob.get("api")
    if not isinstance(api, dict):
        return frozenset()
    return frozenset(name for name, value in api.items() if value is True)


def worker_can_execute(
    contract: ResolvedLaunchContract,
    worker: dict[str, Any],
    fleet_api_paused: bool = False,
) -> bool:
    """Would this worker claim a revision job launched from ``contract``?

    ``worker`` is one row as ``workers_repo.list_with_liveness`` returns it:
    ``{"pc_id", "last_heartbeat", "status", "notes", "capabilities", "online"}``.

    Three independent refusals:

    * **registry** — the worker must be live (``online``) AND registered
      ``status == 'online'``. An ALLOWLIST: see the module docstring for why
      refusing only ``draining`` fails open on ``offline``. Preflight-only; a
      stale heartbeat already arrives here as ``online=False``, so "stale" and
      "not live" are one rule, decided against the DB clock inside the
      repository.
    * **fleet api pause** — ``_pause_blocks``: refuses iff the contract touches
      api at all, mirroring ``claim_next_job``'s ``fleet_gate`` (deviation 2).
      A cli-only contract is unaffected: the pause is a SPEND lever and a cli
      campaign spends nothing.
    * **credentials** — every provider in ``required_api_providers`` must be
      published as ``True`` by the worker. A subset test, which is how the SQL
      gate ANDs its per-role arms: one missing credential is enough to refuse.
    """
    if worker.get("online") is not True:
        return False
    if worker.get("status") != _STATUS_ONLINE:
        return False

    required = required_api_providers(contract)
    if _pause_blocks(required, fleet_api_paused):
        return False

    return required <= _published_api_credentials(worker)


async def check_active_workers(
    session: AsyncSession,
    contract: ResolvedLaunchContract,
    *,
    stale_after_seconds: int,
) -> WorkerExecutability:
    """Ask the LIVE fleet whether ``contract`` is launchable, with a reason.

    Reads exactly two row-sets: the ``budget_state`` singleton (the fleet-wide
    api spend pause) and the worker registry with derived liveness. Both are
    read through their repositories so this stays the only place regeneration
    reasons about fleet capacity.

    ``stale_after_seconds`` is keyword-only, matching every neighbour that
    takes it (``workers_repo.list_with_liveness`` / ``has_live_workers`` /
    ``aggregate_fleet_capability``) — the value is a policy window, and a bare
    integer at a call site reads as anything.

    **``ok`` is ``worker_can_execute``'s verdict and nothing else.** This
    function does not re-decide any rule; it only counts, names and explains.
    That is what keeps the parity test (which exercises ``worker_can_execute``)
    load-bearing for what an operator actually sees here.

    The REASON ladder is ordered by what an operator can ACT on, and the order
    is part of the contract — each rung is pinned by test:

    1. the fleet api pause — a lever someone deliberately pulled, and the one
       fact that makes every other diagnosis noise. It outranks even an empty
       fleet: starting a worker would not help. Worded from ``_pause_blocks``,
       the same predicate ``worker_can_execute`` refused with, so the verdict
       and its explanation cannot drift apart;
    2. nothing live at all — start a worker. Worded "live", never "online":
       rung 3 reports on ``workers.status``, whose value is the word
       ``'online'``, and one word meaning two things in one panel is how
       ``workers_online: 1`` ends up next to "no worker has status 'online'";
    3. live workers exist but none is registered ``status='online'`` — the
       statuses actually seen are named (capped, ``_MAX_STATUSES_IN_REASON``),
       because "draining" and "offline" call for different operator actions;
    4. accepting workers exist but none holds the credentials — the missing
       providers are named, because that is a ``.env`` fix on a specific host.

    There is deliberately no fifth "none of the above" rung: a contract that
    needs no credential is executable on ANY accepting worker, so rungs 1-4 are
    exhaustive whenever ``compatible`` is empty. A fallback rung would be
    permanently dead code asserting otherwise.

    Raises ``RuntimeError`` (from ``budget_repo.get_state``) if the
    ``budget_state`` singleton is missing — a broken migration state. That is
    deliberately NOT converted into ``ok=False``: "the fleet cannot run this"
    and "this deployment is misconfigured" are different answers, and silently
    reporting the first would send an operator hunting for credentials.
    """
    required = required_api_providers(contract)
    required_sorted = tuple(sorted(required))

    budget_state = await budget_repo.get_state(session)
    fleet_api_paused = budget_state.api_paused_at is not None

    workers = await workers_repo.list_with_liveness(
        session, stale_after_seconds=stale_after_seconds
    )
    live = [w for w in workers if w.get("online") is True]
    workers_online = len(live)

    compatible = tuple(
        str(w.get("pc_id"))
        for w in live
        if worker_can_execute(contract, w, fleet_api_paused)
    )
    # Only ever used to WORD rung 3 — never to decide `ok`.
    accepting = [w for w in live if w.get("status") == _STATUS_ONLINE]

    if compatible:
        reason = None
    elif _pause_blocks(required, fleet_api_paused):
        reason = _PAUSED_REASON
    elif workers_online == 0:
        # "live", not "online": rung 3 below reports on `workers.status`, whose
        # value is literally the word "online". Two meanings of one word in the
        # same panel would render as `workers_online: 1` beside "no worker has
        # status 'online'", which reads like a contradiction.
        reason = "no workers are live (no fresh heartbeat)"
    elif not accepting:
        seen = sorted({str(w.get("status")) for w in live})
        shown = seen[:_MAX_STATUSES_IN_REASON]
        if len(seen) > _MAX_STATUSES_IN_REASON:
            shown = shown + ["…"]
        reason = (
            "no live worker has status 'online' "
            f"(saw: {', '.join(shown)})"
        )
    else:
        reason = (
            "no online worker holds api credentials for "
            + ", ".join(required_sorted)
        )

    return WorkerExecutability(
        ok=bool(compatible),
        workers_online=workers_online,
        compatible_worker_ids=compatible,
        required_api_providers=required_sorted,
        fleet_api_paused=fleet_api_paused,
        reason=reason,
    )
