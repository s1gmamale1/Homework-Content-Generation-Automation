"""Manifest hygiene: only models the CLIs actually have may be offerable."""
from app.services.agent_models import (
    API_ONLY_PROVIDERS,
    MODEL_MANIFEST,
    api_supported,
    default_model,
    is_valid,
    validate_transport,
)


def test_phantom_gemini_3_5_flash_removed():
    # gemini-3.5-flash does NOT exist in the gemini CLI — it returns
    # ModelNotFoundError ("Requested entity was not found"), verified live
    # against the CLI. It must not be offerable nor pass is_valid.
    assert "gemini-3.5-flash" not in MODEL_MANIFEST["gemini"]
    assert is_valid("gemini", "gemini-3.5-flash") is False


def test_real_gemini_models_still_valid():
    # The other manifest gemini models all resolved OK against the CLI.
    for m in ("gemini-2.5-flash", "gemini-2.5-pro", "gemini-3.1-pro-preview",
              "gemini-3-flash-preview", "gemini-3.1-flash-lite-preview"):
        assert is_valid("gemini", m) is True, m


def test_clodex_live_text_catalog_and_transport_contract():
    assert MODEL_MANIFEST["clodex"] == [
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-5.6-luna",
        "gpt-5.5",
        "codex-auto-review",
    ]
    assert "gpt-image-2" not in MODEL_MANIFEST["clodex"]
    assert default_model("clodex") == "gpt-5.6-sol"
    assert api_supported("clodex") is True
    assert API_ONLY_PROVIDERS == frozenset({"clodex"})
    assert validate_transport("clodex", "gpt-5.6-luna", "api") is None


def test_clodex_requires_api_and_explicit_model():
    assert "api-only" in (validate_transport("clodex", "gpt-5.6-luna", "cli") or "")
    assert "explicit model" in (validate_transport("clodex", None, "api") or "")
