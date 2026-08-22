"""Preflight: can any worker in the fleet actually RUN this launch contract?

A regeneration campaign is drafted, priced and approved in one sitting and then
launched against whatever fleet happens to be up. The credential shape of that
fleet is not the operator's — an all-Vertex fleet has no ``ANTHROPIC_API_KEY``,
so a contract whose judge resolves to claude/api produces revision jobs that
sit ``pending`` forever: ``jobs_repo.claim_next_job`` refuses them and nothing
in the UI says why. This module's job is to say why BEFORE the campaign is
approved.

Every assertion here is written against the REAL claim gate's rules
(``app/repositories/jobs.py::claim_next_job``), because a preflight that
disagrees with the gate is worse than no preflight: it either promises capacity
that does not exist, or refuses a campaign the fleet would happily run.
``tests/repositories/test_regeneration_claim_parity.py`` proves the agreement
against a real Postgres; the tests here pin the individual rules.

PURE by construction: contracts are real ``ResolvedLaunchContract`` objects (so
every production validator runs) and the only DB boundary — ``workers_repo.
list_with_liveness`` / ``budget_repo.get_state`` — is monkeypatched. No
database, no ``RUN_DB_INTEGRATION``.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.schemas.regeneration_contract import ResolvedLaunchContract
from app.services.regeneration_executability import (
    WorkerExecutability,
    check_active_workers,
    required_api_providers,
    worker_can_execute,
)

# ─────────────────────────────────────────────────────────────────────────
# builders
# ─────────────────────────────────────────────────────────────────────────

# Every model below is valid on BOTH transports, so a test can flip
# `transport` without also having to re-pick models (the gemini-3.5/3.6 flash
# family is api-only — `agent_models.GEMINI_API_ONLY_MODELS` — and would make a
# cli contract fail validation for the wrong reason).
_DEFAULTS = {
    "provider": "gemini",
    "model": "gemini-3.1-pro-preview",
    "transport": "api",
    "extract_transport": "inherit",
    "extract_provider": "gemini",
    "extract_model": "gemini-3.1-flash-lite-preview",
    "judge_transport": "inherit",
    "judge_provider": "claude",
    "judge_model": "claude-opus-4-7",
    "solver_transport": "inherit",
    "solver_provider": "claude",
    "solver_model": "claude-sonnet-4-6",
    "session_limit_strategy": "pause",
}


def resolved_contract(**overrides) -> ResolvedLaunchContract:
    """A real, fully-resolved contract — every production validator runs."""
    return ResolvedLaunchContract(**{**_DEFAULTS, **overrides})


def cli_only_contract(**overrides) -> ResolvedLaunchContract:
    """A contract that spends nothing on an api credential: cli everywhere."""
    return resolved_contract(
        transport="cli",
        extract_transport="cli",
        judge_transport="cli",
        solver_transport="cli",
        **overrides,
    )


_DERIVE = object()


def worker_view(
    pc_id: str = "pc-1",
    *,
    api: dict | None = None,
    online: bool = True,
    status: str = "online",
    capabilities=_DERIVE,
    heartbeat_age_seconds: int = 5,
) -> dict:
    """One row as ``workers_repo.list_with_liveness`` returns it."""
    caps = (
        {"cli": {"gemini": True}, "api": dict(api or {})}
        if capabilities is _DERIVE
        else capabilities
    )
    return {
        "pc_id": pc_id,
        "last_heartbeat": datetime.now(timezone.utc)
        - timedelta(seconds=heartbeat_age_seconds),
        "status": status,
        "notes": None,
        "capabilities": caps,
        "online": online,
    }


_ALL_CREDS = {"claude": True, "gemini": True, "clodex": True}


class _StubSession:
    """`check_active_workers` never touches the session itself — both reads go
    through repository functions the tests monkeypatch."""


@pytest.fixture()
def fleet(monkeypatch):
    """Install a fake fleet + budget state; return the recorded call kwargs."""
    from app.repositories import budget as budget_repo
    from app.repositories import workers as workers_repo

    calls: dict = {}

    def install(workers: list[dict], *, api_paused: bool = False):
        async def _list_with_liveness(session, *, stale_after_seconds):
            calls["session"] = session
            calls["stale_after_seconds"] = stale_after_seconds
            return list(workers)

        async def _get_state(session):
            calls["budget_session"] = session
            return SimpleNamespace(
                api_paused_at=(
                    datetime.now(timezone.utc) if api_paused else None
                ),
                api_paused_reason="daily cap" if api_paused else None,
            )

        monkeypatch.setattr(
            workers_repo, "list_with_liveness", _list_with_liveness
        )
        monkeypatch.setattr(budget_repo, "get_state", _get_state)
        return calls

    return install


# ─────────────────────────────────────────────────────────────────────────
# required_api_providers — one rule per claim-gate arm
# ─────────────────────────────────────────────────────────────────────────


def test_all_cli_contract_requires_no_api_credential():
    """The claim gate's `content_ok` passes on `transport='cli'` with NO
    credential at all, and every role arm is skipped when its resolved
    transport is cli. A cli contract therefore requires nothing — a preflight
    that demanded the content provider's api key here would refuse a campaign
    the fleet can run on any worker."""
    assert required_api_providers(cli_only_contract()) == frozenset()


def test_content_provider_is_required_only_under_api_transport():
    """`content_ok = (transport == 'cli') OR api_cap[provider]`."""
    api = resolved_contract(
        provider="claude",
        model="claude-sonnet-4-6",
        transport="api",
        extract_transport="cli",
        judge_transport="cli",
        solver_transport="cli",
    )
    assert required_api_providers(api) == frozenset({"claude"})

    # Rebuilt through the constructor, NOT `model_copy(update=...)`: model_copy
    # skips `_validate_against_production_rules`, so it can mint a contract
    # shape production could never build and this file's "every production
    # validator runs" promise would quietly stop being true.
    cli = resolved_contract(**{**api.model_dump(), "transport": "cli"})
    assert required_api_providers(cli) == frozenset()


def test_extract_role_drives_its_own_credential_under_a_cli_job():
    """Content is claude/cli (contributes nothing); only extract is api."""
    contract = resolved_contract(
        provider="claude",
        model="claude-sonnet-4-6",
        transport="cli",
        extract_transport="api",
        extract_provider="gemini",
        extract_model="gemini-3.5-flash",
        judge_transport="cli",
        solver_transport="cli",
    )
    assert required_api_providers(contract) == frozenset({"gemini"})


def test_judge_role_drives_its_own_credential_under_a_cli_job():
    contract = resolved_contract(
        provider="claude",
        model="claude-sonnet-4-6",
        transport="cli",
        extract_transport="cli",
        extract_provider="claude",
        extract_model="claude-haiku-4-5-20251001",
        judge_transport="api",
        judge_provider="gemini",
        judge_model="gemini-3.5-flash",
        solver_transport="cli",
    )
    assert required_api_providers(contract) == frozenset({"gemini"})


def test_solver_role_drives_its_own_credential_under_a_cli_job():
    contract = resolved_contract(
        provider="claude",
        model="claude-sonnet-4-6",
        transport="cli",
        extract_transport="cli",
        extract_provider="claude",
        extract_model="claude-haiku-4-5-20251001",
        judge_transport="cli",
        solver_transport="api",
        solver_provider="gemini",
        solver_model="gemini-3.5-flash",
    )
    assert required_api_providers(contract) == frozenset({"gemini"})


def test_inherited_role_transport_follows_a_cli_job():
    """'inherit' under `transport='cli'` resolves to cli for every role."""
    contract = resolved_contract(transport="cli")
    assert contract.judge_transport == "inherit"
    assert required_api_providers(contract) == frozenset()


def test_inherited_role_transport_follows_an_api_job():
    """The same contract under `transport='api'` pulls in every role."""
    contract = resolved_contract(
        transport="api",
        provider="gemini",
        model="gemini-3.1-pro-preview",
        extract_provider="gemini",
        extract_model="gemini-3.1-flash-lite-preview",
        judge_provider="claude",
        judge_model="claude-opus-4-7",
        solver_provider="claude",
        solver_model="claude-sonnet-4-6",
    )
    assert required_api_providers(contract) == frozenset({"gemini", "claude"})


def test_self_solver_requires_peer_provider_credential():
    """Self-solve swaps to a frontier PEER, so the peer's credential is what
    the fleet actually needs. Every other role here is gemini, so a preflight
    that trusted the stamped `solver_provider` would report {'gemini'} and the
    campaign would stall on a Vertex-only worker."""
    contract = resolved_contract(
        provider="gemini",
        model="gemini-3.1-pro-preview",
        transport="api",
        extract_provider="gemini",
        extract_model="gemini-3.1-flash-lite-preview",
        judge_provider="gemini",
        judge_model="gemini-3.5-flash",
        solver_provider="gemini",
        solver_model="gemini-3.1-pro-preview",
    )
    assert required_api_providers(contract) == frozenset({"gemini", "claude"})


def test_self_grade_requires_peer_provider_credential():
    """Mirror of the solver case on the judge arm."""
    contract = resolved_contract(
        provider="gemini",
        model="gemini-3.1-pro-preview",
        transport="api",
        extract_provider="gemini",
        extract_model="gemini-3.1-flash-lite-preview",
        judge_provider="gemini",
        judge_model="gemini-3.1-pro-preview",
        solver_provider="gemini",
        solver_model="gemini-3.5-flash",
    )
    assert required_api_providers(contract) == frozenset({"gemini", "claude"})


def test_self_grade_by_the_primary_peer_falls_back_to_the_alternate():
    """`_self_fallback` is generator-AWARE: a claude-opus-4-7 generator that
    grades itself is swapped to gemini-3.1-pro-preview, not to claude. An
    all-claude fleet cannot run this contract."""
    contract = resolved_contract(
        provider="claude",
        model="claude-opus-4-7",
        transport="api",
        extract_provider="claude",
        extract_model="claude-haiku-4-5-20251001",
        judge_provider="claude",
        judge_model="claude-opus-4-7",
        solver_provider="claude",
        solver_model="claude-sonnet-4-6",
    )
    assert required_api_providers(contract) == frozenset({"claude", "gemini"})


def test_self_solve_by_the_primary_peer_falls_back_to_the_alternate():
    contract = resolved_contract(
        provider="claude",
        model="claude-opus-4-7",
        transport="api",
        extract_provider="claude",
        extract_model="claude-haiku-4-5-20251001",
        judge_provider="claude",
        judge_model="claude-sonnet-4-6",
        solver_provider="claude",
        solver_model="claude-opus-4-7",
    )
    assert required_api_providers(contract) == frozenset({"claude", "gemini"})


def test_clodex_content_requires_its_own_credential():
    """`content_ok`'s third arm (`can_clodex_api`). Clodex is api-ONLY
    (`API_ONLY_PROVIDERS`), so it is the one provider a cli fallback can never
    rescue: without the key the campaign simply never runs."""
    contract = resolved_contract(
        provider="clodex",
        model="gpt-5.6-sol",
        transport="api",
        # extract may not be clodex — `validate_role_provider` refuses it
        # (the vision fallbacks need a CLI-capable provider).
        extract_transport="cli",
        extract_provider="gemini",
        extract_model="gemini-3.1-flash-lite-preview",
        judge_provider="claude",
        judge_model="claude-opus-4-7",
        solver_provider="claude",
        solver_model="claude-sonnet-4-6",
    )
    assert required_api_providers(contract) == frozenset({"clodex", "claude"})

    without_key = worker_view(api={"claude": True, "gemini": True})
    with_key = worker_view(api={"claude": True, "clodex": True})
    assert worker_can_execute(contract, without_key) is False
    assert worker_can_execute(contract, with_key) is True


def test_required_api_providers_returns_a_frozenset():
    assert isinstance(required_api_providers(resolved_contract()), frozenset)


# ─────────────────────────────────────────────────────────────────────────
# worker_can_execute
# ─────────────────────────────────────────────────────────────────────────


def test_worker_needs_every_effective_api_provider():
    """One missing credential is enough: the SQL gate ANDs the role arms."""
    worker = worker_view(api={"gemini": True, "claude": False})
    contract = resolved_contract()  # needs gemini (content/extract) + claude
    assert required_api_providers(contract) == frozenset({"gemini", "claude"})
    assert worker_can_execute(contract, worker) is False


def test_worker_with_every_required_credential_can_execute():
    worker = worker_view(api=_ALL_CREDS)
    assert worker_can_execute(resolved_contract(), worker) is True


def test_worker_missing_a_credential_key_entirely_is_refused():
    """A blob published by an older worker may simply not carry the key."""
    worker = worker_view(api={"gemini": True})
    assert worker_can_execute(resolved_contract(), worker) is False


def test_offline_worker_is_refused():
    """PREFLIGHT-ONLY: `claim_next_job` never sees the registry, so this rule
    exists nowhere else. A dead worker's credentials are not capacity."""
    worker = worker_view(api=_ALL_CREDS, online=False)
    assert worker_can_execute(resolved_contract(), worker) is False


def test_stale_worker_is_refused():
    """Stale IS offline: `list_with_liveness` derives `online` from the
    heartbeat age against the DB clock, so a stale beat arrives here as
    `online=False` with an old `last_heartbeat`."""
    worker = worker_view(
        api=_ALL_CREDS, online=False, heartbeat_age_seconds=3600
    )
    assert worker_can_execute(resolved_contract(), worker) is False


def test_draining_worker_is_refused():
    """A draining worker may still claim until it observes the signal, so the
    SQL gate would let it through. It must not be PROMISED as capacity for a
    campaign that has not launched yet — deliberately a preflight-only
    refusal."""
    worker = worker_view(api=_ALL_CREDS, status="draining")
    assert worker_can_execute(resolved_contract(), worker) is False


def test_live_worker_with_offline_status_is_refused():
    """The reachable fail-open a status DENYLIST would leave behind.

    `mark_stale_offline` stamps `status='offline'` on its own window while
    liveness here is derived from the CALLER's `stale_after_seconds`, so a
    caller with a wider window legitimately sees `offline` + `online=True`.
    Counting that row as capacity approves a campaign that then stalls
    forever."""
    worker = worker_view(api=_ALL_CREDS, status="offline")
    assert worker.get("online") is True, "the trap needs a LIVE offline-status row"
    assert worker_can_execute(resolved_contract(), worker) is False


def test_worker_with_an_unknown_status_is_refused():
    """`workers.status` is a free String(32) with no CHECK constraint, so an
    allowlist is the only shape that stays correct when a status is added."""
    worker = worker_view(api=_ALL_CREDS, status="quarantined")
    assert worker_can_execute(resolved_contract(), worker) is False


def test_cli_contract_is_also_refused_by_a_non_online_status():
    """The status allowlist is not conditional on needing a credential."""
    worker = worker_view(capabilities=None, status="offline")
    assert worker_can_execute(cli_only_contract(), worker) is False


def test_worker_with_null_capabilities_is_refused():
    """`workers.capabilities` is nullable — a worker that has never published
    a blob must be refused, not crash the preflight."""
    worker = worker_view(capabilities=None)
    assert worker_can_execute(resolved_contract(), worker) is False


def test_worker_with_empty_capabilities_is_refused():
    worker = worker_view(capabilities={})
    assert worker_can_execute(resolved_contract(), worker) is False


def test_worker_with_no_api_section_is_refused():
    worker = worker_view(capabilities={"cli": {"gemini": True}})
    assert worker_can_execute(resolved_contract(), worker) is False


def test_worker_with_a_non_dict_capabilities_blob_does_not_raise():
    """JSONB holds any JSON value. A preflight that raises on a malformed blob
    takes the whole approval screen down with one bad worker row."""
    worker = worker_view(capabilities="online")
    assert worker_can_execute(resolved_contract(), worker) is False


def test_worker_with_a_non_dict_api_section_does_not_raise():
    worker = worker_view(capabilities={"api": "yes"})
    assert worker_can_execute(resolved_contract(), worker) is False


def test_stringly_typed_capability_fails_closed():
    """`bool("false")` is True. A blob from an older or hand-edited worker that
    says the STRING "false" must not be read as a credential — only a literal
    `True` counts."""
    worker = worker_view(api={"gemini": "false", "claude": "false"})
    assert worker_can_execute(resolved_contract(), worker) is False


def test_truthy_non_boolean_capability_fails_closed():
    worker = worker_view(api={"gemini": 1, "claude": 1})
    assert worker_can_execute(resolved_contract(), worker) is False


def test_worker_with_null_capabilities_still_runs_a_cli_contract():
    """No credential is required, so a blob-less worker is real capacity."""
    worker = worker_view(capabilities=None)
    assert worker_can_execute(cli_only_contract(), worker) is True


def test_cli_contract_runs_on_a_worker_with_no_credentials():
    worker = worker_view(api={"claude": False, "gemini": False, "clodex": False})
    assert worker_can_execute(cli_only_contract(), worker) is True


def test_fleet_pause_refuses_an_otherwise_compatible_api_contract():
    worker = worker_view(api=_ALL_CREDS)
    assert (
        worker_can_execute(resolved_contract(), worker, fleet_api_paused=True)
        is False
    )


def test_fleet_pause_does_not_refuse_a_cli_only_contract():
    """`claim_next_job`'s `fleet_gate` is `(NOT job_resolved_api) OR (NOT
    fleet_api_paused)` — a cli-only job is NEVER blocked by the api spend
    pause. A preflight that refused it would deadlock cli regeneration behind
    an api budget lever that has nothing to do with it."""
    worker = worker_view(api={"claude": False, "gemini": False})
    assert (
        worker_can_execute(cli_only_contract(), worker, fleet_api_paused=True)
        is True
    )


def test_fleet_pause_refuses_a_cli_job_whose_judge_is_api():
    """`job_resolved_api` is per-ROLE: a cli content job with an api judge
    still spends, so the pause must bite."""
    contract = resolved_contract(
        provider="claude",
        model="claude-sonnet-4-6",
        transport="cli",
        extract_transport="cli",
        extract_provider="claude",
        extract_model="claude-haiku-4-5-20251001",
        judge_transport="api",
        judge_provider="gemini",
        judge_model="gemini-3.5-flash",
        solver_transport="cli",
    )
    worker = worker_view(api=_ALL_CREDS)
    assert worker_can_execute(contract, worker) is True
    assert worker_can_execute(contract, worker, fleet_api_paused=True) is False


# ─────────────────────────────────────────────────────────────────────────
# WorkerExecutability shape
# ─────────────────────────────────────────────────────────────────────────


def test_worker_executability_is_a_frozen_dataclass_with_the_agreed_fields():
    """The API surface later tasks render — pinned so a rename is a test
    failure, not a silently empty panel."""
    assert dataclasses.is_dataclass(WorkerExecutability)
    assert [f.name for f in dataclasses.fields(WorkerExecutability)] == [
        "ok",
        "workers_online",
        "compatible_worker_ids",
        "required_api_providers",
        "fleet_api_paused",
        "reason",
    ]
    value = WorkerExecutability(
        ok=True,
        workers_online=1,
        compatible_worker_ids=("pc-1",),
        required_api_providers=("gemini",),
        fleet_api_paused=False,
        reason=None,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        value.ok = False


# ─────────────────────────────────────────────────────────────────────────
# check_active_workers
# ─────────────────────────────────────────────────────────────────────────


async def test_check_active_workers_finds_the_one_compatible_worker(fleet):
    """Three plausible-looking workers and one that can really run it."""
    calls = fleet(
        [
            worker_view("pc-offline", api=_ALL_CREDS, online=False),
            worker_view("pc-draining", api=_ALL_CREDS, status="draining"),
            worker_view("pc-vertex-only", api={"gemini": True, "claude": False}),
            worker_view("pc-ok", api=_ALL_CREDS),
        ]
    )
    result = await check_active_workers(
        _StubSession(), resolved_contract(), stale_after_seconds=90
    )

    assert result.ok is True
    assert result.compatible_worker_ids == ("pc-ok",)
    assert result.required_api_providers == ("claude", "gemini")
    assert result.fleet_api_paused is False
    assert result.reason is None
    assert result.workers_online == 3, "the offline row is not capacity"
    assert calls["stale_after_seconds"] == 90


async def test_check_active_workers_refuses_when_no_worker_has_the_credential(
    fleet,
):
    fleet(
        [
            worker_view("pc-a", api={"gemini": True, "claude": False}),
            worker_view("pc-b", api={"gemini": True, "claude": False}),
        ]
    )
    result = await check_active_workers(
        _StubSession(), resolved_contract(), stale_after_seconds=90
    )

    assert result.ok is False
    assert result.compatible_worker_ids == ()
    assert result.workers_online == 2
    assert result.reason == (
        "no online worker holds api credentials for claude, gemini"
    )


async def test_check_active_workers_refuses_when_nothing_is_online(fleet):
    fleet([worker_view("pc-dead", api=_ALL_CREDS, online=False)])
    result = await check_active_workers(
        _StubSession(), resolved_contract(), stale_after_seconds=90
    )

    assert result.ok is False
    assert result.workers_online == 0
    assert result.compatible_worker_ids == ()
    assert result.reason == "no workers are live (no fresh heartbeat)"


async def test_check_active_workers_refuses_an_empty_registry(fleet):
    fleet([])
    result = await check_active_workers(
        _StubSession(), resolved_contract(), stale_after_seconds=90
    )

    assert result.ok is False
    assert result.workers_online == 0
    assert result.reason == "no workers are live (no fresh heartbeat)"


async def test_check_active_workers_refuses_first_on_the_fleet_budget_pause(
    fleet,
):
    fleet([worker_view("pc-ok", api=_ALL_CREDS)], api_paused=True)
    result = await check_active_workers(
        _StubSession(), resolved_contract(), stale_after_seconds=90
    )

    assert result.ok is False
    assert result.fleet_api_paused is True
    assert result.reason == "fleet API spend is paused"
    assert result.compatible_worker_ids == ()


async def test_check_active_workers_runs_a_cli_contract_while_paused(fleet):
    """The pause is an api SPEND lever; a cli campaign spends nothing."""
    fleet(
        [worker_view("pc-cli", api={"claude": False, "gemini": False})],
        api_paused=True,
    )
    result = await check_active_workers(
        _StubSession(), cli_only_contract(), stale_after_seconds=90
    )

    assert result.ok is True
    assert result.fleet_api_paused is True
    assert result.required_api_providers == ()
    assert result.compatible_worker_ids == ("pc-cli",)
    assert result.reason is None


async def test_check_active_workers_refuses_a_fleet_that_is_entirely_draining(
    fleet,
):
    fleet([worker_view("pc-drain", api=_ALL_CREDS, status="draining")])
    result = await check_active_workers(
        _StubSession(), resolved_contract(), stale_after_seconds=90
    )

    assert result.ok is False
    assert result.compatible_worker_ids == ()
    assert result.workers_online == 1
    assert result.reason == "no live worker has status 'online' (saw: draining)"


# ─── the reason LADDER: each rung, and the precedence between them ────────
#
# The ordering is the feature ("what can the operator act on first"), so every
# rung is pinned to an EXACT string and every adjacent pair is pinned by a case
# where BOTH conditions hold at once. Without the precedence cases a swapped
# ladder still passes: each individual rung test only ever makes one condition
# true, so it cannot tell rung order from rung content.


async def test_pause_outranks_an_empty_fleet(fleet):
    """Both true at once. Starting a worker would not help while the fleet's
    api spend is paused, so the pause must be what the operator is told."""
    fleet([], api_paused=True)
    result = await check_active_workers(
        _StubSession(), resolved_contract(), stale_after_seconds=90
    )

    assert result.workers_online == 0, "the empty-fleet rung is also available"
    assert result.reason == "fleet API spend is paused"


async def test_pause_outranks_a_missing_credential(fleet):
    fleet([worker_view("pc-vertex", api={"gemini": True})], api_paused=True)
    result = await check_active_workers(
        _StubSession(), resolved_contract(), stale_after_seconds=90
    )

    assert result.reason == "fleet API spend is paused"


async def test_an_empty_fleet_outranks_a_missing_credential(fleet):
    """No worker is online AND no worker holds the credential — naming the
    credential would send the operator to edit a `.env` on a host that is not
    even running."""
    fleet([worker_view("pc-dead", api={}, online=False)])
    result = await check_active_workers(
        _StubSession(), resolved_contract(), stale_after_seconds=90
    )

    assert result.required_api_providers == ("claude", "gemini")
    assert result.reason == "no workers are live (no fresh heartbeat)"


async def test_a_non_online_status_outranks_a_missing_credential(fleet):
    """The one live worker is both draining AND under-credentialed. The drain
    is the actionable fact: fixing the credential changes nothing."""
    fleet([worker_view("pc-drain", api={"gemini": True}, status="draining")])
    result = await check_active_workers(
        _StubSession(), resolved_contract(), stale_after_seconds=90
    )

    assert result.workers_online == 1
    assert result.reason == "no live worker has status 'online' (saw: draining)"


async def test_the_status_rung_names_every_status_it_saw_deterministically(
    fleet,
):
    """`offline` and `draining` call for different operator actions, so the
    rung reports what is actually there — sorted, so the string is stable."""
    fleet(
        [
            worker_view("pc-a", api=_ALL_CREDS, status="offline"),
            worker_view("pc-b", api=_ALL_CREDS, status="draining"),
            worker_view("pc-c", api=_ALL_CREDS, status="offline"),
        ]
    )
    result = await check_active_workers(
        _StubSession(), resolved_contract(), stale_after_seconds=90
    )

    assert result.ok is False
    assert result.workers_online == 3
    assert result.reason == (
        "no live worker has status 'online' (saw: draining, offline)"
    )


async def test_the_status_rung_caps_the_free_text_it_renders(fleet):
    """`workers.status` is an unbounded String(32) written by the worker
    process. A fleet with many distinct statuses must not turn one operator
    sentence into a kilobyte of worker-supplied text."""
    fleet(
        [
            worker_view(f"pc-{i}", api=_ALL_CREDS, status=status)
            for i, status in enumerate(
                ["draining", "offline", "quarantined", "wedged", "unknown"]
            )
        ]
    )
    result = await check_active_workers(
        _StubSession(), resolved_contract(), stale_after_seconds=90
    )

    assert result.workers_online == 5
    # sorted, first three, then an ellipsis — never all five
    assert result.reason == (
        "no live worker has status 'online' "
        "(saw: draining, offline, quarantined, …)"
    )
    assert "wedged" not in result.reason
    assert "unknown" not in result.reason


async def test_the_status_rung_does_not_ellipsize_at_the_cap(fleet):
    """Exactly three distinct statuses is not truncation — no ellipsis."""
    fleet(
        [
            worker_view(f"pc-{i}", api=_ALL_CREDS, status=status)
            for i, status in enumerate(["draining", "offline", "quarantined"])
        ]
    )
    result = await check_active_workers(
        _StubSession(), resolved_contract(), stale_after_seconds=90
    )

    assert result.reason == (
        "no live worker has status 'online' "
        "(saw: draining, offline, quarantined)"
    )


async def test_a_live_offline_status_fleet_is_not_reported_as_draining(fleet):
    """The status rung must describe the fleet it saw, not assume a drain."""
    fleet([worker_view("pc-off", api=_ALL_CREDS, status="offline")])
    result = await check_active_workers(
        _StubSession(), resolved_contract(), stale_after_seconds=90
    )

    assert result.ok is False
    assert result.reason == "no live worker has status 'online' (saw: offline)"


async def test_a_cli_contract_still_needs_an_accepting_worker(fleet):
    """A contract needing no credential cannot reach the credential rung, and
    there is no "none of the above" rung — rungs 1-4 are exhaustive."""
    fleet([worker_view("pc-drain", capabilities=None, status="draining")])
    result = await check_active_workers(
        _StubSession(), cli_only_contract(), stale_after_seconds=90
    )

    assert result.ok is False
    assert result.required_api_providers == ()
    assert result.reason == "no live worker has status 'online' (saw: draining)"


async def test_check_active_workers_names_the_self_grade_peer_credential(fleet):
    """End to end: the self-grade swap must reach the reported requirement."""
    contract = resolved_contract(
        provider="gemini",
        model="gemini-3.1-pro-preview",
        transport="api",
        extract_provider="gemini",
        extract_model="gemini-3.1-flash-lite-preview",
        judge_provider="gemini",
        judge_model="gemini-3.1-pro-preview",
        solver_provider="gemini",
        solver_model="gemini-3.5-flash",
    )
    fleet([worker_view("pc-vertex", api={"gemini": True, "claude": False})])
    result = await check_active_workers(
        _StubSession(), contract, stale_after_seconds=90
    )

    assert result.required_api_providers == ("claude", "gemini")
    assert result.ok is False
    assert result.reason == (
        "no online worker holds api credentials for claude, gemini"
    )
