from pathlib import Path

from app.services import sa_key_apply, sa_key_vault
from app.services.sa_key_apply import clear_credentials_env, set_credentials_env, write_active_key


def test_write_active_key_is_atomic_no_temp_left(monkeypatch, tmp_path):
    import app.config as config

    monkeypatch.setattr(config.settings, "var_dir", str(tmp_path))
    dest = tmp_path / "sa_keys" / "active.json"
    dest.parent.mkdir(parents=True)
    write_active_key(b'{"k":1}', dest)
    assert dest.read_bytes() == b'{"k":1}'
    # no .tmp residue beside it
    assert [p.name for p in dest.parent.iterdir()] == ["active.json"]
    # overwrite in place
    write_active_key(b'{"k":2}', dest)
    assert dest.read_bytes() == b'{"k":2}'
    assert [p.name for p in dest.parent.iterdir()] == ["active.json"]


def test_write_active_key_delegates_to_vault_atomic_write(monkeypatch, tmp_path):
    """Removing the vault delegate would put active credentials back on raw Path I/O."""
    dest = tmp_path / "sa_keys" / "active.json"
    calls = []

    def record(path, body):
        calls.append((path, body))

    monkeypatch.setattr(sa_key_vault, "atomic_write", record)

    write_active_key(b'{"secret":"value"}', dest)

    assert calls == [(dest, b'{"secret":"value"}')]


def test_local_pull_delegates_to_vault_read_bytes(monkeypatch, tmp_path):
    """The head-local pull must not bypass held-vault reads with Path.read_bytes."""
    import app.config as config

    key_id = "11111111-1111-1111-1111-111111111111"
    monkeypatch.setattr(config.settings, "fleet_head_url", "")
    monkeypatch.setattr(config.settings, "var_dir", str(tmp_path))
    calls = []

    def record(path):
        calls.append(path)
        return b'{"type":"service_account"}'

    monkeypatch.setattr(sa_key_vault, "read_bytes", record)

    assert sa_key_apply.pull_key_bytes(key_id) == b'{"type":"service_account"}'
    assert calls == [tmp_path / "sa_keys" / f"{key_id}.json"]


def test_credentials_env_paired():
    env = {}
    set_credentials_env(env, "/abs/active.json", "proj-1")
    assert env["GOOGLE_APPLICATION_CREDENTIALS"] == "/abs/active.json"
    assert env["GOOGLE_CLOUD_PROJECT"] == "proj-1"
    clear_credentials_env(env)
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in env
    assert "GOOGLE_CLOUD_PROJECT" not in env


def test_credentials_env_assignment_wins_over_leftover_gemini_key():
    """An operator SA-key assignment must WIN over a stale GEMINI_API_KEY
    left in the env — else `_gemini_client`/`credential_id.credential_for`
    would keep billing/fingerprinting off the old key instead of the new
    assignment (BE-16 task 5, codex-review #7 — flagged behavior change)."""
    env = {"GEMINI_API_KEY": "stale-leftover-key"}
    set_credentials_env(env, "/abs/active.json", "proj-1")
    assert "GEMINI_API_KEY" not in env
    assert env["GOOGLE_APPLICATION_CREDENTIALS"] == "/abs/active.json"
    assert env["GOOGLE_CLOUD_PROJECT"] == "proj-1"
