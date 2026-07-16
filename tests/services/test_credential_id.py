"""Unit tests for ``app.services.credential_id``.

Pure module: ``credential_for(provider, env)`` takes an explicit env mapping
(never reads ``os.environ`` itself) so these tests need no DB, no asyncio,
and no monkeypatching of process env.

Coverage:
- Per-provider fingerprint mapping (gemini key-path, gemini Vertex-pair path,
  claude, clodex).
- Branch-order parity with ``api_transport._gemini_client``
  (``app/services/api_transport.py:63-69``): that function checks
  ``GEMINI_API_KEY`` FIRST and only falls to the Vertex pair
  (``GOOGLE_APPLICATION_CREDENTIALS`` + ``GOOGLE_CLOUD_PROJECT``) when no key
  is set. A host with both a leftover key AND a Vertex SA assignment bills
  via the key, so the limiter must fingerprint on the key too — this test
  constructs an env with BOTH present and asserts the key fingerprint wins.
- No raw key material leaks into the fingerprint output.
- Missing-credential paths return ``None`` (limiter skips them).
- Unknown provider returns ``None``.
- Determinism: same input -> same output.
"""

import hashlib

from app.services import credential_id


def test_gemini_api_key_present():
    env = {"GEMINI_API_KEY": "super-secret-key"}
    expected = f"gemini:{hashlib.sha256(b'super-secret-key').hexdigest()[:16]}"
    assert credential_id.credential_for("gemini", env) == expected


def test_gemini_vertex_pair_when_no_key():
    env = {
        "GOOGLE_APPLICATION_CREDENTIALS": "/path/to/sa.json",
        "GOOGLE_CLOUD_PROJECT": "my-gcp-project",
    }
    assert credential_id.credential_for("gemini", env) == "gemini:my-gcp-project"


def test_gemini_project_credential_matches_vertex_pair_form():
    """Task 6: the sa-keys API builds the same `gemini:{project_id}` string
    for its slots_in_use/effective_limit lookups via this shared helper —
    it must be byte-identical to what credential_for's own Vertex-pair
    branch produces, or the two sites would silently drift apart."""
    assert credential_id.gemini_project_credential("my-gcp-project") == "gemini:my-gcp-project"
    env = {
        "GOOGLE_APPLICATION_CREDENTIALS": "/path/to/sa.json",
        "GOOGLE_CLOUD_PROJECT": "my-gcp-project",
    }
    assert (
        credential_id.credential_for("gemini", env)
        == credential_id.gemini_project_credential("my-gcp-project")
    )


def test_gemini_key_wins_over_vertex_pair_branch_order_parity():
    """Mirrors api_transport._gemini_client (api_transport.py:63-69): the
    client checks GEMINI_API_KEY first and only falls to the Vertex pair
    when no key is set. If both are present in the env, the key is what
    actually bills — so the fingerprint MUST be the key fingerprint, not
    the project fingerprint, or the limiter would be counting credentials
    that don't match what's actually being spent against."""
    env = {
        "GEMINI_API_KEY": "leftover-key",
        "GOOGLE_APPLICATION_CREDENTIALS": "/path/to/sa.json",
        "GOOGLE_CLOUD_PROJECT": "my-gcp-project",
    }
    expected = f"gemini:{hashlib.sha256(b'leftover-key').hexdigest()[:16]}"
    result = credential_id.credential_for("gemini", env)
    assert result == expected
    assert result != "gemini:my-gcp-project"


def test_gemini_vertex_pair_incomplete_credentials_only_no_project():
    env = {"GOOGLE_APPLICATION_CREDENTIALS": "/path/to/sa.json"}
    assert credential_id.credential_for("gemini", env) is None


def test_gemini_vertex_pair_incomplete_project_only_no_credentials():
    env = {"GOOGLE_CLOUD_PROJECT": "my-gcp-project"}
    assert credential_id.credential_for("gemini", env) is None


def test_gemini_no_credentials_at_all():
    assert credential_id.credential_for("gemini", {}) is None


def test_claude_api_key_present():
    env = {"ANTHROPIC_API_KEY": "sk-ant-secret"}
    expected = f"claude:{hashlib.sha256(b'sk-ant-secret').hexdigest()[:16]}"
    assert credential_id.credential_for("claude", env) == expected


def test_claude_missing_key():
    assert credential_id.credential_for("claude", {}) is None


def test_clodex_api_key_present():
    env = {"CLODEX_API_KEY": "clodex-secret-value"}
    expected = f"clodex:{hashlib.sha256(b'clodex-secret-value').hexdigest()[:16]}"
    assert credential_id.credential_for("clodex", env) == expected


def test_clodex_missing_key():
    assert credential_id.credential_for("clodex", {}) is None


def test_unknown_provider_returns_none():
    env = {
        "GEMINI_API_KEY": "x",
        "ANTHROPIC_API_KEY": "y",
        "CLODEX_API_KEY": "z",
    }
    assert credential_id.credential_for("kimi", env) is None
    assert credential_id.credential_for("codex", env) is None
    assert credential_id.credential_for("opencode", env) is None
    assert credential_id.credential_for("bogus", env) is None


def test_no_raw_key_material_in_output_claude():
    key = "sk-ant-super-secret-do-not-leak-1234567890"
    env = {"ANTHROPIC_API_KEY": key}
    result = credential_id.credential_for("claude", env)
    assert result is not None
    assert key not in result


def test_no_raw_key_material_in_output_gemini():
    key = "gemini-super-secret-do-not-leak-abcdef"
    env = {"GEMINI_API_KEY": key}
    result = credential_id.credential_for("gemini", env)
    assert result is not None
    assert key not in result


def test_no_raw_key_material_in_output_clodex():
    key = "clodex-super-secret-do-not-leak-zzzz"
    env = {"CLODEX_API_KEY": key}
    result = credential_id.credential_for("clodex", env)
    assert result is not None
    assert key not in result


def test_deterministic_same_input_same_output():
    env = {"ANTHROPIC_API_KEY": "stable-key"}
    a = credential_id.credential_for("claude", env)
    b = credential_id.credential_for("claude", env)
    assert a == b


def test_fingerprint_format_hex16_sha256():
    env = {"ANTHROPIC_API_KEY": "some-key-value"}
    result = credential_id.credential_for("claude", env)
    provider, _, digest = result.partition(":")
    assert provider == "claude"
    assert len(digest) == 16
    # must be a prefix of the real sha256 hex digest
    assert hashlib.sha256(b"some-key-value").hexdigest().startswith(digest)
