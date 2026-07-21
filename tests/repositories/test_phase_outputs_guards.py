"""Pure contract tests for phase_outputs.reset_abandoned_phases — the no-op
and guard clauses fire BEFORE any session/DB use, so these run in the
canonical (un-gated) suite. The DB-backed behavior tests live in
test_phase_outputs_abandoned.py behind RUN_DB_INTEGRATION.

Guards are explicit ValueError raises, proven to survive python -O
(PR #110 round-3; closes reset-abandoned-status-assert-1)."""
import uuid

import pytest

from app.repositories import phase_outputs as phase_repo


def test_empty_job_ids_is_noop_without_touching_session():
    """Contract: empty job_ids returns 0 before any session use."""
    import asyncio
    assert asyncio.run(
        phase_repo.reset_abandoned_phases(None, [], status="pending")
    ) == 0


def test_empty_phase_names_list_is_still_noop():
    """phase_names=[] keeps the #109 no-op contract (None means ALL)."""
    import asyncio
    assert asyncio.run(
        phase_repo.reset_abandoned_phases(
            None, [uuid.uuid4()], phase_names=[], status="pending"
        )
    ) == 0


def test_status_rejects_anything_but_pending_or_failed():
    """The status guard is a real raise too (reset-abandoned-status-assert-1:
    a bare assert vanishes under python -O)."""
    import asyncio
    with pytest.raises(ValueError):
        asyncio.run(phase_repo.reset_abandoned_phases(
            None, [uuid.uuid4()], status="done"
        ))


def test_source_statuses_done_is_rejected():
    """Structural guard: 'done' must never be a narrowable source_status —
    the preservation contract is enforced, not conventional."""
    import asyncio
    with pytest.raises(ValueError):
        asyncio.run(
            phase_repo.reset_abandoned_phases(
                None, [uuid.uuid4()], status="pending",
                source_statuses=("done",),
            )
        )


def test_source_statuses_failed_is_rejected():
    """Structural guard: 'failed' is reachable ONLY via include_orphan_failed's
    marker equality, never wholesale through source_statuses."""
    import asyncio
    with pytest.raises(ValueError):
        asyncio.run(
            phase_repo.reset_abandoned_phases(
                None, [uuid.uuid4()], status="pending",
                source_statuses=("failed",),
            )
        )
