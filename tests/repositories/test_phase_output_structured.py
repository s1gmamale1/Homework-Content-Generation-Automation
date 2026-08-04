from app.models.phase_output import AUTHORING_MODES, PhaseOutput


def test_structured_columns_exist_on_model():
    for col in ("content_json", "authoring_mode", "content_schema_version", "renderer_version"):
        assert col in PhaseOutput.__table__.columns


def test_authoring_modes_enumerated():
    assert AUTHORING_MODES == (
        "structured", "markdown_fallback", "markdown_builtin",
        "markdown_custom", "markdown_legacy",
    )
