"""Unit tests for resolve_session_limit_strategy in agent_models.py.

No DB required — pure logic tests with monkeypatching.
"""
from __future__ import annotations

import pytest


def test_batch_switch_wins():
    """batch_value='switch' → 'switch' regardless of env default."""
    from app.services import agent_models

    assert agent_models.resolve_session_limit_strategy("switch") == "switch"


def test_batch_pause_wins():
    """batch_value='pause' → 'pause' regardless of env default."""
    from app.services import agent_models

    assert agent_models.resolve_session_limit_strategy("pause") == "pause"


def test_inherit_falls_back_to_env(monkeypatch):
    """batch_value='inherit' → reads settings.session_limit_strategy."""
    from app.config import settings
    from app.services import agent_models

    monkeypatch.setattr(settings, "session_limit_strategy", "switch")
    assert agent_models.resolve_session_limit_strategy("inherit") == "switch"


def test_none_falls_back_to_env(monkeypatch):
    """batch_value=None → reads settings.session_limit_strategy."""
    from app.config import settings
    from app.services import agent_models

    monkeypatch.setattr(settings, "session_limit_strategy", "switch")
    assert agent_models.resolve_session_limit_strategy(None) == "switch"


def test_env_default_is_not_hardcoded(monkeypatch):
    """Prove the env-default branch reads settings, not a hardcoded literal.

    Set settings.session_limit_strategy to each valid value and confirm the
    resolver mirrors it, so this test would FAIL if the resolver returned a
    hardcoded 'pause' instead of consulting settings.
    """
    from app.config import settings
    from app.services import agent_models

    for val in ("pause", "switch"):
        monkeypatch.setattr(settings, "session_limit_strategy", val)
        assert agent_models.resolve_session_limit_strategy("inherit") == val
        assert agent_models.resolve_session_limit_strategy(None) == val
