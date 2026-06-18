from datetime import datetime
from typing import Optional
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
    validation_warnings: Optional[list[str]] = None


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    book_id: UUID
    toc_entry_id: UUID
    subject: str
    status: str
    current_phase: Optional[str] = None
    error_message: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    transport: str = "cli"
    extract_transport: str = "inherit"   # "cli" | "api" | "inherit" (follow `transport`)
    judge_transport: str = "inherit"
    phases: list[PhaseOut] = []
    added_phases: list[str] = []   # deps the closure auto-added beyond the user's selection (response only)
    notion_skip_reason: Optional[str] = None


class GenerateRequest(BaseModel):
    force: bool = False
    provider: str = "gemini"     # default to gemini for backwards compat
    model: str | None = None     # None ⇒ provider's default model
    transport: str = "cli"       # "cli" (subprocess) vs "api" (claude/gemini only)
    extract_transport: str = "inherit"   # per-role override; "inherit" follows `transport`
    judge_transport: str = "inherit"
    custom_prompts: dict[str, str] | None = None   # {phase: markdown}; replaces built-in. Not persisted to prompts/.
    selected_phases: list[str] | None = None        # subset to run; None = full flow. Dependency-closure-expanded server-side.
