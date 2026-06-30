import pytest
import app.services.worker as worker


@pytest.mark.asyncio
async def test_run_syncs_key_before_claiming(monkeypatch):
    calls = []
    w = worker.Worker(concurrency=1)

    async def fake_sync():
        calls.append(("sync", len(calls)))
    async def fake_sweep():
        calls.append(("sweep", len(calls)))
    async def fake_claim():
        # stop after the first claim attempt so run() exits
        w.stop()
        return None

    monkeypatch.setattr(w, "_sync_sa_key", fake_sync)
    monkeypatch.setattr(w, "_sweep_stuck_jobs", fake_sweep)
    monkeypatch.setattr(w, "_claim_one", fake_claim)
    # neuter the registry heartbeat loop so the test stays in-process
    async def noop():
        return
    monkeypatch.setattr(w, "_registry_heartbeat_loop", noop)

    await w.run()
    # a sync happened before the first claim attempt
    assert "sync" in [c[0] for c in calls]
    sync_idx = next(i for i, c in enumerate(calls) if c[0] == "sync")
    claim_present = any(c[0] == "sweep" for c in calls)
    assert sync_idx is not None and claim_present
