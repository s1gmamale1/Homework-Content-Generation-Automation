from typing import Literal, Optional

from pydantic import BaseModel

from app.schemas.toc import TOCEntryOut


class TOCStatusEvent(BaseModel):
    status: Literal["uploading", "toc_extracting"]


class TOCReadyEvent(BaseModel):
    entries: list[TOCEntryOut]


class TOCErrorEvent(BaseModel):
    message: str


class PhaseStartedEvent(BaseModel):
    phase_name: str
    phase_order: int


class PhaseCompletedEvent(BaseModel):
    phase_name: str
    phase_order: int
    output_md: str
    tokens_input: Optional[int] = None
    tokens_output: Optional[int] = None


class DifficultyClassifiedEvent(BaseModel):
    difficulty: Literal["easy", "hard"]


class JobCompletedEvent(BaseModel):
    job_id: str
    download_url: str


class JobErrorEvent(BaseModel):
    phase_name: Optional[str] = None
    message: str
