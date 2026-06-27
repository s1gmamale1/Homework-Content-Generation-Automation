"""Pure unit tests for agent_models role-resolution helpers.

Tests are offline (no DB, no HTTP). Each covers one resolution rule.
"""
from app.services.agent_models import (
    resolve_role_selection,
    resolve_role_transport_default,
)


def test_auto_provider_uses_global_default_pair():
    assert resolve_role_selection(None, None, "gemini", "gemini-2.5-flash") == (
        "gemini", "gemini-2.5-flash",
    )


def test_explicit_provider_and_model_passthrough():
    assert resolve_role_selection("claude", "claude-opus-4-7", "gemini", "gemini-2.5-flash") == (
        "claude", "claude-opus-4-7",
    )


def test_explicit_provider_auto_model_uses_that_providers_default_not_global():
    # claude picked, model Auto -> claude's own default, NOT gemini-2.5-flash
    p, m = resolve_role_selection("claude", None, "gemini", "gemini-2.5-flash")
    assert p == "claude"
    assert m == "claude-sonnet-4-6"  # default_model("claude") == first manifest entry


def test_transport_inherit_falls_to_default_explicit_wins():
    assert resolve_role_transport_default("inherit", "api") == "api"
    assert resolve_role_transport_default("cli", "api") == "cli"
    assert resolve_role_transport_default("inherit", "inherit") == "inherit"
