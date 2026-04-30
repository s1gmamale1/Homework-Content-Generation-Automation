from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.schemas.toc import TOCEntryOut


class BookOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    subject: str
    original_filename: str
    status: str
    error_message: Optional[str] = None
    gemini_file_expires_at: Optional[datetime] = None
    toc: Optional[list[TOCEntryOut]] = None
