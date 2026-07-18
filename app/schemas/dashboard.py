from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class CoverageEntryOut(BaseModel):
    grade: Optional[str]
    subject: str
    book_id: str
    book_status: str
    source_language: str
    original_filename: str
    toc_validation: Optional[str]
    lessons_total: int
    done: int
    running: int
    pending: int
    failed: int
    cancelled: int
    batch_id: Optional[str]
    paused: bool


class CoverageOut(BaseModel):
    output_language: str
    entries: list[CoverageEntryOut]
