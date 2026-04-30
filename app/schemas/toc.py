from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class TOCEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    chapter_number: Optional[str] = None
    chapter_title: Optional[str] = None
    section_number: str
    section_title: str
    page_start: Optional[int] = None
    page_end: Optional[int] = None
    order_index: int


class TOCEntryExtracted(BaseModel):
    chapter_number: Optional[str] = None
    chapter_title: Optional[str] = None
    section_number: str
    section_title: str
    page_start: Optional[int] = None
    page_end: Optional[int] = None


class ExtractedTOC(BaseModel):
    entries: list[TOCEntryExtracted]
