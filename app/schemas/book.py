from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, computed_field

from app.schemas.toc import TOCEntryOut
from app.services import subjects


class BookOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    subject: str
    grade: Optional[str] = None
    original_filename: str
    status: str
    error_message: Optional[str] = None
    gemini_file_expires_at: Optional[datetime] = None
    file_size_bytes: Optional[int] = None
    created_at: Optional[datetime] = None
    toc: Optional[list[TOCEntryOut]] = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def subject_variant(self) -> Optional[str]:
        """"jahon"|"ozbekiston" for a history book (derived from the filename),
        else None — lets the FE distinguish the two history textbooks without a
        coarser subject code."""
        return subjects.history_variant(self.subject, self.original_filename)
