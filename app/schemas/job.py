from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class PhaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    phase_name: str
    phase_order: int
    status: str
    output_md: Optional[str] = None
    tokens_input: Optional[int] = None
    tokens_output: Optional[int] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    book_id: UUID
    toc_entry_id: UUID
    subject: str
    difficulty: Optional[str] = None
    status: str
    current_phase: Optional[str] = None
    error_message: Optional[str] = None
    assembled_md: Optional[str] = None
    games_json: Optional[dict[str, Any]] = None
    flashcards_json: Optional[dict[str, Any]] = None
    final_challenge_json: Optional[dict[str, Any]] = None
    memory_sprint_json: Optional[dict[str, Any]] = None
    reading_json: Optional[dict[str, Any]] = None
    source_map_json: Optional[dict[str, Any]] = None
    boss_arena_json: Optional[dict[str, Any]] = None
    cbp_json: Optional[dict[str, Any]] = None
    memory_check_json: Optional[dict[str, Any]] = None
    practice_rlc_json: Optional[dict[str, Any]] = None
    practice_error_detection_json: Optional[dict[str, Any]] = None
    practice_memory_match_json: Optional[dict[str, Any]] = None
    practice_tictactoe_json: Optional[dict[str, Any]] = None
    practice_jigsaw_json: Optional[dict[str, Any]] = None
    practice_sentence_json: Optional[dict[str, Any]] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    phases: list[PhaseOut] = []


class GenerateRequest(BaseModel):
    force: bool = False
    provider: str = "gemini"     # default to gemini for backwards compat
    model: str | None = None     # None ⇒ provider's default model
