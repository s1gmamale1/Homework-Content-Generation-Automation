from app.services.agent_models import (
    OUTPUT_LANGUAGES, validate_output_language, resolve_output_language)

def test_domain_is_exactly_three():
    assert OUTPUT_LANGUAGES == frozenset({"uz", "en", "ru"})

def test_valid_values_pass():
    for v in ("uz", "en", "ru"):
        assert validate_output_language(v, allow_none=False) is None

def test_off_domain_returns_error():
    assert validate_output_language("fr", allow_none=False) is not None

def test_none_rejected_when_not_allowed_allowed_when_allowed():
    assert validate_output_language(None, allow_none=False) is not None
    assert validate_output_language(None, allow_none=True) is None

def test_resolve_prefers_explicit_then_default():
    assert resolve_output_language("en", "uz") == "en"
    assert resolve_output_language(None, "ru") == "ru"
