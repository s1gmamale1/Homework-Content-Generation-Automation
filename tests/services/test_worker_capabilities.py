"""Tests for worker._capability_blob — the published worker capability shape.

TDD: these tests are written BEFORE implementation to drive the design.
RED → verify tests fail (function absent / wrong shape) → implement → GREEN.

Monkeypatching policy:
  - shutil.which: mocked at the boundary (cli-installed probe)
  - env dict: passed directly as argument (pure function)
  - NEVER stub the function under test
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# 1. _capability_blob exists and returns the expected top-level shape
# ---------------------------------------------------------------------------

def test_capability_blob_top_level_keys():
    """_capability_blob must return a dict with exactly 'cli' and 'api' keys.

    BITE: removing either key from the return dict breaks this assertion.
    """
    from app.services.worker import _capability_blob

    blob = _capability_blob({})
    assert isinstance(blob, dict), "_capability_blob must return a dict"
    assert set(blob.keys()) == {"cli", "api"}, (
        f"blob must have exactly 'cli' and 'api' keys; got {set(blob.keys())}"
    )


def test_capability_blob_cli_has_all_five_providers():
    """_capability_blob['cli'] must contain exactly the 5 registered provider names.

    BITE: removing a provider from the cli sub-dict breaks this assertion.
    BITE: adding an extra key (e.g. from a wrong source) also breaks it.
    """
    from app.services.worker import _capability_blob
    from app.services import providers

    blob = _capability_blob({})
    cli = blob["cli"]
    assert isinstance(cli, dict), "blob['cli'] must be a dict"
    assert set(cli.keys()) == set(providers.PROVIDERS.keys()), (
        f"blob['cli'] keys must match providers.PROVIDERS keys; "
        f"got {set(cli.keys())} vs {set(providers.PROVIDERS.keys())}"
    )


def test_capability_blob_api_has_claude_and_gemini():
    """_capability_blob['api'] must contain exactly 'claude' and 'gemini' keys.

    BITE: renaming or removing either key breaks this assertion.
    """
    from app.services.worker import _capability_blob

    blob = _capability_blob({})
    api = blob["api"]
    assert isinstance(api, dict), "blob['api'] must be a dict"
    assert set(api.keys()) == {"claude", "gemini"}, (
        f"blob['api'] must have exactly 'claude' and 'gemini' keys; got {set(api.keys())}"
    )


# ---------------------------------------------------------------------------
# 2. API flags follow env — real truthiness rules
# ---------------------------------------------------------------------------

def test_api_claude_true_when_key_set():
    """blob['api']['claude'] must be True when ANTHROPIC_API_KEY is in env.

    BITE: removing the ANTHROPIC_API_KEY check makes this return False.
    """
    from app.services.worker import _capability_blob

    blob = _capability_blob({"ANTHROPIC_API_KEY": "sk-ant-test"})
    assert blob["api"]["claude"] is True, (
        "api.claude must be True when ANTHROPIC_API_KEY is set"
    )


def test_api_claude_false_when_key_absent():
    """blob['api']['claude'] must be False when ANTHROPIC_API_KEY is absent or empty.

    BITE: hardcoding True breaks this assertion.
    """
    from app.services.worker import _capability_blob

    assert _capability_blob({})["api"]["claude"] is False, (
        "api.claude must be False when ANTHROPIC_API_KEY is absent"
    )
    assert _capability_blob({"ANTHROPIC_API_KEY": ""})["api"]["claude"] is False, (
        "api.claude must be False when ANTHROPIC_API_KEY is empty string"
    )


def test_api_gemini_true_with_ai_studio_key():
    """blob['api']['gemini'] must be True when GEMINI_API_KEY is set.

    BITE: removing the GEMINI_API_KEY check makes this return False.
    """
    from app.services.worker import _capability_blob

    blob = _capability_blob({"GEMINI_API_KEY": "AIza-test"})
    assert blob["api"]["gemini"] is True, (
        "api.gemini must be True when GEMINI_API_KEY is set"
    )


def test_api_gemini_true_with_vertex_pair():
    """blob['api']['gemini'] must be True when GOOGLE_APPLICATION_CREDENTIALS+GOOGLE_CLOUD_PROJECT
    are both set (Vertex service-account path).

    BITE: removing the Vertex pair check makes this return False.
    """
    from app.services.worker import _capability_blob

    blob = _capability_blob({
        "GOOGLE_APPLICATION_CREDENTIALS": "/path/to/sa.json",
        "GOOGLE_CLOUD_PROJECT": "my-project",
    })
    assert blob["api"]["gemini"] is True, (
        "api.gemini must be True when GOOGLE_APPLICATION_CREDENTIALS+GOOGLE_CLOUD_PROJECT are set"
    )


def test_api_gemini_false_when_half_configured():
    """blob['api']['gemini'] must be False when only one of the Vertex pair is set.

    BITE: relaxing the AND to OR would make this return True.
    """
    from app.services.worker import _capability_blob

    # Only GOOGLE_APPLICATION_CREDENTIALS — no project
    blob = _capability_blob({"GOOGLE_APPLICATION_CREDENTIALS": "/path/to/sa.json"})
    assert blob["api"]["gemini"] is False, (
        "api.gemini must be False when GOOGLE_APPLICATION_CREDENTIALS is set but GOOGLE_CLOUD_PROJECT is absent"
    )

    # Only GOOGLE_CLOUD_PROJECT — no SA file
    blob = _capability_blob({"GOOGLE_CLOUD_PROJECT": "my-project"})
    assert blob["api"]["gemini"] is False, (
        "api.gemini must be False when GOOGLE_CLOUD_PROJECT is set but GOOGLE_APPLICATION_CREDENTIALS is absent"
    )


def test_api_gemini_false_empty_env():
    """blob['api']['gemini'] must be False when no gemini credentials are set.

    BITE: hardcoding True breaks this assertion.
    """
    from app.services.worker import _capability_blob

    assert _capability_blob({})["api"]["gemini"] is False, (
        "api.gemini must be False when no gemini credentials are in env"
    )


# ---------------------------------------------------------------------------
# 3. CLI flags follow shutil.which (monkeypatched at the boundary)
# ---------------------------------------------------------------------------

def test_cli_flag_true_when_which_finds_binary(monkeypatch):
    """blob['cli'][name] must be True when shutil.which finds any binary for that provider.

    BITE: always returning False for cli flags breaks this assertion.
    We monkeypatch shutil.which at the boundary — not the function under test.
    """
    import shutil
    from app.services.worker import _capability_blob

    # Make shutil.which always return a fake path (simulates all CLIs installed)
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")

    blob = _capability_blob({})
    for name, installed in blob["cli"].items():
        assert installed is True, (
            f"cli[{name!r}] must be True when shutil.which returns a path; got {installed}"
        )


def test_cli_flag_false_when_which_returns_none(monkeypatch):
    """blob['cli'][name] must be False when shutil.which returns None (not installed).

    BITE: always returning True for cli flags breaks this assertion.
    We monkeypatch shutil.which at the boundary — not the function under test.
    """
    import shutil
    from app.services.worker import _capability_blob

    # Make shutil.which always return None (simulates no CLIs installed)
    monkeypatch.setattr(shutil, "which", lambda name: None)

    blob = _capability_blob({})
    for name, installed in blob["cli"].items():
        assert installed is False, (
            f"cli[{name!r}] must be False when shutil.which returns None; got {installed}"
        )


def test_cli_flag_mixed_per_provider(monkeypatch):
    """blob['cli'] flags are per-provider: only installed providers are True.

    This test picks 'claude' (installed) vs 'kimi' (not installed) to verify
    per-provider resolution. We mock at shutil.which so only 'claude' binary names
    return a path.

    BITE: coalescing all providers to the same boolean breaks this.
    """
    import shutil
    from app.services.worker import _capability_blob
    from app.services import providers

    # Get the actual binary names for 'claude' so we can match them
    claude_binaries = set(providers.PROVIDERS["claude"].binary_names)

    def fake_which(name: str):
        return f"/usr/bin/{name}" if name in claude_binaries else None

    monkeypatch.setattr(shutil, "which", fake_which)

    blob = _capability_blob({})
    assert blob["cli"]["claude"] is True, "claude binary installed → cli['claude'] must be True"
    assert blob["cli"]["kimi"] is False, "kimi binary not installed → cli['kimi'] must be False"


# ---------------------------------------------------------------------------
# 4. _api_capable factored shared helper matches _compute_capabilities
# ---------------------------------------------------------------------------

def test_api_capable_and_compute_capabilities_agree():
    """_api_capable must agree with _compute_capabilities on can_claude_api + can_gemini_api.

    This is the drift-prevention test: if the two diverge, one of them is wrong.
    BITE: implementing _api_capable differently from _compute_capabilities breaks this.
    """
    from app.services.worker import _api_capable, _compute_capabilities

    env_cases = [
        {},
        {"ANTHROPIC_API_KEY": "sk-ant"},
        {"GEMINI_API_KEY": "aiza"},
        {"GOOGLE_APPLICATION_CREDENTIALS": "/sa.json", "GOOGLE_CLOUD_PROJECT": "proj"},
        {"ANTHROPIC_API_KEY": "sk-ant", "GEMINI_API_KEY": "aiza"},
    ]

    for env in env_cases:
        api = _api_capable(env)
        caps = _compute_capabilities(env)
        assert api["claude"] == caps["can_claude_api"], (
            f"_api_capable['claude'] != _compute_capabilities['can_claude_api'] for env={env}"
        )
        assert api["gemini"] == caps["can_gemini_api"], (
            f"_api_capable['gemini'] != _compute_capabilities['can_gemini_api'] for env={env}"
        )


# ---------------------------------------------------------------------------
# 5. CAPABILITY_BLOB module-level constant exists and has the right shape
# ---------------------------------------------------------------------------

def test_capability_blob_module_constant_exists():
    """CAPABILITY_BLOB must be a module-level constant in worker.py with the right shape.

    BITE: removing CAPABILITY_BLOB from module level breaks this assertion.
    """
    import app.services.worker as worker_module

    assert hasattr(worker_module, "CAPABILITY_BLOB"), (
        "worker module must expose CAPABILITY_BLOB at module level"
    )
    blob = worker_module.CAPABILITY_BLOB
    assert isinstance(blob, dict), "CAPABILITY_BLOB must be a dict"
    assert "cli" in blob and "api" in blob, (
        f"CAPABILITY_BLOB must have 'cli' and 'api' keys; got {set(blob.keys())}"
    )


# ---------------------------------------------------------------------------
# 6. _drain_check_and_beat passes CAPABILITY_BLOB to upsert_heartbeat
# ---------------------------------------------------------------------------

def test_drain_check_and_beat_passes_capabilities():
    """_drain_check_and_beat source must pass capabilities= to upsert_heartbeat.

    BITE: removing 'capabilities' from the upsert_heartbeat call breaks this.
    """
    import inspect
    from app.services.worker import Worker

    src = inspect.getsource(Worker._drain_check_and_beat)
    assert "capabilities" in src, (
        "_drain_check_and_beat must pass capabilities= keyword arg to upsert_heartbeat"
    )
    assert "CAPABILITY_BLOB" in src, (
        "_drain_check_and_beat must reference CAPABILITY_BLOB (the module-level constant)"
    )
