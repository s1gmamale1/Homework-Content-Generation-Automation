"""The preflight and the SQL claim gate must decide the same way.

``regeneration_executability.worker_can_execute`` is a Python transcription of
``jobs_repo.claim_next_job``'s capability routing. Two copies of one rule drift,
and both drift directions are silent:

* preflight too LAX  -> the campaign is approved, every revision job is created
  and stamped, and then no worker claims any of them. Nothing errors; the
  operator watches a launched campaign make zero progress.
* preflight too STRICT -> a campaign the fleet would have run is refused at the
  approval gate with a credential complaint that is simply false.

The rules most likely to drift are the two where the credential a job needs is
NOT the one stamped on it: a judge (or solver) that would grade its own
generator is hard-swapped to a frontier PEER — ``model_tiers._self_fallback``
in Python, a ``CASE`` expression in SQL — and the swap is generator-aware, so a
claude-opus-4-7 generator needs a *gemini* key it never mentions.

So this table runs BOTH implementations against the same input: a real pending
revision job stamped from the contract, and the credential dict a worker
publishes. The assertion is equality of the two verdicts, per row.

Deliberately NOT covered here: offline/stale/``draining`` refusals.
``claim_next_job`` is handed a credential dict and never sees the worker
registry, so it cannot express them; they are preflight-only judgements about
whether a worker is capacity (see the service module's docstring). Every worker
view in this table is therefore live and accepting, and the pure tests in
``tests/services/test_regeneration_executability.py`` own those rules.

Isolation, and what it does NOT promise: each row seeds its OWN book/TOC/
source/campaign/target/revision, asserts on the claimed job's IDENTITY (not
merely "something was claimed"), and rolls the claim back so no row's claim can
leak into the next one's.

``priority`` is set above every literal this repository uses so the row wins
the claim ordering against anything left behind by a hard-killed run — but that
is a best effort, not a guarantee: the queue is global and any future fixture
may outrank it. So the failure mode is handled explicitly instead of being
assumed away. If the gate claims SOMEBODY ELSE's job, ``_sql_gate_would_claim``
fails with that job's id and says so, rather than folding it into ``False`` and
reporting a preflight/gate "drift" that did not happen — a confidently wrong
diagnosis is worse than a loud unexplained one.
"""

from __future__ import annotations

import os
import uuid
from typing import Any, NamedTuple

import pytest

from app.repositories import jobs as jobs_repo
from app.schemas.regeneration_contract import ResolvedLaunchContract
from app.services.agent_models import resolve_role_transport
from app.services.regeneration_executability import (
    required_api_providers,
    worker_can_execute,
)

db_only = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)

_SUBJECT = "math-algebra"
_MAX_ATTEMPTS = 3
# Priority is the dominant sort key in `claim_next_job`, so this outranks every
# priority literal in the TEST suite — `tests/services/
# test_solver_fail_closed_e2e.py` and `tests/services/test_queue_retry_e2e.py`
# both seed at 1_000_000 against this same scratch DATABASE_URL, and a row left
# behind by a hard-killed run would otherwise win the claim instead of ours.
#
# It does NOT outrank everything in the repository, and is deliberately not
# raised until it does: `scripts/smoke_solver_fail_closed.py` seeds
# 2_000_000_000 (control 1_999_999_999) and also demands a scratch database
# name, so it can target this same DB. Going above it would sit inside a
# rounding error of the int4 ceiling (2_147_483_647) — a worse failure than the
# one it would fix. That case is handled BEHAVIOURALLY instead, by
# `_sql_gate_would_claim`'s foreign-claim `pytest.fail`, which is the only real
# guarantee here; the priority is a convenience that avoids the noise.
_PRIORITY = 1_000_000_000


# ─────────────────────────────────────────────────────────────────────────
# contracts
# ─────────────────────────────────────────────────────────────────────────

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


def contract(**overrides) -> ResolvedLaunchContract:
    return ResolvedLaunchContract(**{**_DEFAULTS, **overrides})


def worker(**api) -> dict[str, Any]:
    """A live, accepting worker holding exactly the named credentials."""
    return {
        "pc_id": "parity-worker",
        "status": "online",
        "online": True,
        "notes": None,
        "capabilities": {"cli": {"gemini": True}, "api": dict(api)},
    }


def credential_caps(worker_view: dict[str, Any]) -> dict[str, bool]:
    """The published blob -> the credential dict `claim_next_job` receives.

    Derived independently of the service under test (it is `worker.
    _compute_capabilities`'s shape, keyed off `_capability_blob`'s `api`
    section), so the parity assertion compares two implementations rather than
    one implementation with itself.
    """
    api = (worker_view.get("capabilities") or {}).get("api") or {}
    return {
        "can_claude_api": bool(api.get("claude")),
        "can_gemini_api": bool(api.get("gemini")),
        "can_clodex_api": bool(api.get("clodex")),
    }


class Row(NamedTuple):
    name: str
    contract: ResolvedLaunchContract
    worker: dict[str, Any]
    fleet_api_paused: bool


# A cli-valid claude pair used wherever a role must contribute nothing.
_CLAUDE_CLI = {"provider": "claude", "model": "claude-sonnet-4-6"}

_ALL_CLI = {
    "transport": "cli",
    "extract_transport": "cli",
    "judge_transport": "cli",
    "solver_transport": "cli",
}

PARITY_TABLE: list[Row] = [
    # ── content arm: `content_ok = transport=='cli' OR api_cap[provider]` ──
    Row(
        "api-content-with-every-credential",
        contract(),
        worker(gemini=True, claude=True),
        False,
    ),
    Row(
        "api-content-missing-the-judge-credential",
        contract(),
        worker(gemini=True, claude=False),
        False,
    ),
    Row(
        "api-content-missing-the-content-credential",
        contract(**_CLAUDE_CLI, transport="api",
                 extract_transport="cli", judge_transport="cli",
                 solver_transport="cli"),
        worker(gemini=True, claude=False),
        False,
    ),
    # A cli job needs NO credential — the brief's literal `required =
    # {contract.provider}` would refuse this row while the gate claims it.
    Row(
        "cli-only-on-a-worker-with-no-credentials",
        contract(**_ALL_CLI),
        worker(gemini=False, claude=False),
        False,
    ),
    # ── fleet pause: `(NOT job_resolved_api) OR (NOT fleet_api_paused)` ──
    Row(
        "cli-only-while-fleet-api-spend-is-paused",
        contract(**_ALL_CLI),
        worker(gemini=False, claude=False),
        True,
    ),
    Row(
        "api-contract-while-fleet-api-spend-is-paused",
        contract(),
        worker(gemini=True, claude=True),
        True,
    ),
    # ── per-role transports ──
    Row(
        "cli-job-with-an-api-judge-only",
        contract(
            **_CLAUDE_CLI,
            transport="cli",
            extract_transport="cli",
            extract_provider="claude",
            extract_model="claude-haiku-4-5-20251001",
            judge_transport="api",
            judge_provider="gemini",
            judge_model="gemini-3.5-flash",
            solver_transport="cli",
            solver_provider="claude",
            solver_model="claude-opus-4-7",
        ),
        worker(gemini=True, claude=False),
        False,
    ),
    Row(
        "cli-job-with-an-api-judge-and-no-gemini-key",
        contract(
            **_CLAUDE_CLI,
            transport="cli",
            extract_transport="cli",
            extract_provider="claude",
            extract_model="claude-haiku-4-5-20251001",
            judge_transport="api",
            judge_provider="gemini",
            judge_model="gemini-3.5-flash",
            solver_transport="cli",
            solver_provider="claude",
            solver_model="claude-opus-4-7",
        ),
        worker(gemini=False, claude=True),
        False,
    ),
    Row(
        "cli-job-with-an-api-extract-only",
        contract(
            **_CLAUDE_CLI,
            transport="cli",
            extract_transport="api",
            extract_provider="gemini",
            extract_model="gemini-3.5-flash",
            judge_transport="cli",
            judge_provider="claude",
            judge_model="claude-opus-4-7",
            solver_transport="cli",
            solver_provider="claude",
            solver_model="claude-haiku-4-5-20251001",
        ),
        worker(gemini=True, claude=False),
        False,
    ),
    Row(
        "api-job-with-every-role-pinned-to-cli",
        contract(
            **_CLAUDE_CLI,
            transport="api",
            extract_transport="cli",
            extract_provider="gemini",
            extract_model="gemini-3.1-flash-lite-preview",
            judge_transport="cli",
            judge_provider="gemini",
            judge_model="gemini-3.1-pro-preview",
            solver_transport="cli",
            solver_provider="gemini",
            solver_model="gemini-3-flash-preview",
        ),
        worker(gemini=False, claude=True),
        False,
    ),
    # ── self-grade: the stamped judge is NOT the credential that is needed ──
    Row(
        "self-grade-without-the-peer-credential",
        contract(
            judge_provider="gemini",
            judge_model="gemini-3.1-pro-preview",
            solver_provider="gemini",
            solver_model="gemini-3.5-flash",
        ),
        worker(gemini=True, claude=False),
        False,
    ),
    Row(
        "self-grade-with-the-peer-credential",
        contract(
            judge_provider="gemini",
            judge_model="gemini-3.1-pro-preview",
            solver_provider="gemini",
            solver_model="gemini-3.5-flash",
        ),
        worker(gemini=True, claude=True),
        False,
    ),
    # ── self-solve: same rule mirrored onto the solver_* columns ──
    Row(
        "self-solve-without-the-peer-credential",
        contract(
            judge_provider="gemini",
            judge_model="gemini-3.5-flash",
            solver_provider="gemini",
            solver_model="gemini-3.1-pro-preview",
        ),
        worker(gemini=True, claude=False),
        False,
    ),
    Row(
        "self-solve-with-the-peer-credential",
        contract(
            judge_provider="gemini",
            judge_model="gemini-3.5-flash",
            solver_provider="gemini",
            solver_model="gemini-3.1-pro-preview",
        ),
        worker(gemini=True, claude=True),
        False,
    ),
    # The fallback is generator-AWARE: a claude-opus-4-7 generator grading
    # itself is swapped to gemini, so an all-claude fleet CANNOT run it.
    Row(
        "primary-peer-generator-self-grade-needs-gemini",
        contract(
            provider="claude",
            model="claude-opus-4-7",
            transport="api",
            extract_provider="claude",
            extract_model="claude-haiku-4-5-20251001",
            judge_provider="claude",
            judge_model="claude-opus-4-7",
            solver_provider="claude",
            solver_model="claude-sonnet-4-6",
        ),
        worker(gemini=False, claude=True),
        False,
    ),
    Row(
        "primary-peer-generator-self-grade-with-gemini",
        contract(
            provider="claude",
            model="claude-opus-4-7",
            transport="api",
            extract_provider="claude",
            extract_model="claude-haiku-4-5-20251001",
            judge_provider="claude",
            judge_model="claude-opus-4-7",
            solver_provider="claude",
            solver_model="claude-sonnet-4-6",
        ),
        worker(gemini=True, claude=True),
        False,
    ),
    Row(
        "primary-peer-generator-self-solve-needs-gemini",
        contract(
            provider="claude",
            model="claude-opus-4-7",
            transport="api",
            extract_provider="claude",
            extract_model="claude-haiku-4-5-20251001",
            judge_provider="claude",
            judge_model="claude-sonnet-4-6",
            solver_provider="claude",
            solver_model="claude-opus-4-7",
        ),
        worker(gemini=False, claude=True),
        False,
    ),
    # A self-match on a role whose transport is CLI must pull in nothing: the
    # transport arm gates BEFORE the self-fallback, on both sides.
    Row(
        "self-solve-on-a-cli-role-requires-no-credential",
        contract(
            **_CLAUDE_CLI,
            transport="cli",
            extract_transport="cli",
            extract_provider="claude",
            extract_model="claude-haiku-4-5-20251001",
            judge_transport="cli",
            judge_provider="claude",
            judge_model="claude-sonnet-4-6",
            solver_transport="cli",
            solver_provider="claude",
            solver_model="claude-sonnet-4-6",
        ),
        worker(gemini=False, claude=False),
        False,
    ),
    # ── fleet pause driven by a per-ROLE arm, not by `transport == 'api'` ──
    # `job_resolved_api` is `transport=='api' OR judge_needs_api OR
    # extract_needs_api OR solver_needs_api`. The two pause rows above all
    # resolve through the FIRST disjunct, so the three role arms are never the
    # deciding term; deleting any of them from `jobs.py` would leave the table
    # green. These rows make each role arm decide the pause on its own.
    Row(
        "paused-cli-job-whose-JUDGE-is-api",
        contract(
            **_CLAUDE_CLI,
            transport="cli",
            extract_transport="cli",
            extract_provider="claude",
            extract_model="claude-haiku-4-5-20251001",
            judge_transport="api",
            judge_provider="gemini",
            judge_model="gemini-3.5-flash",
            solver_transport="cli",
            solver_provider="claude",
            solver_model="claude-opus-4-7",
        ),
        worker(gemini=True, claude=True),
        True,
    ),
    Row(
        "paused-cli-job-whose-EXTRACT-is-api",
        contract(
            **_CLAUDE_CLI,
            transport="cli",
            extract_transport="api",
            extract_provider="gemini",
            extract_model="gemini-3.5-flash",
            judge_transport="cli",
            judge_provider="claude",
            judge_model="claude-opus-4-7",
            solver_transport="cli",
            solver_provider="claude",
            solver_model="claude-haiku-4-5-20251001",
        ),
        worker(gemini=True, claude=True),
        True,
    ),
    Row(
        "paused-cli-job-whose-SOLVER-is-api",
        contract(
            **_CLAUDE_CLI,
            transport="cli",
            extract_transport="cli",
            extract_provider="claude",
            extract_model="claude-haiku-4-5-20251001",
            judge_transport="cli",
            judge_provider="claude",
            judge_model="claude-opus-4-7",
            solver_transport="api",
            solver_provider="gemini",
            solver_model="gemini-3.5-flash",
        ),
        worker(gemini=True, claude=True),
        True,
    ),
    # `job_resolved_api`'s FIRST disjunct (`transport == 'api'`), alone. Every
    # other paused row leaves at least one role arm true as well, so any single
    # disjunct satisfies the gate and the content arm is never the deciding
    # term. Without this row, deleting `HomeworkJob.transport == "api"` from
    # `job_resolved_api` goes unnoticed and an api-transport job with every
    # role pinned to cli SPENDS API TOKENS during a fleet-wide budget pause.
    Row(
        "paused-api-job-whose-ONLY-api-arm-is-content",
        contract(
            **_CLAUDE_CLI,
            transport="api",
            extract_transport="cli",
            extract_provider="gemini",
            extract_model="gemini-3.1-flash-lite-preview",
            judge_transport="cli",
            judge_provider="gemini",
            judge_model="gemini-3.1-pro-preview",
            solver_transport="cli",
            solver_provider="gemini",
            solver_model="gemini-3-flash-preview",
        ),
        worker(gemini=False, claude=True),
        True,
    ),
    # ── clodex: `content_ok`'s third arm, and the one provider with no cli
    # fallback (API_ONLY_PROVIDERS) — without the key nothing ever runs it.
    Row(
        "clodex-content-without-the-clodex-key",
        contract(
            provider="clodex",
            model="gpt-5.6-sol",
            transport="api",
            extract_transport="cli",
            extract_provider="gemini",
            extract_model="gemini-3.1-flash-lite-preview",
            judge_provider="claude",
            judge_model="claude-opus-4-7",
            solver_provider="claude",
            solver_model="claude-sonnet-4-6",
        ),
        worker(gemini=True, claude=True),
        False,
    ),
    Row(
        "clodex-content-with-the-clodex-key",
        contract(
            provider="clodex",
            model="gpt-5.6-sol",
            transport="api",
            extract_transport="cli",
            extract_provider="gemini",
            extract_model="gemini-3.1-flash-lite-preview",
            judge_provider="claude",
            judge_model="claude-opus-4-7",
            solver_provider="claude",
            solver_model="claude-sonnet-4-6",
        ),
        worker(clodex=True, claude=True),
        False,
    ),
    # ── mixed credentials, inherited transports ──
    Row(
        "inherited-api-roles-with-only-the-gemini-key",
        contract(
            provider="gemini",
            model="gemini-3.1-pro-preview",
            extract_provider="gemini",
            extract_model="gemini-3.1-flash-lite-preview",
            judge_provider="gemini",
            judge_model="gemini-3-flash-preview",
            solver_provider="gemini",
            solver_model="gemini-3.5-flash",
        ),
        worker(gemini=True, claude=False),
        False,
    ),
    Row(
        "inherited-api-roles-needing-a-claude-solver",
        contract(
            provider="gemini",
            model="gemini-3.1-pro-preview",
            extract_provider="gemini",
            extract_model="gemini-3.1-flash-lite-preview",
            judge_provider="gemini",
            judge_model="gemini-3-flash-preview",
            solver_provider="claude",
            solver_model="claude-opus-4-7",
        ),
        worker(gemini=True, claude=False),
        False,
    ),
]


# ─────────────────────────────────────────────────────────────────────────
# fixture plumbing
# ─────────────────────────────────────────────────────────────────────────


async def _seed(row: Row) -> dict:
    """A book, a TOC entry, a done V1 source, a campaign/target, and a PENDING
    revision job stamped verbatim from the row's contract."""
    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.homework_job import HomeworkJob
    from app.models.regeneration_campaign import RegenerationCampaign
    from app.models.regeneration_target import RegenerationTarget
    from app.models.toc_entry import TOCEntry
    from app.services.regeneration_planner import build_phase_plan

    c = row.contract
    async with SessionLocal() as session:
        book = Book(
            subject=_SUBJECT,
            original_filename="regen_claim_parity.pdf",
            content_sha256=uuid.uuid4().hex * 2,
            file_size_bytes=1,
            status="toc_ready",
        )
        session.add(book)
        await session.flush()
        toc = TOCEntry(book_id=book.id, section_title="L1", order_index=0)
        session.add(toc)
        await session.flush()
        v1 = HomeworkJob(
            book_id=book.id, toc_entry_id=toc.id, subject=_SUBJECT,
            status="done", provider="gemini", transport="api",
            output_language="uz",
        )
        session.add(v1)
        await session.flush()
        campaign = RegenerationCampaign(
            status="draft", selection_spec={}, requested_phases=[],
            excluded_phases=[], launch_contract=c.model_dump(),
        )
        session.add(campaign)
        await session.flush()
        target = RegenerationTarget(
            campaign_id=campaign.id, toc_entry_id=toc.id,
            output_language="uz",
            phase_plan=build_phase_plan(
                subject=_SUBJECT, selected_phases=["flashcards"]
            ).to_json(),
            source_job_id=v1.id, status="generating",
        )
        session.add(target)
        await session.flush()
        revision = HomeworkJob(
            book_id=book.id, toc_entry_id=toc.id, subject=_SUBJECT,
            status="pending", output_language="uz",
            revision_of_job_id=v1.id, regeneration_target_id=target.id,
            priority=_PRIORITY,
            # every launch option, stamped verbatim from the frozen contract
            provider=c.provider, model=c.model, transport=c.transport,
            extract_transport=c.extract_transport,
            extract_provider=c.extract_provider,
            extract_model=c.extract_model,
            judge_transport=c.judge_transport,
            judge_provider=c.judge_provider,
            judge_model=c.judge_model,
            solver_transport=c.solver_transport,
            solver_provider=c.solver_provider,
            solver_model=c.solver_model,
            session_limit_strategy=c.session_limit_strategy,
        )
        session.add(revision)
        await session.commit()
        return {
            "book_id": book.id, "toc_id": toc.id, "v1_id": v1.id,
            "revision_id": revision.id, "campaign_id": campaign.id,
            "target_id": target.id,
        }


async def _purge(ids: dict) -> None:
    """Child-first: every regeneration FK is RESTRICT on purpose."""
    from sqlalchemy import delete

    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.homework_job import HomeworkJob
    from app.models.job_lease_event import JobLeaseEvent
    from app.models.regeneration_campaign import RegenerationCampaign
    from app.models.regeneration_target import RegenerationTarget
    from app.models.toc_entry import TOCEntry

    async with SessionLocal() as session:
        await session.execute(
            delete(JobLeaseEvent).where(
                JobLeaseEvent.job_id.in_([ids["revision_id"], ids["v1_id"]])))
        await session.execute(
            delete(HomeworkJob).where(HomeworkJob.id == ids["revision_id"]))
        await session.execute(
            delete(RegenerationTarget).where(
                RegenerationTarget.id == ids["target_id"]))
        await session.execute(
            delete(RegenerationCampaign).where(
                RegenerationCampaign.id == ids["campaign_id"]))
        await session.execute(
            delete(HomeworkJob).where(HomeworkJob.book_id == ids["book_id"]))
        await session.execute(
            delete(TOCEntry).where(TOCEntry.book_id == ids["book_id"]))
        await session.execute(delete(Book).where(Book.id == ids["book_id"]))
        await session.commit()


async def _sql_gate_would_claim(row: Row, revision_id) -> bool:
    """Did `claim_next_job` claim OUR revision? Rolled back either way, so a
    claim (ours or a stray one) cannot bleed into the next row.

    Three outcomes, not two. "The gate refused our row" and "the gate claimed
    somebody else's row" must NOT both collapse to False: one leaked pending
    row would turn every expected-True case into a failure whose message blames
    a preflight/claim-gate drift that never happened. The foreign claim is
    therefore its own, differently-worded failure.
    """
    from app.db import SessionLocal

    async with SessionLocal() as session:
        try:
            claimed = await jobs_repo.claim_next_job(
                session,
                worker_id="parity-worker",
                max_attempts=_MAX_ATTEMPTS,
                capabilities=credential_caps(row.worker),
                fleet_api_paused=row.fleet_api_paused,
            )
            if claimed is None:
                return False
            if claimed.job.id != revision_id:
                pytest.fail(
                    f"[{row.name}] claim_next_job claimed a FOREIGN job "
                    f"({claimed.job.id}, priority={claimed.job.priority}) "
                    f"instead of this row's revision ({revision_id}). That is "
                    "stale rows in the scratch DB, NOT a preflight/claim-gate "
                    "drift: clear the leftover pending jobs and re-run."
                )
            return True
        finally:
            await session.rollback()


# ─────────────────────────────────────────────────────────────────────────
# the parity assertion
# ─────────────────────────────────────────────────────────────────────────


@db_only
@pytest.mark.parametrize("row", PARITY_TABLE, ids=[r.name for r in PARITY_TABLE])
async def test_preflight_matches_the_sql_claim_gate(row: Row):
    ids = await _seed(row)
    try:
        claimed = await _sql_gate_would_claim(row, ids["revision_id"])
    finally:
        await _purge(ids)

    preflight = worker_can_execute(
        row.contract, row.worker, fleet_api_paused=row.fleet_api_paused
    )
    assert preflight is claimed, (
        f"[{row.name}] preflight says {preflight}, claim_next_job says "
        f"{claimed} — the regeneration preflight and the SQL claim gate have "
        "drifted; one of them is lying to the operator"
    )


# Deliberately NOT @db_only: it is a table-quality guard, not a DB assertion,
# so it also runs in the ordinary (no-Postgres) suite where a degenerate
# all-one-verdict table would otherwise go unnoticed.
def test_the_parity_table_exercises_both_verdicts():
    """A table that only ever asserts False (or only True) proves nothing —
    every row would pass against a constant. Prove both branches are present,
    and that every disjunct of the claim gate's fleet-pause rule has a row in
    which it is the SOLE reason the row is blocked."""
    verdicts = {
        worker_can_execute(
            r.contract, r.worker, fleet_api_paused=r.fleet_api_paused
        )
        for r in PARITY_TABLE
    }
    assert verdicts == {True, False}
    assert any(
        required_api_providers(r.contract) == frozenset() for r in PARITY_TABLE
    ), "no cli-only row: the conditional content-credential rule is untested"
    # `job_resolved_api` is a FOUR-disjunct OR (`jobs.py`): content transport,
    # then judge/extract/solver. A row satisfies the gate as soon as ANY one is
    # true, so an arm is only PROVEN by a row in which it is the SOLE api arm —
    # every clause below therefore requires the other three to be cli, not just
    # this one to be api.
    #
    # Two ways to get this wrong, both already made once here:
    #   * requiring merely "a paused row with transport='cli'" — satisfied by
    #     the cli-only paused row, which touches NO api arm at all, so all three
    #     role rows could be deleted with the guard still green;
    #   * requiring "this role is api" without the sole-arm conjunct — satisfied
    #     by ONE row with judge AND extract AND solver all api, which would
    #     answer all three clauses at once while proving no individual arm.
    #     That is a realistic maintenance edit (consolidating the role rows),
    #     and the guard would report everything fine with all three protections
    #     gone.
    _ROLE_ARMS = ("judge", "extract", "solver")
    for role in _ROLE_ARMS:
        assert any(
            r.fleet_api_paused
            and r.contract.transport == "cli"
            and resolve_role_transport(
                getattr(r.contract, f"{role}_transport"), r.contract.transport
            )
            == "api"
            and all(
                resolve_role_transport(
                    getattr(r.contract, f"{other}_transport"),
                    r.contract.transport,
                )
                != "api"
                for other in _ROLE_ARMS
                if other != role
            )
            for r in PARITY_TABLE
        ), (
            f"no paused row whose SOLE api arm is {role} (cli content "
            f"transport, the other two roles cli): `job_resolved_api`'s "
            f"{role}_needs_api arm is the only thing that would block such a "
            "job, so deleting that arm from jobs.py would leave this table "
            "green while api spend continues through a fleet-wide budget pause"
        )
    assert any(
        r.fleet_api_paused
        and r.contract.transport == "api"
        and all(
            resolve_role_transport(
                getattr(r.contract, f"{role}_transport"), r.contract.transport
            )
            != "api"
            for role in _ROLE_ARMS
        )
        for r in PARITY_TABLE
    ), (
        "no paused row whose ONLY api arm is the content transport: "
        "`job_resolved_api`'s `transport == 'api'` disjunct is unproven, and "
        "an api job with every role pinned to cli would be claimed and billed "
        "during a fleet-wide budget pause"
    )
    assert any(
        r.fleet_api_paused and required_api_providers(r.contract) == frozenset()
        for r in PARITY_TABLE
    ), (
        "no paused CLI-ONLY row: `fleet_gate`'s `NOT job_resolved_api` arm — "
        "the rule that a cli campaign is never blocked by an api spend pause "
        "(deviation 2) — is unproven against the real gate"
    )
    assert any(r.contract.provider == "clodex" for r in PARITY_TABLE), (
        "no clodex row: `content_ok`'s third arm is unproven, and clodex is "
        "the one provider a cli fallback cannot rescue"
    )
