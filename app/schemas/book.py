from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.schemas.toc import TOCEntryOut


class BookOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    subject: str
    grade: Optional[int] = None
    language: Optional[str] = None
    original_filename: str
    status: str
    error_message: Optional[str] = None
    gemini_file_expires_at: Optional[datetime] = None
    file_size_bytes: Optional[int] = None
    created_at: Optional[datetime] = None
    toc: Optional[list[TOCEntryOut]] = None
