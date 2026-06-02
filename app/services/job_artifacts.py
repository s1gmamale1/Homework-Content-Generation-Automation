"""Serialize a HomeworkJob's structured-phase columns into downloadable
artifacts. Single source of truth shared by the /download endpoint and the
Notion archive."""

from __future__ import annotations

from typing import Any


def structured_artifacts(job: Any) -> dict[str, dict]:
    """Map filename → structured payload, with the same empty-defaults the
    download endpoint uses so every key is always present."""
    return {
        "games.json": job.games_json or {"games": []},
        "flashcards.json": job.flashcards_json or {"cards": []},
        "final-challenge.json": job.final_challenge_json or {"questions": []},
        "memory-sprint.json": job.memory_sprint_json or {"items": []},
        "reading.json": job.reading_json or {"passage_md": "", "checkpoints": []},
        "case-based-preview.json": job.cbp_json or {},
        "memory-check.json": job.memory_check_json or {"items": [], "pass_threshold": 0.60},
        "boss-arena.json": job.boss_arena_json or {"questions": []},
        "source-map.json": job.source_map_json or {"concepts": []},
        # PR-3 Practice Arc games (only the ones a subject ran are non-empty).
        "practice-rlc.json": job.practice_rlc_json or {},
        "practice-error-detection.json": job.practice_error_detection_json or {},
        "practice-memory-match.json": job.practice_memory_match_json or {},
        "practice-tictactoe.json": job.practice_tictactoe_json or {},
        "practice-jigsaw.json": job.practice_jigsaw_json or {},
        "practice-sentence.json": job.practice_sentence_json or {},
    }


def build_content_json(job: Any, *, generated_at: str) -> dict:
    """One combined document for the Notion `content.json` attachment."""
    return {
        "metadata": {
            "job_id": str(job.id),
            "subject": job.subject,
            "provider": getattr(job, "provider", None),
            "model": getattr(job, "model", None),
            "generated_at": generated_at,
        },
        "phases": structured_artifacts(job),
    }
