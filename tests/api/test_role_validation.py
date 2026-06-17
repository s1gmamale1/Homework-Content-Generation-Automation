from app.services.agent_models import validate_transport


def test_api_role_requires_explicit_model():
    assert validate_transport("claude", None, "api") is not None      # api needs a model
    assert validate_transport("gemini", "gemini-2.5-flash", "api") is None
    assert validate_transport("kimi", "k2", "api") is not None         # non-api provider
    assert validate_transport("claude", None, "cli") is None           # cli is lenient
