"""Tests for Worker._sync_sa_key — idle-gated SA key apply / scrub / change-detection.

Isolation: _sync_sa_key writes directly to os.environ (by design — the next
agent spawn must see it). We snapshot and restore the two env keys so the test
does not leak into the rest of the suite.
"""
import os
import pytest
import app.services.worker as worker
import app.services.sa_key_apply as apply_mod


class _FakeSession:
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False


@pytest.mark.asyncio
async def test_sync_applies_when_idle_and_noops_when_unchanged(monkeypatch, tmp_path):
    import app.config as config
    monkeypatch.setattr(config.settings, "var_dir", str(tmp_path))

    # Pre-clear the two env keys so the test starts clean.
    for k in ("GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_CLOUD_PROJECT", "GEMINI_API_KEY"):
        monkeypatch.delenv(k, raising=False)

    # Re-compute CAPABILITIES from the now-clean env so can_gemini_api starts False.
    worker.CAPABILITIES = worker._compute_capabilities(os.environ)

    w = worker.Worker(concurrency=1)
    w._tasks = set()  # idle

    monkeypatch.setattr(worker, "SessionLocal", lambda: _FakeSession())

    async def fake_lookup(session, hostname):
        return {
            "key_id": "11111111-1111-1111-1111-111111111111",
            "sha256": "SHA-NEW",
            "project_id": "proj-live",
            "scrub": False,
        }

    monkeypatch.setattr(worker.sa_keys_repo, "get_assignment_with_key", fake_lookup)
    monkeypatch.setattr(apply_mod, "pull_key_bytes", lambda kid: b'{"type":"service_account"}')

    # Point _WORKER_ENV_PATH at a temp file so we never touch the repo's real .env.
    envfile = tmp_path / ".env"
    monkeypatch.setattr(worker, "_WORKER_ENV_PATH", envfile, raising=False)

    # ── First sync: new sha → should apply ───────────────────────────────
    await w._sync_sa_key()

    assert os.environ.get("GOOGLE_CLOUD_PROJECT") == "proj-live", (
        "GOOGLE_CLOUD_PROJECT should be set after apply"
    )
    assert worker.CAPABILITIES["can_gemini_api"] is True, (
        "can_gemini_api should be True after apply"
    )
    assert w._applied_key_sha == "SHA-NEW"
    assert (tmp_path / "sa_keys" / "active.json").exists(), (
        "active.json should exist after apply"
    )

    # ── Second sync: unchanged sha → no re-pull ──────────────────────────
    monkeypatch.setattr(
        apply_mod,
        "pull_key_bytes",
        lambda kid: (_ for _ in ()).throw(AssertionError("should not pull — sha unchanged")),
    )
    w._last_key_sync_at = 0.0
    await w._sync_sa_key()  # must be a no-op
