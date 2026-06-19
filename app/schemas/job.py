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
    extract_provider: Optional[str] = None
    extract_model: Optional[str] = None
    judge_provider: Optional[str] = None
    judge_model: Optional[str] = None
    phases: list[PhaseOut] = []
    notion_skip_reason: Optional[str] = None
    # fleet-api-4: never-pay-twice rebill warning (additive; absent on normal creates).
    # Set only when force=True and a prior done api job exists for this section.
    prior_api_cost_usd: Optional[float] = None
    would_rebill: Optional[bool] = None


class GenerateRequest(BaseModel):
    force: bool = False
    provider: str = "gemini"     # default to gemini for backwards compat
    model: str | None = None     # None ⇒ provider's default model
    transport: str = "cli"       # "cli" (subprocess) vs "api" (claude/gemini only)
    extract_transport: str = "inherit"   # per-role override; "inherit" follows `transport`
    judge_transport: str = "inherit"
    extract_provider: str | None = None   # None ⇒ settings.extract_provider
    extract_model: str | None = None
    judge_provider: str | None = None      # None ⇒ model_tiers auto-tier
    judge_model: str | None = None
