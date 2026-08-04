import pytest

from app.schemas.content_json import SentenceFillConfig
from app.services import phase_render
from app.services.phase_artifact import (
    PhaseArtifact, StructuredPhaseError, artifact_from_config, artifact_from_markdown,
)


def _cfg():
    return SentenceFillConfig.model_validate({"items": [{
        "id": "i1", "mode": "word_bank", "passage": "A ___ ran.",
        "answers": ["cat"], "word_bank": ["cat", "dog"]}]})


def test_artifact_from_config_is_complete_and_consistent():
    art = artifact_from_config("practice-sentence", _cfg())
    assert art.authoring_mode == "structured"
    assert art.content_schema_version == "sentence_fill_config@1"
    # Assert against the live constant, never a hard-coded literal, so this
    # cannot rot when the renderer gains a new section (it's "2" as of the
    # author-only "## Answer key" addition).
    assert art.renderer_version == phase_render.RENDERER_VERSION
    assert "A ___ ran." in art.output_md
    # content_json is a plain dict (model_dump), never the model itself
    assert isinstance(art.content_json, dict)
    assert art.content_json["items"][0]["id"] == "i1"


def test_artifact_from_markdown_has_no_structured_fields():
    art = artifact_from_markdown("# hi", mode="markdown_fallback")
    assert art.content_json is None
    assert art.content_schema_version is None
    assert art.renderer_version is None
    assert art.authoring_mode == "markdown_fallback"


def test_artifact_from_markdown_rejects_unknown_mode():
    with pytest.raises(ValueError):
        artifact_from_markdown("# hi", mode="structured")


def test_render_failure_becomes_structured_phase_error():
    with pytest.raises(StructuredPhaseError):
        artifact_from_config("flashcards", _cfg())
