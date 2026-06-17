from app.services.pipeline import _resolve_extract
from app.config import settings


def test_resolve_extract_explicit_override():
    assert _resolve_extract("claude", "claude-opus-4-7") == ("claude", "claude-opus-4-7")


def test_resolve_extract_falls_back_to_settings():
    assert _resolve_extract(None, None) == (settings.extract_provider, settings.extract_model)


def test_resolve_extract_partial_override_uses_settings_for_missing():
    assert _resolve_extract("gemini", None) == ("gemini", settings.extract_model)
