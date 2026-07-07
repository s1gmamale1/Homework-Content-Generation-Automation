from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class TOCEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    chapter_number: Optional[str] = None
    chapter_title: Optional[str] = None
    # Real-world TOCs often contain unnumbered sections (intros, prefaces,
    # appendices). Make ``section_number`` optional so extraction does not
    # reject otherwise-valid entries.
    section_number: Optional[str] = None
    section_title: str
    page_start: Optional[int] = None
    page_end: Optional[int] = None
    order_index: int
    # Latest homework job status for this section (None if no job exists).
    # Populated by /api/v1/books/{id} so the TOC list shows a per-row indicator.
    latest_job_id: Optional[UUID] = None
    latest_job_status: Optional[str] = None
    # Row class (lesson/header/recall/revision/test/other), computed on-the-fly
    # by app.services.toc_classifier at read time — no DB column, so it
    # self-heals on TOC edits. Populated by _enriched_toc_entries.
    entry_class: Optional[str] = None


class TOCEntryExtracted(BaseModel):
    chapter_number: Optional[str] = None
    chapter_title: Optional[str] = None
    # See ``TOCEntryOut.section_number`` — same rationale.
    section_number: Optional[str] = None
    section_title: str
    page_start: Optional[int] = None
    page_end: Optional[int] = None


class ExtractedTOC(BaseModel):
    entries: list[TOCEntryExtracted]


class TOCValidation(BaseModel):
    verdict: Literal["verified", "mismatch"]
    confidence: Literal["low", "medium", "high"]
    issues: list[str]  # concrete problems; empty when verified
