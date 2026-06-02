from types import SimpleNamespace
from app.services.job_artifacts import structured_artifacts, build_content_json


def _job():
    fields = dict.fromkeys(
        [
            "games_json", "flashcards_json", "final_challenge_json", "memory_sprint_json",
            "reading_json", "cbp_json", "memory_check_json", "boss_arena_json",
            "source_map_json", "practice_rlc_json", "practice_error_detection_json",
            "practice_memory_match_json", "practice_tictactoe_json", "practice_jigsaw_json",
            "practice_sentence_json",
        ],
        None,
    )
    return SimpleNamespace(
        id="job-uuid", subject="geometriya-g7-11", provider="claude", model="claude-sonnet-4-6",
        assembled_md="# hw", **fields,
    )


def test_structured_artifacts_has_all_phase_files_with_defaults():
    arts = structured_artifacts(_job())
    assert arts["boss-arena.json"] == {"questions": []}
    assert arts["source-map.json"] == {"concepts": []}
    assert arts["case-based-preview.json"] == {}
    assert "memory-check.json" in arts


def test_build_content_json_wraps_metadata_and_phases():
    doc = build_content_json(_job(), generated_at="2026-06-02T00:00:00Z")
    assert doc["metadata"]["job_id"] == "job-uuid"
    assert doc["metadata"]["subject"] == "geometriya-g7-11"
    assert doc["metadata"]["generated_at"] == "2026-06-02T00:00:00Z"
    assert "boss-arena.json" in doc["phases"]
