"""Manifest hygiene: only models the CLIs actually have may be offerable."""
from app.services.agent_models import MODEL_MANIFEST, is_valid


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
