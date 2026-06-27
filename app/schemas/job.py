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
    judge_status: Optional[str] = None    # ok | major_shipped | major_regen_failed | unavailable | refused | None


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
    added_phases: list[str] = []   # deps the closure auto-added beyond the user's selection (response only)
    planned_phases: list[str] = []  # the full content-phase list this job will run (subset closure, or the full subject flow); excludes extract
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
    custom_prompts: dict[str, str] | None = None   # {phase: markdown}; replaces built-in. Not persisted to prompts/.
    selected_phases: list[str] | None = None        # subset to run; None = full flow. Dependency-closure-expanded server-side.
    extract_provider: str | None = None   # None ⇒ global default (launch_defaults)
    extract_model: str | None = None
    judge_provider: str | None = None      # None ⇒ model_tiers auto-tier
    judge_model: str | None = None
