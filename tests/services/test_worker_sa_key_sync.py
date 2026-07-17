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


@pytest.fixture(autouse=True)
def _host_idle_by_default(monkeypatch):
    """Default the HOST-WIDE idle gate to 'no sibling running' so scrub tests
    exercise the clear. `_scrub_if_idle` calls `jobs_repo.count_running_for_host`
    (a real-DB query, proven in tests/integration/test_count_running_for_host.py)
    — here it's monkeypatched to 0 so these unit tests never touch a DB. The
    sibling-busy test overrides it."""
    async def _zero(session, hostname):
        return 0
    monkeypatch.setattr(worker.jobs_repo, "count_running_for_host", _zero)


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


# ── Restart-scrub residue matrix ─────────────────────────────────────────
# Bug: the scrub branch used to gate on the in-memory `_applied_key_sha`,
# which starts None on every process boot. A worker that restarts while a
# scrub instruction is pending (sha never re-learned — the assignment IS the
# scrub) would see `_applied_key_sha is None` and skip the clear entirely,
# leaving a stale active.json / env vars / .env line behind forever. The
# fix (task 1 GREEN step) is a four-source residue predicate: sha OR
# active-key file OR either var present in os.environ OR the env-file
# carrying either line.


def _scrub_assignment():
    return {"scrub": True, "sha256": None, "key_id": None, "project_id": None}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case_name",
    [
        "active_file_only",
        "process_env_creds_only",
        "process_env_project_only",
        "env_file_only",
        "in_memory_sha_only",
    ],
)
async def test_restart_scrub_clears_each_residue_source(monkeypatch, tmp_path, case_name):
    import app.config as config
    monkeypatch.setattr(config.settings, "var_dir", str(tmp_path))

    for k in ("GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_CLOUD_PROJECT", "GEMINI_API_KEY"):
        monkeypatch.delenv(k, raising=False)

    envfile = tmp_path / ".env"
    monkeypatch.setattr(worker, "_WORKER_ENV_PATH", envfile, raising=False)

    active_path = tmp_path / "sa_keys" / "active.json"

    w = worker.Worker(concurrency=1)
    w._tasks = set()  # idle
    w._applied_key_sha = None  # fresh boot — the reported regression's starting state

    if case_name == "active_file_only":
        active_path.parent.mkdir(parents=True, exist_ok=True)
        active_path.write_text('{"type":"service_account"}')
    elif case_name == "process_env_creds_only":
        monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(active_path))
    elif case_name == "process_env_project_only":
        # Presence-not-truthiness: an empty-string value is still residue —
        # the predicate must check `in os.environ`, not `bool(...)`.
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "")
    elif case_name == "env_file_only":
        envfile.write_text("GOOGLE_APPLICATION_CREDENTIALS=/some/stale/path.json\n")
    elif case_name == "in_memory_sha_only":
        w._applied_key_sha = "SHA-OLD"
    else:
        raise AssertionError(f"unhandled case {case_name!r}")

    worker.CAPABILITIES = worker._compute_capabilities(os.environ)

    monkeypatch.setattr(worker, "SessionLocal", lambda: _FakeSession())

    async def fake_lookup(session, hostname):
        return _scrub_assignment()

    monkeypatch.setattr(worker.sa_keys_repo, "get_assignment_with_key", fake_lookup)

    await w._sync_sa_key()

    if case_name == "active_file_only":
        assert not active_path.exists(), "active.json must be unlinked after scrub"
    elif case_name == "process_env_creds_only":
        assert "GOOGLE_APPLICATION_CREDENTIALS" not in os.environ
    elif case_name == "process_env_project_only":
        assert "GOOGLE_CLOUD_PROJECT" not in os.environ
    elif case_name == "env_file_only":
        assert "GOOGLE_APPLICATION_CREDENTIALS" not in envfile.read_text(encoding="utf-8")
    elif case_name == "in_memory_sha_only":
        assert w._applied_key_sha is None

    assert w._applied_key_sha is None
    assert worker.CAPABILITIES["can_gemini_api"] is False


@pytest.mark.asyncio
async def test_scrub_noop_when_no_residue(monkeypatch, tmp_path):
    import app.config as config
    monkeypatch.setattr(config.settings, "var_dir", str(tmp_path))

    for k in ("GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_CLOUD_PROJECT", "GEMINI_API_KEY"):
        monkeypatch.delenv(k, raising=False)

    envfile = tmp_path / ".env"
    monkeypatch.setattr(worker, "_WORKER_ENV_PATH", envfile, raising=False)

    w = worker.Worker(concurrency=1)
    w._tasks = set()  # idle
    w._applied_key_sha = None  # clean — nothing to scrub

    monkeypatch.setattr(worker, "SessionLocal", lambda: _FakeSession())

    async def fake_lookup(session, hostname):
        return _scrub_assignment()

    monkeypatch.setattr(worker.sa_keys_repo, "get_assignment_with_key", fake_lookup)

    calls = []
    monkeypatch.setattr(apply_mod, "clear_credentials_env", lambda env: calls.append(env))

    await w._sync_sa_key()

    assert calls == [], "clear_credentials_env must not be called when there is no residue"


@pytest.mark.asyncio
async def test_scrub_defers_while_busy(monkeypatch, tmp_path):
    import app.config as config
    monkeypatch.setattr(config.settings, "var_dir", str(tmp_path))

    for k in ("GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_CLOUD_PROJECT", "GEMINI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/some/stale/path.json")

    envfile = tmp_path / ".env"
    monkeypatch.setattr(worker, "_WORKER_ENV_PATH", envfile, raising=False)

    w = worker.Worker(concurrency=1)
    w._tasks = {object()}  # busy — must defer
    w._applied_key_sha = None

    monkeypatch.setattr(worker, "SessionLocal", lambda: _FakeSession())

    async def fake_lookup(session, hostname):
        return _scrub_assignment()

    monkeypatch.setattr(worker.sa_keys_repo, "get_assignment_with_key", fake_lookup)

    calls = []
    monkeypatch.setattr(apply_mod, "clear_credentials_env", lambda env: calls.append(env))

    await w._sync_sa_key()

    assert calls == [], "busy worker must defer the clear, not perform it"
    assert os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") == "/some/stale/path.json", (
        "residue must remain untouched while busy"
    )


@pytest.mark.asyncio
async def test_scrub_defers_while_sibling_process_busy(monkeypatch, tmp_path):
    """Host-wide idle gate (gate finding 1): THIS process is idle
    (`self._tasks` empty) and residue is present, but a SIBLING worker process
    on the same host is running a job (`count_running_for_host` > 0). The clear
    must be deferred — an idle process must not yank the shared active.json/.env
    out from under a sibling that's mid-spawn."""
    import app.config as config
    monkeypatch.setattr(config.settings, "var_dir", str(tmp_path))

    for k in ("GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_CLOUD_PROJECT", "GEMINI_API_KEY"):
        monkeypatch.delenv(k, raising=False)

    envfile = tmp_path / ".env"
    monkeypatch.setattr(worker, "_WORKER_ENV_PATH", envfile, raising=False)

    active_path = tmp_path / "sa_keys" / "active.json"
    active_path.parent.mkdir(parents=True, exist_ok=True)
    active_path.write_text('{"type":"service_account"}')

    w = worker.Worker(concurrency=1)
    w._tasks = set()  # THIS process is idle
    w._applied_key_sha = None

    monkeypatch.setattr(worker, "SessionLocal", lambda: _FakeSession())

    async def fake_lookup(session, hostname):
        return _scrub_assignment()

    monkeypatch.setattr(worker.sa_keys_repo, "get_assignment_with_key", fake_lookup)

    # A sibling process on this host is running a job.
    async def _one(session, hostname):
        return 1
    monkeypatch.setattr(worker.jobs_repo, "count_running_for_host", _one)

    calls = []
    monkeypatch.setattr(apply_mod, "clear_credentials_env", lambda env: calls.append(env))

    await w._sync_sa_key()

    assert calls == [], "must defer while a sibling process on the host is busy"
    assert active_path.exists(), "shared active.json must survive a sibling's in-flight job"


@pytest.mark.asyncio
async def test_scrub_swallows_malformed_env_file(monkeypatch, tmp_path):
    """Best-effort (gate finding 2): a malformed/unreadable `.env` that raises
    (here a UnicodeDecodeError from invalid UTF-8) while the scrub path reads it
    must be logged and swallowed — never propagated. `_sync_sa_key` runs at
    startup BEFORE the main loop's guard, so an unwrapped raise would crash the
    worker."""
    import app.config as config
    monkeypatch.setattr(config.settings, "var_dir", str(tmp_path))

    for k in ("GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_CLOUD_PROJECT", "GEMINI_API_KEY"):
        monkeypatch.delenv(k, raising=False)

    envfile = tmp_path / ".env"
    # Invalid UTF-8 — env_file_has_credentials' read_text(encoding="utf-8") raises.
    envfile.write_bytes(b"\xff\xfe GOOGLE_CLOUD_PROJECT=x\n")
    monkeypatch.setattr(worker, "_WORKER_ENV_PATH", envfile, raising=False)

    w = worker.Worker(concurrency=1)
    w._tasks = set()
    w._applied_key_sha = None

    monkeypatch.setattr(worker, "SessionLocal", lambda: _FakeSession())

    async def fake_lookup(session, hostname):
        return _scrub_assignment()

    monkeypatch.setattr(worker.sa_keys_repo, "get_assignment_with_key", fake_lookup)

    # Must NOT raise. (Pre-fix, the unwrapped read_text propagated out.)
    await w._sync_sa_key()


@pytest.mark.asyncio
async def test_restarted_worker_scrubs_persisted_key(monkeypatch, tmp_path):
    """The reviewer's original case 4 — the combined real-world scenario: a
    worker restarts (fresh `_applied_key_sha=None`) with EVERY residue source
    still present (active.json on disk, both env vars set, and the .env file
    line), and a pending scrub assignment. All four must be cleared in one
    sync."""
    import app.config as config
    monkeypatch.setattr(config.settings, "var_dir", str(tmp_path))

    for k in ("GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_CLOUD_PROJECT", "GEMINI_API_KEY"):
        monkeypatch.delenv(k, raising=False)

    envfile = tmp_path / ".env"
    monkeypatch.setattr(worker, "_WORKER_ENV_PATH", envfile, raising=False)

    active_path = tmp_path / "sa_keys" / "active.json"
    active_path.parent.mkdir(parents=True, exist_ok=True)
    active_path.write_text('{"type":"service_account"}')

    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(active_path.resolve()))
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "proj-stale")
    envfile.write_text(
        "GOOGLE_APPLICATION_CREDENTIALS="
        + str(active_path.resolve())
        + "\nGOOGLE_CLOUD_PROJECT=proj-stale\n"
    )

    worker.CAPABILITIES = worker._compute_capabilities(os.environ)
    assert worker.CAPABILITIES["can_gemini_api"] is True, "sanity: apply looked live pre-restart"

    w = worker.Worker(concurrency=1)  # simulates the fresh-boot process
    w._tasks = set()  # idle
    assert w._applied_key_sha is None, "sanity: fresh boot never re-learned the sha"

    monkeypatch.setattr(worker, "SessionLocal", lambda: _FakeSession())

    async def fake_lookup(session, hostname):
        return _scrub_assignment()

    monkeypatch.setattr(worker.sa_keys_repo, "get_assignment_with_key", fake_lookup)

    await w._sync_sa_key()

    assert not active_path.exists()
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in os.environ
    assert "GOOGLE_CLOUD_PROJECT" not in os.environ
    env_text = envfile.read_text(encoding="utf-8")
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in env_text
    assert "GOOGLE_CLOUD_PROJECT" not in env_text
    assert w._applied_key_sha is None
    assert worker.CAPABILITIES["can_gemini_api"] is False


# ── env_file_has_credentials ──────────────────────────────────────────────
def test_env_file_has_credentials_missing_file(tmp_path):
    assert apply_mod.env_file_has_credentials(tmp_path / "nope.env") is False


def test_env_file_has_credentials_empty_file(tmp_path):
    p = tmp_path / ".env"
    p.write_text("")
    assert apply_mod.env_file_has_credentials(p) is False


def test_env_file_has_credentials_other_keys_only(tmp_path):
    p = tmp_path / ".env"
    p.write_text("ANTHROPIC_API_KEY=sk-abc\nFOO=bar\n")
    assert apply_mod.env_file_has_credentials(p) is False


def test_env_file_has_credentials_google_creds_line(tmp_path):
    p = tmp_path / ".env"
    p.write_text("GOOGLE_APPLICATION_CREDENTIALS=/var/sa_keys/active.json\n")
    assert apply_mod.env_file_has_credentials(p) is True


def test_env_file_has_credentials_google_project_line(tmp_path):
    p = tmp_path / ".env"
    p.write_text("GOOGLE_CLOUD_PROJECT=my-proj\n")
    assert apply_mod.env_file_has_credentials(p) is True


def test_env_file_has_credentials_ignores_commented_line(tmp_path):
    p = tmp_path / ".env"
    p.write_text("# GOOGLE_APPLICATION_CREDENTIALS=/var/sa_keys/active.json\n")
    assert apply_mod.env_file_has_credentials(p) is False
