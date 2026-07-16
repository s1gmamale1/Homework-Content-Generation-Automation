from pathlib import Path
from app.services.sa_key_apply import write_active_key, set_credentials_env, clear_credentials_env


def test_write_active_key_is_atomic_no_temp_left(tmp_path):
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
